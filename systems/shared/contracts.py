"""
Systems AI Contracts — Pydantic models for structured LLM output.

Exact copy of backend/ai/contracts.py with ZERO backend imports.
All LLM responses that need structure must use one of these contracts
as the response_model in call_llm().
"""

from typing import Any, Dict, List, Literal, Optional
from pydantic import BaseModel, Field


# ── System 1: Node Generation ────────────────────────────────────────────────

class SpecContract(BaseModel):
    """Specification for a new UPIAA node, produced by SpecWriter."""
    node_type: str = Field(..., description="Snake_case node type, e.g. 'slack_sender'")
    label: str = Field(..., description="Human-readable label, e.g. 'Slack Sender'")
    icon: str = Field(..., description="Emoji icon, e.g. '💬'")
    category: str = Field(default="Actions", description="Node category")
    description: str = Field(..., description="Short description of what the node does")
    config_schema: Dict[str, Any] = Field(
        default_factory=dict,
        description="Config fields with type/default/description"
    )
    handler_logic: str = Field(
        ...,
        description="Plain English description of what handle_{node_type} should do"
    )


class CodeContract(BaseModel):
    """Generated UPIAA node code, produced by CodeGenerator."""
    filename: str = Field(..., description="Filename, e.g. 'slack_sender.py'")
    code: str = Field(
        ...,
        description="Complete Python file with @register_action and async def handle_{node_type}"
    )


class FrontendContract(BaseModel):
    """Frontend integration hints for a generated node."""
    sidebar_template: str = Field(default="", description="Angular template snippet for sidebar")
    icon: str = Field(..., description="Emoji icon")
    accent_color: str = Field(default="#6366f1", description="Hex color for node accent")


# ── System 2: Workflow Assembly ──────────────────────────────────────────────

class PlannedNode(BaseModel):
    """A node in a planned workflow."""
    id: str
    type: str
    label: str
    config: Dict[str, Any] = Field(default_factory=dict)
    position: Dict[str, float] = Field(default_factory=lambda: {"x": 0, "y": 0})


class PlannedEdge(BaseModel):
    """An edge in a planned workflow."""
    id: str
    source: str
    target: str
    sourceHandle: Optional[str] = None


class WorkflowPlan(BaseModel):
    """Complete workflow plan produced by WorkflowAssembler."""
    name: str = Field(default="AI Generated Workflow")
    nodes: List[PlannedNode] = Field(default_factory=list)
    edges: List[PlannedEdge] = Field(default_factory=list)


# ── System 2: Intent Classification ─────────────────────────────────────────

class IntentResult(BaseModel):
    """Intent classification result produced by IntentParser."""
    type: Literal["workflow_request", "chat_response"] = Field(
        ...,
        description="Whether the user wants to build a workflow or is asking a question"
    )
    prompt: str = Field(..., description="The original or reformulated user prompt")
    explanation: str = Field(
        default="",
        description="Brief explanation of classification reasoning"
    )
    requirements: List[str] = Field(
        default_factory=list,
        description="Extracted workflow requirements if type is workflow_request"
    )


# ── System 2: Review ────────────────────────────────────────────────────────

class ReviewResult(BaseModel):
    """Structured review result for workflow validation."""
    is_valid: bool
    structural_errors: List[str] = Field(default_factory=list)
    semantic_feedback: str = Field(default="")
    suggestions: List[str] = Field(default_factory=list)
