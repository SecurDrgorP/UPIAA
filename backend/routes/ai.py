"""
AI Routes — HTTP proxy to the systems service.

All AI logic has been moved to the systems/ container.
This route module proxies requests to SYSTEMS_URL.
"""

import logging
import os

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from typing import Any, Dict

logger = logging.getLogger(__name__)

SYSTEMS_URL = os.getenv("SYSTEMS_URL", "http://systems:8001")

router = APIRouter(prefix="/ai", tags=["ai"])


# ── Request model (kept for backward compat) ─────────────────────────────────

class AIBuildRequest(BaseModel):
    prompt: str
    context: Dict[str, Any] = Field(default_factory=dict)


# ── Proxy helpers ────────────────────────────────────────────────────────────

async def _proxy_post(path: str, body: dict):
    """Proxy a POST request to the systems service."""
    async with httpx.AsyncClient(timeout=600) as client:
        resp = await client.post(f"{SYSTEMS_URL}{path}", json=body)
        resp.raise_for_status()
        return resp.json()


async def _proxy_stream(path: str, body: dict):
    """Proxy a streaming POST request to the systems service."""
    async with httpx.AsyncClient(timeout=300) as client:
        async with client.stream("POST", f"{SYSTEMS_URL}{path}", json=body) as resp:
            async for chunk in resp.aiter_bytes():
                yield chunk


# ── Routes ───────────────────────────────────────────────────────────────────

@router.post("/chat")
async def ai_chat(request: AIBuildRequest):
    """Hermies conversational endpoint — proxied to System 3."""
    try:
        return await _proxy_post("/system3/chat", request.model_dump())
    except httpx.HTTPStatusError as e:
        logger.error(f"Systems service error on /system3/chat: {e}")
        return {"explanation": f"Systems service error: {e.response.status_code}", "confidence": 0.0}
    except httpx.ConnectError:
        logger.error("Cannot reach systems service")
        return {"explanation": "AI systems service is currently unavailable.", "confidence": 0.0}


@router.post("/build-workflow")
async def build_workflow(request: AIBuildRequest):
    """System 2 workflow builder — proxied to systems service via SSE stream."""
    return StreamingResponse(
        _proxy_stream("/system2/build-workflow", request.model_dump()),
        media_type="text/event-stream",
    )


@router.post("/build-node")
async def build_node(request: AIBuildRequest):
    """System 1 Node builder — proxied to systems service."""
    try:
        return await _proxy_post("/system1/build-node", request.model_dump())
    except httpx.HTTPStatusError as e:
        logger.error(f"Systems service error on /system1/build-node: {e}")
        return {"error": f"Systems service error: {e.response.status_code}"}
    except httpx.ConnectError:
        logger.error("Cannot reach systems service for build-node")
        return {"error": "AI systems service is currently unavailable."}


@router.post("/build-app")
async def build_app(request: AIBuildRequest):
    """System 1 app builder — proxied to systems service."""
    try:
        return await _proxy_post("/system1/build-app", request.model_dump())
    except httpx.HTTPStatusError as e:
        logger.error(f"Systems service error on /system1/build-app: {e}")
        return {"error": f"Systems service error: {e.response.status_code}"}
    except httpx.ConnectError:
        logger.error("Cannot reach systems service for build-app")
        return {"error": "AI systems service is currently unavailable."}


@router.get("/jobs/{job_id}/stream")
async def proxy_job_stream(job_id: str):
    """Proxy SSE job progress stream from systems service."""
    async def stream():
        async with httpx.AsyncClient(timeout=300) as client:
            async with client.stream("GET", f"{SYSTEMS_URL}/jobs/{job_id}/stream") as resp:
                async for chunk in resp.aiter_bytes():
                    yield chunk
    return StreamingResponse(stream(), media_type="text/event-stream")
