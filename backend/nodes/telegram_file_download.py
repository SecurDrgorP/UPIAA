# FILE: backend/nodes/telegram_file_download.py
#
# Telegram File Download — UPIAA
# ─────────────────────────────────────────────────────────────────────────────
# Downloads a file from Telegram's servers given a file_id.
#
# Telegram stores uploaded files on their servers and assigns each a unique
# file_id.  This node resolves that ID to a download URL via the getFile
# endpoint, pulls the bytes, and writes them to /app/uploads/ so downstream
# nodes (pdf_document, word_document, speech_to_text) can consume them
# through the same file-path convention they already use.
#
# WHAT THIS NODE DOES
# ───────────────────
#   1. Calls Telegram's getFile to resolve a file_id into a download path.
#   2. Downloads the file bytes via httpx.
#   3. Saves to /app/uploads/{original_filename}.
#   4. Returns file_path, file_name, mime_type, and file_size.
#
# LIMITS
# ──────
#   Telegram's Bot API getFile endpoint only serves files up to 20 MB.
#   Files larger than that must be downloaded through Telegram's TDLib
#   (not supported here).  This node surfaces that ceiling as a clear
#   input error rather than letting an oversized file crash silently.
#
# OUTPUT FIELDS
# ─────────────
#   file_path       Absolute path where the file was saved
#   file_name       Original filename (basename)
#   mime_type       MIME type guessed from the file extension
#   file_size       Size in bytes as reported by Telegram
#
# ─────────────────────────────────────────────────────────────────────────────

import logging
import mimetypes
import os
from typing import Any

import httpx

from core.node_registry import register_action
from core.expressions import resolve_template, apply_output_mapping

logger = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────────────────

_TELEGRAM_BASE = "https://api.telegram.org/bot{token}"
_TELEGRAM_FILE = "https://api.telegram.org/file/bot{token}/{path}"
_GETFILE_LIMIT = 20_000_000  # 20 MB — Telegram Bot API hard ceiling for getFile
_DOWNLOAD_DIR  = "/app/uploads"
_HTTP_TIMEOUT  = 30  # seconds


@register_action(
    type        = "telegram_file_download",
    label       = "Telegram: File Download",
    icon        = "📥",
    category    = "Communication",
    description = (
        "Download a file from Telegram given its file_id. "
        "Resolves the file via getFile, downloads the bytes, and saves "
        "to /app/uploads/ for downstream processing."
    ),
    config_schema = {
        "credential_id": {
            "type":        "string",
            "label":       "Credential ID",
            "required":    False,
            "placeholder": "cred_...",
            "description": (
                "Optional encrypted credential bundle ID. If set, the bundle "
                "should provide bot_token."
            ),
        },
        "bot_token": {
            "type":        "string",
            "label":       "Bot Token",
            "secret":      True,
            "required":    False,
            "placeholder": "123456789:AABBccDDeeFFggHHiiJJkkLLmmNNoo",
            "visibleWhen": {"credential_id": ""},
            "description": (
                "Manual bot token — only needed when no credential bundle is "
                "selected above. Get one from @BotFather on Telegram."
            ),
        },
        "file_id": {
            "type":        "string",
            "label":       "File ID",
            "required":    True,
            "template":    True,
            "placeholder": "{{telegram_trigger.raw_update.message.voice.file_id}}",
            "description": (
                "Telegram file_id to download. Use {{expression}} to pull it "
                "from the trigger's raw_update — voice, audio, and document "
                "messages each expose file_id at a different path.\n\n"
                "  Voice:    {{telegram_trigger.raw_update.message.voice.file_id}}\n"
                "  Audio:    {{telegram_trigger.raw_update.message.audio.file_id}}\n"
                "  Document: {{telegram_trigger.raw_update.message.document.file_id}}"
            ),
        },
        "output_mapping": {
            "type":        "code",
            "label":       "Output mapping",
            "required":    False,
            "description": 'Rename or extract output keys. Example: {"path": "file_path"}',
        },
    },
    example_output = {
        "file_path":  "/app/uploads/voice_note.ogg",
        "file_name":  "voice_note.ogg",
        "mime_type":  "audio/ogg",
        "file_size":  45678,
    },
)
async def handle_telegram_file_download(
    config:     dict[str, Any],
    input_data: dict[str, Any],
    context:    dict[str, Any],
) -> dict[str, Any]:

    # ── 1. Validate config ────────────────────────────────────────────────────

    bot_token = (config.get("bot_token") or "").strip()
    if not bot_token:
        return {
            "__input_error__": (
                "bot_token is required. Get one from @BotFather on Telegram."
            )
        }

    file_id = resolve_template(config.get("file_id", ""), input_data).strip()
    if not file_id:
        return {"error": "missing field: file_id", "__input_error__": True}

    base_url = _TELEGRAM_BASE.format(token=bot_token)

    # ── 2. Resolve file_id → download path via getFile ────────────────────────

    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
            resp = await client.get(
                f"{base_url}/getFile",
                params={"file_id": file_id},
            )
            resp.raise_for_status()
            data = resp.json()

            if not data.get("ok"):
                return {
                    "__service_error__": (
                        f"Telegram getFile failed: "
                        f"{data.get('description', 'unknown error')}"
                    )
                }

            file_info: dict = data["result"]

    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 400:
            return {
                "__input_error__": (
                    "Telegram rejected the file_id (400 Bad Request). "
                    "The file_id may be expired or invalid."
                )
            }
        return {
            "__service_error__": (
                f"Telegram getFile returned HTTP {exc.response.status_code}."
            )
        }
    except httpx.RequestError as exc:
        return {
            "__service_error__": f"Could not reach Telegram API: {exc}"
        }

    # ── 3. Enforce 20 MB getFile ceiling ──────────────────────────────────────

    tg_file_size: int = file_info.get("file_size", 0)
    if tg_file_size > _GETFILE_LIMIT:
        size_mb = tg_file_size / (1024 * 1024)
        return {
            "__input_error__": (
                f"File is {size_mb:.1f} MB — exceeds Telegram's 20 MB "
                f"getFile limit. Files larger than 20 MB cannot be "
                f"downloaded through the Bot API."
            )
        }

    tg_file_path: str = file_info.get("file_path", "")
    if not tg_file_path:
        return {
            "__service_error__": (
                "Telegram returned an empty file_path — the file may have "
                "expired. Ask the sender to resend it."
            )
        }

    # ── 4. Download the file bytes ────────────────────────────────────────────

    download_url = _TELEGRAM_FILE.format(token=bot_token, path=tg_file_path)

    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
            resp = await client.get(download_url)
            resp.raise_for_status()
            file_bytes = resp.content

    except httpx.HTTPStatusError as exc:
        return {
            "__service_error__": (
                f"Failed to download file: HTTP {exc.response.status_code}"
            )
        }
    except httpx.RequestError as exc:
        return {
            "__service_error__": f"File download failed: {exc}"
        }

    # ── 5. Save to /app/uploads/ ──────────────────────────────────────────────

    file_name = os.path.basename(tg_file_path)
    mime_type = mimetypes.guess_type(file_name)[0] or "application/octet-stream"

    os.makedirs(_DOWNLOAD_DIR, exist_ok=True)
    local_path = os.path.join(_DOWNLOAD_DIR, file_name)

    try:
        with open(local_path, "wb") as f:
            f.write(file_bytes)
    except OSError as exc:
        return {
            "__service_error__": f"Failed to write file to disk: {exc}"
        }

    actual_size = len(file_bytes)
    logger.info(
        "telegram_file_download: saved %s (%s, %d bytes) → %s",
        file_name, mime_type, actual_size, local_path,
    )

    # ── 6. Return structured output ───────────────────────────────────────────

    result = {
        "file_path":  local_path,
        "file_name":  file_name,
        "mime_type":  mime_type,
        "file_size":  actual_size,
    }
    return apply_output_mapping(result, config.get("output_mapping", ""))
