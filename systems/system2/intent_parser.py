"""
Systems — System 2 — Intent Parser

Classifies user intent using the LLM to determine whether the user wants
to build a workflow or is asking a general question.
"""

import logging
from typing import Any, Dict, Optional

from shared.llm_client import call_llm
from shared.contracts import IntentResult

logger = logging.getLogger(__name__)


SYSTEM_PROMPT = """You are an intent classifier for M8M, an AI workflow automation platform.

Your job is to determine if the user wants to:
1. BUILD a workflow (type: "workflow_request") — they want to automate something
2. ASK a question (type: "chat_response") — they want information or help

IMPORTANT: Many requests that START with question words are actually workflow requests.
Examples:
- "How do I set up a morning email digest?" → workflow_request (they want to build it)
- "What's the best way to monitor my website?" → workflow_request (they want automation)
- "How does the HTTP request node work?" → chat_response (asking about platform)
- "What is a webhook?" → chat_response (asking for information)

For workflow_request, extract specific requirements about what the workflow should do.

Return JSON:
{
    "type": "workflow_request" or "chat_response",
    "prompt": "the original or clarified prompt",
    "explanation": "why you classified it this way",
    "requirements": ["requirement 1", "requirement 2"] (only for workflow_request)
}"""


class IntentParser:
    @staticmethod
    async def parse(prompt: str, context: Optional[Dict[str, Any]] = None) -> IntentResult:
        """
        Use the LLM to classify user intent and extract structured requirements.
        """
        user_prompt = f"Classify this user request:\n\n\"{prompt}\""

        if context:
            import json
            user_prompt += f"\n\nPrevious context: {json.dumps(context)}"

        try:
            result = await call_llm(
                prompt=user_prompt,
                system_prompt=SYSTEM_PROMPT,
                response_model=IntentResult,
            )
            logger.info(f"Intent classified as: {result.type} (explanation: {result.explanation})")
            return result

        except Exception as e:
            logger.error(f"IntentParser LLM call failed: {e}")
            # Fallback: assume workflow request to avoid blocking the user
            return IntentResult(
                type="workflow_request",
                prompt=prompt,
                explanation=f"LLM classification failed ({e}), defaulting to workflow_request",
                requirements=[],
            )
