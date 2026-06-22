"""
Systems — System 4 — Node Tester

Multi-stage validation pipeline for AI-generated nodes:
1. AST validation via System 1 Validator
2. Secure subprocess test via backend's /internal/test-node
3. Schema validation (config_schema completeness)
"""

import ast as ast_mod
import logging
import os
from typing import Any, Dict

from shared.http import internal_post

logger = logging.getLogger(__name__)

BACKEND_URL = os.getenv("BACKEND_URL", "http://backend:8000")


class NodeTester:
    """Multi-stage testing pipeline for AI-generated nodes."""

    @classmethod
    async def test(cls, code: str, node_type: str) -> Dict[str, Any]:
        """
        Run the full testing pipeline on generated node code.

        Stages:
        1. AST validation (syntax, forbidden imports, schema)
        2. Secure subprocess test via backend
        3. Schema validation (config_schema completeness)

        Returns:
            {"passed": bool, "stages": {...}, "errors": [...]}
        """
        results: Dict[str, Any] = {}
        errors: list = []

        # ── Stage 1: AST validate ────────────────────────────────────────
        from system1.validator import Validator
        ast_result = await Validator.validate(
            code_or_files={f"{node_type}.py": code},
            node_type=node_type,
        )
        results["ast"] = ast_result
        if not ast_result.get("valid"):
            return {
                "passed": False,
                "stages": results,
                "errors": ast_result.get("errors", ["AST validation failed"]),
            }

        # ── Stage 2: Secure subprocess test via backend ──────────────────
        try:
            test_result = await internal_post(
                f"{BACKEND_URL}/internal/test-node",
                json={"code": code, "node_type": node_type},
            )
            results["registry_test"] = test_result
            if not test_result.get("passed"):
                errors.extend(test_result.get("errors", []))
        except Exception as e:
            results["registry_test"] = {"passed": False, "error": str(e)}
            errors.append(f"Registry test unreachable: {e}")

        # ── Stage 3: Schema validation ───────────────────────────────────
        try:
            tree = ast_mod.parse(code)
            schema_result = cls._validate_config_schema(tree, node_type)
            results["schema"] = schema_result
            if not schema_result.get("valid"):
                errors.extend(schema_result.get("errors", []))
        except SyntaxError:
            # Already caught in Stage 1, shouldn't happen here
            pass

        return {
            "passed": len(errors) == 0,
            "stages": results,
            "errors": errors,
        }

    @staticmethod
    def _validate_config_schema(tree: ast_mod.Module, node_type: str) -> Dict[str, Any]:
        """
        Check that @register_action has a populated config_schema.
        Validates that config_schema is not empty for non-trivial nodes.
        """
        errors: list = []
        config_schema_found = False

        for node in ast_mod.walk(tree):
            if not isinstance(node, (ast_mod.FunctionDef, ast_mod.AsyncFunctionDef)):
                continue

            for decorator in node.decorator_list:
                if not isinstance(decorator, ast_mod.Call):
                    continue

                # Check if this is @register_action(...)
                func = decorator.func
                is_register_action = False
                if isinstance(func, ast_mod.Name) and func.id == "register_action":
                    is_register_action = True
                elif isinstance(func, ast_mod.Attribute) and func.attr == "register_action":
                    is_register_action = True

                if not is_register_action:
                    continue

                # Look for config_schema= keyword
                for keyword in decorator.keywords:
                    if keyword.arg == "config_schema":
                        config_schema_found = True
                        # Check it's a dict with at least one key
                        if isinstance(keyword.value, ast_mod.Dict):
                            if len(keyword.value.keys) == 0:
                                errors.append(
                                    "config_schema is empty — nodes should define "
                                    "at least one configuration field"
                                )

        if not config_schema_found:
            errors.append(
                "No config_schema found in @register_action — "
                "consider adding configuration fields"
            )

        return {"valid": len(errors) == 0, "errors": errors}
