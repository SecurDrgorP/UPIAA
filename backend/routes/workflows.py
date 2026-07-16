"""
Workflow CRUD Routes — PostgreSQL-backed workflow management.
"""

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import get_db
from core.db_models import WorkflowModel
from core.schemas import WorkflowSchema
from core.node_registry import registry
from core.secret_vault import secret_vault
from core.json_utils import loads_tolerant, normalize_workflow_payload
from typing import Dict, Any

router = APIRouter(tags=["workflows"])


async def _read_workflow_payload(request: Request, *, unwrap: bool = False) -> WorkflowSchema:
    raw = await request.body()
    try:
        payload = loads_tolerant(raw)
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"Invalid workflow JSON: {e}") from e

    payload = normalize_workflow_payload(payload)
    wf_data = payload.get("workflow", payload) if unwrap and isinstance(payload, dict) else payload
    try:
        return WorkflowSchema(**wf_data)
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"Invalid workflow JSON: {e}") from e

async def _auto_encrypt_secrets(nodes: list[Dict[str, Any]], wf_id: str) -> None:
    for node in nodes:
        node_type = node.get("type")
        meta = registry.get_meta(node_type)
        if not meta or not meta.config_schema:
            continue
        
        config = node.get("config", {})
        
        # Schema can be a dict (most nodes) or a list (e.g. webhook_trigger)
        schema_items = []
        if isinstance(meta.config_schema, dict):
            schema_items = meta.config_schema.items()
        elif isinstance(meta.config_schema, list):
            schema_items = [(item.get("key"), item) for item in meta.config_schema if isinstance(item, dict) and "key" in item]
            
        for key, schema in schema_items:
            if isinstance(schema, dict) and schema.get("secret") is True:
                val = config.get(key)
                if val and isinstance(val, str) and not secret_vault.SECRET_PATTERN.match(val):
                    secret_key = f"{wf_id}_{node.get('id', 'node')}_{key}"
                    await secret_vault.store(secret_key, val)
                    config[key] = f"{{{{$secret.{secret_key}}}}}"

@router.post("/workflows")
async def create_workflow(request: Request, db: AsyncSession = Depends(get_db)):
    """Create or update a workflow in PostgreSQL."""
    workflow = await _read_workflow_payload(request)
    dag_data = workflow.model_dump(by_alias=True)
    wf_id = dag_data.pop("id", workflow.id)
    wf_name = dag_data.pop("name", workflow.name)

    if not wf_id or not str(wf_id).strip():
        wf_id = str(uuid.uuid4())[:8]

    # Auto-encrypt sensitive fields before storing
    await _auto_encrypt_secrets(dag_data.get("nodes", []), wf_id)

    existing = await db.get(WorkflowModel, wf_id)
    if existing:
        existing.name = wf_name
        existing.dag = dag_data
        existing.updated_at = datetime.now(timezone.utc)
    else:
        wf_model = WorkflowModel(id=wf_id, name=wf_name, dag=dag_data)
        db.add(wf_model)

    await db.commit()
    return {"id": wf_id, "message": "Workflow saved"}


@router.get("/workflows")
async def list_workflows(db: AsyncSession = Depends(get_db)):
    """List all workflows."""
    result = await db.execute(
        select(WorkflowModel).order_by(WorkflowModel.updated_at.desc())
    )
    workflows = result.scalars().all()
    return [wf.to_api() for wf in workflows]


@router.get("/workflows/{workflow_id}")
async def get_workflow(workflow_id: str, db: AsyncSession = Depends(get_db)):
    wf = await db.get(WorkflowModel, workflow_id)
    if not wf:
        raise HTTPException(status_code=404, detail="Workflow not found")
    return wf.to_api()


@router.delete("/workflows/{workflow_id}")
async def delete_workflow(workflow_id: str, db: AsyncSession = Depends(get_db)):
    import logging as _logging
    _del_log = _logging.getLogger(__name__)

    wf = await db.get(WorkflowModel, workflow_id)
    if not wf:
        raise HTTPException(status_code=404, detail="Workflow not found")

    # Clean up any encrypted secrets stored for this workflow's nodes
    nodes = (wf.dag or {}).get("nodes", [])
    for node in nodes:
        node_id = node.get("id", "")
        node_type = node.get("type")
        meta = registry.get_meta(node_type)
        if not meta or not meta.config_schema:
            continue

        schema_items = []
        if isinstance(meta.config_schema, dict):
            schema_items = meta.config_schema.items()
        elif isinstance(meta.config_schema, list):
            schema_items = [
                (item.get("key"), item)
                for item in meta.config_schema
                if isinstance(item, dict) and "key" in item
            ]

        for key, schema in schema_items:
            if isinstance(schema, dict) and schema.get("secret") is True:
                secret_key = f"{workflow_id}_{node_id}_{key}"
                deleted = await secret_vault.delete(secret_key)
                if deleted:
                    _del_log.debug(f"Deleted secret '{secret_key}' for workflow {workflow_id}")

    await db.delete(wf)
    await db.commit()
    return {"message": "Workflow deleted"}


@router.get("/workflows/{workflow_id}/export")
async def export_workflow(workflow_id: str, db: AsyncSession = Depends(get_db)):
    """Export a workflow as JSON."""
    wf = await db.get(WorkflowModel, workflow_id)
    if not wf:
        raise HTTPException(status_code=404, detail="Workflow not found")
    return {"workflow": wf.to_api(), "export_version": "1.0", "engine": "UPIAA"}


@router.post("/workflows/import")
async def import_workflow(request: Request, db: AsyncSession = Depends(get_db)):
    """Import a workflow from JSON."""
    workflow = await _read_workflow_payload(request, unwrap=True)

    dag_data = workflow.model_dump(by_alias=True)
    wf_id = dag_data.pop("id", workflow.id)
    wf_name = dag_data.pop("name", workflow.name)

    existing = await db.get(WorkflowModel, wf_id)
    
    # Auto-encrypt sensitive fields before storing
    await _auto_encrypt_secrets(dag_data.get("nodes", []), wf_id)

    if existing:
        existing.name = wf_name
        existing.dag = dag_data
    else:
        db.add(WorkflowModel(id=wf_id, name=wf_name, dag=dag_data))
    await db.commit()
    return {"id": wf_id, "name": wf_name, "message": "Workflow imported"}
