"""
Systems Service — FastAPI app for AI subsystems (System 1–4).

Runs as a separate container on port 8001. All AI logic lives here.
Communicates with the backend via authenticated HTTP calls.
"""

import asyncio
import json
import logging
import os
import uuid

from fastapi import FastAPI
from fastapi.responses import StreamingResponse

logger = logging.getLogger(__name__)

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)


# ─── FastAPI Application ─────────────────────────────────────────────────────

app = FastAPI(
    title="UPIAA Systems Service",
    description="AI subsystems: Node Builder, Workflow Builder, Hermies, Tester",
    version="1.0.0",
)


# ─── SSE Job Event Infrastructure ────────────────────────────────────────────

_job_event_queues: dict[str, list[asyncio.Queue]] = {}


async def push_job_event(
    job_id: str, stage: str, message: str, status: str
) -> None:
    """Push a progress event to all SSE subscribers for a given job."""
    event = {"stage": stage, "message": message, "status": status}
    queues = _job_event_queues.get(job_id, [])
    for q in queues:
        await q.put(event)


@app.get("/jobs/{job_id}/stream")
async def stream_job_events(job_id: str):
    """SSE endpoint for streaming AI generation progress events."""
    queue: asyncio.Queue = asyncio.Queue()

    if job_id not in _job_event_queues:
        _job_event_queues[job_id] = []
    _job_event_queues[job_id].append(queue)

    async def event_generator():
        try:
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=60.0)
                except asyncio.TimeoutError:
                    yield f"data: {json.dumps({'stage': 'keepalive', 'message': '', 'status': 'running'})}\n\n"
                    continue

                yield f"data: {json.dumps(event)}\n\n"

                if event.get("status") in ("done", "error"):
                    break
        finally:
            if job_id in _job_event_queues:
                try:
                    _job_event_queues[job_id].remove(queue)
                except ValueError:
                    pass
                if not _job_event_queues[job_id]:
                    del _job_event_queues[job_id]

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ─── System 1: Node Builder ─────────────────────────────────────────────────

from pydantic import BaseModel, Field
from typing import Any, Dict


class AIBuildRequest(BaseModel):
    prompt: str
    context: Dict[str, Any] = Field(default_factory=dict)


@app.post("/system1/build-node")
async def build_node(request: AIBuildRequest):
    """System 1 node builder — generates a custom node from a prompt."""
    from system1.node_generator import NodeGenerator

    job_id = str(uuid.uuid4())
    _job_event_queues[job_id] = []

    try:
        intent = {"prompt": request.prompt}

        async def _push(stage, message, status="running"):
            await push_job_event(job_id, stage, message, status)

        result = await NodeGenerator.generate(intent=intent, push_fn=_push)

        if result.get("status") == "error":
            return {"error": result.get("error", "Generation failed"), "job_id": job_id}
        result["job_id"] = job_id
        return result
    except Exception as e:
        logger.error(f"Error generating node: {e}")
        return {"error": str(e), "job_id": job_id}


@app.post("/system1/build-app")
async def build_app(request: AIBuildRequest):
    """System 1 app builder — generates a full application from a prompt."""
    try:
        from system1.spec_writer import SpecWriter
        from system1.code_generator import CodeGenerator
        from system1.validator import Validator

        spec = await SpecWriter.write(request.prompt, request.context)
        code = await CodeGenerator.generate(spec)
        validation = await Validator.validate(
            code_or_files={code.filename: code.code},
            node_type=spec.node_type,
        )
        return {
            "spec": spec.model_dump(),
            "code": code.model_dump(),
            "validation": validation,
        }
    except Exception as e:
        logger.error(f"Build app error: {e}")
        return {"error": str(e)}


# ─── System 2: Workflow Builder ──────────────────────────────────────────────

@app.post("/system2/build-workflow")
async def build_workflow(request: AIBuildRequest):
    """System 2 workflow builder — generates a complete workflow from a prompt via SSE."""
    async def event_generator():
        try:
            from system2.intent_parser import IntentParser
            from system2.node_planner import NodePlanner
            from system2.workflow_assembler import WorkflowAssembler
            from system2.reviewer import Reviewer

            q: asyncio.Queue = asyncio.Queue()

            async def run_assembler():
                try:
                    intent = await IntentParser.parse(request.prompt, request.context)
                    plan = await NodePlanner.plan(intent.model_dump())
                    workflow = await WorkflowAssembler.assemble(plan)
                    review = await Reviewer.review(workflow, request.prompt)
                    await q.put({"status": "done", "workflow": workflow, "review": review})
                except Exception as e:
                    logger.error(f"Assembler task error: {e}")
                    await q.put({"status": "error", "message": str(e)})

            asyncio.create_task(run_assembler())
            progress = 0

            while True:
                try:
                    msg = await asyncio.wait_for(q.get(), timeout=2.0)
                    yield f"data: {json.dumps(msg)}\n\n"
                    if msg["status"] in ("done", "error"):
                        break
                except asyncio.TimeoutError:
                    progress += 2
                    yield f"data: {json.dumps({'status': 'thinking', 'progress': progress})}\n\n"

        except Exception as e:
            logger.error(f"Build workflow error: {e}")
            yield f"data: {json.dumps({'status': 'error', 'message': str(e)})}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")


# ─── System 3: Hermies Chat ─────────────────────────────────────────────────

@app.post("/system3/chat")
async def hermies_chat(request: AIBuildRequest):
    """Hermies conversational endpoint with graceful workflow fallback."""
    from system3.hermies import HermiesChat

    try:
        return await HermiesChat.handle(request.prompt, request.context)
    except Exception as e:
        logger.error(f"Hermies chat error: {e}")
        return {"type": "chat", "explanation": f"Error: {str(e)}", "confidence": 0.0}


# ─── Health Check ────────────────────────────────────────────────────────────

@app.get("/health")
async def health_check():
    return {"status": "ok", "service": "systems"}
