"""
nodes/pdf_document.py
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📄  PDF Document — Read or Create .pdf Files
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

WHAT THIS NODE DOES
───────────────────
Reads or creates PDF documents. Operates in two modes:

  • **read**  — Opens an existing .pdf file and returns its full text content.
  • **write** — Creates a new .pdf file from the provided content string
                and returns the output file path.

QUICK START
───────────
  1. Select a mode: "read" or "write".
  2. For read:  provide the file_path to an existing .pdf file.
  3. For write: provide the output_path and content to write.
  4. All text fields support {{expression}} interpolation.

OUTPUT FIELDS
─────────────
  mode            The mode used ("read" or "write")
  text            (read mode) The full text extracted from the document
  word_count      (read mode) Number of words in the document
  file_path       (write mode) The path of the created document
  pages           (write mode) Number of paragraphs written
"""

import asyncio
import os
import html
from typing import Any

from core.node_registry import register_action
from core.expressions import resolve_template, apply_output_mapping


@register_action(
    type="pdf_document",
    label="PDF Document",
    icon="📑",
    description="Read or create .pdf files.",
    category="Actions",
    config_schema={
        "mode": {
            "type": "select",
            "label": "Mode",
            "required": True,
            "default": "read",
            "options": [
                {"value": "read", "label": "Read existing file"},
                {"value": "write", "label": "Write / Create file"},
            ],
            "description": "Whether to read an existing document or create a new one.",
        },
        "file_path": {
            "type": "file",
            "label": "File Path",
            "required": True,
            "template": True,
            "placeholder": "/app/uploads/report.pdf",
            "visibleWhen": {"mode": "read"},
            "description": "Path to the .pdf file to read. Supports {{expression}} interpolation.",
        },
        "output_path": {
            "type": "string",
            "label": "Output Path",
            "required": True,
            "template": True,
            "placeholder": "/app/uploads/output.pdf",
            "visibleWhen": {"mode": "write"},
            "description": "Path where the new .pdf file will be saved. Supports {{expression}} interpolation.",
        },
        "content": {
            "type": "string",
            "label": "Content",
            "multiline": True,
            "template": True,
            "placeholder": "Paragraph 1\\n\\nParagraph 2",
            "visibleWhen": {"mode": "write"},
            "description": (
                "Text content to write. "
                "Supports {{expression}} interpolation."
            ),
        },
        "output_mapping": {
            "type": "code",
            "label": "Output mapping",
            "required": False,
            "description": 'Rename or extract output keys. Example: {"summary": "text"}',
        },
    },
    example_output={
        "mode": "read",
        "text": "This is the document content...",
        "word_count": 42,
    },
)
async def handle_pdf_document(
    config: dict[str, Any],
    input_data: dict[str, Any],
    context: dict[str, Any],
) -> dict[str, Any]:

    mode = config.get("mode", "read")

    # ── READ mode ──────────────────────────────────────────────────────────────
    if mode == "read":
        try:
            import pypdf
        except ImportError:
            return {
                "error": "pypdf is not installed. Run: pip install pypdf",
                "__input_error__": True,
            }

        file_path = resolve_template(config.get("file_path", ""), input_data).strip()
        if not file_path:
            return {"error": "missing field: file_path", "__input_error__": True}

        if file_path.startswith("/data/"):
            file_path = "/app/uploads/" + file_path[6:]
        elif "/" not in file_path and "\\" not in file_path:
            file_path = "/app/uploads/" + file_path

        try:
            def _read_pdf():
                reader = pypdf.PdfReader(file_path)
                return "\n\n".join(page.extract_text() for page in reader.pages if page.extract_text())

            text = await asyncio.to_thread(_read_pdf)
            word_count = len(text.split())

            result = {
                "mode": "read",
                "text": text,
                "word_count": word_count,
                "file_path": file_path,
            }
            return apply_output_mapping(result, config.get("output_mapping", ""))

        except FileNotFoundError:
            return {"error": f"File not found: {file_path}", "__input_error__": True}
        except Exception as e:
            return {"error": f"Failed to read document: {e}", "__service_error__": True}

    # ── WRITE mode ─────────────────────────────────────────────────────────────
    elif mode == "write":
        try:
            from reportlab.lib.pagesizes import letter
            from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
            from reportlab.lib.styles import getSampleStyleSheet
        except ImportError:
            return {
                "error": "reportlab is not installed. Run: pip install reportlab",
                "__input_error__": True,
            }

        output_path = resolve_template(config.get("output_path", ""), input_data).strip()
        content = resolve_template(config.get("content", ""), input_data)

        if not output_path:
            return {"error": "missing field: output_path", "__input_error__": True}
        if not content.strip():
            return {"error": "missing field: content — cannot create an empty document", "__input_error__": True}

        if output_path.startswith("/data/"):
            output_path = "/app/uploads/" + output_path[6:]
        elif "/" not in output_path and "\\" not in output_path:
            output_path = "/app/uploads/" + output_path

        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

        try:
            def _write_pdf():
                doc = SimpleDocTemplate(output_path, pagesize=letter)
                styles = getSampleStyleSheet()
                style = styles["Normal"]
                
                # Sanitize to strictly printable unicode to avoid ReportLab exceptions
                # and remove XML control chars if present.
                import re
                safe_content = re.sub(r'[\x00-\x08\x0b-\x0c\x0e-\x1f]', '', content)
                safe_content = html.escape(safe_content)
                
                flowables = []
                paragraphs = safe_content.split("\n\n")
                for para in paragraphs:
                    if para.strip():
                        # Replace single newlines with br tags for reportlab
                        flowables.append(Paragraph(para.replace('\n', '<br />'), style))
                        flowables.append(Spacer(1, 12))

                doc.build(flowables)
                return len(paragraphs)

            paragraphs_written = await asyncio.to_thread(_write_pdf)

            result = {
                "mode": "write",
                "file_path": output_path,
                "paragraphs_written": paragraphs_written,
            }
            return apply_output_mapping(result, config.get("output_mapping", ""))

        except Exception as e:
            return {"error": f"Failed to write document: {e}", "__service_error__": True}

    else:
        return {"error": f"Unknown mode: {mode}", "__input_error__": True}
