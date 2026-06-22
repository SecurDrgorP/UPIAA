"""
Systems — System 1 — Frontend Integrator

Generates frontend integration hints (icon, color, sidebar template)
for a newly generated node. Uses call_llm() with FrontendContract.
"""

import json
import logging
from typing import Any, Dict

from shared.llm_client import call_llm
from shared.contracts import FrontendContract, SpecContract

logger = logging.getLogger(__name__)


SYSTEM_PROMPT = """You are a frontend integration expert for M8M, a workflow automation platform.

Given an M8M node specification, generate frontend hints as JSON:
{
    "icon": "single emoji representing this node",
    "accent_color": "#hexcolor for the node's accent",
    "sidebar_template": "brief HTML/Angular hint for the config form (optional)"
}

Rules:
- icon must be a single emoji
- accent_color must be a valid hex color
- sidebar_template can be empty string if the dynamic form renderer handles it
- Return ONLY valid JSON"""


class FrontendIntegrator:
    """Generates frontend integration hints for generated M8M nodes."""

    @classmethod
    async def generate(cls, spec: SpecContract | Dict[str, Any]) -> FrontendContract:
        """Generate frontend integration data from a node specification."""
        if isinstance(spec, SpecContract):
            spec_data = spec.model_dump()
        else:
            spec_data = spec

        prompt = (
            f"Generate frontend integration hints for this M8M node:\n"
            f"{json.dumps(spec_data, indent=2)}"
        )

        try:
            result = await call_llm(
                prompt=prompt,
                system_prompt=SYSTEM_PROMPT,
                response_model=FrontendContract,
            )
            logger.info(f"Frontend integration: icon={result.icon}, color={result.accent_color}")
            return result

        except Exception as e:
            logger.error(f"FrontendIntegrator failed: {e}")
            # Return sensible defaults
            return FrontendContract(
                icon=spec_data.get("icon", "⚡"),
                accent_color="#6366f1",
                sidebar_template="",
            )
