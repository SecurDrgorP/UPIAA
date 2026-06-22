"""
nodes/telegram_send_message.py
─────────────────────────────────────────────────────────────────────────────
Sends a message to a Telegram chat via the Telegram Bot API.
Supports plain text, Markdown, and HTML formatting, plus optional
reply-to and silent delivery.
"""

import httpx
from typing import Any

from core.node_registry import register_action
from core.expressions import apply_output_mapping


@register_action(
    type="telegram_send_message",
    label="Telegram: Send Message",
    icon="✈️",
    description="Send a message to a Telegram chat via a Bot token.",
    category="Communication",
    config_schema={
        "credential_id": {
            "type": "string",
            "label": "Credential ID",
            "required": False,
            "placeholder": "cred_...",
            "description": "Optional encrypted credential bundle ID. If set, the bundle should provide bot_token.",
        },
        "bot_token": {
            "type": "string",
            "label": "Bot Token",
            "required": False,
            "secret": True,
            "placeholder": "123456789:AABBccDDeeFFggHHiiJJkkLLmmNNoo",
            "visibleWhen": {"credential_id": ""},
            "description": "Manual bot token — only needed when no credential bundle is selected above.",
        },
        "chat_id": {
            "type": "string",
            "label": "Chat ID",
            "required": True,
            "template": True,
            "placeholder": "-1001234567890",
            "description": (
                "Target chat, group, or channel ID. "
                "For channels use the @username or numeric ID. "
                "Supports {{node_id.field}} interpolation."
            ),
        },
        "message": {
            "type": "string",
            "label": "Message",
            "required": True,
            "multiline": True,
            "template": True,
            "placeholder": "Hello from M8M! Result: {{n1.response}}",
            "description": "Message text. Supports {{node_id.field}} interpolation.",
        },
        "parse_mode": {
            "type": "select",
            "label": "Parse Mode",
            "required": False,
            "default": "Markdown",
            "options": [
                {"value": "", "label": "Plain Text"},
                {"value": "Markdown", "label": "Markdown"},
                {"value": "MarkdownV2", "label": "MarkdownV2"},
                {"value": "HTML", "label": "HTML"},
            ],
            "description": "Formatting mode for the message text.",
        },
        "disable_notification": {
            "type": "boolean",
            "label": "Silent Message",
            "default": False,
            "description": "Send silently — recipients receive no notification sound.",
        },
        "reply_to_message_id": {
            "type": "string",
            "label": "Reply to Message ID",
            "required": False,
            "template": True,
            "placeholder": "{{n1.message_id}}",
            "description": "Optional. Reply to a specific message by its ID. Supports {{node_id.field}} interpolation.",
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
        "message_id": 42,
        "chat_id": "-1001234567890",
        "text": "Hello from M8M!",
    },
)
async def handle_telegram_send_message(
    config: dict[str, Any],
    input_data: dict[str, Any],
    context: dict[str, Any],
) -> dict[str, Any]:

    # ── Validate required config fields ───────────────────────────────────────
    bot_token = config.get("bot_token", "").strip()
    if not bot_token:
        return {"error": "missing field: bot_token", "__input_error__": True}

    chat_id_raw = config.get("chat_id", "")
    chat_id = str(chat_id_raw).strip()
    if not chat_id:
        return {"error": "missing field: chat_id", "__input_error__": True}

    # ── Resolve templated fields ───────────────────────────────────────────────
    message_raw = config.get("message", "")
    message = str(message_raw)
    if not message.strip():
        return {"error": "missing field: message — cannot send an empty message", "__input_error__": True}

    parse_mode = config.get("parse_mode", "Markdown")
    disable_notification = bool(config.get("disable_notification", False))

    reply_to_raw = str(config.get("reply_to_message_id", "")).strip()
    reply_to = reply_to_raw

    # ── Build request payload ──────────────────────────────────────────────────
    payload: dict[str, Any] = {
        "chat_id": chat_id,
        "text": message,
        "disable_notification": disable_notification,
    }

    if parse_mode:
        payload["parse_mode"] = parse_mode

    if reply_to:
        try:
            payload["reply_to_message_id"] = int(reply_to)
        except ValueError:
            return {
                "error": f"reply_to_message_id must be a numeric message ID, got: {reply_to!r}",
                "__input_error__": True,
            }

    # ── POST to Telegram Bot API ───────────────────────────────────────────────
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(url, json=payload)

        resp_json: dict[str, Any] = resp.json()

        if not resp_json.get("ok"):
            description = resp_json.get("description", resp.text)
            return {
                "error": f"Telegram API error ({resp.status_code}): {description}",
                "__service_error__": True,
            }

    except httpx.TimeoutException:
        return {"error": "Telegram API request timed out after 15 seconds.", "__service_error__": True}
    except Exception as e:
        return {"error": f"Telegram API error: {e}", "__service_error__": True}

    # ── Extract result from response ───────────────────────────────────────────
    tg_message: dict[str, Any] = resp_json.get("result", {})
    tg_chat: dict[str, Any] = tg_message.get("chat", {})

    result = {
        "sent": True,
        "message_id": tg_message.get("message_id"),
        "chat_id": str(tg_chat.get("id", chat_id)),
        "text": tg_message.get("text", message),
    }
    return apply_output_mapping(result, config.get("output_mapping", ""))
