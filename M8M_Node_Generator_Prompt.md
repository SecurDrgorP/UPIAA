# M8M Node Generator Prompt

> Copy this entire prompt, fill in the `[PLACEHOLDERS]`, and send to any LLM.
> The output will be a ready-to-drop-in Python file for `backend/nodes/`.

---

## PROMPT START — copy everything below this line

You are a senior Python engineer on the **M8M Agentic AI Workflow Engine**.
Your task is to generate a single, production-ready Python file for a new **M8M node**.

---

### 1 — WHAT YOU ARE BUILDING

Node name  : [e.g. "Slack Notifier"]
Node type  : [snake_case, e.g. "slack_notify"]
Icon       : [single emoji, e.g. "💬"]
Category   : [one of: Triggers | Actions | LLM Actions | Communication | Logic | Model Actions | Control Flow]
Description: [one sentence, e.g. "Send a message to a Slack channel via the Incoming Webhooks API."]
Inputs     : [describe what input_data fields you expect from upstream nodes]
Outputs    : [describe what dict keys the node should return on success]

---

### 2 — M8M ARCHITECTURE RULES  (read carefully — do not deviate)

#### 2.1 File location & auto-discovery
- Drop the file in `backend/nodes/<node_type>.py`.
- The node **self-registers** at import time via `@register_action`.
- No other file needs to be changed.

#### 2.2 Handler function signature
```python
async def handle_<node_type>(
    config: dict[str, Any],
    input_data: dict[str, Any],
    context: dict[str, Any],
) -> dict[str, Any]:
```
- `config` — user-provided sidebar values
- `input_data` — merged output of all upstream nodes feeding into this node
- `context` — full execution context keyed by node_id (e.g. `context["n1"]["response"]`)
- **Always async** — use `asyncio`, `httpx`, or `asyncio.to_thread()` for blocking I/O

#### 2.3 Error return conventions
Return a plain dict — never raise an exception out of the handler.

| Situation | Return dict |
|-----------|-------------|
| Bad user config | `{"error": "message", "__input_error__": True}` |
| External service failure | `{"error": "message", "__service_error__": True}` |
| Success | Any dict with meaningful keys |

#### 2.4 Template & dotpath resolution
```python
from core.expressions import resolve_template, resolve_dotpath, apply_output_mapping

# Template: replaces {{node_id.field}} in strings
url = resolve_template(config.get("url", ""), input_data)

# Dotpath: resolves "n1.items" against the context dict
iterable = resolve_dotpath(config.get("iterable_key", ""), context)
```

#### 2.5 Output mapping (add to every node)
```python
return apply_output_mapping(result, config.get("output_mapping", ""))
```

#### 2.6 Temporal heartbeat (add for long-running / blocking nodes only)
```python
try:
    from temporalio import activity as _temporal_activity
    _HAS_TEMPORAL = True
except ImportError:
    _HAS_TEMPORAL = False

def _heartbeat(msg: str) -> None:
    if not _HAS_TEMPORAL or not _temporal_activity.in_activity():
        return
    try:
        _temporal_activity.heartbeat(msg)
    except Exception:
        pass
```
Call `_heartbeat("...")` inside any polling loop.

---

### 3 — CONFIG SCHEMA RULES  (this is what drives the sidebar UI)

The `config_schema` is a **dict of field descriptors**. Every field key maps to a descriptor dict.

#### 3.1 Field types reference

| `type` value | Renders as | Notes |
|---|---|---|
| `"string"` | Text input | Add `"multiline": True` for textarea |
| `"number"` | Number input | Add `"min"` / `"max"` |
| `"boolean"` | Toggle switch | |
| `"select"` | Dropdown | Requires `"options"` (see §3.2) |
| `"multiselect"` | Checkbox group | Requires `"options"` |
| `"object"` or `"json"` | JSON editor | For dicts |
| `"code"` | Monospace textarea | For output_mapping, expressions |
| `"array"` | JSON array editor | For lists |

#### 3.2 Options format (for select / multiselect)
```python
# Simple string list:
"options": ["GET", "POST", "PUT", "DELETE"]

# Labeled object list:
"options": [
    {"value": "plain", "label": "Plain Text"},
    {"value": "html",  "label": "HTML"},
]
```

#### 3.3 Special field flags

| Flag | Type | Effect |
|---|---|---|
| `"required": True` | bool | Shows * in label |
| `"secret": True` | bool | Password input + eye toggle + **auto-stripped from exported JSON** |
| `"template": True` | bool | Shows ❴❵ badge — field supports `{{node_id.field}}` interpolation |
| `"dotpath": True` | bool | Shows ⚬ badge — field supports `n1.items` dot-path syntax |
| `"multiline": True` | bool | Expands string to textarea |
| `"default": <value>` | any | Pre-fills the field |
| `"placeholder": "..."` | str | Input placeholder text |
| `"description": "..."` | str | Help text shown below the field |
| `"label": "..."` | str | Human-readable field label |
| `"min": N` / `"max": N` | int | Numeric bounds |

#### 3.4 Conditional visibility — `visibleWhen`
```python
# Show this field ONLY when another field equals a specific value:
"visibleWhen": {"schedule_type": "cron"}

# Show only when another field is one of several values:
"visibleWhen": {"method": ["POST", "PUT", "PATCH"]}
```

#### 3.5 Standard output_mapping field — ALWAYS include this last
```python
"output_mapping": {
    "type": "code",
    "label": "Output mapping",
    "required": False,
    "description": 'Rename or extract output keys. Example: {"summary": "response"}',
},
```

---

### 4 — COMPLETE WORKED EXAMPLE

```python
"""
nodes/slack_notify.py
─────────────────────────────────────────────────────────────────────────────
Sends a message to a Slack channel via Incoming Webhooks.
"""

import httpx
from typing import Any

from core.node_registry import register_action
from core.expressions import resolve_template, apply_output_mapping


@register_action(
    type="slack_notify",
    label="Slack Notifier",
    icon="💬",
    description="Send a message to a Slack channel via Incoming Webhooks.",
    category="Communication",
    config_schema={
        "webhook_url": {
            "type": "string",
            "label": "Webhook URL",
            "required": True,
            "secret": True,
            "placeholder": "https://hooks.slack.com/services/...",
            "description": "Your Slack Incoming Webhook URL.",
        },
        "channel": {
            "type": "string",
            "label": "Channel Override",
            "placeholder": "#general",
            "description": "Override the default channel. Leave blank to use the webhook default.",
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
        "username": {
            "type": "string",
            "label": "Bot Username",
            "default": "M8M Workflow",
            "description": "Display name for the bot.",
        },
        "icon_emoji": {
            "type": "string",
            "label": "Icon Emoji",
            "default": ":robot_face:",
            "description": "Emoji icon for the bot (e.g. :robot_face:).",
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
        "channel": "#general",
        "message": "Hello from M8M!",
        "status_code": 200,
    },
)
async def handle_slack_notify(
    config: dict[str, Any],
    input_data: dict[str, Any],
    context: dict[str, Any],
) -> dict[str, Any]:

    # ── Resolve config values ──────────────────────────────────────────────────
    webhook_url = config.get("webhook_url", "").strip()
    if not webhook_url:
        return {"error": "missing field: webhook_url", "__input_error__": True}

    message_raw = config.get("message", "")
    message     = resolve_template(message_raw, input_data)
    if not message.strip():
        return {"error": "missing field: message — cannot send an empty message", "__input_error__": True}

    channel      = config.get("channel", "").strip() or None
    username     = config.get("username", "M8M Workflow")
    icon_emoji   = config.get("icon_emoji", ":robot_face:")

    # ── Build payload ─────────────────────────────────────────────────────────
    payload: dict[str, Any] = {
        "text":       message,
        "username":   username,
        "icon_emoji": icon_emoji,
    }
    if channel:
        payload["channel"] = channel

    # ── Send request ──────────────────────────────────────────────────────────
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(webhook_url, json=payload)

        if resp.status_code != 200:
            return {
                "error": f"Slack webhook returned {resp.status_code}: {resp.text}",
                "__service_error__": True,
            }

    except httpx.TimeoutException:
        return {"error": "Slack webhook timed out after 15 seconds.", "__service_error__": True}
    except Exception as e:
        return {"error": f"Slack webhook error: {e}", "__service_error__": True}

    # ── Return result ─────────────────────────────────────────────────────────
    result = {
        "sent":        True,
        "channel":     channel or "(default)",
        "message":     message,
        "status_code": resp.status_code,
    }
    return apply_output_mapping(result, config.get("output_mapping", ""))
```

---

### 5 — CATEGORIES & CONVENTIONS

**Available categories:**
- `"Triggers"` — Nodes that start a workflow (timer, webhook, email listener)
- `"Actions"` — General-purpose actions (HTTP request, file write)
- `"LLM Actions"` — Nodes that call an AI model (`is_llm_type=True`)
- `"Communication"` — Email, Slack, Telegram, etc.
- `"Logic"` — If/Else, For Loop, While Loop (`is_loop_type=True` for loops)
- `"Control Flow"` — Timer/scheduler nodes
- `"Model Actions"` — ML inference nodes

**Naming conventions:**
- File: `backend/nodes/<node_type>.py`
- Handler: `async def handle_<node_type>(...)`
- Type string: `snake_case`, globally unique
- All imports at the top of the file

**Do NOT:**
- Import from `routes/`, `main.py`, or `ai/`
- Call `exit()`, `os.system()`, `subprocess`, or `eval()`
- Raise unhandled exceptions from the handler
- Block the event loop — use `asyncio.to_thread()` for sync I/O

---

### 6 — NOW GENERATE THE NODE

Using everything above, generate a complete, production-ready Python file for:

**Node name:** [FILL IN]
**Node type:** [FILL IN — snake_case]
**Icon:** [FILL IN — emoji]
**Category:** [FILL IN]
**Description:** [FILL IN]
**What it does:**
[Describe the logic in plain English. Be specific about:
- What config fields the user fills in
- What input_data fields it reads from upstream nodes
- What external API or service it calls (include the endpoint/SDK)
- What the output dict should contain
- Any conditional fields (visibleWhen)
- Any long-running / blocking behavior (Temporal heartbeats needed?)
- Any secret fields]

Generate ONLY the Python file. No explanations outside of inline comments.

## PROMPT END
