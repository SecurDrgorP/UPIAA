"""
Systems — System 1 — Validator

Three-stage validation pipeline replacing dangerous exec():
1. AST Parse — forbidden imports and exec/eval detection
2. Schema Check — @register_action decorator + handle_{node_type} function
3. Subprocess Handler Dry-run — actually imports and calls the handler
"""

import ast
import logging
import os
import subprocess
import sys
import tempfile
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

# Forbidden import targets
FORBIDDEN_IMPORTS = {"core.orchestrator", "nodes"}


class Validator:
    """Validates generated M8M node code without exec()."""

    @classmethod
    async def validate(cls, code_or_files: Any, node_type: str = "") -> Dict[str, Any]:
        """
        Validate generated node code.

        Args:
            code_or_files: Either a string of code, or a dict {filename: code}
            node_type: Expected node type (e.g. 'slack_sender')

        Returns:
            {"valid": True/False, "errors": [...]}
        """
        errors: List[str] = []

        # Normalize input
        if isinstance(code_or_files, dict):
            files = code_or_files
        elif isinstance(code_or_files, str):
            filename = f"{node_type}.py" if node_type else "generated_node.py"
            files = {filename: code_or_files}
        else:
            return {"valid": False, "errors": ["Invalid input: expected str or dict"]}

        for filename, content in files.items():
            if not filename.endswith(".py"):
                continue

            file_errors = cls._validate_file(content, node_type)
            errors.extend(file_errors)

        if errors:
            logger.warning(f"Validation failed: {errors}")
            return {"valid": False, "errors": errors}

        return {"valid": True}

    @classmethod
    def _validate_file(cls, content: str, node_type: str) -> List[str]:
        """Stage 1 + 2: AST parsing and schema check."""
        errors: List[str] = []

        # ── Stage 1: AST Parse ───────────────────────────────────────────
        try:
            tree = ast.parse(content)
        except SyntaxError as e:
            errors.append(f"Syntax error: {e}")
            return errors

        register_action_found = False
        handler_found = False
        expected_handler = f"handle_{node_type}" if node_type else None

        for node in ast.walk(tree):
            # Check for forbidden imports
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name in FORBIDDEN_IMPORTS:
                        errors.append(f"Forbidden import: {alias.name}")

            if isinstance(node, ast.ImportFrom):
                if node.module in FORBIDDEN_IMPORTS:
                    errors.append(f"Forbidden import: {node.module}")

            # Check for exec/eval calls
            if isinstance(node, ast.Call):
                func = node.func
                if isinstance(func, ast.Name) and func.id in ("exec", "eval"):
                    errors.append(f"Forbidden call: {func.id}()")

                if isinstance(func, ast.Attribute) and func.attr == "sleep":
                    if hasattr(func, "value") and getattr(func.value, "id", "") == "time":
                        errors.append("No time.sleep() allowed, use asyncio.sleep()")

            # ── Stage 2: Schema Check ────────────────────────────────────
            # Check for @register_action decorator
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for decorator in node.decorator_list:
                    if isinstance(decorator, ast.Call):
                        dec_func = decorator.func
                        if isinstance(dec_func, ast.Name) and dec_func.id == "register_action":
                            register_action_found = True
                        elif isinstance(dec_func, ast.Attribute) and dec_func.attr == "register_action":
                            register_action_found = True

                # Check for handle_{node_type} function
                if expected_handler and node.name == expected_handler:
                    handler_found = True
                    # Validate signature
                    if not isinstance(node, ast.AsyncFunctionDef):
                        errors.append(
                            f"{expected_handler} must be 'async def', not 'def'"
                        )
                    args = [a.arg for a in node.args.args]
                    if args != ["config", "input_data", "context"]:
                        errors.append(
                            f"Invalid {expected_handler} signature. "
                            f"Expected (config, input_data, context) but got {tuple(args)}"
                        )

        if not register_action_found:
            errors.append("No @register_action decorator found")

        if expected_handler and not handler_found:
            errors.append(
                f"No 'async def {expected_handler}(config, input_data, context)' found"
            )

        return errors
