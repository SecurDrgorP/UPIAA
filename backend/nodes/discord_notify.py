"""
nodes/discord_notify.py
─────────────────────────────────────────────────────────────────────────────
Sends a message to a Discord channel via the Discord Bot API (v10).
Supports optional rich embeds via embed_title.
"""

import httpx
from typing import Any

from core.node_registry import register_action
from core.expressions import resolve_template, apply_output_mapping


@register_action(
    type="discord_notify",
    label="Discord Notifier",
    icon="🎮",
    description="Send a message to a Discord channel via a Bot token.",
    category="Communication",
    config_schema={
        "bot_token": {
            "type": "string",
            "label": "Bot Token",
            "required": True,
            "secret": True,
            "placeholder": "Bot MTxxxxxxxxxxxxxxxxxxxxxxxx.Gyyyyy.zzzzzzzzzzzzzzzzzzzzzzzzzzz",
            "description": "Your Discord Bot token. Found in the Discord Developer Portal under your application's Bot settings.",
        },
        "channel_id": {
            "type": "string",
            "label": "Channel ID",
            "required": True,
            "placeholder": "123456789012345678",
            "description": "The ID of the Discord channel to send the message to. Right-click the channel in Discord and select 'Copy Channel ID' (Developer Mode must be enabled).",
        },
        "message": {
            "type": "string",
            "label": "Message",
            "required": True,
            "multiline": True,
            "template": True,
            "placeholder": "Hello from UPIAA! Result: {{n1.response}}",
            "description": "The message content to send. Supports {{node_id.field}} interpolation.",
        },
        "embed_enabled": {
            "type": "boolean",
            "label": "Send as Embed",
            "default": False,
            "description": "Wrap the message in a Discord rich embed.",
        },
        "embed_title": {
            "type": "string",
            "label": "Embed Title",
            "required": False,
            "template": True,
            "placeholder": "Workflow Notification",
            "description": "Title for the embed card. Supports {{node_id.field}} interpolation.",
            "visibleWhen": {"embed_enabled": True},
        },
        "embed_color": {
            "type": "number",
            "label": "Embed Color (decimal)",
            "required": False,
            "default": 5814783,
            "min": 0,
            "max": 16777215,
            "description": "Embed sidebar color as a decimal integer (e.g. 5814783 = #58B9FF). Convert hex at bgreco.net/hexcolor.",
            "visibleWhen": {"embed_enabled": True},
        },
        "output_mapping": {
            "type": "code",
            "label": "Output mapping",
            "required": False,
            "description": 'Rename or extract output keys. Example: {"summary": "response"}',
        },
    },
    example_output={
        "sent": True,
        "message_id": "1234567890123456789",
        "channel_id": "123456789012345678",
    },
)
async def handle_discord_notify(
    config: dict[str, Any],
    input_data: dict[str, Any],
    context: dict[str, Any],
) -> dict[str, Any]:

    # ── Validate required config fields ───────────────────────────────────────
    bot_token = config.get("bot_token", "").strip()
    if not bot_token:
        return {"error": "missing field: bot_token", "__input_error__": True}

    channel_id = config.get("channel_id", "").strip()
    if not channel_id:
        return {"error": "missing field: channel_id", "__input_error__": True}

    # ── Resolve templated fields ───────────────────────────────────────────────
    message_raw = config.get("message", "")
    message = resolve_template(message_raw, input_data)
    if not message.strip():
        return {"error": "missing field: message — cannot send an empty message", "__input_error__": True}

    embed_enabled = bool(config.get("embed_enabled", False))

    # ── Build request payload ──────────────────────────────────────────────────
    payload: dict[str, Any] = {}

    if embed_enabled:
        embed: dict[str, Any] = {"description": message}

        embed_title_raw = config.get("embed_title", "").strip()
        if embed_title_raw:
            embed["title"] = resolve_template(embed_title_raw, input_data)

        embed_color = config.get("embed_color")
        if embed_color is not None:
            try:
                embed["color"] = int(embed_color)
            except (TypeError, ValueError):
                pass  # silently ignore a malformed color value

        payload["embeds"] = [embed]
        # Discord requires at least one of content/embeds; embed covers this.
        # Include content as empty string so the message preview is clean.
        payload["content"] = ""
    else:
        payload["content"] = message

    # ── POST to Discord API ────────────────────────────────────────────────────
    url = f"https://discord.com/api/v10/channels/{channel_id}/messages"
    headers = {
        "Authorization": f"Bot {bot_token}",
        "Content-Type": "application/json",
    }

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(url, json=payload, headers=headers)

        if resp.status_code not in (200, 201):
            return {
                "error": f"Discord API returned {resp.status_code}: {resp.text}",
                "__service_error__": True,
            }

        resp_json: dict[str, Any] = resp.json()

    except httpx.TimeoutException:
        return {"error": "Discord API request timed out after 15 seconds.", "__service_error__": True}
    except Exception as e:
        return {"error": f"Discord API error: {e}", "__service_error__": True}

    # ── Return result ──────────────────────────────────────────────────────────
    result = {
        "sent": True,
        "message_id": resp_json.get("id", ""),
        "channel_id": resp_json.get("channel_id", channel_id),
    }
    return apply_output_mapping(result, config.get("output_mapping", ""))
