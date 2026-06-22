"""
Systems — System 1 — Deployer (BUG-3 fix: env var paths + fsync + rollback)

Deploys generated node files to NODES_DIR (env var, defaults to /shared/ai_nodes),
triggers registry reload via authenticated HTTP, includes fsync for write safety
and rollback helper for removing failed nodes.
"""

import ast
import logging
import os
from typing import Any, Dict, List

from shared.http import internal_post, internal_delete

logger = logging.getLogger(__name__)

NODES_DIR = os.getenv("NODES_DIR", "/shared/ai_nodes")
BACKEND_RELOAD_URL = os.getenv("BACKEND_RELOAD_URL", "http://backend:8000/internal/registry/reload")
BACKEND_URL = os.getenv("BACKEND_URL", "http://backend:8000")


class Deployer:
    """Deploys generated M8M node files and triggers registry reload."""

    @classmethod
    async def deploy(cls, files: Dict[str, str]) -> Dict[str, Any]:
        """
        Deploy generated node files:
        1. Test via NodeTester (runs BEFORE writing any file)
        2. Write .py files to NODES_DIR with fsync
        3. Extract node type from @register_action via AST
        4. Trigger registry reload via authenticated HTTP

        Args:
            files: Dict of {filename: code_content}

        Returns:
            {"status": "deployed", "type": "node_type"}
        """
        os.makedirs(NODES_DIR, exist_ok=True)
        deployed_nodes: List[str] = []

        for fname, content in files.items():
            if not fname.endswith(".py"):
                continue

            # Extract node type before testing
            node_type = cls._extract_node_type(content)

            # Stage 0: Test BEFORE writing
            from system4.node_tester import NodeTester
            test_result = await NodeTester.test(content, node_type or fname[:-3])
            if not test_result["passed"]:
                raise ValueError(
                    f"Failed testing: {test_result.get('errors', ['Unknown error'])}"
                )

            # Write with fsync for guaranteed persistence
            file_path = os.path.join(NODES_DIR, os.path.basename(fname))
            with open(file_path, "w") as f:
                f.write(content)
                f.flush()
                os.fsync(f.fileno())
            logger.info(f"Wrote node file: {file_path}")

            if node_type:
                deployed_nodes.append(node_type)
            else:
                logger.warning(
                    f"Could not extract node type from {fname} via AST"
                )

        # Trigger registry reload via authenticated HTTP
        await cls._trigger_reload()

        type_name = deployed_nodes[0] if deployed_nodes else "unknown"
        return {"status": "deployed", "type": type_name}

    @staticmethod
    def _extract_node_type(code: str) -> str | None:
        """
        Parse the code with AST and find the type= keyword argument
        in the @register_action(...) decorator call.
        """
        try:
            tree = ast.parse(code)
        except SyntaxError:
            return None

        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue

            for decorator in node.decorator_list:
                if not isinstance(decorator, ast.Call):
                    continue

                # Check if this is @register_action(...)
                func = decorator.func
                is_register_action = False
                if isinstance(func, ast.Name) and func.id == "register_action":
                    is_register_action = True
                elif isinstance(func, ast.Attribute) and func.attr == "register_action":
                    is_register_action = True

                if not is_register_action:
                    continue

                # Look for type= keyword argument
                for keyword in decorator.keywords:
                    if keyword.arg == "type" and isinstance(
                        keyword.value, ast.Constant
                    ):
                        return keyword.value.value

        return None

    @staticmethod
    async def _trigger_reload():
        """Trigger the backend's internal registry reload endpoint."""
        try:
            result = await internal_post(BACKEND_RELOAD_URL)
            logger.info(f"Registry reload triggered successfully: {result}")
        except Exception as e:
            logger.error(f"Failed to trigger registry reload: {e}")

    @classmethod
    async def rollback(cls, node_type: str) -> dict:
        """Remove a deployed node file via backend API and reload the registry."""
        try:
            return await internal_delete(f"{BACKEND_URL}/internal/ai-nodes/{node_type}")
        except Exception as e:
            logger.error(f"Rollback failed for {node_type}: {e}")
            return {"status": "error", "detail": str(e)}
