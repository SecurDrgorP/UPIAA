"""
Hermies Routes — Memory CRUD endpoints for the Hermies AI assistant (System 3).
All endpoints require internal authentication via X-Internal-Token header.
These endpoints are consumed by the systems container's MemoryClient.
"""

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from datetime import datetime, timezone

from core.database import get_db
from core.db_models import HermiesUserModel, HermiesSkillModel

logger = logging.getLogger(__name__)


# ── Request/Response Models ──────────────────────────────────────────────────

class HermiesStoreRequest(BaseModel):
    user_id: str
    prompt: str
    response: str
    context: Dict[str, Any] = Field(default_factory=dict)
    feedback: Optional[str] = None


class SkillRegisterRequest(BaseModel):
    name: str
    description: str
    parameters: Dict[str, Any] = Field(default_factory=dict)
    workflow_id: Optional[str] = None


# ── Lazy import verify_internal to avoid circular imports ────────────────────

def _get_verify_internal():
    from main import verify_internal
    return verify_internal


router = APIRouter(
    prefix="/hermies",
    tags=["hermies"],
    dependencies=[Depends(_get_verify_internal)],
)


@router.post("/memory/store")
async def store_memory(
    payload: HermiesStoreRequest, db: AsyncSession = Depends(get_db)
):
    """Store a user interaction for Hermies learning."""
    interaction = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "prompt": payload.prompt[:500],
        "response": payload.response[:500],
        "context": payload.context,
        "feedback": payload.feedback,
    }

    result = await db.execute(
        select(HermiesUserModel).where(HermiesUserModel.user_id == payload.user_id)
    )
    user_model = result.scalar_one_or_none()

    if user_model:
        logs = list(user_model.interaction_log or [])
        logs.append(interaction)
        # Keep last 100 interactions
        if len(logs) > 100:
            logs = logs[-100:]
        user_model.interaction_log = logs
        user_model.updated_at = datetime.now(timezone.utc)
    else:
        db.add(HermiesUserModel(
            user_id=payload.user_id,
            preferences={},
            interaction_log=[interaction],
        ))
    await db.commit()
    return {"status": "stored"}


@router.get("/memory/{user_id}")
async def get_memory(
    user_id: str, limit: int = 10, db: AsyncSession = Depends(get_db)
):
    """Get recent interactions for context building."""
    result = await db.execute(
        select(HermiesUserModel).where(HermiesUserModel.user_id == user_id)
    )
    user_model = result.scalar_one_or_none()
    if not user_model:
        return []
    logs = user_model.interaction_log or []
    return logs[-limit:]


@router.post("/skills/register")
async def register_skill(
    payload: SkillRegisterRequest, db: AsyncSession = Depends(get_db)
):
    """Register or update a Hermies skill."""
    result = await db.execute(
        select(HermiesSkillModel).where(HermiesSkillModel.name == payload.name)
    )
    skill = result.scalar_one_or_none()

    if skill:
        skill.description = payload.description
        skill.parameters = payload.parameters
        skill.workflow_id = payload.workflow_id
    else:
        skill = HermiesSkillModel(
            name=payload.name,
            description=payload.description,
            parameters=payload.parameters,
            workflow_id=payload.workflow_id,
        )
        db.add(skill)
    await db.commit()
    return {"name": payload.name, "status": "registered"}


@router.get("/skills")
async def list_skills(db: AsyncSession = Depends(get_db)):
    """List all available Hermies skills."""
    result = await db.execute(select(HermiesSkillModel))
    skills = result.scalars().all()
    return [
        {
            "id": s.id,
            "name": s.name,
            "description": s.description,
            "parameters": s.parameters,
            "workflow_id": s.workflow_id,
        }
        for s in skills
    ]
