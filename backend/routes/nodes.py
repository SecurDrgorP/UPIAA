"""
Node Routes — Node type catalog and model management.
"""

import os
import sys
from fastapi import APIRouter, UploadFile, File
from core.schemas import get_node_type_catalog
from core.node_registry import registry
from nodes.model_action import list_available_models

router = APIRouter(tags=["nodes"])

MODEL_STORAGE = os.getenv("MODEL_STORAGE_PATH", "/app/storage/models")


@router.get("/node-types")
async def get_node_types():
    """Returns available node types grouped by category."""
    return get_node_type_catalog()


@router.get("/models")
async def get_models():
    """List available ML model files in storage."""
    return await list_available_models()


@router.post("/models/upload")
async def upload_model(file: UploadFile = File(...)):
    """Upload a trained model file (.pkl, .h5, .joblib)."""
    os.makedirs(MODEL_STORAGE, exist_ok=True)
    filepath = os.path.join(MODEL_STORAGE, file.filename)
    with open(filepath, "wb") as f:
        content = await file.read()
        f.write(content)
    return {"filename": file.filename, "path": filepath, "message": "Model uploaded"}


@router.get("/node-docs")
async def get_node_docs():
    """Returns module-level docstrings for every registered node type."""
    result = {}
    for meta in registry.get_catalog():
        handler = registry.get_handler(meta.type)
        doc = ""
        if handler:
            module = sys.modules.get(handler.__module__)
            if module:
                doc = module.__doc__ or ""
        result[meta.type] = {
            "label": meta.label,
            "category": meta.category,
            "icon": meta.icon,
            "description": meta.description,
            "doc": doc.strip(),
        }
    return result
