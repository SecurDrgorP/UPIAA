# FILE: backend/core/webhook_registry.py
#
# WebhookRegistry — bridges FastAPI webhook routes ↔ webhook_trigger nodes
# ─────────────────────────────────────────────────────────────────────────────
#
# This module owns two things:
#
#   1.  WebhookRegistry (singleton)
#       A dict of asyncio.Queue objects keyed by (workflow_id, path).
#       The webhook_trigger node calls register() to get its queue.
#       The FastAPI route handler calls deposit() to push a request in.
#
#   2.  register_webhook_routes(app)
#       Called once in main.py lifespan startup.
#       Adds the catch-all route:
#         POST/GET/PUT/PATCH/DELETE /api/webhooks/{workflow_id}/{path}
#       to the FastAPI app.
#
# ─────────────────────────────────────────────────────────────────────────────

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Optional, Set

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Data structures
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class _WebhookEntry:
    """One registered webhook endpoint — owned by one workflow_trigger node."""
    queue:         asyncio.Queue
    methods:       Set[str]                  # accepted HTTP methods (uppercase)
    secret_token:  Optional[str]             # None = no auth required
    response_mode: str                       # "acknowledge" | "last_node"
    registered_at: float = field(default_factory=time.monotonic)

    # For response_mode="last_node":
    # The FastAPI route will suspend the HTTP response until this future
    # is resolved by the orchestrator with the workflow's final output.
    response_future: Optional[asyncio.Future] = field(default=None)


# ─────────────────────────────────────────────────────────────────────────────
# Registry singleton
# ─────────────────────────────────────────────────────────────────────────────

class WebhookRegistry:
    """
    Singleton.  Manages the mapping:
      (workflow_id, path) → _WebhookEntry

    Thread/task safety: all mutations happen on the event loop;
    asyncio.Queue is inherently coroutine-safe.
    """

    _instance: Optional["WebhookRegistry"] = None

    def __init__(self) -> None:
        # key: (workflow_id, path)
        self._entries: dict[tuple[str, str], _WebhookEntry] = {}

    @classmethod
    def get_instance(cls) -> "WebhookRegistry":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    # ── registration ──────────────────────────────────────────────────────────

    def register(
        self,
        *,
        workflow_id:    str,
        path:           str,
        methods:        Set[str],
        secret_token:   Optional[str],
        response_mode:  str,
    ) -> asyncio.Queue:
        """
        Called by webhook_trigger node when it starts.
        Creates (or reuses) the queue for this (workflow_id, path) pair.
        Returns the queue; the node will block on queue.get().
        """
        key = (workflow_id, path)

        if key in self._entries:
            # Already registered — reuse existing queue.
            # This happens if the Temporal activity is retried.
            entry = self._entries[key]
            entry.methods       = methods
            entry.secret_token  = secret_token
            entry.response_mode = response_mode
            logger.debug(
                "webhook_registry: re-registered %s/%s", workflow_id, path
            )
        else:
            entry = _WebhookEntry(
                queue         = asyncio.Queue(maxsize=1),
                methods       = methods,
                secret_token  = secret_token,
                response_mode = response_mode,
            )
            self._entries[key] = entry
            logger.info(
                "webhook_registry: registered  /api/webhooks/%s/%s  [%s]",
                workflow_id,
                path,
                "|".join(sorted(methods)),
            )

        return entry.queue

    def unregister(self, workflow_id: str, path: str) -> None:
        """Called by webhook_trigger node after it receives a request."""
        key = (workflow_id, path)
        self._entries.pop(key, None)
        logger.info(
            "webhook_registry: unregistered /api/webhooks/%s/%s",
            workflow_id, path,
        )

    def is_registered(self, workflow_id: str, path: str) -> bool:
        return (workflow_id, path) in self._entries

    def deposit(
        self,
        workflow_id: str,
        path:        str,
        payload:     dict[str, Any],
    ) -> Optional[asyncio.Future]:
        """
        Called by the FastAPI route handler when a request arrives.
        Puts payload into the queue.

        Returns the response_future if response_mode is 'last_node',
        otherwise None.  The route handler should await this future if
        non-None, then return its value as the HTTP response.
        """
        key   = (workflow_id, path)
        entry = self._entries.get(key)

        if entry is None:
            return None   # no node is listening — caller will get 404

        try:
            entry.queue.put_nowait(payload)
        except asyncio.QueueFull:
            # A previous request is still being processed.
            # Return 429 to the caller (handled in the route below).
            raise _QueueFullError()

        if entry.response_mode == "last_node":
            loop   = asyncio.get_event_loop()
            future = loop.create_future()
            entry.response_future = future
            return future

        return None

    def resolve_response(
        self,
        workflow_id: str,
        path:        str,
        result:      Any,
    ) -> None:
        """
        Called by the workflow orchestrator when execution completes,
        only relevant for response_mode='last_node'.
        Resolves the pending HTTP response future.
        """
        key   = (workflow_id, path)
        entry = self._entries.get(key)
        if entry and entry.response_future and not entry.response_future.done():
            entry.response_future.set_result(result)

    def get_entry(self, workflow_id: str, path: str) -> Optional[_WebhookEntry]:
        return self._entries.get((workflow_id, path))


class _QueueFullError(Exception):
    pass


# ─────────────────────────────────────────────────────────────────────────────
# FastAPI route registration — call once in main.py lifespan startup
# ─────────────────────────────────────────────────────────────────────────────

def register_webhook_routes(app: FastAPI) -> None:
    """
    Adds the catch-all webhook route to the FastAPI app.
    Call this inside the lifespan startup, AFTER all other routes are mounted.

    Usage in main.py:
        from core.webhook_registry import register_webhook_routes
        register_webhook_routes(app)
    """

    @app.api_route(
        "/api/webhooks/{workflow_id}/{path}",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        tags=["webhooks"],
        summary="Webhook trigger endpoint",
        response_model=None,
    )
    async def webhook_endpoint(
        workflow_id: str,
        path:        str,
        request:     Request,
    ) -> Response:
        """
        Catch-all webhook route.
        Any HTTP method is accepted here; per-node method filtering
        is enforced inside this handler using the registry entry.
        """
        registry = WebhookRegistry.get_instance()

        # ── 1. Check if a node is listening ───────────────────────────────────
        if not registry.is_registered(workflow_id, path):
            return JSONResponse(
                status_code = 404,
                content     = {
                    "error":   "no_listener",
                    "message": (
                        f"No webhook_trigger node is currently listening on "
                        f"/api/webhooks/{workflow_id}/{path}. "
                        f"Make sure the workflow is running and the node is active."
                    ),
                },
            )

        entry = registry.get_entry(workflow_id, path)

        # ── 2. Method check ────────────────────────────────────────────────────
        method = request.method.upper()
        if entry.methods and method not in entry.methods:
            return JSONResponse(
                status_code = 405,
                headers     = {"Allow": ", ".join(sorted(entry.methods))},
                content     = {
                    "error":   "method_not_allowed",
                    "message": (
                        f"This webhook only accepts: "
                        f"{', '.join(sorted(entry.methods))}. "
                        f"You sent: {method}."
                    ),
                },
            )

        # ── 3. Secret token check ──────────────────────────────────────────────
        if entry.secret_token:
            provided = request.headers.get("x-webhook-token", "")
            # constant-time comparison to prevent timing attacks
            if not hmac.compare_digest(
                provided.encode(),
                entry.secret_token.encode(),
            ):
                return JSONResponse(
                    status_code = 401,
                    content     = {
                        "error":   "unauthorized",
                        "message": (
                            "Missing or invalid X-Webhook-Token header."
                        ),
                    },
                )

        # ── 4. Parse body ──────────────────────────────────────────────────────
        body_raw: str  = ""
        body_dict: dict = {}

        raw_bytes = await request.body()
        if raw_bytes:
            body_raw = raw_bytes.decode("utf-8", errors="replace")
            content_type = request.headers.get("content-type", "")

            if "application/json" in content_type:
                try:
                    body_dict = json.loads(body_raw)
                    if not isinstance(body_dict, dict):
                        # JSON array or scalar — wrap it
                        body_dict = {"data": body_dict}
                except json.JSONDecodeError:
                    body_dict = {"raw": body_raw}

            elif "application/x-www-form-urlencoded" in content_type \
                    or "multipart/form-data" in content_type:
                form = await request.form()
                body_dict = dict(form)

            else:
                # Unknown content type — expose as raw_body only
                body_dict = {}

        # ── 5. Build payload dict ──────────────────────────────────────────────
        # This is exactly what downstream nodes receive via
        # {{webhook_trigger.body.xxx}}, {{webhook_trigger.query.xxx}}, etc.

        query_params = dict(request.query_params)

        # Flatten: if body is empty but query params exist, promote them
        # into body so that GET ?text=hello works the same as POST {"text":"hello"}
        if not body_dict and query_params:
            body_dict = dict(query_params)

        # Sanitise headers — exclude auth headers from output
        safe_headers = {
            k.lower(): v
            for k, v in request.headers.items()
            if k.lower() not in {
                "x-webhook-token", "authorization",
                "cookie", "set-cookie",
            }
        }

        client_host = (request.client.host if request.client else "unknown")

        payload: dict[str, Any] = {
            "method":      method,
            "path":        path,
            "workflow_id": workflow_id,
            "headers":     safe_headers,
            "query":       query_params,
            "body":        body_dict,
            "raw_body":    body_raw,
            "received_at": time.time(),
            "client_ip":   client_host,
        }

        # ── 6. Deposit into the node's queue ──────────────────────────────────
        try:
            response_future = registry.deposit(workflow_id, path, payload)
        except _QueueFullError:
            return JSONResponse(
                status_code = 429,
                content     = {
                    "error":   "too_many_requests",
                    "message": (
                        "A previous request is still being processed. "
                        "Wait for the current workflow execution to finish."
                    ),
                },
            )

        # ── 7. Send HTTP response to caller ───────────────────────────────────

        if entry.response_mode == "last_node" and response_future is not None:
            # Suspend the HTTP connection until the workflow finishes.
            # We cap this at 55 seconds to stay under nginx proxy_read_timeout.
            try:
                result = await asyncio.wait_for(response_future, timeout=55.0)
                return JSONResponse(
                    status_code = 200,
                    content     = result if isinstance(result, dict) else {"result": result},
                )
            except asyncio.TimeoutError:
                return JSONResponse(
                    status_code = 202,
                    content     = {
                        "status":  "accepted",
                        "message": (
                            "Workflow is still running after 55s. "
                            "Poll /api/executions to get the result."
                        ),
                    },
                )
        else:
            # Default: acknowledge immediately
            return JSONResponse(
                status_code = 202,
                content     = {
                    "status":      "accepted",
                    "workflow_id": workflow_id,
                    "path":        path,
                    "message":     "Request received. Workflow execution started.",
                },
            )
