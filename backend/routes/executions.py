"""
Execution Routes — Start, list, get, stop, publish, unpublish executions.
"""

import uuid
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import get_db
from core.db_models import WorkflowModel, ExecutionModel
from core.schemas import WorkflowSchema
from core.exec_singleton import exec_manager
from core.json_utils import loads_tolerant, normalize_workflow_payload
from core.temporal_workflows import (
    get_temporal_client,
    OneTimeWorkflowExecution,
    PublishedWorkflowExecution,
    TASK_QUEUE,
)

logger = logging.getLogger(__name__)
router = APIRouter(tags=["executions"])




@router.post("/executions")
async def start_execution(request: Request, db: AsyncSession = Depends(get_db)):
    """Start a workflow execution via Temporal, fallback to in-memory."""
    try:
        workflow = WorkflowSchema(**normalize_workflow_payload(loads_tolerant(await request.body())))
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"Invalid workflow JSON: {e}") from e

    exec_id = f"exec_{uuid.uuid4().hex[:16]}"

    try:
        client = await get_temporal_client()

        exec_model = ExecutionModel(
            id=exec_id,
            workflow_id=workflow.id,
            state="running",
            started_at=datetime.now(timezone.utc),
        )
        db.add(exec_model)
        await db.commit()

        await client.start_workflow(
            OneTimeWorkflowExecution.run,
            {"workflow": workflow.model_dump(by_alias=True), "exec_id": exec_id},
            id=exec_id,
            task_queue=TASK_QUEUE,
        )

        return {"exec_id": exec_id, "status": "running", "engine": "temporal"}
    except Exception as e:
        logger.warning(f"Temporal unavailable, falling back to in-memory: {e}")
        exec_id = exec_manager.start(workflow)
        return {"exec_id": exec_id, "status": "running", "engine": "in-memory"}


@router.get("/executions")
async def list_executions(db: AsyncSession = Depends(get_db)):
    """List recent executions."""
    try:
        result = await db.execute(
            select(ExecutionModel).order_by(ExecutionModel.started_at.desc()).limit(50)
        )
        execs = result.scalars().all()
        return [e.to_summary() for e in execs]
    except Exception:
        return exec_manager.list_all()


@router.get("/executions/{exec_id}")
async def get_execution(exec_id: str, db: AsyncSession = Depends(get_db)):
    """Get execution details."""
    exec_model = await db.get(ExecutionModel, exec_id)
    if exec_model:
        return exec_model.to_summary()
    execution = exec_manager.get(exec_id)
    if not execution:
        raise HTTPException(status_code=404, detail="Execution not found")
    return execution.to_detail()


@router.post("/executions/{exec_id}/stop")
async def stop_execution(exec_id: str):
    """Stop a running execution."""
    try:
        client = await get_temporal_client()
        handle = client.get_workflow_handle(exec_id)
        await handle.cancel()
        return {"exec_id": exec_id, "status": "stopping"}
    except Exception:
        pass

    success = exec_manager.stop(exec_id)
    if not success:
        raise HTTPException(status_code=400, detail="Execution not running or not found")
    return {"exec_id": exec_id, "status": "stopping"}


@router.post("/workflows/{workflow_id}/publish")
async def publish_workflow(workflow_id: str, db: AsyncSession = Depends(get_db)):
    """Publish a workflow — runs continuously via Temporal."""
    wf = await db.get(WorkflowModel, workflow_id)
    if not wf:
        raise HTTPException(status_code=404, detail="Workflow not found")

    if wf.is_published:
        return {"workflow_id": workflow_id, "status": "already_published"}

    exec_id = f"pub_{workflow_id}"
    wf_data = wf.to_api()

    try:
        client = await get_temporal_client()

        exec_model = ExecutionModel(
            id=exec_id,
            workflow_id=workflow_id,
            state="running",
            started_at=datetime.now(timezone.utc),
        )
        existing_exec = await db.get(ExecutionModel, exec_id)
        if existing_exec:
            existing_exec.state = "running"
            existing_exec.started_at = datetime.now(timezone.utc)
            existing_exec.finished_at = None
        else:
            db.add(exec_model)

        wf.is_published = True
        await db.commit()

        await client.start_workflow(
            PublishedWorkflowExecution.run,
            {"workflow": wf_data, "exec_id": exec_id},
            id=exec_id,
            task_queue=TASK_QUEUE,
        )

        return {"exec_id": exec_id, "status": "published", "engine": "temporal"}
    except Exception as e:
        logger.warning(f"Temporal unavailable, falling back to in-memory: {e}")
        wf_schema = WorkflowSchema(**wf_data)
        fallback_id = exec_manager.start(wf_schema, published=True)
        wf.is_published = True
        await db.commit()
        return {"exec_id": fallback_id, "status": "published", "engine": "in-memory"}


@router.post("/workflows/{workflow_id}/unpublish")
async def unpublish_workflow(workflow_id: str, db: AsyncSession = Depends(get_db)):
    """Unpublish a workflow."""
    wf = await db.get(WorkflowModel, workflow_id)
    if not wf:
        raise HTTPException(status_code=404, detail="Workflow not found")

    if not wf.is_published:
        return {"workflow_id": workflow_id, "status": "not_published"}

    exec_id = f"pub_{workflow_id}"
    try:
        client = await get_temporal_client()
        handle = client.get_workflow_handle(exec_id)
        await handle.signal(PublishedWorkflowExecution.stop)
    except Exception:
        old_exec_id = exec_manager.get_published(workflow_id)
        if old_exec_id:
            exec_manager.unpublish(old_exec_id)

    wf.is_published = False
    await db.commit()
    return {"workflow_id": workflow_id, "status": "unpublished"}


@router.get("/workflows/{workflow_id}/publish-status")
async def get_publish_status(workflow_id: str, db: AsyncSession = Depends(get_db)):
    """Check if a workflow is published."""
    wf = await db.get(WorkflowModel, workflow_id)
    if not wf:
        return {"published": False, "workflow_id": workflow_id}
    return {"published": wf.is_published, "workflow_id": workflow_id}
