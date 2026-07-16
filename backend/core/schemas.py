"""
UPIAA — Pydantic v2 Schemas for the Agentic AI Workflow Engine.
Defines all node types, workflow structures, and execution models.
"""

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field, model_validator

from core.node_registry import registry


class NodeCategory(str, Enum):
    MODEL = "Model Actions"
    LLM = "LLM Actions"
    LOGIC = "Logic"
    ACTIONS = "Actions"
    COMMUNICATION = "Communication"
    TRIGGERS = "Triggers"


# ─── Node & Edge Data ────────────────────────────────────────────────────────

class NodeData(BaseModel):
    model_config = {"populate_by_name": True}

    id: str = Field(default_factory=lambda: str(uuid.uuid4())[:8])
    type: str
    label: str = ""
    config: dict[str, Any] = Field(default_factory=dict)
    position: dict[str, float] = Field(default_factory=lambda: {"x": 0, "y": 0})

    def dict(self, **kwargs) -> dict:
        """Pydantic v2 compatibility shim."""
        return self.model_dump(**kwargs)


class EdgeData(BaseModel):
    model_config = {"populate_by_name": True}

    id: str = Field(default_factory=lambda: str(uuid.uuid4())[:8])
    source: str
    target: str
    source_handle: Optional[str] = Field(None, alias="sourceHandle")

    def dict(self, **kwargs) -> dict:
        return self.model_dump(by_alias=True, **kwargs)


# ─── Workflow Schema ─────────────────────────────────────────────────────────

class WorkflowSchema(BaseModel):
    model_config = {"populate_by_name": True}

    id: str = Field(default_factory=lambda: str(uuid.uuid4())[:8])
    name: str = "Untitled Workflow"
    nodes: list[NodeData] = Field(default_factory=list)
    edges: list[EdgeData] = Field(default_factory=list)
    created_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def dict(self, **kwargs) -> dict:
        return self.model_dump(by_alias=True, **kwargs)


# ─── Execution Status ────────────────────────────────────────────────────────

class ExecutionStatus(BaseModel):
    node_id: str
    node_label: str
    status: str  # "running" | "success" | "failed" | "error" | "skipped"
    result: Any = None
    error: Optional[str] = None
    error_type: Optional[str] = None
    message: Optional[str] = None
    invalid_config_fields: list[str] = Field(default_factory=list)


# ─── Node Type Info (for frontend palette) ───────────────────────────────────

class NodeTypeInfo(BaseModel):
    type: str
    label: str
    icon: str
    description: str
    category: str
    default_config: dict[str, Any] = Field(default_factory=dict)


# ─── Secret Schema ──────────────────────────────────────────────────────────

class SecretCreate(BaseModel):
    key_name: str
    value: str


class SecretResponse(BaseModel):
    key_name: str
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class CredentialCreate(BaseModel):
    id: Optional[str] = None
    name: str
    provider: str
    fields: dict[str, Any] = Field(default_factory=dict)


class CredentialResponse(BaseModel):
    id: str
    name: str
    provider: str
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


# ─── AI Builder Schemas ─────────────────────────────────────────────────────

class AIBuildRequest(BaseModel):
    prompt: str
    context: dict[str, Any] = Field(default_factory=dict)


class AIBuildResponse(BaseModel):
    workflow: Optional[dict] = None
    explanation: str = ""
    confidence: float = 0.0


# ─── Registry of all available node types ────────────────────────────────────

def get_node_type_catalog() -> list[dict]:
    return [meta.dict() for meta in registry.get_catalog()]
