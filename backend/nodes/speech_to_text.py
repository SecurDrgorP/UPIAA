# FILE: backend/nodes/speech_to_text.py
#
# Speech-to-Text — UPIAA (Multi-Provider)
# ─────────────────────────────────────────────────────────────────────────────
# Transcribes audio files to text using a hosted speech-to-text API.
#
# Supports three providers via a config selector — swap providers from the
# canvas UI without rewiring the workflow DAG:
#
#   • OpenAI  (Whisper)          — /v1/audio/transcriptions
#   • Deepgram (Nova-2)          — /v1/listen
#   • Google  (Cloud Speech STT) — /v1/speech:recognize
#
# Each provider is called directly via httpx (no SDK dependencies), following
# the same pattern as llm_openai.py, llm_gemini.py, and every other AI node
# in this backend.
#
# WHAT THIS NODE DOES
# ───────────────────
#   1. Reads an audio file from disk (file_path, typically from
#      telegram_file_download).
#   2. Sends it to the selected provider's transcription endpoint.
#   3. Returns the transcript text, detected language, and duration.
#
# OUTPUT FIELDS
# ─────────────
#   text        The transcribed text
#   language    Detected or specified language (ISO-639-1)
#   duration    Audio duration in seconds (when available from provider)
#   provider    Which provider was used ("openai" | "deepgram" | "google")
#
# ─────────────────────────────────────────────────────────────────────────────

import base64
import logging
import mimetypes
import os
from typing import Any

import httpx

from core.node_registry import register_action
from core.expressions import resolve_template, apply_output_mapping

logger = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────────────────

_HTTP_TIMEOUT = 120  # seconds — transcription can be slow for long audio
_MAX_FILE_SIZE = 25 * 1024 * 1024  # 25 MB — OpenAI Whisper limit (most restrictive)


# ─────────────────────────────────────────────────────────────────────────────
# Provider implementations — each is a standalone async function
# ─────────────────────────────────────────────────────────────────────────────

async def _transcribe_openai(
    api_key: str,
    file_path: str,
    file_name: str,
    model: str,
    language: str,
) -> dict[str, Any]:
    """OpenAI Whisper — POST multipart to /v1/audio/transcriptions."""

    url = "https://api.openai.com/v1/audio/transcriptions"
    headers = {"Authorization": f"Bearer {api_key}"}

    data: dict[str, str] = {
        "model": model or "whisper-1",
        "response_format": "verbose_json",
    }
    if language:
        data["language"] = language

    try:
        with open(file_path, "rb") as f:
            files = {"file": (file_name, f, "application/octet-stream")}
            async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
                resp = await client.post(url, headers=headers, data=data, files=files)

        if resp.status_code == 401:
            return {"error": "Invalid OpenAI API key (401 Unauthorized).", "__input_error__": True}
        if resp.status_code != 200:
            return {"error": f"OpenAI Whisper error {resp.status_code}: {resp.text}", "__service_error__": True}

        result = resp.json()
        return {
            "text":     result.get("text", ""),
            "language": result.get("language", language or "unknown"),
            "duration": result.get("duration", 0.0),
        }
    except Exception as e:
        return {"error": f"OpenAI Whisper request failed: {e}", "__service_error__": True}


async def _transcribe_deepgram(
    api_key: str,
    file_path: str,
    file_name: str,
    language: str,
) -> dict[str, Any]:
    """Deepgram Nova-2 — POST binary body to /v1/listen."""

    mime_type = mimetypes.guess_type(file_name)[0] or "audio/ogg"

    params: dict[str, str] = {
        "model": "nova-2",
        "smart_format": "true",
        "detect_language": "true" if not language else "false",
    }
    if language:
        params["language"] = language

    url = "https://api.deepgram.com/v1/listen"
    headers = {
        "Authorization": f"Token {api_key}",
        "Content-Type": mime_type,
    }

    try:
        with open(file_path, "rb") as f:
            audio_bytes = f.read()

        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
            resp = await client.post(url, headers=headers, params=params, content=audio_bytes)

        if resp.status_code == 401:
            return {"error": "Invalid Deepgram API key (401 Unauthorized).", "__input_error__": True}
        if resp.status_code != 200:
            return {"error": f"Deepgram error {resp.status_code}: {resp.text}", "__service_error__": True}

        data = resp.json()
        channel = (
            data.get("results", {})
                .get("channels", [{}])[0]
                .get("alternatives", [{}])[0]
        )
        metadata = data.get("metadata", {})

        return {
            "text":     channel.get("transcript", ""),
            "language": (
                channel.get("detected_language")
                or metadata.get("language", language or "unknown")
            ),
            "duration": metadata.get("duration", 0.0),
        }
    except Exception as e:
        return {"error": f"Deepgram request failed: {e}", "__service_error__": True}


async def _transcribe_google(
    api_key: str,
    file_path: str,
    file_name: str,
    language: str,
) -> dict[str, Any]:
    """Google Cloud Speech-to-Text — POST JSON with base64 audio."""

    mime_type = mimetypes.guess_type(file_name)[0] or "audio/ogg"

    # Map common MIME types to Google encoding enums
    encoding_map = {
        "audio/ogg":  "OGG_OPUS",
        "audio/mpeg": "MP3",
        "audio/mp3":  "MP3",
        "audio/wav":  "LINEAR16",
        "audio/x-wav": "LINEAR16",
        "audio/flac": "FLAC",
        "audio/webm": "WEBM_OPUS",
    }
    encoding = encoding_map.get(mime_type, "ENCODING_UNSPECIFIED")

    url = f"https://speech.googleapis.com/v1/speech:recognize?key={api_key}"

    try:
        with open(file_path, "rb") as f:
            audio_b64 = base64.b64encode(f.read()).decode("ascii")

        payload = {
            "config": {
                "encoding": encoding,
                "languageCode": language or "en-US",
                "enableAutomaticPunctuation": True,
            },
            "audio": {
                "content": audio_b64,
            },
        }

        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
            resp = await client.post(url, json=payload)

        if resp.status_code == 403:
            return {"error": "Invalid or unauthorized Google API key (403).", "__input_error__": True}
        if resp.status_code != 200:
            return {"error": f"Google STT error {resp.status_code}: {resp.text}", "__service_error__": True}

        data = resp.json()
        results = data.get("results", [])
        if not results:
            return {
                "text":     "",
                "language": language or "en-US",
                "duration": 0.0,
            }

        transcript_parts = []
        for result in results:
            alts = result.get("alternatives", [])
            if alts:
                transcript_parts.append(alts[0].get("transcript", ""))

        # Google STT v1 doesn't return duration in the response — estimate
        # from file size (rough, but better than nothing).
        file_size = os.path.getsize(file_path)
        estimated_duration = round(file_size / 16000, 1)  # ~16kB/s for typical speech audio

        return {
            "text":     " ".join(transcript_parts),
            "language": language or "en-US",
            "duration": estimated_duration,
        }
    except Exception as e:
        return {"error": f"Google STT request failed: {e}", "__service_error__": True}


# ─────────────────────────────────────────────────────────────────────────────
# Node registration
# ─────────────────────────────────────────────────────────────────────────────

@register_action(
    type        = "speech_to_text",
    label       = "Speech to Text",
    icon        = "🎙️",
    category    = "Actions",
    description = (
        "Transcribe audio files to text using a hosted speech-to-text API. "
        "Supports OpenAI Whisper, Deepgram Nova-2, and Google Cloud Speech."
    ),
    config_schema = {
        "provider": {
            "type":     "select",
            "label":    "Provider",
            "required": True,
            "default":  "openai",
            "options": [
                {"value": "openai",   "label": "OpenAI (Whisper)"},
                {"value": "deepgram", "label": "Deepgram (Nova-2)"},
                {"value": "google",   "label": "Google Cloud Speech"},
            ],
            "description": (
                "Which speech-to-text provider to use. All providers produce "
                "the same output shape (text, language, duration)."
            ),
        },
        "credential_id": {
            "type":        "string",
            "label":       "Credential ID",
            "required":    False,
            "placeholder": "cred_...",
            "description": (
                "Optional encrypted credential bundle ID. If set, the bundle "
                "should provide api_key for the selected provider."
            ),
        },
        "api_key": {
            "type":        "string",
            "label":       "API Key",
            "secret":      True,
            "required":    False,
            "placeholder": "sk-... / dg-... / AIza...",
            "visibleWhen": {"credential_id": ""},
            "description": (
                "API key for the selected provider. "
                "OpenAI: starts with sk-  •  Deepgram: starts with dg-  •  "
                "Google: your Cloud API key."
            ),
        },
        "file_path": {
            "type":        "string",
            "label":       "Audio File Path",
            "required":    True,
            "template":    True,
            "placeholder": "{{telegram_file_download.file_path}}",
            "description": (
                "Path to the audio file to transcribe. Typically comes from "
                "the Telegram File Download node. "
                "Supports {{expression}} interpolation."
            ),
        },
        "model": {
            "type":        "string",
            "label":       "Model",
            "required":    False,
            "default":     "whisper-1",
            "placeholder": "whisper-1",
            "visibleWhen": {"provider": "openai"},
            "description": "OpenAI Whisper model name.",
        },
        "language": {
            "type":        "string",
            "label":       "Language Hint",
            "required":    False,
            "placeholder": "en",
            "description": (
                "Optional ISO-639-1 language code (e.g. en, fr, ar). "
                "Helps the model produce more accurate results. "
                "Leave empty for auto-detection."
            ),
        },
        "output_mapping": {
            "type":        "code",
            "label":       "Output mapping",
            "required":    False,
            "description": 'Rename or extract output keys. Example: {"transcript": "text"}',
        },
    },
    example_output = {
        "text":     "Hello, this is a voice message.",
        "language": "en",
        "duration": 4.2,
        "provider": "openai",
    },
)
async def handle_speech_to_text(
    config:     dict[str, Any],
    input_data: dict[str, Any],
    context:    dict[str, Any],
) -> dict[str, Any]:

    # ── 1. Validate config ────────────────────────────────────────────────────

    provider = config.get("provider", "openai")
    if provider not in ("openai", "deepgram", "google"):
        return {"error": f"Unsupported provider: {provider}", "__input_error__": True}

    api_key = (config.get("api_key") or "").strip()
    if not api_key:
        return {"error": "missing field: api_key", "__input_error__": True}

    file_path = resolve_template(config.get("file_path", ""), input_data).strip()
    if not file_path:
        return {"error": "missing field: file_path", "__input_error__": True}

    # Normalize path — same convention as pdf_document.py / word_document.py
    if file_path.startswith("/data/"):
        file_path = "/app/uploads/" + file_path[6:]
    elif "/" not in file_path and "\\" not in file_path:
        file_path = "/app/uploads/" + file_path

    if not os.path.isfile(file_path):
        return {"error": f"Audio file not found: {file_path}", "__input_error__": True}

    file_size = os.path.getsize(file_path)
    if file_size > _MAX_FILE_SIZE:
        size_mb = file_size / (1024 * 1024)
        return {
            "__input_error__": (
                f"Audio file is {size_mb:.1f} MB — exceeds the 25 MB limit "
                f"for hosted transcription APIs."
            )
        }

    file_name = os.path.basename(file_path)
    language = (config.get("language") or "").strip()

    # ── 2. Dispatch to the selected provider ──────────────────────────────────

    logger.info(
        "speech_to_text: transcribing %s (%d bytes) via %s",
        file_name, file_size, provider,
    )

    if provider == "openai":
        model = (config.get("model") or "whisper-1").strip()
        result = await _transcribe_openai(api_key, file_path, file_name, model, language)

    elif provider == "deepgram":
        result = await _transcribe_deepgram(api_key, file_path, file_name, language)

    elif provider == "google":
        result = await _transcribe_google(api_key, file_path, file_name, language)

    else:
        return {"error": f"Unsupported provider: {provider}", "__input_error__": True}

    # ── 3. Handle provider errors ─────────────────────────────────────────────

    if result.get("error"):
        return result

    # ── 4. Build final output ─────────────────────────────────────────────────

    result["provider"] = provider

    logger.info(
        "speech_to_text: transcribed %d chars via %s (lang=%s, dur=%.1fs)",
        len(result.get("text", "")),
        provider,
        result.get("language", "?"),
        result.get("duration", 0),
    )

    return apply_output_mapping(result, config.get("output_mapping", ""))
