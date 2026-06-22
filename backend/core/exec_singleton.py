"""
exec_singleton.py — Single shared ExecutionManager instance.

Both main.py and routes/executions.py import from here to guarantee they
operate on the same in-memory state.  This eliminates the dual-instance bug
where a workflow started via WebSocket could not be stopped via the REST API.

Usage:
    from core.exec_singleton import exec_manager
"""
from core.execution_manager import ExecutionManager

exec_manager: ExecutionManager = ExecutionManager()
