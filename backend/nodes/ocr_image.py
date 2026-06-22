"""
nodes/ocr_image.py
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🔍  OCR Image — Extract Text from Images
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

WHAT THIS NODE DOES
───────────────────
Extracts text from images using Tesseract OCR via the pytesseract Python
wrapper. Accepts images from three sources:

  • **file**   — A local file path to an image
  • **url**    — A URL pointing to an image (downloaded automatically)
  • **base64** — A base64-encoded image string

Returns the extracted text and a word count.

QUICK START
───────────
  1. Select the image source type.
  2. Provide the image path, URL, or base64 string.
  3. Optionally set the Tesseract language (default: eng).
  4. All text fields support {{expression}} interpolation.

OUTPUT FIELDS
─────────────
  text            The extracted text from the image
  word_count      Number of words extracted
  source_type     The source type used ("file", "url", or "base64")
  language        The OCR language used

REQUIREMENTS
────────────
  • System: tesseract-ocr must be installed (apt install tesseract-ocr)
  • Python: pytesseract and Pillow packages
"""

import asyncio
import base64
import io
from typing import Any

import httpx

from core.node_registry import register_action
from core.expressions import resolve_template, apply_output_mapping


@register_action(
    type="ocr_image",
    label="OCR Image",
    icon="🔍",
    description="Extract text from images using Tesseract OCR.",
    category="Actions",
    config_schema={
        "source_type": {
            "type": "select",
            "label": "Image Source",
            "required": True,
            "default": "file",
            "options": [
                {"value": "file", "label": "Local file path"},
                {"value": "url", "label": "URL"},
                {"value": "base64", "label": "Base64 string"},
            ],
            "description": "How the image is provided.",
        },
        "image_path": {
            "type": "file",
            "label": "File Path",
            "required": True,
            "template": True,
            "placeholder": "/app/uploads/image.png",
            "visibleWhen": {"source_type": "file"},
            "description": "Path to the image file. Supports {{expression}} interpolation.",
        },
        "image_url": {
            "type": "string",
            "label": "Image URL",
            "required": True,
            "template": True,
            "placeholder": "https://example.com/image.png",
            "visibleWhen": {"source_type": "url"},
            "description": "URL of the image to download and OCR. Supports {{expression}} interpolation.",
        },
        "image_base64": {
            "type": "string",
            "label": "Base64 Image",
            "multiline": True,
            "template": True,
            "placeholder": "iVBORw0KGgo...",
            "visibleWhen": {"source_type": "base64"},
            "description": "Base64-encoded image data. Supports {{expression}} interpolation.",
        },
        "language": {
            "type": "string",
            "label": "OCR Language",
            "default": "eng",
            "placeholder": "eng",
            "description": "Tesseract language code (e.g. eng, fra, deu). Multiple: eng+fra.",
        },
        "output_mapping": {
            "type": "code",
            "label": "Output mapping",
            "required": False,
            "description": 'Rename or extract output keys. Example: {"content": "text"}',
        },
    },
    example_output={
        "text": "Hello World",
        "word_count": 2,
        "source_type": "file",
        "language": "eng",
    },
)
async def handle_ocr_image(
    config: dict[str, Any],
    input_data: dict[str, Any],
    context: dict[str, Any],
) -> dict[str, Any]:

    # ── Validate dependencies ──────────────────────────────────────────────────
    try:
        import pytesseract
    except ImportError:
        return {
            "error": (
                "pytesseract is not installed. "
                "Install with: pip install pytesseract\n"
                "Also ensure tesseract-ocr is installed on the system:\n"
                "  Ubuntu/Debian: apt install tesseract-ocr\n"
                "  macOS: brew install tesseract"
            ),
            "__input_error__": True,
        }

    try:
        from PIL import Image
    except ImportError:
        return {
            "error": (
                "Pillow is not installed. "
                "Install with: pip install Pillow"
            ),
            "__input_error__": True,
        }

    source_type = config.get("source_type", "file")
    language = config.get("language", "eng") or "eng"

    # ── Load image based on source type ────────────────────────────────────────
    try:
        if source_type == "file":
            image_path = resolve_template(config.get("image_path", ""), input_data).strip()
            if not image_path:
                return {"error": "missing field: image_path", "__input_error__": True}

            if image_path.startswith("/data/"):
                image_path = "/app/uploads/" + image_path[6:]
            elif "/" not in image_path and "\\" not in image_path:
                image_path = "/app/uploads/" + image_path

            img = await asyncio.to_thread(Image.open, image_path)

        elif source_type == "url":
            image_url = resolve_template(config.get("image_url", ""), input_data).strip()
            if not image_url:
                return {"error": "missing field: image_url", "__input_error__": True}

            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.get(image_url)
                if resp.status_code != 200:
                    return {
                        "error": f"Failed to download image: HTTP {resp.status_code}",
                        "__service_error__": True,
                    }
                img = Image.open(io.BytesIO(resp.content))

        elif source_type == "base64":
            image_b64 = resolve_template(config.get("image_base64", ""), input_data).strip()
            if not image_b64:
                return {"error": "missing field: image_base64", "__input_error__": True}

            # Strip data URI prefix if present
            if "," in image_b64:
                image_b64 = image_b64.split(",", 1)[1]

            try:
                img_bytes = base64.b64decode(image_b64)
            except Exception:
                return {"error": "Invalid base64 image data", "__input_error__": True}

            img = Image.open(io.BytesIO(img_bytes))

        else:
            return {"error": f"Unknown source_type: {source_type}", "__input_error__": True}

    except FileNotFoundError:
        return {"error": f"Image file not found: {config.get('image_path', '')}", "__input_error__": True}
    except Exception as e:
        return {"error": f"Failed to load image: {e}", "__service_error__": True}

    # ── Run OCR ────────────────────────────────────────────────────────────────
    try:
        text = await asyncio.to_thread(pytesseract.image_to_string, img, lang=language)
        text = text.strip()
        word_count = len(text.split()) if text else 0

        result = {
            "text": text,
            "word_count": word_count,
            "source_type": source_type,
            "language": language,
        }
        return apply_output_mapping(result, config.get("output_mapping", ""))

    except pytesseract.TesseractNotFoundError:
        return {
            "error": (
                "Tesseract OCR engine not found on the system. "
                "Install with: apt install tesseract-ocr (Linux) "
                "or brew install tesseract (macOS)"
            ),
            "__input_error__": True,
        }
    except Exception as e:
        return {"error": f"OCR failed: {e}", "__service_error__": True}
