"""
Systems — System 2 — Reviewer

Validates generated workflows with structural + semantic checks.
Fetches node catalog from backend via HTTP.
"""

import logging
import os
from typing import Any, Dict, List

from shared.llm_client import call_llm_raw
from shared.http import internal_get

logger = logging.getLogger(__name__)

BACKEND_URL = os.getenv("BACKEND_URL", "http://backend:8000")


class Reviewer:
    @staticmethod
    async def review(workflow: dict, original_prompt: str) -> dict:
        """
        Full validation of a generated workflow against structural rules
        and semantic intent.
        """
        errors: List[str] = []
        suggestions: List[str] = []

        nodes = workflow.get("nodes", [])
        edges = workflow.get("edges", [])

        # ── 1. Basic structure check ─────────────────────────────────────
        if not nodes:
            errors.append("Workflow has no nodes.")
            return {
                "is_valid": False,
                "errors": errors,
                "feedback": "Workflow is empty.",
                "suggestions": [],
            }

        node_ids = {n.get("id") for n in nodes if n.get("id")}

        # ── 2. Edge structural check ─────────────────────────────────────
        for edge in edges:
            source = edge.get("source", "")
            target = edge.get("target", "")
            edge_id = edge.get("id", "unknown")

            if source not in node_ids:
                errors.append(
                    f"Edge '{edge_id}' references non-existent source node '{source}'"
                )
            if target not in node_ids:
                errors.append(
                    f"Edge '{edge_id}' references non-existent target node '{target}'"
                )

        # ── 3. Node type check (via HTTP catalog) ────────────────────────
        registered_types = set()
        type_schemas = {}
        try:
            catalog = await internal_get(f"{BACKEND_URL}/api/nodes/catalog")
            for item in catalog:
                registered_types.add(item.get("type", ""))
                type_schemas[item.get("type", "")] = item.get("config_schema", {})
        except Exception as e:
            logger.warning(f"Could not fetch catalog for review: {e}")
            suggestions.append("Could not verify node types against registry")

        if registered_types:
            for node in nodes:
                node_type = node.get("type") or (node.get("data", {}).get("type"))
                node_id = node.get("id", "unknown")

                if not node_type:
                    errors.append(f"Node '{node_id}' has no type defined")
                    continue

                if node_type not in registered_types:
                    errors.append(
                        f"Node '{node_id}' uses unregistered type '{node_type}'. "
                        f"Available: {sorted(registered_types)}"
                    )

        # ── 4. Config check ──────────────────────────────────────────────
        for node in nodes:
            node_type = node.get("type") or (node.get("data", {}).get("type"))
            node_id = node.get("id", "unknown")
            config = node.get("config") or (node.get("data", {}).get("config", {}))

            if not node_type or node_type not in registered_types:
                continue

            schema = type_schemas.get(node_type, {})
            if schema:
                for field_name, field_def in schema.items():
                    if isinstance(field_def, dict):
                        required = field_def.get("required", False)
                        has_default = "default" in field_def
                        if required and not has_default and field_name not in config:
                            errors.append(
                                f"Node '{node_id}' ({node_type}): missing required "
                                f"config field '{field_name}'"
                            )

        # ── 5. Semantic review via LLM ───────────────────────────────────
        semantic_feedback = ""
        if not errors:
            try:
                import json
                review_prompt = (
                    f"Review this workflow and determine if it correctly fulfills "
                    f"the user's request.\n\n"
                    f"User's request: \"{original_prompt}\"\n\n"
                    f"Workflow:\n{json.dumps(workflow, indent=2)}\n\n"
                    f"Respond with:\n"
                    f"1. Whether the workflow satisfies the request (YES/NO)\n"
                    f"2. Any issues or improvements\n"
                    f"3. Missing steps or incorrect configurations"
                )
                semantic_feedback = await call_llm_raw(
                    prompt=review_prompt,
                    system_prompt=(
                        "You are a workflow review expert. Analyze whether the "
                        "workflow correctly implements the user's request. Be concise."
                    ),
                )
                logger.info("Semantic review completed")
            except Exception as e:
                logger.warning(f"Semantic review LLM call failed: {e}")
                semantic_feedback = f"Semantic review unavailable: {e}"
                suggestions.append("Consider manually verifying the workflow logic")

        is_valid = len(errors) == 0

        return {
            "is_valid": is_valid,
            "errors": errors,
            "feedback": semantic_feedback if is_valid else "; ".join(errors),
            "suggestions": suggestions,
        }
