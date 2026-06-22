"""
nodes/discord_receive_messages.py
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📨  Discord: Receive Messages
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

WHAT THIS NODE DOES
───────────────────
Reads messages from a Discord channel using your Bot token.

Two modes are available (toggle "Wait for New Message"):

  ┌─────────────────────────────────────────────────────────────────────┐
  │  SNAPSHOT (default, wait_for_new = OFF)                             │
  │  ─────────────────────────────────────                              │
  │  Fetches the latest N messages right now and moves on immediately.  │
  │  Use this when you just need recent history.                        │
  ├─────────────────────────────────────────────────────────────────────┤
  │  WAIT / TRIGGER (wait_for_new = ON)                                 │
  │  ──────────────────────────────────                                 │
  │  Blocks the workflow and polls every few seconds until a NEW        │
  │  message appears in the channel, then passes it downstream.         │
  │  Use this to build a "respond when someone sends a message" bot.    │
  └─────────────────────────────────────────────────────────────────────┘

HOW TO USE — QUICK START
────────────────────────
  1. Paste your Bot Token (from discord.com/developers → your app → Bot → Reset Token).
  2. IMPORTANT: Enable "Message Content Intent" in the same portal under
     Bot → Privileged Gateway Intents. Without this, content will always be empty.
  3. Paste your Channel ID (right-click channel → Copy Channel ID).
  4. Leave "Wait for New Message" OFF for a one-time fetch,
     or turn it ON to make the workflow wait for the next message.
  5. To chain runs continuously, wire  last_message_id  →  After Message ID
     so each run only sees messages that arrived after the previous one.

OUTPUT FIELDS
─────────────
  messages[]           List of message objects (sorted oldest → newest)
    .id                Discord message snowflake ID
    .content           The text of the message
    .author_username   Who sent it
    .author_display_name  The user's display name (global_name), falls back to username
    .author_id         Their Discord user ID
    .timestamp         ISO-8601 send time
    .is_bot            True if the author is a bot
    .attachments[]     Any attached files {url, filename}
    .has_embeds        True if the message contains embeds
  count                How many messages are in this batch
  last_message_id      Pass this into "After Message ID" on the next run
  channel_id           The channel that was polled
  timed_out            True if Wait mode expired before a message arrived

ARCHITECTURAL NOTES
───────────────────
  • wait_for_new defaults to FALSE to protect existing snapshot workflows
    from accidentally blocking after a node upgrade.
  • Timeout is capped at 240 s (< Temporal's 300 s activity ceiling)
    so the node always resolves gracefully before Temporal force-kills it.
  • _heartbeat() is called every poll cycle to keep the Temporal activity
    alive during long waits.
"""

import asyncio
import time
import httpx
from typing import Any

from core.node_registry import register_action
from core.expressions import resolve_template, apply_output_mapping

# ── Optional Temporal heartbeat ───────────────────────────────────────────────
# Emitted every poll cycle so Temporal does not kill a long-running wait.
try:
    from temporalio import activity as _temporal_activity
    _HAS_TEMPORAL = True
except ImportError:
    _HAS_TEMPORAL = False


def _heartbeat(msg: str) -> None:
    if not _HAS_TEMPORAL or not _temporal_activity.in_activity():
        return
    try:
        _temporal_activity.heartbeat(msg)
    except Exception:
        pass


@register_action(
    type="discord_receive_messages",
    label="Discord: Receive Messages",
    icon="📨",
    description=(
        "Fetch messages from a Discord channel. "
        "Use Snapshot mode to grab recent history instantly, "
        "or enable Wait mode to block until a new message arrives.\n\n"
        "⚠️  Requires 'Message Content Intent' enabled in the Discord Developer Portal "
        "(your app → Bot → Privileged Gateway Intents) — without it, all message "
        "content fields will be empty even though the bot connects successfully."
    ),
    category="Communication",
    config_schema={

        # ── Credentials ────────────────────────────────────────────────────────
        "bot_token": {
            "type": "string",
            "label": "Bot Token",
            "required": True,
            "secret": True,
            "placeholder": "MTQ5NDg3…",
            "description": (
                "Your Discord Bot token — found in the Discord Developer Portal "
                "under your app → Bot → Reset Token.\n\n"
                "IMPORTANT: You must enable 'Message Content Intent' in the "
                "Developer Portal (Bot → Privileged Gateway Intents) or the "
                "returned messages will have empty content fields."
            ),
        },

        "channel_id": {
            "type": "string",
            "label": "Channel ID",
            "required": True,
            "placeholder": "1494872699887943830",
            "description": (
                "The numeric ID of the channel to read from. "
                "To find it: enable Developer Mode in Discord (Settings → Advanced), "
                "then right-click any channel → Copy Channel ID."
            ),
        },

        # ── Mode selector ──────────────────────────────────────────────────────
        "wait_for_new": {
            "type": "boolean",
            "label": "Wait for New Message",
            "default": False,       # ← FALSE to protect legacy snapshot workflows
            "description": (
                "OFF (default): fetch the latest messages right now and continue immediately.\n"
                "ON: pause the workflow and keep checking until a new message arrives, "
                "then pass it to the next node. "
                "Ideal for building reply-bots or message-triggered automations."
            ),
        },

        # ── Cursor (used in both modes) ────────────────────────────────────────
        "after_message_id": {
            "type": "string",
            "label": "After Message ID  (cursor)",
            "required": False,
            "template": True,
            "dotpath": True,
            "placeholder": "{{node_3.last_message_id}}  ← use this node's own ID",
            "description": (
                "Only return messages newer than this ID.\n\n"
                "• Snapshot mode: acts as a 'newer than' filter.\n"
                "• Wait mode: waits until something newer than this ID arrives. "
                "Leave blank to auto-seed (only returns messages that arrive after this run starts).\n\n"
                "To chain runs: wire this node's own last_message_id output back into this field. "
                "Click 'Insert expression' below this field to find and insert it in one click. "
                "The expression will look like: {{node_3.last_message_id}} where node_3 is this node's ID."
            ),
        },

        # ── Wait-mode options (hidden when wait_for_new is OFF) ────────────────
        "poll_interval": {
            "type": "number",
            "label": "Poll Interval (seconds)",
            "required": False,
            "default": 3,
            "min": 1,
            "max": 60,
            "description": (
                "How often to ask Discord for new messages while waiting. "
                "Lower = faster reaction, higher = fewer API calls. "
                "3 seconds is a safe default that stays well within Discord's rate limits."
            ),
            "visibleWhen": {"wait_for_new": True},
        },

        "timeout": {
            "type": "number",
            "label": "Timeout (seconds)",
            "required": False,
            "default": 120,
            "min": 5,
            "max": 240,     # ← Hard ceiling: Temporal kills activities at 300 s.
                            #   240 s leaves a 60 s safety margin for graceful resolution.
            "description": (
                "How long to wait before giving up. "
                "If no message arrives within this window the node returns "
                "{ timed_out: true, messages: [] } so downstream nodes can handle the timeout gracefully.\n\n"
                "Maximum is 240 s — this leaves a safety margin below Temporal's "
                "300 s activity timeout so the node always resolves cleanly."
            ),
            "visibleWhen": {"wait_for_new": True},
        },

        # ── Fetch options ──────────────────────────────────────────────────────
        "limit": {
            "type": "number",
            "label": "Message Limit",
            "required": False,
            "default": 10,
            "min": 1,
            "max": 100,
            "description": (
                "Maximum number of messages to return per fetch (Discord allows up to 100). "
                "In Wait mode this caps how many new messages are returned once they arrive."
            ),
        },

        "filter_bots": {
            "type": "boolean",
            "label": "Exclude Bot Messages",
            "default": True,
            "description": (
                "When ON (default), messages sent by bot accounts are silently dropped. "
                "This prevents your workflow bot from triggering itself. "
                "Turn OFF only if you intentionally want to process bot-to-bot messages."
            ),
        },

        "filter_author_id": {
            "type": "string",
            "label": "Filter by Author ID",
            "required": False,
            "placeholder": "123456789012345678",
            "description": (
                "Optional. When set, only messages from this specific Discord user ID are returned. "
                "Leave blank to accept messages from everyone. "
                "Find a user's ID by right-clicking their name → Copy User ID (Developer Mode)."
            ),
        },

        # ── Standard output mapping ────────────────────────────────────────────
        "output_mapping": {
            "type": "code",
            "label": "Output mapping",
            "required": False,
            "description": (
                'Rename or extract fields for downstream nodes. '
                'Example: {"first_message": "messages[0].content"}'
            ),
        },
    },
    example_output={
        "messages": [
            {
                "id": "1234567890123456789",
                "content": "Hello world",
                "author_id": "9876543210987654321",
                "author_username": "alice",
                "author_display_name": "Alice Smith",
                "timestamp": "2024-01-15T12:34:56.789000+00:00",
                "is_bot": False,
                "attachments": [],
                "has_embeds": False,
            }
        ],
        "count": 1,
        "last_message_id": "1234567890123456789",
        "channel_id": "123456789012345678",
        "timed_out": False,
    },
)
async def handle_discord_receive_messages(
    config: dict[str, Any],
    input_data: dict[str, Any],
    context: dict[str, Any],
) -> dict[str, Any]:

    # ── 1. Validate required credentials ──────────────────────────────────────
    bot_token = config.get("bot_token", "").strip()
    if not bot_token:
        return {"error": "missing field: bot_token", "__input_error__": True}

    channel_id = config.get("channel_id", "").strip()
    if not channel_id:
        return {"error": "missing field: channel_id", "__input_error__": True}

    # ── 2. Resolve config values ───────────────────────────────────────────────
    try:
        limit = max(1, min(100, int(config.get("limit", 10))))
    except (TypeError, ValueError):
        limit = 10

    # wait_for_new defaults FALSE for one-time (manual) runs.
    # In published mode (continuous loop), force wait mode so the node blocks
    # until a NEW message arrives instead of replaying the same snapshot on
    # every iteration — snapshot in a loop would spam the Discord API.
    # NOTE: the frontend always serialises unchecked booleans as `false`,
    # so we cannot use `is None` to distinguish "user left at default" from
    # "user explicitly unchecked".  We therefore force wait in published mode
    # unconditionally.
    wait_for_new = bool(config.get("wait_for_new", False))
    exec_id = context.get("__exec_id__", "")
    if not wait_for_new and isinstance(exec_id, str) and exec_id.startswith("pub_"):
        wait_for_new = True
    poll_interval    = max(1, int(config.get("poll_interval", 3)))
    # Clamp timeout to [5, 240] — must stay below Temporal's 300 s ceiling.
    timeout_secs     = max(5, min(240, int(config.get("timeout", 120))))
    filter_bots      = bool(config.get("filter_bots", True))
    filter_author_id = config.get("filter_author_id", "").strip()

    after_raw = config.get("after_message_id", "")
    cursor    = resolve_template(str(after_raw), input_data).strip() if after_raw else ""

    headers = {
        "Authorization": f"Bot {bot_token}",
        "Content-Type": "application/json",
    }
    api_url = f"https://discord.com/api/v10/channels/{channel_id}/messages"

    # ── 3. Inner fetch helper ──────────────────────────────────────────────────
    async def fetch_messages(after: str) -> tuple[list[dict], str | None]:
        """
        GETs up to `limit` messages from the channel.
        Returns (normalised_list, error_message_or_None).
        Messages are sorted oldest → newest before being returned.
        """
        params: dict[str, Any] = {"limit": limit}
        if after:
            params["after"] = after

        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(api_url, params=params, headers=headers)

        if resp.status_code == 403:
            return [], (
                "Discord API returned 403 Forbidden. "
                "Make sure the bot has been invited to the server and has "
                "'View Channel' + 'Read Message History' permissions on this channel."
            )
        if resp.status_code == 404:
            return [], (
                f"Discord API returned 404 — channel {channel_id!r} was not found. "
                "Double-check the Channel ID."
            )
        if resp.status_code != 200:
            return [], f"Discord API returned {resp.status_code}: {resp.text}"

        raw_list: list[dict] = resp.json()
        normalised: list[dict] = []

        for raw in raw_list:
            author = raw.get("author") or {}
            is_bot = bool(author.get("bot", False))

            if filter_bots and is_bot:
                continue
            if filter_author_id and author.get("id") != filter_author_id:
                continue

            normalised.append({
                "id":              raw.get("id", ""),
                "content":         raw.get("content", ""),
                "author_id":       author.get("id", ""),
                "author_username":     author.get("username", ""),
                "author_display_name": author.get("global_name") or author.get("username", ""),
                "timestamp":       raw.get("timestamp", ""),
                "is_bot":          is_bot,
                "attachments": [
                    {"url": a.get("url"), "filename": a.get("filename")}
                    for a in raw.get("attachments", [])
                ],
                "has_embeds": len(raw.get("embeds", [])) > 0,
            })

        # Discord returns newest-first; flip to chronological order.
        normalised.sort(key=lambda m: m["id"])
        return normalised, None

    # ── 4a. SNAPSHOT MODE ─────────────────────────────────────────────────────
    if not wait_for_new:
        try:
            messages, err = await fetch_messages(cursor)
        except httpx.TimeoutException:
            return {"error": "Discord API request timed out (15 s).", "__service_error__": True}
        except Exception as e:
            return {"error": f"Discord API error: {e}", "__service_error__": True}

        if err:
            return {"error": err, "__service_error__": True}

        # ── Detect Message Content Intent not enabled ──────────────────────────────
        # Discord silently returns content="" for all messages when the bot lacks
        # the Message Content privileged intent in the Developer Portal.
        # Warn early so the user doesn't spend time debugging empty expressions.
        empty_content_count = sum(1 for m in messages if m["content"] == "" and not m["has_embeds"] and not m["attachments"])
        if messages and empty_content_count == len(messages):
            import logging
            logger = logging.getLogger("backend.nodes.discord")
            logger.warning(
                "discord_receive_messages: ALL returned messages have empty content. "
                "This almost always means the bot is missing the 'Message Content Intent'. "
                "Fix: Discord Developer Portal → your app → Bot → Privileged Gateway Intents "
                "→ enable 'Message Content Intent' and save."
            )

        last_id = messages[-1]["id"] if messages else cursor
        result  = {
            "messages":        messages,
            "count":           len(messages),
            "last_message_id": last_id,
            "channel_id":      channel_id,
            "timed_out":       False,
        }
        return apply_output_mapping(result, config.get("output_mapping", ""))

    # ── 4b. WAIT MODE ─────────────────────────────────────────────────────────
    # If no cursor was given, seed it from the channel's current newest message
    # so we don't surface messages that were already there before this run started.
    if not cursor:
        try:
            seed, err = await fetch_messages("")
            if err:
                return {"error": err, "__service_error__": True}
            cursor = seed[-1]["id"] if seed else ""
        except Exception as e:
            return {"error": f"Discord API error while seeding cursor: {e}", "__service_error__": True}

    deadline   = time.monotonic() + timeout_secs
    poll_count = 0

    while True:
        # ── Timeout guard ──────────────────────────────────────────────────────
        if time.monotonic() >= deadline:
            result = {
                "messages":        [],
                "count":           0,
                "last_message_id": cursor,
                "channel_id":      channel_id,
                "timed_out":       True,
            }
            return apply_output_mapping(result, config.get("output_mapping", ""))

        # ── Heartbeat so Temporal knows this activity is still alive ───────────
        poll_count += 1
        _heartbeat(f"Discord poll #{poll_count} — channel {channel_id}")

        # ── Poll ───────────────────────────────────────────────────────────────
        try:
            messages, err = await fetch_messages(cursor)
        except httpx.TimeoutException:
            # Transient network issue — retry on the next cycle.
            try:
                await asyncio.sleep(poll_interval)
            except asyncio.CancelledError:
                return apply_output_mapping(
                    {
                        "messages":        [],
                        "count":           0,
                        "last_message_id": cursor,
                        "channel_id":      channel_id,
                        "timed_out":       True,
                    },
                    config.get("output_mapping", ""),
                )
            continue
        except Exception as e:
            return {"error": f"Discord API error: {e}", "__service_error__": True}

        if err:
            return {"error": err, "__service_error__": True}

        # ── New messages arrived → return them ─────────────────────────────────
        if messages:
            # ── Detect Message Content Intent not enabled ──────────────────────────────
            empty_content_count = sum(1 for m in messages if m["content"] == "" and not m["has_embeds"] and not m["attachments"])
            if messages and empty_content_count == len(messages):
                import logging
                logger = logging.getLogger("backend.nodes.discord")
                logger.warning(
                    "discord_receive_messages: ALL returned messages have empty content. "
                    "This almost always means the bot is missing the 'Message Content Intent'. "
                    "Fix: Discord Developer Portal → your app → Bot → Privileged Gateway Intents "
                    "→ enable 'Message Content Intent' and save."
                )

            result = {
                "messages":        messages,
                "count":           len(messages),
                "last_message_id": messages[-1]["id"],
                "channel_id":      channel_id,
                "timed_out":       False,
            }
            return apply_output_mapping(result, config.get("output_mapping", ""))

        # ── Nothing yet — sleep and retry ─────────────────────────────────────
        remaining = deadline - time.monotonic()
        try:
            await asyncio.sleep(min(poll_interval, max(0.0, remaining)))
        except asyncio.CancelledError:
            # Temporal cancelled this activity — return clean result, not a failure.
            return apply_output_mapping(
                {
                    "messages":        [],
                    "count":           0,
                    "last_message_id": cursor,
                    "channel_id":      channel_id,
                    "timed_out":       True,
                },
                config.get("output_mapping", ""),
            )