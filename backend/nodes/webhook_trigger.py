# FILE: backend/nodes/webhook_trigger.py
#
# Webhook Trigger Node — M8M
# ─────────────────────────────────────────────────────────────────────────────
#
# ARCHITECTURE NOTE — why this node works the way it does
# ────────────────────────────────────────────────────────
# FastAPI already owns port 8000. The node cannot start its own server.
# Instead, it works in two halves:
#
#   HALF A — FastAPI route (registered once at startup by WebhookRegistry):
#     POST /api/webhooks/{workflow_id}/{path}
#     GET  /api/webhooks/{workflow_id}/{path}      ← for curl -G, browser tests
#     Any request to that URL is deposited into an asyncio.Queue keyed by
#     (workflow_id, path).
#
#   HALF B — this node (runs as a Temporal activity):
#     Calls WebhookRegistry.wait_for_request(workflow_id, path, timeout)
#     which is just queue.get() with a timeout.
#     When an item arrives, returns it as the node output.
#     Downstream nodes access {{webhook_trigger.body.text}} etc.
#
# This is the same pattern used by email_receive.py (blocks on imap poll)
# and timer_cron.py (blocks on asyncio.sleep), per ADR-005.
#
# ─────────────────────────────────────────────────────────────────────────────
#
# USAGE — curl examples
# ─────────────────────
# The webhook URL is always:
#   http://localhost:8000/api/webhooks/{workflow_id}/{path}
#
# The {workflow_id} is shown on the canvas node config panel.
# The {path} is configured by you (default: "trigger").
#
# POST with JSON body:
#   curl -X POST http://localhost:8000/api/webhooks/wckh8dea/trigger \
#        -H "Content-Type: application/json" \
#        -d '{"message": {"text": "what is 1+1"}, "user": "alice"}'
#
# POST with form data:
#   curl -X POST http://localhost:8000/api/webhooks/wckh8dea/trigger \
#        -F "text=what is 1+1" -F "user=alice"
#
# GET with query string:
#   curl "http://localhost:8000/api/webhooks/wckh8dea/trigger?text=hello&user=alice"
#
# Downstream nodes receive:
#   {{webhook_trigger.body.message.text}}   → "what is 1+1"
#   {{webhook_trigger.body.user}}           → "alice"
#   {{webhook_trigger.method}}              → "POST"
#   {{webhook_trigger.headers.content-type}}→ "application/json"
#   {{webhook_trigger.query.user}}          → (for GET requests)
#   {{webhook_trigger.raw_body}}            → raw string if not JSON
#
# ─────────────────────────────────────────────────────────────────────────────
#
# OUTPUT SHAPE
# ────────────
# {
#   "method":       "POST" | "GET" | "PUT" | ...,
#   "path":         "trigger",               ← the configured path segment
#   "workflow_id":  "wckh8dea",
#   "headers":      {"content-type": "application/json", ...},
#   "query":        {"param": "value", ...}, ← URL query string params
#   "body":         { ... },                 ← parsed JSON body (dict)
#   "raw_body":     "...",                   ← raw body string (always set)
#   "received_at":  1711234567.891,          ← unix timestamp (float)
#   "client_ip":    "172.19.0.1",
# }
#
# ─────────────────────────────────────────────────────────────────────────────

from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import Any

try:
    from temporalio.activity import heartbeat as temporal_heartbeat
    _TEMPORAL_AVAILABLE = True
except ImportError:
    _TEMPORAL_AVAILABLE = False

from core.node_registry import register_action
from core.webhook_registry import WebhookRegistry   # see companion file below

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────

_HEARTBEAT_INTERVAL = 30          # seconds — keeps Temporal activity alive
_POLL_SLICE         = 25          # seconds — how long each queue.get() waits
                                  # before we loop, send heartbeat, and retry
_MAX_WAIT_DAYS      = 7           # hard ceiling — node gives up after 7 days

# ─────────────────────────────────────────────────────────────────────────────
# Node registration
# ─────────────────────────────────────────────────────────────────────────────

@register_action(
    type        = "webhook_trigger",
    label       = "Webhook Trigger",
    icon        = "🔗",
    category    = "Triggers",
    description = (
        "Starts the workflow when an HTTP request arrives at its unique URL. "
        "Supports GET, POST (JSON or form), and custom headers. "
        "Use curl, Postman, or any HTTP client to trigger it."
    ),
    config_schema = [
        {
            "key":         "path",
            "label":       "Webhook path",
            "type":        "string",
            "required":    False,
            "default":     "trigger",
            "placeholder": "trigger",
            "description": (
                "The last segment of the webhook URL. "
                "Only letters, numbers, and hyphens. "
                "Example: 'trigger' → /api/webhooks/{workflow_id}/trigger"
            ),
        },
        {
            "key":         "methods",
            "label":       "Accepted HTTP methods",
            "type":        "multiselect",
            "required":    False,
            "default":     ["GET", "POST"],
            "options": [
                {"value": "GET",    "label": "GET"},
                {"value": "POST",   "label": "POST"},
                {"value": "PUT",    "label": "PUT"},
                {"value": "PATCH",  "label": "PATCH"},
                {"value": "DELETE", "label": "DELETE"},
            ],
            "description": (
                "Which HTTP methods will trigger the workflow. "
                "Requests with other methods receive 405 Method Not Allowed."
            ),
        },
        {
            "key":         "secret_token",
            "label":       "Secret token (optional)",
            "type":        "string",
            "secret":      True,
            "required":    False,
            "placeholder": "my-secret-token",
            "description": (
                "If set, every incoming request must include the header "
                "X-Webhook-Token: <token>. Requests without it receive 401."
            ),
        },
        {
            "key":         "response_mode",
            "label":       "HTTP response to caller",
            "type":        "select",
            "required":    False,
            "default":     "acknowledge",
            "options": [
                {
                    "value":       "acknowledge",
                    "label":       "Acknowledge immediately (202 Accepted)",
                    "description": "Caller gets 202 instantly; workflow runs async.",
                },
                {
                    "value":       "last_node",
                    "label":       "Wait and return last node output",
                    "description": (
                        "Caller blocks until the workflow finishes, "
                        "then receives the last node's output as JSON. "
                        "Max wait: 55 seconds (proxy timeout)."
                    ),
                },
            ],
            "description": (
                "What the webhook caller receives as an HTTP response. "
                "'Acknowledge' is best for fire-and-forget callers. "
                "'Last node output' is useful when curl needs the result."
            ),
        },
        {
            "key":         "timeout_hours",
            "label":       "Wait timeout (hours)",
            "type":        "number",
            "required":    False,
            "default":     168,
            "description": (
                "How many hours to wait for an incoming request before "
                "the node gives up with an error. Default 168 = 7 days."
            ),
        },
    ],
)
async def handle_webhook_trigger(
    config:     dict,
    input_data: dict,
    context:    dict,
) -> dict:
    """
    Webhook Trigger — blocks until an HTTP request arrives at the node's URL.

    This is a TRIGGER node. Place it at the START of a workflow.
    input_data is ignored (nothing comes before a trigger).
    """

    # ── 1. Validate and normalise config ─────────────────────────────────────

    raw_path: str = (config.get("path") or "trigger").strip().lower()
    if not re.fullmatch(r"[a-z0-9][a-z0-9\-]{0,63}", raw_path):
        return {
            "__input_error__": (
                f"path {raw_path!r} is invalid. "
                "Use only lowercase letters, numbers, and hyphens (max 64 chars). "
                "Example: 'trigger', 'my-hook', 'step1'."
            )
        }

    # Accepted methods — stored as a list in config, normalise to uppercase set
    raw_methods = config.get("methods") or ["GET", "POST"]
    if isinstance(raw_methods, str):
        raw_methods = [m.strip() for m in raw_methods.split(",")]
    accepted_methods: set[str] = {m.upper() for m in raw_methods if m}
    if not accepted_methods:
        accepted_methods = {"GET", "POST"}

    secret_token: str | None = (config.get("secret_token") or "").strip() or None
    response_mode: str       = config.get("response_mode", "acknowledge")
    timeout_hours: float     = float(config.get("timeout_hours") or 168)
    timeout_hours            = max(0.1, min(timeout_hours, 168))  # clamp [6min, 7d]
    timeout_seconds: float   = timeout_hours * 3600

    # ── 2. Resolve workflow_id from execution context ─────────────────────────
    #
    # The context dict is populated by the Temporal workflow before calling
    # each activity.  workflow_id is always present.

    workflow_id: str = (
        context.get("__workflow_id__")      # injected by _execute_graph (primary)
        or context.get("workflow_id")        # legacy fallback
        or context.get("execution_id")       # legacy fallback
        or ""
    )
    if not workflow_id:
        return {
            "__input_error__": (
                "Could not determine workflow_id from execution context. "
                "This node must be run inside a Temporal workflow execution."
            )
        }

    # ── 3. Register the webhook path ──────────────────────────────────────────
    #
    # WebhookRegistry.register() creates (or reuses) an asyncio.Queue for
    # this (workflow_id, path) pair and tells the FastAPI webhook route
    # which methods and secret token to enforce.
    #
    # The FastAPI route at /api/webhooks/{workflow_id}/{path} is a
    # PERMANENT route registered at startup — it always accepts requests
    # and queues them.  When no workflow is listening the queue just grows
    # until a workflow starts (or until the registry evicts old entries).

    registry = WebhookRegistry.get_instance()
    queue    = registry.register(
        workflow_id    = workflow_id,
        path           = raw_path,
        methods        = accepted_methods,
        secret_token   = secret_token,
        response_mode  = response_mode,
    )

    webhook_url = f"/api/webhooks/{workflow_id}/{raw_path}"
    logger.info(
        "webhook_trigger: registered %s — waiting for %s request "
        "(timeout %.0fh)",
        webhook_url,
        "|".join(sorted(accepted_methods)),
        timeout_hours,
    )

    # ── 4. Blocking wait loop (ADR-005 pattern) ───────────────────────────────
    #
    # We cannot do a single queue.get(timeout=timeout_seconds) because
    # Temporal needs a heartbeat every ≤30 seconds.
    # So we loop in _POLL_SLICE increments, heartbeating each iteration,
    # until either a request arrives or the total timeout expires.

    deadline     = time.monotonic() + timeout_seconds
    slice_number = 0

    while True:
        slice_number += 1
        remaining   = deadline - time.monotonic()

        if remaining <= 0:
            registry.unregister(workflow_id, raw_path)
            return {
                "__service_error__": (
                    f"webhook_trigger: no request received on {webhook_url} "
                    f"within {timeout_hours:.0f} hours. Node timed out."
                )
            }

        # ── heartbeat ─────────────────────────────────────────────────────────
        if _TEMPORAL_AVAILABLE:
            temporal_heartbeat(
                f"webhook_trigger: waiting on {webhook_url} "
                f"(slice {slice_number}, "
                f"{remaining / 3600:.1f}h remaining)"
            )

        # ── wait for next request ─────────────────────────────────────────────
        wait_for = min(_POLL_SLICE, remaining)
        try:
            request_payload: dict[str, Any] = await asyncio.wait_for(
                queue.get(),
                timeout=wait_for,
            )
        except asyncio.TimeoutError:
            # No request in this slice — loop and heartbeat again
            continue
        except asyncio.CancelledError:
            # Temporal cancelled this activity (workflow stopped or paused).
            # Unregister the path and return a clean timed-out result so the
            # workflow history is not contaminated with a hard failure.
            registry.unregister(workflow_id, raw_path)
            return {
                "method":      "",
                "path":        raw_path,
                "workflow_id": workflow_id,
                "headers":     {},
                "query":       {},
                "body":        {},
                "raw_body":    "",
                "received_at": 0.0,
                "client_ip":   "",
                "timed_out":   True,
            }

        # ── request arrived ───────────────────────────────────────────────────
        registry.unregister(workflow_id, raw_path)

        logger.info(
            "webhook_trigger: received %s %s from %s",
            request_payload.get("method", "?"),
            webhook_url,
            request_payload.get("client_ip", "unknown"),
        )

        return request_payload
