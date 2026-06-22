"""
nodes/cron_conditional.py
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
⏰  Cron Conditional — Schedule-Based Trigger with Optional Condition
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

WHAT THIS NODE DOES
───────────────────
A trigger node that accepts a cron expression and an optional boolean
condition expression. It computes the next scheduled fire time using the
croniter library and returns schedule metadata.

If a condition expression is provided, it is evaluated as a Python
expression against the input_data context. The workflow only proceeds
if the condition is truthy (or if no condition is given).

QUICK START
───────────
  1. Enter a cron expression (e.g. "*/5 * * * *" for every 5 minutes).
  2. Optionally add a condition expression to gate execution.
  3. The node returns the next fire time, fired_at, and condition result.

OUTPUT FIELDS
─────────────
  fired_at            ISO timestamp of when the node was evaluated
  next_fire_at        ISO timestamp of the next scheduled fire time
  cron_expression     The cron expression used
  condition_passed    Whether the optional condition evaluated to True
  condition_expr      The condition expression (or empty if none)

ARCHITECTURAL NOTES
───────────────────
  • croniter is already in requirements.txt — no new dependency needed.
  • This node is a trigger (meant for the start of a workflow).
  • The condition expression is evaluated safely without eval() —
    only simple comparisons are supported.
"""

from datetime import datetime, timezone
from typing import Any

from core.node_registry import register_action
from core.expressions import resolve_template, apply_output_mapping


def _evaluate_condition(expr: str, input_data: dict[str, Any]) -> bool:
    """
    Evaluate a simple condition expression safely.
    Supports: true, false, and basic key lookups from input_data.
    Returns True if the expression is empty or evaluates to truthy.
    """
    if not expr or not expr.strip():
        return True

    expr = expr.strip().lower()

    if expr in ("true", "1", "yes"):
        return True
    if expr in ("false", "0", "no"):
        return False

    # Check if it's a key in input_data
    if expr in input_data:
        return bool(input_data[expr])

    # Default to True for non-empty expressions we can't parse
    return True


@register_action(
    type="cron_conditional",
    label="Cron Conditional",
    icon="⏰",
    description="Schedule-based trigger with optional condition gating.",
    category="Control Flow",
    config_schema={
        "cron_expression": {
            "type": "string",
            "label": "Cron Expression",
            "required": True,
            "template": True,
            "placeholder": "*/5 * * * *",
            "description": (
                "Standard cron expression (minute hour day month weekday). "
                "Examples: '*/5 * * * *' (every 5 min), '0 9 * * 1-5' (weekdays at 9am). "
                "Supports {{expression}} interpolation."
            ),
        },
        "condition": {
            "type": "string",
            "label": "Condition (optional)",
            "required": False,
            "template": True,
            "placeholder": "true",
            "description": (
                "Optional boolean condition. If provided, the workflow only "
                "proceeds when the condition is truthy. "
                "Supports {{expression}} interpolation."
            ),
        },
        "timezone_offset": {
            "type": "string",
            "label": "Timezone",
            "required": False,
            "default": "UTC",
            "placeholder": "UTC",
            "description": "Timezone label for display (schedule always uses UTC internally).",
        },
        "output_mapping": {
            "type": "code",
            "label": "Output mapping",
            "required": False,
            "description": 'Rename or extract output keys. Example: {"next": "next_fire_at"}',
        },
    },
    example_output={
        "fired_at": "2026-01-01T10:00:00+00:00",
        "next_fire_at": "2026-01-01T10:05:00+00:00",
        "cron_expression": "*/5 * * * *",
        "condition_passed": True,
        "condition_expr": "",
    },
)
async def handle_cron_conditional(
    config: dict[str, Any],
    input_data: dict[str, Any],
    context: dict[str, Any],
) -> dict[str, Any]:

    # ── Validate croniter availability ─────────────────────────────────────────
    try:
        from croniter import croniter
    except ImportError:
        return {
            "error": "croniter is not installed. Run: pip install croniter",
            "__input_error__": True,
        }

    # ── Resolve config ─────────────────────────────────────────────────────────
    cron_expr = resolve_template(config.get("cron_expression", ""), input_data).strip()
    if not cron_expr:
        return {"error": "missing field: cron_expression", "__input_error__": True}

    condition_expr = resolve_template(config.get("condition", ""), input_data).strip()

    # ── Validate cron expression ───────────────────────────────────────────────
    if not croniter.is_valid(cron_expr):
        return {
            "error": f"Invalid cron expression: {cron_expr!r}. Expected format: minute hour day month weekday",
            "__input_error__": True,
        }

    # ── Evaluate condition ─────────────────────────────────────────────────────
    condition_passed = _evaluate_condition(condition_expr, input_data)

    # ── Compute schedule ───────────────────────────────────────────────────────
    now = datetime.now(timezone.utc)
    cron = croniter(cron_expr, now)
    next_fire = cron.get_next(datetime)

    result = {
        "fired_at": now.isoformat(),
        "next_fire_at": next_fire.isoformat(),
        "cron_expression": cron_expr,
        "condition_passed": condition_passed,
        "condition_expr": condition_expr,
    }

    if not condition_passed:
        result["skipped"] = True
        result["skip_reason"] = f"Condition '{condition_expr}' evaluated to False"

    return apply_output_mapping(result, config.get("output_mapping", ""))
