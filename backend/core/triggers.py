"""
Triggers — Webhook + schedule management with PostgreSQL persistence.
"""

import logging
import uuid
from typing import Optional

logger = logging.getLogger(__name__)


# ════════════════════════════════════════════════════════════════════════════
# TRIGGER MANAGEMENT
# ════════════════════════════════════════════════════════════════════════════

class TriggerManager:
    """Manages webhook and schedule triggers with PostgreSQL persistence."""

    @classmethod
    async def create_webhook_trigger(cls, workflow_id: str, config: dict = None) -> dict:
        """Create a webhook trigger for a workflow."""
        from core.database import AsyncSessionLocal
        from core.db_models import TriggerModel

        webhook_id = str(uuid.uuid4())[:12]
        trigger_config = {
            "webhook_id": webhook_id,
            "webhook_path": f"/api/webhooks/{webhook_id}",
            **(config or {}),
        }

        async with AsyncSessionLocal() as session:
            trigger = TriggerModel(
                workflow_id=workflow_id,
                trigger_type="webhook",
                config=trigger_config,
                is_active=True,
            )
            session.add(trigger)
            await session.commit()

        return {"trigger_id": trigger.id, **trigger_config}

    @classmethod
    async def create_schedule_trigger(
        cls, workflow_id: str, cron_expression: str, config: dict = None
    ) -> dict:
        """Create a schedule (cron) trigger for a workflow."""
        from core.database import AsyncSessionLocal
        from core.db_models import TriggerModel

        trigger_config = {
            "cron": cron_expression,
            **(config or {}),
        }

        async with AsyncSessionLocal() as session:
            trigger = TriggerModel(
                workflow_id=workflow_id,
                trigger_type="schedule",
                config=trigger_config,
                is_active=True,
            )
            session.add(trigger)
            await session.commit()

        return {"trigger_id": trigger.id, "cron": cron_expression}

    @classmethod
    async def list_triggers(cls, workflow_id: str) -> list[dict]:
        """List all triggers for a workflow."""
        from core.database import AsyncSessionLocal
        from core.db_models import TriggerModel
        from sqlalchemy import select

        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(TriggerModel).where(TriggerModel.workflow_id == workflow_id)
            )
            triggers = result.scalars().all()
            return [
                {
                    "id": t.id,
                    "workflow_id": t.workflow_id,
                    "trigger_type": t.trigger_type,
                    "config": t.config,
                    "is_active": t.is_active,
                    "created_at": t.created_at.isoformat() if t.created_at else None,
                }
                for t in triggers
            ]

    @classmethod
    async def deactivate_trigger(cls, trigger_id: int) -> bool:
        """Deactivate a trigger."""
        from core.database import AsyncSessionLocal
        from core.db_models import TriggerModel

        async with AsyncSessionLocal() as session:
            trigger = await session.get(TriggerModel, trigger_id)
            if trigger:
                trigger.is_active = False
                await session.commit()
                return True
            return False

    @classmethod
    async def delete_trigger(cls, trigger_id: int) -> bool:
        """Delete a trigger."""
        from core.database import AsyncSessionLocal
        from core.db_models import TriggerModel

        async with AsyncSessionLocal() as session:
            trigger = await session.get(TriggerModel, trigger_id)
            if trigger:
                await session.delete(trigger)
                await session.commit()
                return True
            return False

    @classmethod
    async def get_active_webhooks(cls) -> list[dict]:
        """Get all active webhook triggers (for route registration)."""
        from core.database import AsyncSessionLocal
        from core.db_models import TriggerModel
        from sqlalchemy import select

        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(TriggerModel).where(
                    TriggerModel.trigger_type == "webhook",
                    TriggerModel.is_active == True,  # noqa: E712
                )
            )
            triggers = result.scalars().all()
            return [
                {
                    "id": t.id,
                    "workflow_id": t.workflow_id,
                    "config": t.config,
                }
                for t in triggers
            ]


trigger_manager = TriggerManager()
