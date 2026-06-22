"""
Health Routes — Service health checks and Ollama status.
"""

import os
import httpx
from fastapi import APIRouter

router = APIRouter(tags=["health"])

OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen3-vl")


@router.get("/")
async def root():
    return {
        "service": "M8M Agentic AI Workflow Engine",
        "version": "1.0.0",
        "status": "running",
        "database": "postgresql",
        "orchestration": "temporal",
    }


@router.get("/api/health")
async def health_check():
    """Deep health check for Docker/load balancer."""
    checks = {"api": "ok"}

    # Check database
    try:
        from core.database import AsyncSessionLocal
        from sqlalchemy import text
        async with AsyncSessionLocal() as session:
            await session.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception as e:
        checks["database"] = f"error: {e}"

    # Check Redis
    try:
        import redis.asyncio as redis_pkg
        redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
        client = redis_pkg.from_url(redis_url)
        await client.ping()
        await client.close()
        checks["redis"] = "ok"
    except Exception as e:
        checks["redis"] = f"error: {e}"

    # Check Temporal
    try:
        from core.temporal_workflows import get_temporal_client
        await get_temporal_client()
        checks["temporal"] = "ok"
    except Exception as e:
        checks["temporal"] = f"error: {e}"

    all_ok = all(v == "ok" for v in checks.values())
    return {"status": "healthy" if all_ok else "degraded", "checks": checks}


@router.get("/api/ollama/status")
async def ollama_status():
    """Pre-flight check: is Ollama reachable and is the model available?"""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{OLLAMA_BASE_URL.rstrip('/')}/api/tags")
            if resp.status_code == 200:
                models = [m["name"] for m in resp.json().get("models", [])]
                available = any(OLLAMA_MODEL in m for m in models)
                return {
                    "ollama_url": OLLAMA_BASE_URL,
                    "configured_model": OLLAMA_MODEL,
                    "reachable": True,
                    "model_available": available,
                    "available_models": models,
                }
            return {
                "reachable": True,
                "model_available": False,
                "error": f"Status {resp.status_code}",
            }
    except Exception as e:
        return {
            "ollama_url": OLLAMA_BASE_URL,
            "configured_model": OLLAMA_MODEL,
            "reachable": False,
            "model_available": False,
            "error": str(e),
        }
