import asyncio
import logging
import smtplib
import os
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
from typing import Any
from core.node_registry import register_action
from core.expressions import resolve_template, apply_output_mapping

logger = logging.getLogger(__name__)

async def execute_send_email(config: dict[str, Any], input_data: dict[str, Any]) -> dict[str, Any]:
    smtp_host = resolve_template(config.get("smtp_host", "smtp.gmail.com"), input_data)
    smtp_port = int(config.get("smtp_port", 587))
    sender = resolve_template(config.get("email", ""), input_data)
    password = resolve_template(config.get("app_password", ""), input_data)
    recipients = resolve_template(config.get("to", ""), input_data)
    subject = resolve_template(config.get("subject", ""), input_data)
    body = resolve_template(config.get("body", ""), input_data)
    content_type = resolve_template(config.get("content_type", "plain"), input_data)
    attachment_path = resolve_template(config.get("attachment_path", ""), input_data).strip()

    if not sender or not password:
        return {"error": "Email and App Password are required", "__input_error__": True}
    if not recipients:
        return {"error": "Recipient email ('to') is required", "__input_error__": True}

    msg = MIMEMultipart()
    msg["From"] = sender
    msg["To"] = recipients
    msg["Subject"] = subject
    msg.attach(MIMEText(body, content_type))

    if attachment_path:
        if attachment_path.startswith("/data/"):
            attachment_path = "/app/uploads/" + attachment_path[6:]
        elif "/" not in attachment_path and "\\" not in attachment_path:
            attachment_path = "/app/uploads/" + attachment_path
        
        try:
            import mimetypes
            mime_type, _ = mimetypes.guess_type(attachment_path)
            if mime_type is None:
                mime_type = "application/octet-stream"
            
            maintype, subtype = mime_type.split("/", 1)
            
            with open(attachment_path, "rb") as att_file:
                part = MIMEBase(maintype, subtype)
                part.set_payload(att_file.read())
            encoders.encode_base64(part)
            filename = os.path.basename(attachment_path)
            part.add_header("Content-Disposition", f"attachment; filename={filename}")
            msg.attach(part)
        except Exception as e:
            return {"error": f"Failed to attach file: {e}", "__service_error__": True}

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

    return {
        "sent": True,
        "from": sender,
        "to": recipients,
        "subject": subject,
    }

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
        "attachment_path": {
            "type": "file",
            "label": "Attachment Path (optional)",
            "required": False,
            "template": True,
            "placeholder": "/tmp/m8m_docs/report.docx",
            "description": "Path to a file to attach to the email. Supports template interpolation.",
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
