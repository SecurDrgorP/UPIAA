"""
Systems — System 1 — Spec Writer

Generates a structured node specification from a natural language prompt.
Uses call_llm() with SpecContract for validated output.
"""

import logging
from typing import Any, Dict

from shared.llm_client import call_llm
from shared.contracts import SpecContract

logger = logging.getLogger(__name__)


# ── M8M Node Specification Prompt ────────────────────────────────────────────
SYSTEM_PROMPT = """You are an expert M8M workflow node architect. Given a user's request,
produce a JSON specification for a single M8M workflow node.

An M8M node is a self-contained action in an automation workflow (similar to n8n or Zapier).
Each node has a type, config schema, and a handler function.

You MUST return valid JSON with exactly these fields:
{
    "node_type": "snake_case_name (e.g. slack_sender, csv_parser)",
    "label": "Human-Readable Label",
    "icon": "single emoji",
    "category": "one of: Actions, Triggers, AI, Data, Communication",
    "description": "One sentence describing what this node does",
    "config_schema": {
        "field_name": {
            "type": "string|number|boolean|object",
            "default": "default value",
            "description": "what this field controls"
        }
    },
    "handler_logic": "Plain English description of the step-by-step logic the handler should execute"
}

Rules:
- node_type must be snake_case, no spaces
- config_schema must include all fields the user would need to configure
- handler_logic must be specific enough that a developer can implement it
- Do NOT include import statements or code in handler_logic, just describe the logic
- Return ONLY valid JSON, no markdown fences"""


class SpecWriter:
    """Generates structured M8M node specifications from prompts."""

    @classmethod
    async def write(
        cls, prompt: str, context: Dict[str, Any] = None
    ) -> SpecContract:
        """Generate an M8M node specification from a natural language prompt."""
        user_prompt = f"Create an M8M workflow node for: {prompt}"

        if context:
            import json
            user_prompt += f"\n\nAdditional context: {json.dumps(context)}"

        try:
            spec = await call_llm(
                prompt=user_prompt,
                system_prompt=SYSTEM_PROMPT,
                response_model=SpecContract,
            )
            logger.info(f"Generated spec for node type: {spec.node_type}")
            return spec

        except Exception as e:
            logger.error(f"SpecWriter failed: {e}")
            raise
