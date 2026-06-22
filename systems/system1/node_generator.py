"""
Systems — System 1 — Node Generator Pipeline (BUG-2 fix: push_fn injection)

Orchestrates the full node generation pipeline:
  spec → code → validate → deploy → frontend integration

push_fn is injected as a parameter, NOT imported from main.
"""

import logging
from typing import Any, Callable, Dict, Optional

from system1.spec_writer import SpecWriter
from system1.code_generator import CodeGenerator
from system1.validator import Validator
from system1.deployer import Deployer
from system1.frontend_integrator import FrontendIntegrator

logger = logging.getLogger(__name__)


class NodeGenerator:
    """Orchestrates the full AI node generation pipeline."""

    @classmethod
    async def generate(
        cls,
        intent: Dict[str, Any],
        push_fn: Optional[Callable] = None,
    ) -> Dict[str, Any]:
        """
        Run the complete node generation pipeline.

        Args:
            intent: {"prompt": "user description of the node"}
            push_fn: Optional async callable for SSE progress streaming.
                     Signature: push_fn(stage, message, status="running")

        Returns:
            {"status": "success", "node_type": "...", ...} or error dict
        """
        prompt = intent.get("prompt", intent.get("intent", ""))
        if not prompt:
            return {"status": "error", "error": "No prompt provided"}

        async def _push(stage: str, message: str, status: str = "running"):
            if push_fn:
                await push_fn(stage, message, status)

        try:
            # ── Stage 1: Spec Generation ─────────────────────────────────
            await _push("spec", "Generating node specification...")
            try:
                spec = await SpecWriter.write(prompt)
                await _push("spec", f"Spec complete: {spec.node_type} ({spec.label})")
            except Exception as e:
                await _push("spec", f"Spec generation failed: {e}", "error")
                return {"status": "error", "stage": "spec", "error": str(e)}

            # ── Stage 2: Code Generation ─────────────────────────────────
            await _push("code", f"Generating code for {spec.node_type}...")
            try:
                code_result = await CodeGenerator.generate(spec)
                await _push("code", f"Code generated: {code_result.filename}")
            except Exception as e:
                await _push("code", f"Code generation failed: {e}", "error")
                return {"status": "error", "stage": "code", "error": str(e)}

            # ── Stage 3: Validation ──────────────────────────────────────
            await _push("validate", "Validating generated code...")
            try:
                validation = await Validator.validate(
                    code_or_files={code_result.filename: code_result.code},
                    node_type=spec.node_type,
                )
                if not validation["valid"]:
                    errors = "; ".join(validation["errors"])
                    await _push("validate", f"Validation failed: {errors}", "error")
                    return {
                        "status": "error",
                        "stage": "validate",
                        "errors": validation["errors"],
                    }
                await _push("validate", "Validation passed ✓")
            except Exception as e:
                await _push("validate", f"Validation error: {e}", "error")
                return {"status": "error", "stage": "validate", "error": str(e)}

            # ── Stage 4: Deployment ──────────────────────────────────────
            await _push("deploy", f"Deploying {spec.node_type} to ai_nodes/...")
            try:
                deploy_result = await Deployer.deploy(
                    {code_result.filename: code_result.code}
                )
                await _push("deploy", f"Deployed: {deploy_result['type']}")
            except Exception as e:
                await _push("deploy", f"Deployment failed: {e}", "error")
                return {"status": "error", "stage": "deploy", "error": str(e)}

            # ── Stage 5: Frontend Integration ────────────────────────────
            await _push("frontend", "Generating frontend integration...")
            try:
                frontend_result = await FrontendIntegrator.generate(spec)
                await _push(
                    "frontend",
                    f"Frontend integration complete (icon: {frontend_result.icon})",
                )
            except Exception as e:
                # Non-fatal: node works without frontend hints
                logger.warning(f"Frontend integration failed (non-fatal): {e}")
                await _push("frontend", f"Frontend integration skipped: {e}")

            # ── Done ─────────────────────────────────────────────────────
            result = {
                "status": "success",
                "node_type": spec.node_type,
                "label": spec.label,
                "icon": spec.icon,
                "filename": code_result.filename,
            }
            await _push("done", f"Node '{spec.label}' generated successfully!", "done")
            return result

        except Exception as e:
            logger.error(f"Node generation pipeline failed: {e}")
            await _push("error", f"Pipeline failed: {e}", "error")
            return {"status": "error", "error": str(e)}
