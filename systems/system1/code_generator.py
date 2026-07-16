"""
Systems — System 1 — Code Generator

Generates a complete UPIAA node Python file from a SpecContract.
Output follows the canonical UPIAA node contract:
  - from core.node_registry import register_action
  - @register_action(type=..., label=..., icon=..., ...)
  - async def handle_{node_type}(config, input_data, context) -> dict
"""

import logging
from typing import Any, Dict

from shared.llm_client import call_llm
from shared.contracts import SpecContract, CodeContract

logger = logging.getLogger(__name__)


# ── UPIAA Node Code Generation Prompt ─────────────────────────────────────────
SYSTEM_PROMPT = """You are an expert Python developer writing UPIAA workflow nodes.

Given a node specification, generate a COMPLETE Python file that follows the UPIAA node contract.

The file MUST follow this EXACT structure:

```python
from typing import Any
from core.node_registry import register_action
from core.expressions import resolve_template, apply_output_mapping
# ... any other imports needed

@register_action(
    type="node_type_here",
    label="Human Label",
    icon="emoji",
    description="What this node does",
    category="Actions",
    config_schema={{
        "field": {{"type": "string", "default": "", "description": "..."}},
    }}
)
async def handle_node_type_here(config: dict[str, Any], input_data: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    # Validate required config
    some_field = config.get("field", "")
    if not some_field:
        return {{"error": "missing field: field", "__input_error__": True}}

    # Main logic here
    result = ...

    return apply_output_mapping(result, config.get("output_mapping", ""))
```

CRITICAL RULES:
1. The function MUST be named handle_{{node_type}} where node_type matches the type in @register_action
2. The function MUST be async
3. The function MUST accept exactly (config, input_data, context) as parameters
4. The function MUST return a dict
5. On missing required config, return {{"error": "missing field: X", "__input_error__": True}}
6. Use resolve_template() for any config value that might contain template expressions
7. Use apply_output_mapping() on the final result
8. Do NOT use exec(), eval(), or import from core.orchestrator
9. Do NOT use time.sleep() — use asyncio.sleep() if needed
10. Include proper error handling with try/except

Return ONLY valid JSON: {{"filename": "node_type.py", "code": "the complete Python file content"}}"""


class CodeGenerator:
    """Generates UPIAA node Python files from specifications."""

    @classmethod
    async def generate(cls, spec: SpecContract | Dict[str, Any]) -> CodeContract:
        """Generate an UPIAA node Python file from a specification."""
        import json

        if isinstance(spec, SpecContract):
            spec_dict = spec.model_dump()
        else:
            spec_dict = spec

        prompt = (
            f"Generate a complete UPIAA node Python file for this specification:\n"
            f"{json.dumps(spec_dict, indent=2)}\n\n"
            f"The handler function must be named: handle_{spec_dict['node_type']}"
        )

        try:
            result = await call_llm(
                prompt=prompt,
                system_prompt=SYSTEM_PROMPT,
                response_model=CodeContract,
            )
            logger.info(f"Generated code: {result.filename}")
            return result

        except Exception as e:
            logger.error(f"CodeGenerator failed: {e}")
            raise
