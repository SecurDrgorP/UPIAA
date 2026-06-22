"""
ExecutionManager — Runs workflow executions as background asyncio tasks.

Decouples execution from WebSocket lifecycle so workflows keep running
even if the client disconnects.  Stores logs in memory for later retrieval.

Supports "published" (active) workflows that keep running continuously.
"""

import asyncio
import logging
import os
import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

from core.schemas import WorkflowSchema, ExecutionStatus
from core.orchestrator import WorkflowOrchestrator

logger = logging.getLogger(__name__)


class ExecState(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    ERROR = "error"
    STOPPED = "stopped"


class Execution:
    """Represents a single workflow execution."""

    __slots__ = (
        "id", "workflow_id", "workflow_name", "state",
        "logs", "context", "started_at", "finished_at",
        "run_count", "is_published",
        "_task", "_orchestrator",
    )

    def __init__(self, exec_id: str, workflow: WorkflowSchema, is_published: bool = False):
        self.id = exec_id
        self.workflow_id = workflow.id
        self.workflow_name = workflow.name
        self.state: ExecState = ExecState.PENDING
        self.logs: list[dict] = []
        self.context: dict[str, Any] = {}
        self.started_at: Optional[str] = None
        self.finished_at: Optional[str] = None
        self.run_count: int = 0
        self.is_published: bool = is_published
        self._task: Optional[asyncio.Task] = None
        self._orchestrator: Optional[WorkflowOrchestrator] = None

    def to_summary(self) -> dict:
        return {
            "id": self.id,
            "workflow_id": self.workflow_id,
            "workflow_name": self.workflow_name,
            "state": self.state.value,
            "node_count": len([l for l in self.logs if l.get("node_id") not in ("engine", "__done__")]),
            "log_count": len(self.logs),
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "run_count": self.run_count,
            "is_published": self.is_published,
        }

    def to_detail(self) -> dict:
        return {
            **self.to_summary(),
            "logs": self.logs,
        }


class ExecutionManager:
    """
    Manages background workflow executions.
    Each execution runs as an asyncio task that persists independently of
    any WebSocket or HTTP connection.

    "Published" workflows run continuously — after each completion they
    automatically restart (with a short cooldown) until unpublished.
    """

    PUBLISH_COOLDOWN_SECONDS: int = int(os.getenv("PUBLISH_COOLDOWN_SECONDS", "10"))
    MAX_COMPLETED_EXECUTIONS = 200  # Evict oldest completed entries beyond this cap

    def __init__(self, ollama: Any = None):
        self._executions: dict[str, Execution] = {}
        self._ollama = ollama
        self._subscribers: dict[str, set[asyncio.Queue]] = {}
        # Published workflows: workflow_id -> exec_id
        self._published: dict[str, str] = {}

    def start(self, workflow: WorkflowSchema, published: bool = False) -> str:
        """Start a workflow execution in the background. Returns exec_id."""
        exec_id = f"exec_{uuid.uuid4().hex[:12]}"
        execution = Execution(exec_id, workflow, is_published=published)
        self._executions[exec_id] = execution

        orch = WorkflowOrchestrator(ollama=self._ollama)
        execution._orchestrator = orch

        if published:
            self._published[workflow.id] = exec_id
            execution._task = asyncio.create_task(
                self._run_published(exec_id, workflow)
            )
            logger.info(f"Published workflow '{workflow.name}' as {exec_id}")
        else:
            execution._task = asyncio.create_task(
                self._run(exec_id, orch, workflow)
            )
            logger.info(f"Started execution {exec_id} for workflow '{workflow.name}'")

        return exec_id

    def _evict_old(self) -> None:
        """
        Drop oldest completed executions that exceed MAX_COMPLETED_EXECUTIONS.
        Running and published executions are never evicted.
        """
        terminal_states = {ExecState.SUCCESS, ExecState.ERROR, ExecState.STOPPED}
        completed = [
            (exec_id, e)
            for exec_id, e in self._executions.items()
            if e.state in terminal_states and not e.is_published
        ]
        if len(completed) <= self.MAX_COMPLETED_EXECUTIONS:
            return
        # Sort oldest-first by finished_at (None sorts last)
        completed.sort(key=lambda t: t[1].finished_at or "")
        to_evict = completed[: len(completed) - self.MAX_COMPLETED_EXECUTIONS]
        for exec_id, _ in to_evict:
            self._executions.pop(exec_id, None)
            self._subscribers.pop(exec_id, None)
            logger.debug(f"Evicted old execution {exec_id} from memory")

    async def _run(
        self,
        exec_id: str,
        orch: WorkflowOrchestrator,
        workflow: WorkflowSchema,
    ):
        """Background coroutine that runs the workflow once."""
        execution = self._executions[exec_id]
        execution.state = ExecState.RUNNING
        execution.started_at = datetime.now(timezone.utc).isoformat()
        execution.run_count += 1

        async def collect_and_broadcast(status: ExecutionStatus):
            log_entry = status.model_dump()
            execution.logs.append(log_entry)
            queues = self._subscribers.get(exec_id, set())
            for q in list(queues):
                try:
                    q.put_nowait(log_entry)
                except asyncio.QueueFull:
                    pass

        try:
            ctx = await orch.execute(workflow, collect_and_broadcast)
            execution.context = ctx or {}

            if orch.is_cancelled:
                execution.state = ExecState.STOPPED
            else:
                execution.state = ExecState.SUCCESS
        except Exception as e:
            logger.error(f"Execution {exec_id} failed: {e}")
            execution.state = ExecState.ERROR
            log_entry = {
                "node_id": "engine",
                "node_label": "Engine",
                "status": "failed",
                "error_type": type(e).__name__,
                "error": str(e),
                "message": str(e),
            }
            execution.logs.append(log_entry)
            for q in list(self._subscribers.get(exec_id, set())):
                try:
                    q.put_nowait(log_entry)
                except asyncio.QueueFull:
                    pass
        finally:
            execution.finished_at = datetime.now(timezone.utc).isoformat()
            queues = self._subscribers.get(exec_id, set())
            for q in list(queues):
                q.put_nowait(None)
            self._evict_old()

    async def _run_published(
        self,
        exec_id: str,
        workflow: WorkflowSchema,
    ):
        """
        Continuously run a published workflow.
        After each round completes, waits a cooldown and starts again.
        Stops only when unpublished or stopped.
        """
        execution = self._executions[exec_id]
        execution.state = ExecState.RUNNING
        execution.started_at = datetime.now(timezone.utc).isoformat()

        async def collect_and_broadcast(status: ExecutionStatus):
            log_entry = status.model_dump()
            execution.logs.append(log_entry)
            queues = self._subscribers.get(exec_id, set())
            for q in list(queues):
                try:
                    q.put_nowait(log_entry)
                except asyncio.QueueFull:
                    pass

        try:
            while execution.is_published:
                execution.run_count += 1
                run_num = execution.run_count

                # Notify round start
                round_start = {
                    "node_id": "engine",
                    "node_label": f"Published Run #{run_num}",
                    "status": "running",
                }
                execution.logs.append(round_start)
                queues = self._subscribers.get(exec_id, set())
                for q in list(queues):
                    try:
                        q.put_nowait(round_start)
                    except asyncio.QueueFull:
                        pass

                # Create fresh orchestrator per round
                orch = WorkflowOrchestrator(ollama=self._ollama)
                execution._orchestrator = orch

                try:
                    ctx = await orch.execute(
                        workflow,
                        collect_and_broadcast,
                        initial_context=execution.context or {},
                    )
                    execution.context = ctx or {}

                    if orch.is_cancelled:
                        execution.state = ExecState.STOPPED
                        break

                    # Check if any node returned an error (orchestrator early return)
                    has_error = any(
                        isinstance(v, dict) and v.get("error")
                        for v in (ctx or {}).values()
                    )
                    if has_error:
                        execution.state = ExecState.ERROR
                        execution.is_published = False
                        break
                except Exception as e:
                    logger.error(f"Published run #{run_num} failed: {e}")
                    log_entry = {
                        "node_id": "engine",
                        "node_label": f"Run #{run_num} Error",
                        "status": "failed",
                        "error_type": type(e).__name__,
                        "error": str(e),
                        "message": str(e),
                    }
                    execution.logs.append(log_entry)
                    for q in list(self._subscribers.get(exec_id, set())):
                        try:
                            q.put_nowait(log_entry)
                        except asyncio.QueueFull:
                            pass
                    execution.state = ExecState.ERROR
                    execution.is_published = False
                    break
                if not execution.is_published:
                    break

                # Notify round complete
                round_done = {
                    "node_id": "engine",
                    "node_label": f"Run #{run_num} Complete",
                    "status": "success",
                    "result": {"run": run_num, "next_in": self.PUBLISH_COOLDOWN_SECONDS},
                }
                execution.logs.append(round_done)
                for q in list(self._subscribers.get(exec_id, set())):
                    try:
                        q.put_nowait(round_done)
                    except asyncio.QueueFull:
                        pass

                # Cooldown before next round
                await asyncio.sleep(self.PUBLISH_COOLDOWN_SECONDS)

            if execution.state == ExecState.RUNNING:
                execution.state = ExecState.STOPPED
        finally:
            execution.finished_at = datetime.now(timezone.utc).isoformat()
            execution.is_published = False
            self._published.pop(workflow.id, None)
            queues = self._subscribers.get(exec_id, set())
            for q in list(queues):
                q.put_nowait(None)
            self._evict_old()

    def unpublish(self, exec_id: str) -> bool:
        """Stop a published workflow after the current round finishes."""
        execution = self._executions.get(exec_id)
        if not execution:
            return False
        execution.is_published = False
        if execution._orchestrator:
            execution._orchestrator.request_stop()
        return True

    def stop(self, exec_id: str) -> bool:
        """Stop a running execution immediately."""
        execution = self._executions.get(exec_id)
        if not execution or not execution._orchestrator:
            return False
        if execution.state != ExecState.RUNNING:
            return False
        execution.is_published = False
        execution._orchestrator.request_stop()
        return True

    def get_published(self, workflow_id: str) -> Optional[str]:
        """Get the exec_id of a published workflow, if any."""
        return self._published.get(workflow_id)

    def get(self, exec_id: str) -> Optional[Execution]:
        return self._executions.get(exec_id)

    def list_all(self) -> list[dict]:
        execs = sorted(
            self._executions.values(),
            key=lambda e: e.started_at or "",
            reverse=True,
        )
        return [e.to_summary() for e in execs]

    def subscribe(self, exec_id: str) -> Optional[asyncio.Queue]:
        if exec_id not in self._executions:
            return None
        q: asyncio.Queue = asyncio.Queue(maxsize=500)
        if exec_id not in self._subscribers:
            self._subscribers[exec_id] = set()
        self._subscribers[exec_id].add(q)
        return q

    def unsubscribe(self, exec_id: str, q: asyncio.Queue):
        queues = self._subscribers.get(exec_id)
        if queues:
            queues.discard(q)
