"""
DB Models — SQLAlchemy ORM models for PostgreSQL.

Workflow DAGs are stored as JSONB for maximum flexibility.
Execution logs and context are also JSONB.
Includes Secret, HermiesSkill, HermiesUserModel, TokenUsage, and Trigger tables.
"""

from datetime import datetime, timezone
from sqlalchemy import String, Boolean, DateTime, Text, Integer, Numeric, ForeignKey, Index
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from core.database import Base


class WorkflowModel(Base):
    __tablename__ = "workflows"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), default="Untitled Workflow")
    is_published: Mapped[bool] = mapped_column(Boolean, default=False)

    # The full workflow DAG (nodes, edges, config) as JSONB
    dag: Mapped[dict] = mapped_column(JSONB, default=dict)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    # Relationships
    executions: Mapped[list["ExecutionModel"]] = relationship(
        back_populates="workflow", cascade="all, delete-orphan"
    )
    triggers: Mapped[list["TriggerModel"]] = relationship(
        back_populates="workflow", cascade="all, delete-orphan"
    )

    def to_api(self) -> dict:
        """Convert to API-compatible dict."""
        result = dict(self.dag) if self.dag else {}
        result["id"] = self.id
        result["name"] = self.name
        result["is_published"] = self.is_published
        result["created_at"] = self.created_at.isoformat() if self.created_at else None
        result["updated_at"] = self.updated_at.isoformat() if self.updated_at else None
        return result


class ExecutionModel(Base):
    __tablename__ = "executions"
    __table_args__ = (
        Index("ix_executions_workflow_id", "workflow_id"),
        Index("ix_executions_state", "state"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    workflow_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("workflows.id", ondelete="CASCADE")
    )
    state: Mapped[str] = mapped_column(String(32), default="pending")

    logs: Mapped[list] = mapped_column(JSONB, default=list)
    context: Mapped[dict] = mapped_column(JSONB, default=dict)

    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Temporal workflow run ID for correlation
    temporal_run_id: Mapped[str | None] = mapped_column(String(128), nullable=True)

    workflow: Mapped["WorkflowModel"] = relationship(back_populates="executions")
    token_usage: Mapped[list["TokenUsageModel"]] = relationship(
        back_populates="execution", cascade="all, delete-orphan"
    )

    def to_summary(self) -> dict:
        return {
            "id": self.id,
            "workflow_id": self.workflow_id,
            "state": self.state,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "log_count": len(self.logs) if self.logs else 0,
        }


class SecretModel(Base):
    """Encrypted secrets stored in PostgreSQL (Fernet + PBKDF2HMAC)."""
    __tablename__ = "secrets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    key_name: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    cipher_text: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


class CredentialModel(Base):
    """Encrypted credential bundles for node execution-time injection."""
    __tablename__ = "credentials"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    cipher_text: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


class HermiesSkillModel(Base):
    """Hermies learned skills — each maps to a workflow template."""
    __tablename__ = "hermies_skills"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    parameters: Mapped[dict] = mapped_column(JSONB, default=dict)
    workflow_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("workflows.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


class HermiesUserModel(Base):
    """Hermies user model — stores preferences and interaction history."""
    __tablename__ = "hermies_user_models"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    preferences: Mapped[dict] = mapped_column(JSONB, default=dict)
    interaction_log: Mapped[list] = mapped_column(JSONB, default=list)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


class TokenUsageModel(Base):
    """Per-node token usage tracking with PostgreSQL persistence."""
    __tablename__ = "token_usage"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    execution_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("executions.id", ondelete="CASCADE")
    )
    node_id: Mapped[str] = mapped_column(String(64), nullable=False)
    model: Mapped[str] = mapped_column(String(128), nullable=False)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Numeric(10, 6), default=0)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    execution: Mapped["ExecutionModel"] = relationship(back_populates="token_usage")


class TriggerModel(Base):
    """Workflow triggers — webhook, schedule, or event-based."""
    __tablename__ = "triggers"
    __table_args__ = (
        Index("ix_triggers_workflow_id", "workflow_id"),
        Index("ix_triggers_is_active", "is_active"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    workflow_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("workflows.id", ondelete="CASCADE"), nullable=False
    )
    trigger_type: Mapped[str] = mapped_column(String(32), nullable=False)  # 'webhook', 'schedule', 'event'
    config: Mapped[dict] = mapped_column(JSONB, default=dict)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    workflow: Mapped["WorkflowModel"] = relationship(back_populates="triggers")
