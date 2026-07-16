"""
nodes/telegram_send_document.py
─────────────────────────────────────────────────────────────────────────────
Sends a local file as a document to a Telegram chat via the Bot API.
Supports an optional caption and parse mode formatting.

WHAT THIS NODE DOES
───────────────────
Uploads a file from the local filesystem to a Telegram chat using the
sendDocument API endpoint (multipart/form-data).  Designed to sit directly
after document-generation nodes (word_document, excel_document) in the
workflow DAG.

QUICK START
───────────
  1. Paste your Telegram Bot token.
  2. Set the target chat/group/channel ID.
  3. Set file_path to the document to send, e.g.
     /tmp/UPIAA_docs/{{node_d1.response}}.docx
  4. Optionally add a caption.

OUTPUT FIELDS
─────────────
  sent          (bool)   Whether the file was delivered successfully
  message_id    (int)    Telegram message ID of the sent document
  file_name     (str)    Basename of the uploaded file
  chat_id       (str)    Chat the document was sent to
"""

import os
import logging
import httpx
from typing import Any

from core.node_registry import register_action
from core.expressions import resolve_template, apply_output_mapping

logger = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────────────────
ALLOWED_DIR = "/tmp/UPIAA_docs/"
MAX_FILE_SIZE = 50 * 1024 * 1024  # 50 MB — Telegram Bot API limit for documents


@register_action(
    type="telegram_send_document",
    label="Telegram: Send Document",
    icon="📎",
    description="Send a local file as a document to a Telegram chat.",
    category="Communication",
    config_schema={
        "credential_id": {
            "type": "string",
            "label": "Credential ID",
            "required": False,
            "placeholder": "cred_...",
            "description": (
                "Optional encrypted credential bundle ID. If set, the bundle "
                "should provide bot_token."
            ),
        },
        "bot_token": {
            "type": "string",
            "label": "Bot Token",
            "required": False,
            "secret": True,
            "placeholder": "123456789:AABBccDDeeFFggHHiiJJkkLLmmNNoo",
            "visibleWhen": {"credential_id": ""},
            "description": (
                "Manual bot token — only needed when no credential bundle is "
                "selected above. Get one from @BotFather on Telegram."
            ),
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
        "file_path": {
            "type": "string",
            "label": "File Path",
            "required": True,
            "template": True,
            "placeholder": "/tmp/UPIAA_docs/{{node_d1.response}}.docx",
            "description": (
                "Absolute path to the file to upload. Must be under /tmp/UPIAA_docs/. "
                "Supports {{node_id.field}} interpolation."
            ),
        },
        "caption": {
            "type": "string",
            "label": "Caption",
            "required": False,
            "multiline": True,
            "template": True,
            "placeholder": "Here is your report! 📄",
            "description": (
                "Optional caption sent with the document. "
                "Supports {{node_id.field}} interpolation."
            ),
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
            "description": "Formatting mode for the caption text.",
        },
        "disable_notification": {
            "type": "boolean",
            "label": "Silent Message",
            "default": False,
            "description": "Send silently — recipients receive no notification sound.",
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
        "file_name": "report.docx",
        "chat_id": "-1001234567890",
    },
)
async def handle_telegram_send_document(
    config: dict[str, Any],
    input_data: dict[str, Any],
    context: dict[str, Any],
) -> dict[str, Any]:

    # ── Validate required config fields ───────────────────────────────────────
    bot_token = config.get("bot_token", "").strip()
    if not bot_token:
        return {"error": "missing field: bot_token", "__input_error__": True}

    chat_id = resolve_template(str(config.get("chat_id", "")), input_data).strip()
    if not chat_id:
        return {"error": "missing field: chat_id", "__input_error__": True}

    # ── Resolve and validate file path ─────────────────────────────────────────
    file_path = resolve_template(config.get("file_path", ""), input_data).strip()
    if not file_path:
        return {"error": "missing field: file_path", "__input_error__": True}

    # Security: restrict to allowed directory
    real_path = os.path.realpath(file_path)
    if not real_path.startswith(os.path.realpath(ALLOWED_DIR)):
        return {
            "error": f"file_path must be under {ALLOWED_DIR}, got: {real_path}",
            "__input_error__": True,
        }

    if not os.path.isfile(real_path):
        return {"error": f"File not found: {real_path}", "__input_error__": True}

    file_size = os.path.getsize(real_path)
    if file_size > MAX_FILE_SIZE:
        size_mb = file_size / (1024 * 1024)
        return {
            "error": f"File too large: {size_mb:.1f} MB (max {MAX_FILE_SIZE // (1024*1024)} MB)",
            "__input_error__": True,
        }

    file_name = os.path.basename(real_path)

    # ── Resolve optional fields ────────────────────────────────────────────────
    caption_raw = config.get("caption", "")
    caption = resolve_template(caption_raw, input_data) if caption_raw else ""

    parse_mode = config.get("parse_mode", "Markdown")
    disable_notification = bool(config.get("disable_notification", False))

    # ── Build multipart payload ────────────────────────────────────────────────
    data: dict[str, Any] = {
        "chat_id": chat_id,
        "disable_notification": str(disable_notification).lower(),
    }

    if caption.strip():
        data["caption"] = caption

    if parse_mode:
        data["parse_mode"] = parse_mode

    # ── POST to Telegram Bot API ───────────────────────────────────────────────
    url = f"https://api.telegram.org/bot{bot_token}/sendDocument"

    try:
        with open(real_path, "rb") as f:
            files = {"document": (file_name, f, "application/octet-stream")}

            async with httpx.AsyncClient(timeout=60) as client:
                resp = await client.post(url, data=data, files=files)

        resp_json: dict[str, Any] = resp.json()

        if not resp_json.get("ok"):
            description = resp_json.get("description", resp.text)
            return {
                "error": f"Telegram API error ({resp.status_code}): {description}",
                "__service_error__": True,
            }

    except httpx.TimeoutException:
        return {
            "error": "Telegram API request timed out (file upload, 60s limit).",
            "__service_error__": True,
        }
    except Exception as e:
        return {"error": f"Telegram API error: {e}", "__service_error__": True}

    # ── Extract result from response ───────────────────────────────────────────
    tg_message: dict[str, Any] = resp_json.get("result", {})
    tg_chat: dict[str, Any] = tg_message.get("chat", {})

    result = {
        "sent": True,
        "message_id": tg_message.get("message_id"),
        "file_name": file_name,
        "chat_id": str(tg_chat.get("id", chat_id)),
    }
    logger.info(f"Telegram document sent: {file_name} → chat {chat_id}")
    return apply_output_mapping(result, config.get("output_mapping", ""))
