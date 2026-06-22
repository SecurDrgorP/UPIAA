"""
Systems — System 3 — Hermies Chat Handler

Conversational AI with graceful fallback when workflow assembly fails.
Uses MemoryClient (HTTP) instead of direct DB imports.
"""

import logging
from typing import Any, Dict, Optional

from system2.intent_parser import IntentParser
from system2.node_planner import NodePlanner
from system2.workflow_assembler import WorkflowAssembler
from system2.reviewer import Reviewer
from system3.memory_client import MemoryClient

logger = logging.getLogger(__name__)


class HermiesChat:
    """Hermies conversational handler with graceful workflow fallback."""

    @classmethod
    async def handle(
        cls,
        prompt: str,
        context: Optional[Dict[str, Any]] = None,
        user_id: str = "default",
    ) -> Dict[str, Any]:
        """
        Handle a conversational request from the user.
        Routes to workflow building or direct chat based on intent.
        Implements graceful fallback if workflow assembly fails.
        """
        try:
            intent = await IntentParser.parse(prompt, context)
        except Exception as e:
            logger.error(f"Intent parsing failed: {e}")
            return {
                "type": "chat",
                "explanation": f"I had trouble understanding your request ({type(e).__name__}). Could you rephrase?",
                "confidence": 0.0,
            }

        response_payload: Dict[str, Any]

        if intent.type == "workflow_request":
            try:
                plan = await NodePlanner.plan(intent.model_dump())
                workflow = await WorkflowAssembler.assemble(plan)
                review = await Reviewer.review(workflow, prompt)
                response_payload = {
                    "type": "workflow",
                    "workflow": workflow,
                    "review": review,
                    "explanation": intent.explanation,
                    "confidence": 1.0,
                }
            except Exception as e:
                logger.error(f"Workflow assembly failed, falling back: {e}")
                response_payload = {
                    "type": "chat",
                    "explanation": (
                        f"I understood you want to build a workflow, but I couldn't "
                        f"assemble it right now ({type(e).__name__}). Try again."
                    ),
                    "confidence": 0.3,
                }
        else:
            response_payload = {
                "type": "chat",
                "explanation": intent.explanation or "How can I help you?",
                "confidence": 0.5,
            }

        # Store the interaction in memory (non-blocking)
        try:
            await MemoryClient.store_interaction(
                user_id=user_id,
                prompt=prompt,
                response=response_payload.get("explanation", ""),
            )
        except Exception as e:
            logger.warning(f"Failed to store interaction (non-fatal): {e}")

        return response_payload
