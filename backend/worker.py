"""
M8M Temporal Worker — Dual task queue entry point with SIGTERM drain.

Runs as a separate container in Docker Compose.
Handles both the standard 'm8m-workflows' queue and the 'ai-activities' queue
for AI-layer operations.
"""

import asyncio
import logging
import signal
import sys
import os

# Add parent dir to path for imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

logger = logging.getLogger(__name__)

# Import all node modules (triggers self-registration)
import nodes     # noqa: F401
import ai_nodes  # noqa: F401

from core.temporal_workflows import run_worker, AI_TASK_QUEUE


_shutdown_event = asyncio.Event()


def _handle_sigterm(*_):
    """SIGTERM handler — set shutdown event for graceful drain."""
    logger.info("SIGTERM received, initiating graceful shutdown...")
    _shutdown_event.set()


async def main():
    """Run the dual-queue Temporal worker with graceful shutdown."""
    signal.signal(signal.SIGTERM, _handle_sigterm)
    signal.signal(signal.SIGINT, _handle_sigterm)

    logger.info("Starting M8M Temporal worker (dual queue)")

    try:
        worker_task = asyncio.create_task(run_worker())
        shutdown_task = asyncio.create_task(_shutdown_event.wait())

        done, pending = await asyncio.wait(
            [worker_task, shutdown_task],
            return_when=asyncio.FIRST_COMPLETED,
        )

        # Cancel pending tasks on shutdown
        for task in pending:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        logger.info("M8M Temporal worker stopped gracefully")

    except Exception as e:
        logger.exception(f"Worker fatal error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
