import asyncio
import base64
import email
import email.utils
import imaplib
import logging
import re
import time
from typing import Any
from temporalio import activity
from core.node_registry import register_action
from core.expressions import resolve_template, apply_output_mapping
from core.schemas import NodeData

logger = logging.getLogger(__name__)

# Polling configuration constants
EMAIL_CHECK_INTERVAL_SECONDS = 30
EMAIL_MAX_WAIT_SECONDS = 60 * 60 * 24 * 7  # 7 days limit for Temporal activity

def _subject_matches(subject: str, mode: str, filter_val: str, case_sensitive: bool) -> bool:
    """Helper to check if the subject matches the configured filter."""
    if mode == "none" or not filter_val:
        return True
    
    s = subject if case_sensitive else subject.lower()
    f = filter_val if case_sensitive else filter_val.lower()
    
    if mode == "contains":
        return f in s
    if mode == "exact":
        return s == f
    if mode == "regex":
        try:
            flags = 0 if case_sensitive else re.IGNORECASE
            return bool(re.search(filter_val, subject, flags))
        except Exception as e:
            logger.error(f"Invalid regex in email filter: {e}")
            return False
    return True

def _safe_noop(mail: imaplib.IMAP4_SSL) -> bool:
    """Safely check the IMAP connection status."""
    try:
        mail.noop()
        return True
    except:
        return False

def _reconnect(host: str, port: int, user: str, password: str, folder: str) -> imaplib.IMAP4_SSL:
    """Robust reconnection helper."""
    mail = imaplib.IMAP4_SSL(host, port)
    mail.login(user, password)
    mail.select(folder)
    return mail

def _clean_address(raw: str) -> str:
    """Extract bare email address from a display-name format string."""
    name, addr = email.utils.parseaddr(raw)
    return addr.strip() if addr else raw.strip()


def _clean_date(raw: str) -> str:
    """Convert RFC 2822 date string to ISO 8601. Returns original on failure."""
    try:
        dt = email.utils.parsedate_to_datetime(raw)
        return dt.isoformat()
    except Exception:
        return raw


def _fetch_email_by_uid(mail: imaplib.IMAP4_SSL, uid: str, include_attachments: bool = False) -> dict[str, Any] | None:
    """Fetch and parse a single email by its IMAP UID. Returns cleaned fields."""
    try:
        _, msg_data = mail.uid('fetch', uid, '(RFC822)')
        if not msg_data or not msg_data[0]:
            return None

        raw = msg_data[0][1]
        msg = email.message_from_bytes(raw)

        body_text = ""
        attachments = []

        if msg.is_multipart():
            for part in msg.walk():
                content_type = part.get_content_type()
                content_disp = str(part.get("Content-Disposition", ""))
                filename = part.get_filename()

                # Extract attachment if requested
                if include_attachments and filename:
                    payload = part.get_payload(decode=True)
                    if payload:
                        attachments.append({
                            "filename": filename,
                            "content_type": content_type,
                            "size_bytes": len(payload),
                            "data": base64.b64encode(payload).decode("ascii"),
                        })
                    continue

                # Extract body text
                if content_type == "text/plain" and not body_text:
                    payload = part.get_payload(decode=True)
                    if payload:
                        body_text = payload.decode("utf-8", errors="replace")
        else:
            payload = msg.get_payload(decode=True)
            if payload:
                body_text = payload.decode("utf-8", errors="replace")

        result = {
            "subject":  msg.get("Subject", "(No Subject)"),
            "from":     _clean_address(msg.get("From", "")),
            "to":       _clean_address(msg.get("To", "")),
            "date":     _clean_date(msg.get("Date", "")),
            "body":     body_text.strip()[:4000],
            "uid":      uid,
        }

        if include_attachments:
            result["attachments"] = attachments
            result["attachment_count"] = len(attachments)

        return result
    except Exception as e:
        logger.error(f"Error fetching email UID {uid}: {e}")
        return None

async def execute_receive_email(config: dict[str, Any], input_data: dict[str, Any]) -> dict[str, Any]:
    """
    Blocks until a matching email is received or max wait time is exceeded.
    Supports subject filtering and reconnection.
    """
    imap_host = resolve_template(config.get("imap_host", "imap.gmail.com"), input_data)
    imap_port = int(config.get("imap_port", 993))
    user = resolve_template(config.get("email", ""), input_data)
    password = resolve_template(config.get("app_password", ""), input_data)
    folder = resolve_template(config.get("folder", "INBOX"), input_data)
    
    # Filter config
    filter_mode = config.get("subject_filter_mode", "none")
    filter_val = resolve_template(config.get("subject_filter_value", ""), input_data)
    case_sensitive = config.get("subject_case_sensitive", False)
    include_attachments = config.get("include_attachments", False)

    if not user or not password:
        return {"error": "Email and App Password are required", "__input_error__": True}

    loop = asyncio.get_running_loop()
    start_time = time.time()
    last_uid = 0
    mail = None

    try:
        # 1. Initial connection and record current highest UID
        mail = await loop.run_in_executor(None, _reconnect, imap_host, imap_port, user, password, folder)
        _, data = await loop.run_in_executor(None, mail.uid, 'search', None, 'ALL')
        uids = data[0].split()
        if uids:
            last_uid = int(uids[-1])
        
        logger.info(f"Email Listener started. Initial water-mark UID: {last_uid}. Folder: {folder}")

        # 2. Polling loop
        while True:
            # Temporal Heartbeat - required for long running activities
            if activity.in_activity():
                activity.heartbeat(f"Polling for emails in {folder}...")

            # Safety timeout
            if (time.time() - start_time) > EMAIL_MAX_WAIT_SECONDS:
                return {"error": "Maximum wait time exceeded for email", "__service_error__": True}

            # Check for connection drop
            if not await loop.run_in_executor(None, _safe_noop, mail):
                logger.info("IMAP Connection lost, reconnecting...")
                mail = await loop.run_in_executor(None, _reconnect, imap_host, imap_port, user, password, folder)

            # Search for new UIDs
            search_crit = f"UID {last_uid + 1}:*"
            _, data = await loop.run_in_executor(None, mail.uid, 'search', None, search_crit)
            new_uids = [u for u in data[0].split() if int(u) > last_uid]

            for uid_bytes in new_uids:
                uid = uid_bytes.decode()
                email_data = await loop.run_in_executor(None, _fetch_email_by_uid, mail, uid, include_attachments)
                
                if email_data:
                    # Apply filter
                    if _subject_matches(email_data["subject"], filter_mode, filter_val, case_sensitive):
                        logger.info(f"Matched email found: {email_data['subject']}")
                        return {
                            **email_data,
                            "folder": folder,
                            "matched_filter": filter_mode,
                            "wait_time_seconds": int(time.time() - start_time)
                        }
                    else:
                        # Update watermark even if not matched so we don't process it again
                        last_uid = max(last_uid, int(uid))
                
            # If no matches found in this batch, wait for next poll
            await asyncio.sleep(EMAIL_CHECK_INTERVAL_SECONDS)

    except asyncio.CancelledError:
        logger.info("Email listener cancelled")
        raise
    except Exception as e:
        logger.exception(f"Critical error in email listener: {e}")
        return {"error": f"IMAP Error: {str(e)}", "__service_error__": True}
    finally:
        if mail:
            try:
                await loop.run_in_executor(None, mail.logout)
            except:
                pass

@register_action(
    type="receive_email",
    label="Email Listener (IMAP)",
    icon="📬",
    description="Blocking listener that waits for a specific email to arrive via IMAP",
    category="Communication",
    config_schema={
        "imap_host": {
            "type": "string",
            "label": "IMAP Host",
            "default": "imap.gmail.com",
            "placeholder": "imap.gmail.com",
            "description": "IMAP server hostname.",
        },
        "imap_port": {
            "type": "number",
            "label": "IMAP Port",
            "default": 993,
            "description": "IMAP server port (993 for SSL).",
        },
        "email": {
            "type": "string",
            "label": "Email Address",
            "required": True,
            "placeholder": "you@gmail.com",
            "description": "The email address to monitor.",
        },
        "app_password": {
            "type": "string",
            "label": "App Password",
            "secret": True,
            "required": True,
            "description": "App-specific password for IMAP access.",
        },
        "folder": {
            "type": "string",
            "label": "Folder",
            "default": "INBOX",
            "placeholder": "INBOX",
            "description": "IMAP folder to monitor.",
        },
        "subject_filter_mode": {
            "type": "select",
            "label": "Subject Filter",
            "default": "none",
            "options": [
                {"value": "none", "label": "No Filter"},
                {"value": "contains", "label": "Contains"},
                {"value": "exact", "label": "Exact Match"},
                {"value": "regex", "label": "Regex"},
            ],
            "description": "How to filter incoming emails by subject.",
        },
        "subject_filter_value": {
            "type": "string",
            "label": "Filter Value",
            "default": "",
            "placeholder": "Enter search text or regex...",
            "description": "The text or pattern to match against the email subject.",
        },
        "subject_case_sensitive": {
            "type": "boolean",
            "label": "Case Sensitive",
            "default": False,
            "description": "Whether the subject filter should be case-sensitive.",
        },
        "include_attachments": {
            "type": "boolean",
            "label": "Include Attachments",
            "default": False,
            "description": (
                "When enabled, extracts file attachments from each email and "
                "includes them as a list of objects with filename, content_type, "
                "size_bytes, and base64-encoded data."
            ),
        },
        "output_mapping": {
            "type": "code",
            "label": "Output mapping",
            "required": False,
            "description": "Rename or extract output keys before passing downstream.",
        },
    },
    example_output={
        "subject": "Hello from John",
        "from": "john.doe@gmail.com",
        "to": "you@example.com",
        "date": "2026-01-01T10:00:00+00:00",
        "body": "Hi there!",
        "uid": "12345",
        "folder": "INBOX",
        "matched_filter": "contains",
        "wait_time_seconds": 30,
    }
)
async def handle_receive_email(config: dict[str, Any], input_data: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    res = await execute_receive_email(config, input_data)
    return apply_output_mapping(res, config.get("output_mapping", ""))
