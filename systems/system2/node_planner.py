"""
Systems — System 2 — Node Planner

Analyzes user intent against available node types (fetched via HTTP from backend),
identifies gaps, and plans the workflow structure.
Replaces direct registry import with HTTP call to backend.
"""

import json
import logging
import os

from shared.llm_client import call_llm_raw
from shared.http import internal_get

logger = logging.getLogger(__name__)

BACKEND_URL = os.getenv("BACKEND_URL", "http://backend:8000")


class NodePlanner:
    @staticmethod
    async def plan(intent: dict) -> dict:
        """
        Takes the parsed intent and identifies missing node types.
        Fetches the node catalog from backend via HTTP instead of importing registry.
        """
        # Fetch catalog via HTTP (replaces direct registry import)
        available_types = await _get_catalog()

        types_json = json.dumps(
            [{"type": t.get("type", ""), "desc": t.get("description", "")} for t in available_types]
        )

        system_prompt = f"""You are an expert workflow architect. Analyze the user's workflow prompt: "{intent['prompt']}"
Available node types: {types_json}
Identify any workflow steps requested by the user that CANNOT be fulfilled by the available node types.
Return ONLY a JSON list of descriptions for the MISSING nodes. E.g. ["A node that fetches weather data"]
If NO new nodes are needed, return [].
"""

        missing_descriptions = []
        try:
            raw = await call_llm_raw(
                prompt=f"Analyze this workflow request: {intent['prompt']}",
                system_prompt=system_prompt,
            )
            try:
                missing_descriptions = json.loads(raw)
                if not isinstance(missing_descriptions, list):
                    missing_descriptions = []
            except json.JSONDecodeError:
                logger.warning("NodePlanner: could not parse LLM response as JSON list")
                missing_descriptions = []
        except Exception as e:
            logger.error(f"NodePlanner failed to identify missing types: {e}")

        if missing_descriptions:
            logger.info(f"NodePlanner identified {len(missing_descriptions)} missing node types")
            # Note: In the systems container, we log missing types but don't trigger
            # System1 via Temporal directly. The orchestration is handled at a higher level.

        return {
            "prompt": intent["prompt"],
            "strategy": "generate_json_workflow",
            "requirements": intent.get("context", {}),
            "missing_types": missing_descriptions,
        }


async def _get_catalog() -> list:
    """Fetch node catalog from backend via HTTP."""
    try:
        return await internal_get(f"{BACKEND_URL}/api/nodes/catalog")
    except Exception as e:
        logger.warning(f"Could not fetch node catalog from backend: {e}")
        return []
