"""
Metrics Routes — Prometheus-compatible metrics endpoint.
"""

import logging
from fastapi import APIRouter
from fastapi.responses import PlainTextResponse

logger = logging.getLogger(__name__)
router = APIRouter(tags=["metrics"])


@router.get("/metrics", response_class=PlainTextResponse)
async def prometheus_metrics():
    """Expose metrics in Prometheus text format."""
    try:
        from prometheus_client import generate_latest, CONTENT_TYPE_LATEST
        return PlainTextResponse(
            content=generate_latest().decode("utf-8"),
            media_type=CONTENT_TYPE_LATEST,
        )
    except ImportError:
        # Fallback: basic metrics without prometheus_client
        try:
            from core.database import AsyncSessionLocal
            from core.db_models import WorkflowModel, ExecutionModel
            from sqlalchemy import select, func

            async with AsyncSessionLocal() as session:
                wf_count = await session.scalar(select(func.count(WorkflowModel.id)))
                exec_count = await session.scalar(select(func.count(ExecutionModel.id)))
                published_count = await session.scalar(
                    select(func.count(WorkflowModel.id)).where(
                        WorkflowModel.is_published == True  # noqa: E712
                    )
                )

            metrics = [
                f"# HELP m8m_workflows_total Total number of workflows",
                f"# TYPE m8m_workflows_total gauge",
                f"m8m_workflows_total {wf_count or 0}",
                f"# HELP m8m_executions_total Total number of executions",
                f"# TYPE m8m_executions_total gauge",
                f"m8m_executions_total {exec_count or 0}",
                f"# HELP m8m_workflows_published Number of published workflows",
                f"# TYPE m8m_workflows_published gauge",
                f"m8m_workflows_published {published_count or 0}",
            ]
            return PlainTextResponse(
                content="\n".join(metrics) + "\n",
                media_type="text/plain; version=0.0.4; charset=utf-8",
            )
        except Exception as e:
            logger.error(f"Metrics error: {e}")
            return PlainTextResponse(content="# No metrics available\n")
