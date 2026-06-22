"""
M8M — Agentic AI Workflow Engine (Backend)
FastAPI application with PostgreSQL, Temporal, Redis, and WebSocket streaming.

Thin dispatch: all route handlers live in backend/routes/*.
This module handles app setup, middleware, lifespan, and WebSocket streaming.
"""

import asyncio
import json
import logging
import os
import time
import uuid

from contextlib import asynccontextmanager
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Depends, Request, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel

from core.database import get_db, init_db
from core.db_models import ExecutionModel
from core.schemas import WorkflowSchema, ExecutionStatus
from core.exec_singleton import exec_manager
from core.json_utils import loads_tolerant, normalize_workflow_payload
from core.temporal_workflows import (
    get_temporal_client,
    PublishedWorkflowExecution,
)

# ── Import all node modules (triggers self-registration) ─────────────────────
import nodes    # noqa: F401
import ai_nodes  # noqa: F401

# ── Route modules ────────────────────────────────────────────────────────────
from routes.workflows import router as workflows_router
from routes.executions import router as executions_router
from routes.nodes import router as nodes_router
from routes.ai import router as ai_router
from routes.secrets import router as secrets_router
from routes.health import router as health_router
from routes.metrics import router as metrics_router
from routes.hermies import router as hermies_router

logger = logging.getLogger(__name__)
WS_KEEPALIVE_SECONDS = int(os.getenv("WS_KEEPALIVE_SECONDS", "15"))

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

# ─── Singleton exec_manager imported from core.exec_singleton ───────────────
# exec_manager is defined in core/exec_singleton.py and shared with routes/executions.py

# ─── Internal Auth ───────────────────────────────────────────────────────────
INTERNAL_SECRET = os.getenv("INTERNAL_SECRET", "change-me-internal")

if INTERNAL_SECRET in ("change-me-internal", ""):
    logger.warning(
        "INTERNAL_SECRET is set to the default insecure value. "
        "Set a strong, unique INTERNAL_SECRET in your .env file before deploying."
    )

def verify_internal(x_internal_token: str = Header(None)):
    if x_internal_token != INTERNAL_SECRET:
        raise HTTPException(status_code=403, detail="Forbidden")

# ─── SSE Job Event Queues ────────────────────────────────────────────────────
# Legacy SSE infrastructure — job event streaming now lives in systems container.
_job_event_queues: dict[str, list[asyncio.Queue]] = {}


# ─── Lifespan ────────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan: startup and shutdown hooks."""
    # Startup
    await init_db()
    logger.info("Database tables initialized")
    yield
    # Shutdown
    logger.info("Shutting down M8M backend")


# ─── FastAPI Application ─────────────────────────────────────────────────────
app = FastAPI(
    title="M8M — Agentic AI Workflow Engine",
    description="Automated Agentic AI Workflow Generator with PostgreSQL, Temporal, and LLM orchestration",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:4200",
        "http://localhost:4000",
        os.getenv("FRONTEND_ORIGIN", "http://localhost:4200"),
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─── Mount route modules ────────────────────────────────────────────────────
app.include_router(health_router)
app.include_router(workflows_router, prefix="/api")
app.include_router(executions_router, prefix="/api")
app.include_router(nodes_router, prefix="/api")
app.include_router(ai_router, prefix="/api")
app.include_router(secrets_router, prefix="/api")
app.include_router(metrics_router, prefix="/api")
app.include_router(hermies_router, prefix="/api")


async def _send_ws_keepalive(websocket: WebSocket, exec_id: str | None) -> None:
    """Keep browser/proxy WebSocket connections warm during long-running nodes."""
    await websocket.send_json({
        "type": "keepalive",
        "status": "keepalive",
        "exec_id": exec_id,
        "timestamp": time.time(),
    })


@app.get("/pipeline/status")
async def pipeline_status_stub():
    """Silence 404 noise from external monitoring tools."""
    return {"status": "ok", "pipeline": "m8m-backend"}

# ─── Webhook Routes (catch-all — must be LAST) ──────────────────────────────
from core.webhook_registry import register_webhook_routes
register_webhook_routes(app)

# ─── Internal: Registry Reload ──────────────────────────────────────────────────────

from core.node_registry import registry as _node_registry


@app.post("/internal/registry/reload", dependencies=[Depends(verify_internal)])
async def reload_registry():
    """Reload all node modules. Called by deployer after writing new node files."""
    try:
        _node_registry.scan_and_load()
        count = len(_node_registry.list_types())
        logger.info(f"Registry reloaded: {count} node types")
        return {"status": "ok", "node_count": count}
    except Exception as e:
        logger.error(f"Registry reload failed: {e}")
        return {"status": "error", "detail": str(e)}


# ── Internal: Test Node Endpoint ───────────────────────────────────────────

class TestNodePayload(BaseModel):
    code: str
    node_type: str


@app.post("/internal/test-node", dependencies=[Depends(verify_internal)])
async def test_node(payload: TestNodePayload):
    """Test a generated node in a subprocess sandbox."""
    import subprocess, sys, tempfile, pathlib
    errors, tmp_node_path, tmp_script_path = [], None, None
    backend_dir = str(pathlib.Path(__file__).parent)

    try:
        ai_nodes_dir = str(pathlib.Path(__file__).parent / "ai_nodes")
        os.makedirs(ai_nodes_dir, exist_ok=True)

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".py", dir=ai_nodes_dir,
            prefix=f"_test_{payload.node_type}_", delete=False,
        ) as f:
            f.write(payload.code)
            tmp_node_path = f.name
            tmp_module = os.path.basename(tmp_node_path)[:-3]

        test_script = f"""
import sys, asyncio, importlib.util
sys.path.insert(0, {repr(backend_dir)})
spec = importlib.util.spec_from_file_location({repr(tmp_module)}, {repr(tmp_node_path)})
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
handler = getattr(mod, "handle_{payload.node_type}", None)
if handler is None:
    print("ERROR: handle_{payload.node_type} not found")
    sys.exit(1)
result = asyncio.run(handler({{}}, {{}}, {{}}))
if not isinstance(result, dict):
    print(f"ERROR: returned {{type(result).__name__}}, expected dict")
    sys.exit(1)
print("OK")
"""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".py", dir="/tmp",
            prefix="_m8m_backendtest_", delete=False,
        ) as f:
            f.write(test_script)
            tmp_script_path = f.name

        proc = subprocess.run(
            [sys.executable, tmp_script_path],
            capture_output=True, text=True, timeout=10, cwd=backend_dir,
        )
        if proc.returncode != 0:
            msg = (proc.stderr or proc.stdout or "Unknown failure").strip()
            errors.append(f"Subprocess test failed: {msg[:500]}")
    except subprocess.TimeoutExpired:
        errors.append("Test timed out after 10 seconds")
    except Exception as e:
        errors.append(f"Test setup failed: {e}")
    finally:
        for p in [tmp_node_path, tmp_script_path]:
            if p:
                try:
                    os.unlink(p)
                except Exception:
                    pass
    return {"passed": len(errors) == 0, "errors": errors}


# ── Internal: Delete AI Node ──────────────────────────────────────────────

@app.delete("/internal/ai-nodes/{node_type}", dependencies=[Depends(verify_internal)])
async def delete_ai_node(node_type: str):
    """Delete an AI-generated node file and remove from registry."""
    import pathlib
    ai_nodes_dir = pathlib.Path(__file__).parent / "ai_nodes"
    file_path = ai_nodes_dir / f"{node_type}.py"
    if not file_path.exists():
        return {"status": "not_found", "node_type": node_type}
    try:
        file_path.unlink()
        if node_type in _node_registry._handlers:
            del _node_registry._handlers[node_type]
        if node_type in _node_registry._catalog:
            del _node_registry._catalog[node_type]
        return {"status": "deleted", "node_type": node_type}
    except Exception as e:
        return {"status": "error", "detail": str(e)}


# ─── SSE: Job Progress Streaming ────────────────────────────────────────────────────

@app.get("/api/v1/jobs/{job_id}/stream")
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
                    # Send keepalive
                    yield f"data: {json.dumps({'stage': 'keepalive', 'message': '', 'status': 'running'})}\n\n"
                    continue

                yield f"data: {json.dumps(event)}\n\n"

                if event.get("status") in ("done", "error"):
                    break
        finally:
            # Cleanup subscriber
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


# ─── AI: Generate Node Endpoint ────────────────────────────────────────────────────

class GenerateNodeRequest(BaseModel):
    prompt: str


@app.post("/api/v1/ai/generate-node")
async def generate_node_endpoint(req: GenerateNodeRequest):
    """
    Start AI node generation. Returns job_id immediately.
    Client connects to /api/v1/jobs/{job_id}/stream for progress.
    NOTE: This is a legacy endpoint. New generation goes through systems service.
    """
    from ai.system1.node_generator import NodeGenerator

    job_id = str(uuid.uuid4())

    # Initialize the event queue for this job
    _job_event_queues[job_id] = []

    async def _push_event(stage: str, message: str, status: str):
        event = {"stage": stage, "message": message, "status": status}
        queues = _job_event_queues.get(job_id, [])
        for q in queues:
            await q.put(event)

    async def _run_generation():
        try:
            await NodeGenerator.generate(
                intent={"prompt": req.prompt},
                job_id=job_id,
            )
        except Exception as e:
            logger.error(f"Node generation failed for job {job_id}: {e}")
            await _push_event("error", str(e), "error")

    asyncio.create_task(_run_generation())

    return {"job_id": job_id}


# ─── WebSocket: Live Streaming ──────────────────────────────────────────────

@app.websocket("/ws/execute")
async def websocket_execute(websocket: WebSocket):
    """
    Start a new execution and stream logs live.
    Execution continues running even if this WebSocket disconnects.
    """
    await websocket.accept()
    exec_id = None
    queue = None
    try:
        raw = await websocket.receive_text()
        data = normalize_workflow_payload(loads_tolerant(raw))
        workflow = WorkflowSchema(**data)

        # Start background execution
        exec_id = exec_manager.start(workflow)
        await websocket.send_json({"exec_id": exec_id, "status": "started"})

        # Subscribe to live log stream
        queue = exec_manager.subscribe(exec_id)
        if not queue:
            await websocket.send_json({"error": "Failed to subscribe"})
            return

        # Listen for stop signals in a separate task
        async def listen_for_commands():
            try:
                while True:
                    msg = await websocket.receive_text()
                    try:
                        cmd = json.loads(msg)
                        if cmd.get("action") == "stop":
                            exec_manager.stop(exec_id)
                            return
                    except json.JSONDecodeError:
                        pass
            except (WebSocketDisconnect, Exception):
                pass

        cmd_task = asyncio.create_task(listen_for_commands())

        # Send past logs
        execution = exec_manager.get(exec_id)
        if execution:
            for past_log in list(execution.logs):
                try:
                    await websocket.send_json(past_log)
                except Exception:
                    break

        # Stream live logs
        last_keepalive = time.monotonic()
        while True:
            try:
                log_entry = await asyncio.wait_for(queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                execution = exec_manager.get(exec_id)
                if execution and execution.state.value not in ("pending", "running"):
                    break
                if time.monotonic() - last_keepalive >= WS_KEEPALIVE_SECONDS:
                    try:
                        await _send_ws_keepalive(websocket, exec_id)
                        last_keepalive = time.monotonic()
                    except Exception:
                        break
                continue

            if log_entry is None:
                break

            try:
                await websocket.send_json(log_entry)
            except Exception:
                break

        cmd_task.cancel()

        # Send completion signal
        execution = exec_manager.get(exec_id)
        state = execution.state.value if execution else "unknown"
        try:
            await websocket.send_json({
                "node_id": "__done__",
                "node_label": "Execution Complete",
                "status": "complete",
                "result": {
                    "message": f"Execution {state}",
                    "exec_id": exec_id,
                    "state": state,
                },
            })
        except Exception:
            pass

    except WebSocketDisconnect:
        pass
    except json.JSONDecodeError:
        try:
            await websocket.send_json({"error": "Invalid JSON payload"})
        except Exception:
            pass
    except Exception as e:
        try:
            await websocket.send_json({"error": str(e)})
        except Exception:
            pass
    finally:
        if exec_id and queue:
            exec_manager.unsubscribe(exec_id, queue)
        try:
            await websocket.close()
        except Exception:
            pass


@app.websocket("/ws/executions/{exec_id}")
async def websocket_stream_execution(
    websocket: WebSocket,
    exec_id: str,
    db: AsyncSession = Depends(get_db),
):
    """
    Reconnect to an already-running execution to stream its logs.
    Useful when the user navigates away and comes back.
    """
    await websocket.accept()
    execution = exec_manager.get(exec_id)

    # If not in memory, try database
    db_state = "unknown"
    db_logs: list = []
    db_exec = None
    if not execution:
        db_exec = await db.get(ExecutionModel, exec_id)
        if db_exec and hasattr(db_exec, "state"):
            db_state = db_exec.state
            db_logs = db_exec.logs or []
        else:
            await websocket.send_json({"error": "Execution not found"})
            await websocket.close()
            return

    queue = None
    try:
        # Send past logs
        logs_to_send = list(execution.logs) if execution else db_logs
        for past_log in logs_to_send:
            await websocket.send_json(past_log)

        is_running = False
        if execution and execution.state.value in ("pending", "running"):
            is_running = True
        elif not execution and db_state in ("pending", "running"):
            is_running = True

        if not is_running:
            final_state = execution.state.value if execution else db_state
            await websocket.send_json({
                "node_id": "__done__",
                "node_label": "Execution Complete",
                "status": "complete",
                "result": {
                    "message": f"Execution {final_state}",
                    "exec_id": exec_id,
                    "state": final_state,
                },
            })
            return

        # Listen for stop
        async def listen_for_commands():
            try:
                while True:
                    msg = await websocket.receive_text()
                    try:
                        cmd = json.loads(msg)
                        if cmd.get("action") == "stop":
                            if execution:
                                exec_manager.stop(exec_id)
                            else:
                                client = await get_temporal_client()
                                try:
                                    handle = client.get_workflow_handle(exec_id)
                                    await handle.signal(PublishedWorkflowExecution.stop)
                                except Exception:
                                    pass
                            return
                    except json.JSONDecodeError:
                        pass
            except (WebSocketDisconnect, Exception):
                pass

        cmd_task = asyncio.create_task(listen_for_commands())

        if execution:
            queue = exec_manager.subscribe(exec_id)
            if not queue:
                cmd_task.cancel()
                return

            last_keepalive = time.monotonic()
            while True:
                try:
                    log_entry = await asyncio.wait_for(queue.get(), timeout=1.0)
                except asyncio.TimeoutError:
                    _exec = exec_manager.get(exec_id)
                    if _exec and _exec.state.value not in ("pending", "running"):
                        break
                    if time.monotonic() - last_keepalive >= WS_KEEPALIVE_SECONDS:
                        try:
                            await _send_ws_keepalive(websocket, exec_id)
                            last_keepalive = time.monotonic()
                        except Exception:
                            break
                    continue

                if log_entry is None:
                    break

                try:
                    await websocket.send_json(log_entry)
                except Exception:
                    break
        else:
            # Context-diff DB poll (Temporal path: logs[] is always empty,
            # but context is updated by save_execution_to_db after each node).
            last_ctx_snapshot: dict = dict(db_exec.context or {})
            last_keepalive = time.monotonic()
            while True:
                await asyncio.sleep(2.0)
                try:
                    await db.refresh(db_exec)
                except Exception:
                    break

                fresh_ctx: dict = db_exec.context or {}
                for node_id, node_result in fresh_ctx.items():
                    if last_ctx_snapshot.get(node_id) != node_result:
                        # Determine status from result dict
                        node_status = "failed" if (isinstance(node_result, dict) and node_result.get("error")) else "success"
                        node_label = (node_result.get("__meta__", {}).get("label") or node_id) if isinstance(node_result, dict) else node_id
                        try:
                            await websocket.send_json({
                                "node_id": node_id,
                                "node_label": node_label,
                                "status": node_status,
                                "result": node_result,
                            })
                        except Exception:
                            break
                last_ctx_snapshot = dict(fresh_ctx)

                if time.monotonic() - last_keepalive >= WS_KEEPALIVE_SECONDS:
                    try:
                        await _send_ws_keepalive(websocket, exec_id)
                        last_keepalive = time.monotonic()
                    except Exception:
                        break

                if db_exec.state not in ("pending", "running"):
                    break

        cmd_task.cancel()

        execution = exec_manager.get(exec_id)
        state = execution.state.value if execution else "unknown"
        try:
            await websocket.send_json({
                "node_id": "__done__",
                "node_label": "Execution Complete",
                "status": "complete",
                "result": {
                    "message": f"Execution {state}",
                    "exec_id": exec_id,
                    "state": state,
                },
            })
        except Exception:
            pass

    except WebSocketDisconnect:
        pass
    except Exception as e:
        try:
            await websocket.send_json({"error": str(e)})
        except Exception:
            pass
    finally:
        if queue:
            exec_manager.unsubscribe(exec_id, queue)
        try:
            await websocket.close()
        except Exception:
            pass
