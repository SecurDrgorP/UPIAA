"""
Database — Async SQLAlchemy engine + session for PostgreSQL.
Pool tuning with configurable pool_size and max_overflow from env.
"""

import os
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import DeclarativeBase

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+asyncpg://UPIAA:UPIAA_secret@localhost:5432/UPIAA",
)

# Pool tuning — configurable via environment
POOL_SIZE = int(os.getenv("DB_POOL_SIZE", "10"))
MAX_OVERFLOW = int(os.getenv("DB_MAX_OVERFLOW", "20"))
POOL_RECYCLE = int(os.getenv("DB_POOL_RECYCLE", "1800"))  # 30 minutes

engine = create_async_engine(
    DATABASE_URL,
    echo=False,
    pool_size=POOL_SIZE,
    max_overflow=MAX_OVERFLOW,
    pool_recycle=POOL_RECYCLE,
    pool_pre_ping=True,  # verify connections before checkout
)

AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


class Base(DeclarativeBase):
    pass


async def get_db():
    """FastAPI dependency — yields an async DB session."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()


async def init_db():
    """Create all tables (called once on startup)."""
    from core.db_models import (  # noqa: F401
        WorkflowModel,
        ExecutionModel,
        SecretModel,
        CredentialModel,
        HermiesSkillModel,
        HermiesUserModel,
        TokenUsageModel,
        TriggerModel,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
