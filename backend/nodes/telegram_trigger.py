# FILE: backend/nodes/telegram_trigger.py
#
# Telegram Trigger Node — UPIAA
# ─────────────────────────────────────────────────────────────────────────────
# A LISTENER / TRIGGER node. It sits at the START of a workflow and blocks
# (non-blocking async poll) until a new Telegram message arrives, then passes
# that message downstream exactly like n8n's Telegram Trigger node.
#
# Behaviour contract (ADR-005):
#   - Runs as a long-lived Temporal activity (timeout = 8 days)
#   - Sends a Temporal heartbeat every 30 seconds to stay alive
#   - Uses asyncio.sleep() — NEVER time.sleep()
#   - On SIGTERM / cancellation the loop exits cleanly
#
# Output contract — what downstream nodes receive via {{node_id.xxx}}:
#   {
#     "message_id":   int,
#     "chat_id":      int | str,   ← pass this to telegram_send to reply
#     "chat_type":    str,         ← "private" | "group" | "supergroup" | "channel"
#     "from_user_id": int | None,
#     "from_username":str | None,
#     "text":         str,         ← the raw message text
#     "date":         int,         ← unix timestamp
#     "raw_update":   dict,        ← full Telegram Update object for power users
#   }
#
# Polling strategy:
#   Uses Telegram Bot API getUpdates long-polling with timeout=25 (seconds).
#   offset is advanced after each batch so messages are never re-delivered.
#   Between long-poll cycles a short asyncio.sleep(1) prevents tight loops
#   when Telegram returns immediately (e.g. on network error).
#
# ─────────────────────────────────────────────────────────────────────────────

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import httpx

# Temporal heartbeat — imported safely; if Temporal is not in scope
# (e.g. unit tests) the heartbeat call is skipped gracefully.
try:
    from temporalio import activity
    _TEMPORAL_AVAILABLE = True
except ImportError:
    _TEMPORAL_AVAILABLE = False

# NodeRegistry decorator — same pattern as every other node in backend/nodes/
from core.node_registry import register_action

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────

_TELEGRAM_BASE        = "https://api.telegram.org/bot{token}"
_LONG_POLL_TIMEOUT    = 25          # seconds — Telegram keeps the connection open
_HEARTBEAT_INTERVAL   = 30          # seconds — keeps Temporal activity alive
_RETRY_SLEEP          = 3           # seconds — sleep after a network error
_MAX_RETRIES          = 10          # consecutive errors before giving up
_GETME_TIMEOUT        = 10          # seconds — quick connectivity check on start

# ─────────────────────────────────────────────────────────────────────────────
# Node registration
# ─────────────────────────────────────────────────────────────────────────────

@register_action(
    type        = "telegram_trigger",
    label       = "Telegram Trigger",
    icon        = "💬",
    category    = "Communication",
    description = (
        "Listens for incoming Telegram messages and starts the workflow "
        "when one arrives. Place this node at the beginning of your workflow. "
        "Outputs the message text, chat ID, and full update object."
    ),
    config_schema = [
        {
            "key":         "credential_id",
            "label":       "Credential ID",
            "type":        "string",
            "required":    False,
            "placeholder": "cred_...",
            "description": (
                "Optional encrypted credential bundle ID. If set, the bundle "
                "should provide bot_token."
            ),
        },
        {
            "key":         "bot_token",
            "label":       "Bot Token",
            "type":        "string",
            "secret":      True,         # redacted from logs by orchestrator
            "required":    False,
            "placeholder": "123456:ABC-DEF...",
            "visibleWhen": {"credential_id": ""},
            "description": (
                "Manual bot token — only needed when no credential bundle is "
                "selected above. Get one from @BotFather on Telegram."
            ),
        },
        {
            "key":         "allowed_chat_ids",
            "label":       "Allowed Chat IDs (optional)",
            "type":        "string",
            "required":    False,
            "placeholder": "-100123456789, 987654321",
            "description": (
                "Comma-separated list of chat IDs to accept messages from. "
                "Leave empty to accept messages from any chat."
            ),
        },
        {
            "key":         "allowed_usernames",
            "label":       "Allowed Usernames (optional)",
            "type":        "string",
            "required":    False,
            "placeholder": "alice, bob",
            "description": (
                "Comma-separated list of Telegram usernames (without @) "
                "whose messages are accepted. Leave empty to accept from anyone."
            ),
        },
        {
            "key":         "filter_message_types",
            "label":       "Message types to accept",
            "type":        "select",
            "required":    False,
            "default":     "all",
            "options": [
                {"value": "text",  "label": "Text messages only"},
                {"value": "all",   "label": "All message types"},
            ],
            "description": (
                "Which message types trigger the workflow. "
                "'Text only' skips photos, stickers, documents, etc."
            ),
        },
        {
            "key":         "poll_timeout",
            "label":       "Long-poll timeout (seconds)",
            "type":        "number",
            "required":    False,
            "default":     25,
            "description": (
                "How long each getUpdates call waits for a new message "
                "before retrying. 25s is the Telegram-recommended value."
            ),
        },
    ],
)
async def handle_telegram_trigger(
    config:     dict,
    input_data: dict,
    context:    dict,
) -> dict:
    """
    Telegram Trigger — blocks until a qualifying message arrives.

    This is a TRIGGER node. It should be the FIRST node in a workflow.
    input_data is intentionally ignored (nothing comes before a trigger).

    Returns the first qualifying message as a structured dict.
    All downstream nodes access the message via {{telegram_trigger.text}},
    {{telegram_trigger.chat_id}}, etc.
    """

    # ── 1. Validate config ────────────────────────────────────────────────────

    bot_token = (config.get("bot_token") or "").strip()
    if not bot_token:
        return {
            "__input_error__": (
                "bot_token is required. Get one from @BotFather on Telegram."
            )
        }

    # Parse optional allow-lists
    allowed_chat_ids: set[int] = _parse_int_set(
        config.get("allowed_chat_ids") or ""
    )
    allowed_usernames: set[str] = _parse_str_set(
        config.get("allowed_usernames") or ""
    )
    filter_text_only: bool = (
        config.get("filter_message_types", "text") == "text"
    )
    poll_timeout: int = int(config.get("poll_timeout") or _LONG_POLL_TIMEOUT)
    poll_timeout = max(1, min(poll_timeout, 30))   # clamp to [1, 30]

    base_url = _TELEGRAM_BASE.format(token=bot_token)

    # ── 2. Validate bot token with a quick getMe call ─────────────────────────

    async with httpx.AsyncClient(timeout=_GETME_TIMEOUT) as client:
        try:
            resp = await client.get(f"{base_url}/getMe")
            resp.raise_for_status()
            bot_info = resp.json()
            if not bot_info.get("ok"):
                return {
                    "__input_error__": (
                        f"Telegram rejected the bot token: "
                        f"{bot_info.get('description', 'unknown error')}"
                    )
                }
            bot_username = bot_info["result"].get("username", "unknown")
            logger.info(
                "telegram_trigger: connected as @%s — waiting for messages",
                bot_username,
            )
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 401:
                return {
                    "__input_error__": (
                        "Bot token is invalid (401 Unauthorized). "
                        "Check your token from @BotFather."
                    )
                }
            return {
                "__service_error__": (
                    f"Telegram API returned HTTP {exc.response.status_code} "
                    f"during startup check."
                )
            }
        except httpx.RequestError as exc:
            return {
                "__service_error__": (
                    f"Could not reach Telegram API: {exc}. "
                    "Check your internet connection."
                )
            }

    # ── 3. Long-poll loop ─────────────────────────────────────────────────────
    #
    # We maintain a single offset across all poll cycles.
    # Telegram guarantees messages are delivered in order; advancing the
    # offset (offset = last_update_id + 1) marks them as consumed.
    # IMPORTANT: We must flush the final offset back to Telegram (via one
    # extra getUpdates call with the advanced offset) before returning,
    # otherwise the same message is re-delivered on the next run.
    #
    # Heartbeat strategy:
    #   We track wall-clock time via time.monotonic() and send a Temporal
    #   heartbeat every _HEARTBEAT_INTERVAL seconds regardless of whether
    #   a message arrived.  This keeps the Temporal activity alive during
    #   long quiet periods without flooding Temporal with unnecessary beats.

    offset              : int   = 0
    consecutive_errors  : int   = 0
    last_heartbeat_time : float = time.monotonic()

    async with httpx.AsyncClient(
        timeout=httpx.Timeout(
            connect = 10.0,
            read    = poll_timeout + 5.0,   # slightly more than poll_timeout
            write   = 10.0,
            pool    = 5.0,
        )
    ) as client:

        while True:

            # ── heartbeat ────────────────────────────────────────────────────
            now = time.monotonic()
            if _TEMPORAL_AVAILABLE and (now - last_heartbeat_time) >= _HEARTBEAT_INTERVAL:
                if activity.in_activity():
                    activity.heartbeat(
                        f"telegram_trigger: waiting for message, offset={offset}"
                    )
                last_heartbeat_time = now

            # ── getUpdates ───────────────────────────────────────────────────
            try:
                resp = await client.post(
                    f"{base_url}/getUpdates",
                    json={
                        "offset":  offset,
                        "timeout": poll_timeout,
                        # Only request message updates (not edited_message,
                        # channel_post, etc.) unless the user wants all types
                        "allowed_updates": (
                            ["message"]
                            if filter_text_only
                            else ["message", "edited_message",
                                  "channel_post", "edited_channel_post"]
                        ),
                    },
                )
                resp.raise_for_status()
                data = resp.json()

                if not data.get("ok"):
                    raise _TelegramAPIError(
                        data.get("description", "unknown error")
                    )

                updates: list[dict] = data.get("result", [])
                consecutive_errors = 0   # reset on success

            except _TelegramAPIError as exc:
                logger.warning(
                    "telegram_trigger: Telegram API error: %s", exc
                )
                consecutive_errors += 1
                if consecutive_errors >= _MAX_RETRIES:
                    return {
                        "__service_error__": (
                            f"Telegram API returned {consecutive_errors} "
                            f"consecutive errors. Last: {exc}"
                        )
                    }
                try:
                    await asyncio.sleep(_RETRY_SLEEP)
                except asyncio.CancelledError:
                    return {
                        "message_id":    None,
                        "chat_id":       0,
                        "chat_type":     "unknown",
                        "from_user_id":  None,
                        "from_username": None,
                        "from_name":     None,
                        "text":          "",
                        "date":          None,
                        "raw_update":    {},
                        "timed_out":     True,
                    }
                continue

            except (httpx.ReadTimeout, httpx.ConnectTimeout):
                # Normal — long-poll expired with no messages.
                # Just loop again immediately; next heartbeat handles timing.
                try:
                    await asyncio.sleep(1)
                except asyncio.CancelledError:
                    return {
                        "message_id":    None,
                        "chat_id":       0,
                        "chat_type":     "unknown",
                        "from_user_id":  None,
                        "from_username": None,
                        "from_name":     None,
                        "text":          "",
                        "date":          None,
                        "raw_update":    {},
                        "timed_out":     True,
                    }
                continue

            except httpx.HTTPStatusError as exc:
                # ── HTTP error status from Telegram ───────────────────────
                # 409 Conflict means another process is already polling
                # getUpdates for this bot token — an unrecoverable state
                # that will persist until the other poller stops.
                if exc.response.status_code == 409:
                    return {
                        "__service_error__": (
                            "Telegram returned 409 Conflict — another process is "
                            "already polling getUpdates with this bot token. Only "
                            "one trigger instance can poll a given bot at a time."
                        )
                    }
                logger.warning(
                    "telegram_trigger: HTTP %s from Telegram",
                    exc.response.status_code,
                )
                consecutive_errors += 1
                if consecutive_errors >= _MAX_RETRIES:
                    return {
                        "__service_error__": (
                            f"Telegram returned {consecutive_errors} consecutive "
                            f"HTTP errors. Last: HTTP {exc.response.status_code}"
                        )
                    }
                try:
                    await asyncio.sleep(_RETRY_SLEEP)
                except asyncio.CancelledError:
                    return {
                        "message_id":    None,
                        "chat_id":       0,
                        "chat_type":     "unknown",
                        "from_user_id":  None,
                        "from_username": None,
                        "from_name":     None,
                        "text":          "",
                        "date":          None,
                        "raw_update":    {},
                        "timed_out":     True,
                    }
                continue

            except httpx.RequestError as exc:
                logger.warning(
                    "telegram_trigger: network error: %s", exc
                )
                consecutive_errors += 1
                if consecutive_errors >= _MAX_RETRIES:
                    return {
                        "__service_error__": (
                            f"Lost connection to Telegram after "
                            f"{consecutive_errors} retries: {exc}"
                        )
                    }
                try:
                    await asyncio.sleep(_RETRY_SLEEP)
                except asyncio.CancelledError:
                    return {
                        "message_id":    None,
                        "chat_id":       0,
                        "chat_type":     "unknown",
                        "from_user_id":  None,
                        "from_username": None,
                        "from_name":     None,
                        "text":          "",
                        "date":          None,
                        "raw_update":    {},
                        "timed_out":     True,
                    }
                continue

            # ── process updates ───────────────────────────────────────────────

            for update in updates:
                update_id = update["update_id"]
                offset    = update_id + 1   # advance offset past this update

                # Try to get the message object (covers both message and
                # edited_message fields depending on allowed_updates above)
                message: dict[str, Any] | None = (
                    update.get("message")
                    or update.get("edited_message")
                    or update.get("channel_post")
                    or update.get("edited_channel_post")
                )

                if message is None:
                    # Update type we don't handle (inline query, etc.) — skip
                    logger.debug(
                        "telegram_trigger: skipping non-message update %d",
                        update_id,
                    )
                    continue

                # ── filter: text-only ─────────────────────────────────────────
                text: str = message.get("text") or message.get("caption") or ""
                if filter_text_only and not text:
                    logger.debug(
                        "telegram_trigger: skipping non-text message %d",
                        update_id,
                    )
                    continue

                # ── filter: allowed chat IDs ──────────────────────────────────
                chat    : dict = message.get("chat", {})
                chat_id : int  = chat.get("id", 0)
                if allowed_chat_ids and chat_id not in allowed_chat_ids:
                    logger.debug(
                        "telegram_trigger: skipping message from chat %d "
                        "(not in allowed list)",
                        chat_id,
                    )
                    continue

                # ── filter: allowed usernames ─────────────────────────────────
                from_user: dict = message.get("from") or {}
                from_username: str | None = from_user.get("username")
                if (
                    allowed_usernames
                    and from_username not in allowed_usernames
                ):
                    logger.debug(
                        "telegram_trigger: skipping message from @%s "
                        "(not in allowed list)",
                        from_username,
                    )
                    continue

                # ── qualifying message found — build output ───────────────────
                logger.info(
                    "telegram_trigger: received message from chat %d "
                    "(user: @%s): %.80s",
                    chat_id,
                    from_username or "unknown",
                    text or "[no text]",
                )

                # Flush the advanced offset back to Telegram before returning.
                # Without this, the message (and anything skipped earlier in
                # the same batch) gets re-delivered the next time the node runs,
                # because Telegram only marks updates as consumed when a later
                # getUpdates call arrives carrying the higher offset.
                try:
                    await client.post(
                        f"{base_url}/getUpdates",
                        json={"offset": offset, "timeout": 0},
                    )
                except httpx.RequestError:
                    pass  # best-effort flush; worst case is one extra redelivery

                return {
                    # Primary fields — use these in downstream nodes
                    "message_id":    message.get("message_id"),
                    "chat_id":       chat_id,
                    "chat_type":     chat.get("type", "unknown"),
                    "from_user_id":  from_user.get("id"),
                    "from_username": from_username,
                    "from_name": " ".join(
                        filter(None, [
                            from_user.get("first_name"),
                            from_user.get("last_name"),
                        ])
                    ) or None,
                    "text":          text,
                    "date":          message.get("date"),

                    # Full update — for advanced use cases
                    "raw_update":    update,
                }

            # No qualifying messages in this batch — keep polling
            try:
                await asyncio.sleep(1)
            except asyncio.CancelledError:
                return {
                    "message_id":    None,
                    "chat_id":       0,
                    "chat_type":     "unknown",
                    "from_user_id":  None,
                    "from_username": None,
                    "from_name":     None,
                    "text":          "",
                    "date":          None,
                    "raw_update":    {},
                    "timed_out":     True,
                }


# ─────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────────────────────

class _TelegramAPIError(Exception):
    """Raised when Telegram returns ok=false in the response body."""


def _parse_int_set(raw: str) -> set[int]:
    """
    Parse a comma-separated string of integers into a set.
    Silently skips values that aren't valid integers.
    Example: "  -100123, 987654 " → {-100123, 987654}
    """
    result: set[int] = set()
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            result.add(int(part))
        except ValueError:
            logger.warning(
                "telegram_trigger: ignoring non-integer chat ID %r", part
            )
    return result


def _parse_str_set(raw: str) -> set[str]:
    """
    Parse a comma-separated string of usernames into a lowercase set.
    Strips leading @ if present.
    Example: "  @Alice, Bob " → {"alice", "bob"}
    """
    result: set[str] = set()
    for part in raw.split(","):
        part = part.strip().lstrip("@").lower()
        if part:
            result.add(part)
    return result
