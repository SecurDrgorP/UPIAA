"""
Temporal Worker — Production-grade with dual task queues.

TASK QUEUES:
  m8m-workflows    — Standard workflow execution (nodes, loops, control flow)
  ai-activities    — AI-layer operations (Hermies, System1, System2)
"""

import asyncio
import logging
import os
from datetime import timedelta
from typing import Any

from temporalio import activity, workflow
from temporalio.client import Client
from temporalio.worker import Worker
from temporalio.common import RetryPolicy
from temporalio.exceptions import ApplicationError

logger = logging.getLogger(__name__)

# ── Environment ───────────────────────────────────────────────────────────────
TEMPORAL_HOST      = os.getenv("TEMPORAL_HOST", "localhost:7233")
TASK_QUEUE         = "m8m-workflows"
AI_TASK_QUEUE      = "ai-activities"
PUBLISH_COOLDOWN   = int(os.getenv("PUBLISH_COOLDOWN_SECONDS", "10"))
WORKER_CONCURRENCY = int(os.getenv("WORKER_MAX_CONCURRENT_ACTIVITIES", "20"))

# ── Node type sets ────────────────────────────────────────────────────────────
# Nodes that block for extended periods (minutes to days).
# These receive an 8-day start_to_close_timeout, a 2-minute heartbeat_timeout,
# and no automatic retries — a failed trigger should surface as an error, not retry.
_TIMER_NODE_TYPES = {
    "timer_cron",
    "receive_email",
    "discord_receive_messages",   # polls Discord REST API, up to 240 s
    "webhook_trigger",            # blocks until HTTP request arrives, up to 7 days
    "telegram_trigger",           # long-polls Telegram getUpdates indefinitely
}
_LLM_NODE_TYPES   = set()

# ── Retry policies ───────────────────────────────────────────────────────────
_RETRY_PERMANENT_ONLY = RetryPolicy(
    maximum_attempts=1,
    non_retryable_error_types=["ValueError", "BudgetExceededError", "ApplicationError"],
)

_RETRY_TRANSIENT = RetryPolicy(
    maximum_attempts=3,
    initial_interval=timedelta(seconds=2),
    backoff_coefficient=2.0,
    maximum_interval=timedelta(seconds=30),
    non_retryable_error_types=["ValueError", "BudgetExceededError"],
)

_RETRY_NONE = RetryPolicy(maximum_attempts=1)


# ══════════════════════════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def _get_node_timeout(node_type: str) -> timedelta:
    if node_type in _TIMER_NODE_TYPES:
        return timedelta(days=8)
    if node_type in _LLM_NODE_TYPES:
        return timedelta(minutes=10)
    return timedelta(minutes=5)


def _get_node_retry_policy(node_type: str) -> RetryPolicy:
    if node_type in _TIMER_NODE_TYPES:
        return _RETRY_NONE
    return _RETRY_TRANSIENT


def _get_node_heartbeat_timeout(node_type: str) -> timedelta | None:
    if node_type in _TIMER_NODE_TYPES:
        return timedelta(minutes=2)
    return None


def _node_result_has_error(result: dict) -> bool:
    return isinstance(result, dict) and bool(result.get("error"))


def _node_result_is_permanent_error(result: dict) -> bool:
    return _node_result_has_error(result) and bool(result.get("__input_error__"))


# ══════════════════════════════════════════════════════════════════════════════
# ACTIVITIES
# ══════════════════════════════════════════════════════════════════════════════

@activity.defn
async def run_node_activity(payload: dict) -> dict:
    """Execute a single workflow node."""
    from core.schemas import NodeData, WorkflowSchema
    from core.orchestrator import WorkflowOrchestrator

    node = NodeData(**payload["node"])
    wf   = WorkflowSchema(**payload["workflow"])
    context = payload["context"]

    try:
        orchestrator = WorkflowOrchestrator()
        result = await orchestrator._execute_node(node, wf, context)
    except ValueError as e:
        raise ApplicationError(str(e), non_retryable=True) from e
    except Exception as e:
        logger.exception(f"Unexpected error in run_node_activity for node {node.id} ({node.type})")
        raise

    if result.get("__input_error__"):
        raise ApplicationError(
            f"Node {node.id} ({node.type}) config error: {result.get('error', 'unknown')}",
            non_retryable=True,
        )

    return result


@activity.defn
async def trigger_system1(payload: dict) -> dict:
    """Invokes System 1 to generate and deploy a new node type."""
    from ai.system1.spec_writer import SpecWriter
    from ai.system1.code_generator import CodeGenerator
    from ai.system1.validator import Validator
    from ai.system1.deployer import Deployer

    intent = payload["intent"]
    spec = await SpecWriter.write(intent, {})
    
    # Validation / repair loop
    max_attempts = 3
    code = ""
    for attempt in range(max_attempts):
        code = await CodeGenerator.generate(spec)
        validation = await Validator.validate(code, spec)
        if validation.get("valid"):
            break
        spec["errors"] = validation.get("errors", [])
        if attempt == max_attempts - 1:
            raise ApplicationError(f"System1 failed after 3 attempts: {spec['errors']}", non_retryable=True)

    result = await Deployer.deploy(code, spec)
    return {"status": "deployed", "type": result.get("type", "unknown")}

@activity.defn
async def save_execution_to_db(payload: dict) -> None:
    """Persist execution results to PostgreSQL."""
    from sqlalchemy import update
    from core.database import AsyncSessionLocal
    from core.db_models import ExecutionModel
    from datetime import datetime

    finished_at_str = payload.get("finished_at")
    finished_at = datetime.fromisoformat(finished_at_str) if finished_at_str else None

    async with AsyncSessionLocal() as session:
        stmt = (
            update(ExecutionModel)
            .where(ExecutionModel.id == payload["exec_id"])
            .values(
                state=payload["state"],
                logs=payload.get("logs", []),
                context=payload.get("context", {}),
                finished_at=finished_at,
            )
        )
        await session.execute(stmt)
        await session.commit()


@activity.defn
async def mark_workflow_unpublished(workflow_id: str) -> None:
    """Mark a workflow as unpublished in the database."""
    from sqlalchemy import update
    from core.database import AsyncSessionLocal
    from core.db_models import WorkflowModel

    async with AsyncSessionLocal() as session:
        stmt = (
            update(WorkflowModel)
            .where(WorkflowModel.id == workflow_id)
            .values(is_published=False)
        )
        await session.execute(stmt)
        await session.commit()


@activity.defn
async def run_workflow_activity(payload: dict) -> dict:
    """⚠️ DEPRECATED — kept for in-flight workflows during rolling deploys."""
    from core.schemas import WorkflowSchema, ExecutionStatus
    from core.orchestrator import WorkflowOrchestrator

    wf   = WorkflowSchema(**payload["workflow"])
    orch = WorkflowOrchestrator()
    logs: list[dict] = []

    async def collect_status(status: ExecutionStatus):
        logs.append(status.model_dump())

    context = await orch.execute(wf, collect_status)
    has_error = any(
        isinstance(v, dict) and v.get("error")
        for v in (context or {}).values()
    )

    return {"context": context or {}, "logs": logs, "has_error": has_error}


# ══════════════════════════════════════════════════════════════════════════════
# SHARED GRAPH EXECUTION LOGIC
# ══════════════════════════════════════════════════════════════════════════════

async def _execute_graph(
    wf_payload: dict,
    exec_id: str,
    initial_context: dict | None = None,
) -> tuple[dict, bool]:
    """
    Core graph traversal shared by OneTime and Published workflows.
    initial_context seeds nav.context before traversal so trigger nodes
    (e.g. discord_receive_messages) can read {{node_3.last_message_id}}
    from the previous iteration, advancing cursors instead of replaying.
    """
    with workflow.unsafe.imports_passed_through():
        from core.schemas import WorkflowSchema
        from core.graph_navigator import GraphNavigator, SkipNode, ExecuteNode
        from core.node_registry import registry
    wf  = WorkflowSchema(**wf_payload["workflow"])
    nav = GraphNavigator(wf.nodes, wf.edges)

    # Inject execution metadata so trigger nodes (webhook_trigger, telegram_trigger, etc.)
    # can read the workflow_id without coupling to Temporal-specific APIs.
    # Keys are __double_underscored__ so they are excluded from output mapping and DB writes.
    nav.context["__workflow_id__"] = wf.id
    nav.context["__exec_id__"]     = exec_id

    # Seed from previous published-loop run so trigger nodes inherit last_message_id,
    # last_cursor, or any other stateful output from the previous iteration.
    if initial_context:
        for node_id, result in initial_context.items():
            if not node_id.startswith("__"):
                # IMPORTANT: Use direct context assignment, NOT record_result().
                # record_result() also triggers branch resolution (if_else skip logic),
                # which would mark downstream nodes as "already visited" — causing
                # them to be SKIPPED on re-execution.  We only want to seed the
                # data (e.g. last_message_id) so trigger nodes can advance cursors.
                nav.context[node_id] = result

    _LLM_NODE_TYPES.update(
        registry.get_llm_types() if hasattr(registry, "get_llm_types") else set()
    )

    while (action := nav.next()) is not None:
        match action:
            case SkipNode(node_id=node_id, label=label):
                logger.debug(f"Skipping node {node_id} ({label})")

            case ExecuteNode(node=node):
                node_type = node.type
                timeout   = _get_node_timeout(node_type)
                retry     = _get_node_retry_policy(node_type)
                hb        = _get_node_heartbeat_timeout(node_type)

                activity_kwargs: dict[str, Any] = dict(
                    start_to_close_timeout=timeout,
                    retry_policy=retry,
                )
                if hb is not None:
                    activity_kwargs["heartbeat_timeout"] = hb

                result = await workflow.execute_activity(
                    run_node_activity,
                    {
                        "node":     node.dict(),
                        "workflow": wf.dict(),
                        "context":  nav.context,
                    },
                    **activity_kwargs,
                )
                nav.record_result(node.id, result)

                # ── Loop handling ─────────────────────────────────────────
                if node_type in registry.get_loop_types():
                    loop_for = nav.get_for_loop_action(node.id, result)
                    if loop_for:
                        for idx, item in enumerate(loop_for.iterable):
                            loop_ctx = {
                                **nav.context,
                                loop_for.item_variable: item,
                                "__current_item__":  item,
                                "__for_index__":     idx,
                                "__for_total__":     len(loop_for.iterable),
                            }
                            for body_node in loop_for.body_nodes:
                                body_result = await workflow.execute_activity(
                                    run_node_activity,
                                    {
                                        "node":     body_node.dict(),
                                        "workflow": wf.dict(),
                                        "context":  loop_ctx,
                                    },
                                    start_to_close_timeout=_get_node_timeout(body_node.type),
                                    retry_policy=_get_node_retry_policy(body_node.type),
                                )
                                loop_ctx[body_node.id] = body_result
                            for body_node in loop_for.body_nodes:
                                nav.context[body_node.id] = loop_ctx[body_node.id]

                    loop_while = nav.get_while_loop_action(node.id, result)
                    if loop_while:
                        iteration      = 0
                        should_continue = True
                        while should_continue and iteration < loop_while.max_iterations:
                            iteration += 1
                            loop_ctx = {
                                **nav.context,
                                "__while_iteration__": iteration,
                            }
                            for body_node in loop_while.body_nodes:
                                body_result = await workflow.execute_activity(
                                    run_node_activity,
                                    {
                                        "node":     body_node.dict(),
                                        "workflow": wf.dict(),
                                        "context":  loop_ctx,
                                    },
                                    start_to_close_timeout=_get_node_timeout(body_node.type),
                                    retry_policy=_get_node_retry_policy(body_node.type),
                                )
                                loop_ctx[body_node.id] = body_result
                                nav.context[body_node.id] = body_result

                            re_eval = await workflow.execute_activity(
                                run_node_activity,
                                {
                                    "node":     node.dict(),
                                    "workflow": wf.dict(),
                                    "context":  loop_ctx,
                                },
                                start_to_close_timeout=_get_node_timeout(node_type),
                                retry_policy=_get_node_retry_policy(node_type),
                            )
                            nav.context[node.id] = re_eval
                            should_continue = re_eval.get("continue", False)

    has_error = any(_node_result_has_error(v) for v in nav.context.values())
    return nav.context, has_error


# ══════════════════════════════════════════════════════════════════════════════
# WORKFLOWS
# ══════════════════════════════════════════════════════════════════════════════

@workflow.defn
class OneTimeWorkflowExecution:
    """Execute a workflow once from start to finish."""

    @workflow.run
    async def run(self, payload: dict) -> dict:
        try:
            context, has_error = await _execute_graph(payload, payload["exec_id"])
        except Exception as e:
            logger.exception(f"OneTimeWorkflowExecution failed for exec {payload.get('exec_id')}")
            await workflow.execute_activity(
                save_execution_to_db,
                {
                    "exec_id":     payload["exec_id"],
                    "state":       "error",
                    "logs":        [{"error": str(e)}],
                    "context":     {},
                    "finished_at": workflow.now().isoformat(),
                },
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=_RETRY_PERMANENT_ONLY,
            )
            raise

        await workflow.execute_activity(
            save_execution_to_db,
            {
                "exec_id":     payload["exec_id"],
                "state":       "error" if has_error else "success",
                "logs":        [],
                "context":     context,
                "finished_at": workflow.now().isoformat(),
            },
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=_RETRY_TRANSIENT,
        )

        return context


@workflow.defn
class PublishedWorkflowExecution:
    """Execute a workflow continuously until stopped or error."""

    _should_stop: bool = False

    @workflow.signal
    async def stop(self) -> None:
        self._should_stop = True
        logger.info("PublishedWorkflowExecution received stop signal")

    @workflow.run
    async def run(self, payload: dict) -> dict:
        run_count   = 0
        last_context: dict = {}

        while not self._should_stop:
            run_count += 1
            logger.info(f"Published workflow iteration {run_count} starting")

            try:
                context, has_error = await _execute_graph(
                    payload, payload["exec_id"], initial_context=last_context
                )
            except Exception as e:
                logger.exception(
                    f"PublishedWorkflowExecution iteration {run_count} failed "
                    f"for exec {payload.get('exec_id')}"
                )
                await workflow.execute_activity(
                    mark_workflow_unpublished,
                    payload["workflow"]["id"],
                    start_to_close_timeout=timedelta(seconds=30),
                    retry_policy=_RETRY_TRANSIENT,
                )
                await workflow.execute_activity(
                    save_execution_to_db,
                    {
                        "exec_id":     payload["exec_id"],
                        "state":       "error",
                        "logs":        [{"error": str(e), "iteration": run_count}],
                        "context":     last_context,
                        "finished_at": workflow.now().isoformat(),
                    },
                    start_to_close_timeout=timedelta(seconds=30),
                    retry_policy=_RETRY_TRANSIENT,
                )
                break

            last_context = context

            if has_error:
                await workflow.execute_activity(
                    mark_workflow_unpublished,
                    payload["workflow"]["id"],
                    start_to_close_timeout=timedelta(seconds=30),
                    retry_policy=_RETRY_TRANSIENT,
                )
                await workflow.execute_activity(
                    save_execution_to_db,
                    {
                        "exec_id":     payload["exec_id"],
                        "state":       "error",
                        "logs":        [],
                        "context":     context,
                        "finished_at": workflow.now().isoformat(),
                    },
                    start_to_close_timeout=timedelta(seconds=30),
                    retry_policy=_RETRY_TRANSIENT,
                )
                break

            await workflow.execute_activity(
                save_execution_to_db,
                {
                    "exec_id": payload["exec_id"],
                    "state":   "running",
                    "logs":    [],
                    "context": context,
                },
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=_RETRY_TRANSIENT,
            )

            await asyncio.sleep(PUBLISH_COOLDOWN)

        # Graceful stop
        if self._should_stop:
            await workflow.execute_activity(
                mark_workflow_unpublished,
                payload["workflow"]["id"],
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=_RETRY_TRANSIENT,
            )
            await workflow.execute_activity(
                save_execution_to_db,
                {
                    "exec_id":     payload["exec_id"],
                    "state":       "stopped",
                    "logs":        [],
                    "context":     last_context,
                    "finished_at": workflow.now().isoformat(),
                },
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=_RETRY_TRANSIENT,
            )

        return last_context

@workflow.defn
class System1Workflow:
    """Workflow to generate a node via System 1."""

    @workflow.run
    async def run(self, payload: dict) -> dict:
        return await workflow.execute_activity(
            trigger_system1,
            args=[payload],
            task_queue=AI_TASK_QUEUE,
            start_to_close_timeout=timedelta(minutes=15)
        )

# ══════════════════════════════════════════════════════════════════════════════
# CLIENT & WORKER
# ══════════════════════════════════════════════════════════════════════════════

_temporal_client: Client | None = None


async def get_temporal_client() -> Client:
    """Return a cached Temporal client."""
    global _temporal_client
    if _temporal_client is None:
        _temporal_client = await Client.connect(TEMPORAL_HOST)
    return _temporal_client


async def run_worker() -> None:
    """
    Start the Temporal worker with dual task queues.
    Both m8m-workflows and ai-activities are handled.
    """
    client = await Client.connect(TEMPORAL_HOST)

    worker = Worker(
        client,
        task_queue=TASK_QUEUE,
        workflows=[
            OneTimeWorkflowExecution,
            PublishedWorkflowExecution,
            System1Workflow,
        ],
        activities=[
            run_node_activity,
            run_workflow_activity,
            save_execution_to_db,
            mark_workflow_unpublished,
            trigger_system1,
        ],
        max_concurrent_activities=WORKER_CONCURRENCY,
    )

    logger.info(
        f"Temporal worker started | queue='{TASK_QUEUE}' | "
        f"host={TEMPORAL_HOST} | max_concurrent_activities={WORKER_CONCURRENCY}"
    )
    await worker.run()