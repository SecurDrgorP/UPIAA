"""
Systems — System 3 — Memory Client (BUG-5 fix)

Replaces direct core.database imports with HTTP calls to the backend's
/api/hermies/* endpoints. All DB access goes through the backend service.
"""

import logging
import os
from typing import Any, Dict, List, Optional

from shared.http import internal_post, internal_get

logger = logging.getLogger(__name__)

BACKEND_URL = os.getenv("BACKEND_URL", "http://backend:8000")


class MemoryClient:
    """HTTP-based memory client replacing direct DB imports."""

    @classmethod
    async def store_interaction(
        cls,
        user_id: str,
        prompt: str,
        response: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Store a user interaction via backend API."""
        try:
            await internal_post(
                f"{BACKEND_URL}/api/hermies/memory/store",
                json={
                    "user_id": user_id,
                    "prompt": prompt,
                    "response": response,
                    "context": context or {},
                },
            )
        except Exception as e:
            logger.error(f"Failed to store interaction: {e}")

    @classmethod
    async def get_recent_context(
        cls, user_id: str, limit: int = 10
    ) -> List[Dict[str, Any]]:
        """Get recent interactions via backend API."""
        try:
            result = await internal_get(
                f"{BACKEND_URL}/api/hermies/memory/{user_id}",
                params={"limit": limit},
            )
            return result if isinstance(result, list) else []
        except Exception as e:
            logger.error(f"Failed to get recent context: {e}")
            return []

    @classmethod
    async def register_skill(
        cls,
        name: str,
        description: str,
        parameters: Dict[str, Any] = None,
        workflow_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Register a skill via backend API."""
        try:
            return await internal_post(
                f"{BACKEND_URL}/api/hermies/skills/register",
                json={
                    "name": name,
                    "description": description,
                    "parameters": parameters or {},
                    "workflow_id": workflow_id,
                },
            )
        except Exception as e:
            logger.error(f"Failed to register skill: {e}")
            return {"status": "error", "detail": str(e)}

    @classmethod
    async def list_skills(cls) -> List[Dict[str, Any]]:
        """List all skills via backend API."""
        try:
            result = await internal_get(f"{BACKEND_URL}/api/hermies/skills")
            return result if isinstance(result, list) else []
        except Exception as e:
            logger.error(f"Failed to list skills: {e}")
            return []
