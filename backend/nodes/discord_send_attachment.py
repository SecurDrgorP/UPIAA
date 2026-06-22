"""
nodes/discord_send_attachment.py
─────────────────────────────────────────────────────────────────────────────
Sends a local file as an attachment to a Discord channel via the Bot API (v10).
Supports an optional caption message and rich embed wrapper.

WHAT THIS NODE DOES
───────────────────
Uploads a file from the local filesystem to a Discord channel using
multipart/form-data.  Designed to sit directly after document-generation
nodes (word_document, excel_document) in the workflow DAG.

QUICK START
───────────
  1. Paste your Discord Bot token.
  2. Set the target channel ID.
  3. Set file_path to the document to send, e.g.
     /tmp/m8m_docs/{{node_d1.response}}.docx
  4. Optionally add a caption message and/or embed.

OUTPUT FIELDS
─────────────
  sent          (bool)   Whether the file was delivered successfully
  message_id    (str)    Discord message ID of the sent message
  file_name     (str)    Basename of the uploaded file
  channel_id    (str)    Channel the file was sent to
"""

import os
import logging
import httpx
from typing import Any

from core.node_registry import register_action
from core.expressions import resolve_template, apply_output_mapping

logger = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────────────────
ALLOWED_DIR = "/tmp/m8m_docs/"
MAX_FILE_SIZE = 25 * 1024 * 1024  # 25 MB — Discord's upload limit


@register_action(
    type="discord_send_attachment",
    label="Discord: Send File",
    icon="📎",
    description="Send a local file as an attachment to a Discord channel.",
    category="Communication",
    config_schema={
        "bot_token": {
            "type": "string",
            "label": "Bot Token",
            "required": True,
            "secret": True,
            "placeholder": "Bot MTxxxxxxxxxxxxxxxxxxxxxxxx.Gyyyyy.zzzzzzzzzzzzzzzzzzzzzzzzzzz",
            "description": (
                "Your Discord Bot token. Found in the Discord Developer Portal "
                "under your application's Bot settings."
            ),
        },
        "channel_id": {
            "type": "string",
            "label": "Channel ID",
            "required": True,
            "template": True,
            "placeholder": "123456789012345678",
            "description": (
                "The ID of the Discord channel to send the file to. "
                "Right-click the channel in Discord and select 'Copy Channel ID' "
                "(Developer Mode must be enabled)."
            ),
        },
        "file_path": {
            "type": "string",
            "label": "File Path",
            "required": True,
            "template": True,
            "placeholder": "/tmp/m8m_docs/{{node_d1.response}}.docx",
            "description": (
                "Absolute path to the file to upload. Must be under /tmp/m8m_docs/. "
                "Supports {{node_id.field}} interpolation."
            ),
        },
        "message": {
            "type": "string",
            "label": "Caption Message",
            "required": False,
            "multiline": True,
            "template": True,
            "placeholder": "Here is your report! 📄",
            "description": (
                "Optional text message sent alongside the file attachment. "
                "Supports {{node_id.field}} interpolation."
            ),
        },
        "embed_enabled": {
            "type": "boolean",
            "label": "Include Embed",
            "default": False,
            "description": "Add a rich embed card alongside the file attachment.",
        },
        "embed_title": {
            "type": "string",
            "label": "Embed Title",
            "required": False,
            "template": True,
            "placeholder": "Document Delivered",
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
            "description": (
                "Embed sidebar color as a decimal integer "
                "(e.g. 5814783 = #58B9FF)."
            ),
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
        "file_name": "report.docx",
        "channel_id": "123456789012345678",
    },
)
async def handle_discord_send_attachment(
    config: dict[str, Any],
    input_data: dict[str, Any],
    context: dict[str, Any],
) -> dict[str, Any]:

    # ── Validate required config fields ───────────────────────────────────────
    bot_token = config.get("bot_token", "").strip()
    if not bot_token:
        return {"error": "missing field: bot_token", "__input_error__": True}

    channel_id = resolve_template(config.get("channel_id", ""), input_data).strip()
    if not channel_id:
        return {"error": "missing field: channel_id", "__input_error__": True}

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
    message_raw = config.get("message", "")
    message = resolve_template(message_raw, input_data) if message_raw else ""

    embed_enabled = bool(config.get("embed_enabled", False))

    # ── Build multipart payload ────────────────────────────────────────────────
    # Discord file uploads use multipart/form-data with the file in a "file" field
    # and optional JSON payload_json for message content/embeds.
    import json as _json

    payload_json: dict[str, Any] = {}

    if message.strip():
        payload_json["content"] = message

    if embed_enabled:
        embed: dict[str, Any] = {}

        embed_title_raw = config.get("embed_title", "").strip()
        if embed_title_raw:
            embed["title"] = resolve_template(embed_title_raw, input_data)

        embed_color = config.get("embed_color")
        if embed_color is not None:
            try:
                embed["color"] = int(embed_color)
            except (TypeError, ValueError):
                pass

        if not embed.get("title"):
            embed["title"] = f"📎 {file_name}"

        payload_json["embeds"] = [embed]

    # ── POST to Discord API ────────────────────────────────────────────────────
    url = f"https://discord.com/api/v10/channels/{channel_id}/messages"
    headers = {
        "Authorization": f"Bot {bot_token}",
    }

    try:
        with open(real_path, "rb") as f:
            files = {"file": (file_name, f, "application/octet-stream")}
            data = {}
            if payload_json:
                data["payload_json"] = _json.dumps(payload_json)

            async with httpx.AsyncClient(timeout=60) as client:
                resp = await client.post(url, headers=headers, files=files, data=data)

        if resp.status_code not in (200, 201):
            return {
                "error": f"Discord API returned {resp.status_code}: {resp.text}",
                "__service_error__": True,
            }

        resp_json: dict[str, Any] = resp.json()

    except httpx.TimeoutException:
        return {
            "error": "Discord API request timed out (file upload, 60s limit).",
            "__service_error__": True,
        }
    except Exception as e:
        return {"error": f"Discord API error: {e}", "__service_error__": True}

    # ── Return result ──────────────────────────────────────────────────────────
    result = {
        "sent": True,
        "message_id": resp_json.get("id", ""),
        "file_name": file_name,
        "channel_id": resp_json.get("channel_id", channel_id),
    }
    logger.info(f"Discord file sent: {file_name} → channel {channel_id}")
    return apply_output_mapping(result, config.get("output_mapping", ""))
