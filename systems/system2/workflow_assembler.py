"""
Systems — System 2 — Workflow Assembler

Calls the LLM to assemble a complete workflow JSON from an intent plan.
Uses call_llm() with WorkflowPlan for validated, structured output.
Fetches node catalog via HTTP instead of direct registry import.
"""

import json
import logging
import os
import uuid
from typing import Any, Dict

from shared.llm_client import call_llm
from shared.contracts import WorkflowPlan
from shared.http import internal_get

logger = logging.getLogger(__name__)

BACKEND_URL = os.getenv("BACKEND_URL", "http://backend:8000")


class WorkflowAssembler:
    @staticmethod
    async def assemble(plan: dict) -> dict:
        """
        Calls the LLM to assemble the workflow JSON based on the prompt.
        Returns a validated workflow dict ready for the frontend.
        """
        # Build dynamic type catalog from backend via HTTP
        available_types = await _get_catalog()
        type_docs = []
        for t in available_types:
            entry = f"- {t.get('type', 'unknown')}: {t.get('description', '')}"
            schema = t.get("config_schema", {})
            if schema:
                fields = ", ".join(schema.keys())
                entry += f" (config: {fields})"
            type_docs.append(entry)
        types_section = "\n".join(type_docs) if type_docs else "- http_request, timer, send_email, ollama_action"

        system_prompt = f"""You are an expert UPIAA Workflow Architect. Convert the user's request into a valid JSON workflow.

Return JSON with this structure:
{{
  "name": "Workflow Name",
  "nodes": [
    {{
      "id": "node_1",
      "type": "http_request",
      "label": "Fetch Data",
      "config": {{"method": "GET", "url": "https://api.example.com"}},
      "position": {{"x": 100, "y": 100}}
    }}
  ],
  "edges": [
    {{"id": "edge_1", "source": "node_1", "target": "node_2", "sourceHandle": null}}
  ]
}}

Available node types:
{types_section}

Rules:
1. Return ONLY valid JSON, no markdown fences
2. Every edge source/target must reference an existing node ID
3. Space nodes linearly: x=100, 350, 600, 850... y=100
4. Use config fields that match the node type's config_schema
5. Give nodes descriptive labels"""

        user_prompt = f"Create this workflow: {plan['prompt']}"

        try:
            workflow = await call_llm(
                prompt=user_prompt,
                system_prompt=system_prompt,
                response_model=WorkflowPlan,
            )

            # Convert to dict and ensure ID
            result = workflow.model_dump()
            if not result.get("id"):
                result["id"] = f"wf_{uuid.uuid4().hex[:8]}"

            logger.info(
                f"Assembled workflow '{result.get('name')}' with "
                f"{len(result.get('nodes', []))} nodes"
            )
            return result

        except Exception as e:
            logger.error(f"Failed to generate workflow via LLM: {e}")
            return {
                "_ai_error": f"LLM unreachable — fallback scaffold returned: {e}",
                "id": f"wf_{uuid.uuid4().hex[:8]}",
                "name": "Fallback Scaffold Workflow",
                "nodes": [
                    {
                        "id": "node_1",
                        "type": "http_request",
                        "label": "Fetch Data",
                        "config": {
                            "method": "GET",
                            "url": "https://example.com/api",
                        },
                        "position": {"x": 100, "y": 100},
                    }
                ],
                "edges": [],
            }


async def _get_catalog() -> list:
    """Fetch node catalog from backend via HTTP."""
    try:
        return await internal_get(f"{BACKEND_URL}/api/nodes/catalog")
    except Exception as e:
        logger.warning(f"Could not fetch node catalog: {e}")
        return []
