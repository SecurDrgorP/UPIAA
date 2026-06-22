import asyncio
from typing import Any
from datetime import datetime
import logging
from core.node_registry import register_action
from core.expressions import apply_output_mapping

logger = logging.getLogger(__name__)

@register_action(
    type="timer",
    label="Timer",
    icon="⏰",
    description="Schedule a workflow to run at specific intervals or cron expressions",
    category="Triggers",
    config_schema={
        "schedule_type": {
            "type": "select",
            "label": "Schedule Type",
            "required": True,
            "default": "interval",
            "options": [
                {"value": "interval", "label": "Interval"},
                {"value": "cron", "label": "Cron Expression"},
            ],
            "description": "Choose between a fixed interval or a cron expression.",
        },
        "interval_seconds": {
            "type": "number",
            "label": "Interval Seconds",
            "default": 3600,
            "min": 1,
            "description": "Number of seconds between each trigger.",
            "visibleWhen": {"schedule_type": "interval"},
        },
        "cron_expression": {
            "type": "string",
            "label": "Cron Expression",
            "default": "0 0 * * *",
            "placeholder": "0 0 * * *",
            "description": "Standard cron expression (minute hour day month weekday).",
            "visibleWhen": {"schedule_type": "cron"},
        },
        "output_mapping": {
            "type": "code",
            "label": "Output mapping",
            "required": False,
            "description": "Rename or extract output keys before passing downstream. Example: {\"summary\": \"response\"}",
        },
    },
)
async def handle_timer(config: dict[str, Any], input_data: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    schedule_type = config.get("schedule_type", "interval")
    interval = config.get("interval_seconds", 0)
    
    if schedule_type == "interval" and interval > 0:
        logger.info(f"Timer node waiting for {interval}s")
        await asyncio.sleep(interval)
    
    result = {
        "executed_at": datetime.now().isoformat(),
        "schedule": schedule_type,
        "interval_seconds": interval
    }
    
    return apply_output_mapping(result, config.get("output_mapping", ""))
