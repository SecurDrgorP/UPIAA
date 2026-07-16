"""
nodes/llm_openai.py
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🧠  OpenAI Action — ChatGPT / GPT-4o Inference
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

WHAT THIS NODE DOES
───────────────────
Sends a prompt to the OpenAI Chat Completions API and returns the model's
response.  Supports system prompts, temperature control, and output mapping.

QUICK START
───────────
  1. Provide your OpenAI API key.
  2. Choose a model (e.g. gpt-4o, gpt-4o-mini, gpt-3.5-turbo).
  3. Write your prompt using {{expression}} syntax to inject upstream data.
  4. (Optional) Add a system prompt to set the model's behaviour.

OUTPUT FIELDS
─────────────
  response          The model's text response (auto-parsed as JSON if valid)
  model             The model name used
  input_tokens      Number of prompt tokens consumed
  output_tokens     Number of completion tokens generated
  finish_reason     Why generation stopped (e.g. "stop", "length")

ARCHITECTURAL NOTES
───────────────────
  • Response text is auto-parsed as JSON when possible, so downstream
    nodes can access structured fields directly.
  • If the prompt template resolves to empty, an explicit error is
    returned instead of silently dumping raw input_data.
"""
from typing import Any, Optional, Dict
import httpx
from core.node_registry import register_action
from core.expressions import resolve_template, apply_output_mapping
from core.llm_factory import get_llm_provider
import json
import re


def _try_parse_json(text: str) -> Any:
    """
    Parse a string as JSON, handling common LLM formatting issues:
    - Markdown code fences: ```json ... ``` or ``` ... ```
    - Leading/trailing whitespace and newlines
    - Single-line JSON embedded in prose (extracts first {...} or [...] block)
    Returns the parsed object on success, or the original string on failure.
    """
    if not text or not isinstance(text, str):
        return text

    cleaned = text.strip()

    # Strip markdown code fences: ```json ... ``` or ``` ... ```
    fence_match = re.match(
        r"^```(?:json)?\s*\n?(.*?)\n?```\s*$", cleaned, re.DOTALL
    )
    if fence_match:
        cleaned = fence_match.group(1).strip()

    # Try direct parse first
    try:
        return json.loads(cleaned)
    except (json.JSONDecodeError, ValueError):
        pass

    # Try to extract the first JSON object or array from mixed prose
    for pattern in (r"\{.*\}", r"\[.*\]"):
        match = re.search(pattern, cleaned, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(0))
            except (json.JSONDecodeError, ValueError):
                pass

    return text   # Return original string — not JSON

class OpenAIProvider:
    def __init__(self, api_key: str, model: str = "gpt-4o"):
        self.api_key = api_key
        self.model = model

    async def generate(self, prompt: str, system: Optional[str] = None, temperature: float = 0.7, **kwargs) -> Dict[str, Any]:
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            **kwargs
        }

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }

        try:
            async with httpx.AsyncClient(timeout=60) as client:
                resp = await client.post("https://api.openai.com/v1/chat/completions", json=payload, headers=headers)
                if resp.status_code == 200:
                    data = resp.json()
                    usage = data.get("usage", {})
                    response_text = data["choices"][0]["message"]["content"] or ""
                    
                    parsed_response = _try_parse_json(response_text)
                    return {
                        "response": parsed_response,
                        "model": self.model,
                        "input_tokens": usage.get("prompt_tokens", 0),
                        "output_tokens": usage.get("completion_tokens", 0),
                        "finish_reason": data["choices"][0].get("finish_reason", "unknown")
                    }
                return {"response": "", "error": f"OpenAI error {resp.status_code}: {resp.text}"}
        except Exception as e:
            return {"response": "", "error": str(e)}

@register_action(
    type="openai_action",
    label="OpenAI Action",
    icon="🧠",
    description="Execute a task using OpenAI (ChatGPT)",
    category="LLM Actions",
    is_llm_type=True,
    config_schema={
        "api_key": {
            "type": "string",
            "label": "API Key",
            "secret": True,
            "required": True,
            "description": "Your OpenAI API key.",
        },
        "model": {
            "type": "string",
            "label": "Model",
            "default": "gpt-4o",
            "placeholder": "gpt-4o",
            "description": "OpenAI model name (e.g. gpt-4o, gpt-4o-mini, gpt-3.5-turbo).",
        },
        "prompt_template": {
            "type": "string",
            "label": "Prompt",
            "multiline": True,
            "template": True,
            "placeholder": "Summarize the following: {{$input.text}}",
            "description": (
                "The prompt sent to the model. Supports {{expression}} interpolation to inject "
                "values from upstream nodes.\n\n"
                "── How to reference upstream data ──────────────────────\n"
                "  Short form (recommended):\n"
                "    {{node_3.response}}              — top-level field\n"
                "    {{node_3.messages[0].content}}   — array element + subfield\n"
                "    {{node_3.data.title}}            — nested object field\n\n"
                "  Long form (also works):\n"
                "    {{$node.node_3.response}}\n"
                "    {{$input.response}}              — flat-merged from all upstream nodes\n\n"
                "── Where to find the node ID ───────────────────────────\n"
                "  Click any upstream node — its ID (e.g. node_3) is shown in the sidebar header "
                "in monospace below the title. Or switch to the Input tab to see all available "
                "expressions with one-click insert.\n\n"
                "── Common example: Discord bot ─────────────────────────\n"
                "  {{node_3.messages[0].content}}   — first message's text\n"
                "  {{node_3.messages[0].author_username}}  — who sent it\n"
                "  NOT: {{node_3.messages.content}} — messages is a list, [0] is required\n\n"
                "── Tip ─────────────────────────────────────────────────\n"
                "  Leave blank to send the entire upstream output as the prompt.\n\n"
                "── Accessing JSON responses downstream ──────────────────\n"
                "  If your prompt instructs the model to respond in JSON,\n"
                "  UPIAA auto-parses the response. Access fields with:\n"
                "    {{node_2.response.key}}          — top-level JSON key\n"
                "    {{node_2.response.items[0].val}} — array element\n"
                "  If the model wraps JSON in code fences (```json...```)\n"
                "  UPIAA strips them automatically.\n"
                "  To embed the full JSON in a message: {{node_2.response}}\n"
                "  This produces compact JSON, e.g.: {\"key\":\"value\"}\n"
            ),
        },
        "system_prompt": {
            "type": "string",
            "label": "System Prompt",
            "multiline": True,
            "description": "Optional system instructions for the model.",
        },
        "temperature": {
            "type": "number",
            "label": "Temperature",
            "default": 0.7,
            "min": 0,
            "max": 2,
            "description": "Creativity level. 0 = deterministic, 2 = very creative.",
        },
        "inject_context": {
            "type": "boolean",
            "label": "Inject Execution Context",
            "default": False,
            "description": (
                "When ON, wraps your system prompt with workflow metadata (node ID, upstream data, "
                "loop context). Useful for advanced agent orchestration. "
                "Leave OFF for standard chat use."
            ),
        },
        "output_mapping": {
            "type": "code",
            "label": "Output mapping",
            "required": False,
            "description": '{"summary": "response"}',
        },
    },
)
async def handle_openai(config: dict[str, Any], input_data: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    api_key = config.get("api_key", "")
    if not api_key:
        return {"error": "missing field: api_key", "__input_error__": True}

    model = config.get("model", "gpt-4o")

    prompt = config.get("prompt_template", "")
    if not prompt.strip():
        prompt = json.dumps(input_data)

    system = config.get("system_prompt") or None

    provider = get_llm_provider("openai", {"api_key": api_key, "model": model})
    result = await provider.generate(
        prompt=prompt,
        system=system.strip() if system else None,
        temperature=config.get("temperature", 0.7)
    )

    if result.get("error"):
        return {"error": f"LLM Provider failed: {result['error']}", "__input_error__": True}

    return apply_output_mapping(result, config.get("output_mapping", ""))
