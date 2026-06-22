import asyncio
import json
import logging
import os
import smtplib
from email import encoders
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any
from core.node_registry import register_action
from core.expressions import resolve_template, apply_output_mapping

logger = logging.getLogger(__name__)

ALLOWED_DIR = "/tmp/m8m_docs/"


async def execute_send_email(config: dict[str, Any], input_data: dict[str, Any]) -> dict[str, Any]:
    smtp_host = resolve_template(config.get("smtp_host", "smtp.gmail.com"), input_data)
    smtp_port = int(config.get("smtp_port", 587))
    sender = resolve_template(config.get("email", ""), input_data)
    password = resolve_template(config.get("app_password", ""), input_data)
    recipients = resolve_template(config.get("to", ""), input_data)
    subject = resolve_template(config.get("subject", ""), input_data)
    body = resolve_template(config.get("body", ""), input_data)
    content_type = resolve_template(config.get("content_type", "plain"), input_data)

    if not sender or not password:
        return {"error": "Email and App Password are required", "__input_error__": True}
    if not recipients:
        return {"error": "Recipient email ('to') is required", "__input_error__": True}

    msg = MIMEMultipart()
    msg["From"] = sender
    msg["To"] = recipients
    msg["Subject"] = subject
    msg.attach(MIMEText(body, content_type))

    # ── Attachments (JSON array of file paths) ─────────────────────────────────
    attached_files: list[str] = []
    attachments_raw = resolve_template(config.get("attachments", ""), input_data).strip()

    if attachments_raw:
        try:
            attachment_paths = json.loads(attachments_raw)
        except (json.JSONDecodeError, TypeError):
            return {
                "error": (
                    "attachments must be a valid JSON array of file paths, "
                    f'e.g. ["/tmp/m8m_docs/report.docx"]. Got: {attachments_raw!r}'
                ),
                "__input_error__": True,
            }

        if not isinstance(attachment_paths, list):
            return {
                "error": "attachments must be a JSON array, not a single value.",
                "__input_error__": True,
            }

        for fpath in attachment_paths:
            fpath = str(fpath).strip()
            if not fpath:
                continue

            real_path = os.path.realpath(fpath)
            if not real_path.startswith(os.path.realpath(ALLOWED_DIR)):
                return {
                    "error": f"Attachment path must be under {ALLOWED_DIR}, got: {real_path}",
                    "__input_error__": True,
                }
            if not os.path.isfile(real_path):
                return {"error": f"Attachment not found: {real_path}", "__input_error__": True}

            file_name = os.path.basename(real_path)
            with open(real_path, "rb") as f:
                part = MIMEBase("application", "octet-stream")
                part.set_payload(f.read())
            encoders.encode_base64(part)
            part.add_header("Content-Disposition", f'attachment; filename="{file_name}"')
            msg.attach(part)
            attached_files.append(file_name)
            logger.info(f"Attached file: {file_name}")

    def _send():
        with smtplib.SMTP(smtp_host, smtp_port, timeout=30) as server:
            server.ehlo()
            server.starttls()
            server.ehlo()
            server.login(sender, password)
            to_list = [r.strip() for r in recipients.split(",")]
            server.sendmail(sender, to_list, msg.as_string())
            logger.info(f"Email sent to {recipients}")

    await asyncio.get_event_loop().run_in_executor(None, _send)

    result: dict[str, Any] = {
        "sent": True,
        "from": sender,
        "to": recipients,
        "subject": subject,
    }
    if attached_files:
        result["attachment_count"] = len(attached_files)
        result["attachments"] = attached_files

    return result

@register_action(
    type="send_email",
    label="Send Email",
    icon="📤",
    description="Send an email via Gmail SMTP",
    category="Communication",
    config_schema={
        "smtp_host": {
            "type": "string",
            "label": "SMTP Host",
            "default": "smtp.gmail.com",
            "placeholder": "smtp.gmail.com",
            "description": "SMTP server hostname.",
        },
        "smtp_port": {
            "type": "number",
            "label": "SMTP Port",
            "default": 587,
            "description": "SMTP server port (587 for TLS, 465 for SSL).",
        },
        "email": {
            "type": "string",
            "label": "Sender Email",
            "required": True,
            "placeholder": "you@gmail.com",
            "description": "The email address to send from.",
        },
        "app_password": {
            "type": "string",
            "label": "App Password",
            "secret": True,
            "required": True,
            "description": "App-specific password (not your login password). Generate in Google Account settings.",
        },
        "to": {
            "type": "string",
            "label": "Recipient(s)",
            "required": True,
            "template": True,
            "placeholder": "recipient@example.com",
            "description": "Comma-separated list of recipient emails. Supports template interpolation.",
        },
        "subject": {
            "type": "string",
            "label": "Subject",
            "template": True,
            "placeholder": "Report: {{$input.title}}",
            "description": "Email subject line. Supports template interpolation.",
        },
        "body": {
            "type": "string",
            "label": "Body",
            "multiline": True,
            "template": True,
            "description": "Email body content. Supports template interpolation.",
        },
        "content_type": {
            "type": "select",
            "label": "Content Type",
            "default": "plain",
            "options": [
                {"value": "plain", "label": "Plain Text"},
                {"value": "html", "label": "HTML"},
            ],
            "description": "Email body format.",
        },
        "attachments": {
            "type": "string",
            "label": "Attachments (JSON array)",
            "required": False,
            "template": True,
            "placeholder": '["/tmp/m8m_docs/{{node_d7.output_path}}"]',
            "description": (
                'JSON array of local file paths to attach. '
                'Example: ["/tmp/m8m_docs/report.docx", "/tmp/m8m_docs/data.xlsx"]. '
                'Files must be under /tmp/m8m_docs/. Supports {{expression}} interpolation.'
            ),
        },
        "output_mapping": {
            "type": "code",
            "label": "Output mapping",
            "required": False,
            "description": 'Rename or extract output keys. Example: {"summary": "response"}',
        },
    },
)
async def handle_send_email(config: dict[str, Any], input_data: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    res = await execute_send_email(config, input_data)
    return apply_output_mapping(res, config.get("output_mapping", ""))
