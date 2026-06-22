"""
nodes/timer_cron.py
────────────────────────────────────────────────────────────────────────────
Timer / Cron Trigger node for the M8M Agentic AI Workflow Engine.

BEHAVIOR:
  This node BLOCKS execution until the configured schedule condition is met.
  It polls the current time every TIMER_CHECK_INTERVAL_SECONDS seconds and
  only returns (releasing the next node) when the time + recurrence condition
  is satisfied. It never returns matched=False — it waits until matched=True.

TEMPORAL REQUIREMENT:
  Because this node can wait up to 7 days, the Temporal activity that wraps
  it must be configured with:
    - start_to_close_timeout >= timedelta(days=8)
    - heartbeat_timeout       ~= timedelta(minutes=2)
  The handler sends a heartbeat on every poll cycle automatically.

TUNING:
  Adjust TIMER_CHECK_INTERVAL_SECONDS and TIMER_MAX_WAIT_SECONDS below.
"""

import re
import asyncio
import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from core.node_registry import register_action
from core.expressions import resolve_template, resolve_dotpath

# ── Temporal heartbeat (optional — gracefully absent outside Temporal) ────────
try:
    from temporalio import activity as _temporal_activity
    _HAS_TEMPORAL = True
except ImportError:
    _HAS_TEMPORAL = False


# ══════════════════════════════════════════════════════════════════════════════
# TUNING CONSTANTS
# ══════════════════════════════════════════════════════════════════════════════

# How often (seconds) the node wakes up and re-checks the current time.
# 30s means the trigger fires within 30 seconds of the scheduled minute.
# Lower = more responsive. Higher = less CPU / fewer Temporal heartbeats.
TIMER_CHECK_INTERVAL_SECONDS: int = 30

# Hard safety cap. If the timer hasn't fired within this many seconds it gives
# up with a __service_error__. Default: 7 days (covers any weekly schedule).
# Must be strictly less than the Temporal activity start_to_close_timeout.
TIMER_MAX_WAIT_SECONDS: int = 60 * 60 * 24 * 7  # 604 800 s = 7 days


# ══════════════════════════════════════════════════════════════════════════════
# LOOKUP TABLES
# ══════════════════════════════════════════════════════════════════════════════

MONTH_NAME_TO_NUMBER: dict[str, int] = {
    "January": 1,   "February": 2,  "March": 3,    "April": 4,
    "May": 5,       "June": 6,      "July": 7,      "August": 8,
    "September": 9, "October": 10,  "November": 11, "December": 12,
}

WEEKDAY_NAME_TO_NUMBER: dict[str, int] = {
    "Monday": 0, "Tuesday": 1, "Wednesday": 2, "Thursday": 3,
    "Friday": 4, "Saturday": 5, "Sunday": 6,
}


# ══════════════════════════════════════════════════════════════════════════════
# PURE HELPERS (no I/O, fully testable in isolation)
# ══════════════════════════════════════════════════════════════════════════════

def _last_day_of_month(year: int, month: int) -> int:
    """Return the last valid calendar day of the given month/year."""
    if month == 12:
        return 31
    return (datetime.date(year, month + 1, 1) - datetime.timedelta(days=1)).day


def _check_schedule(
    now: datetime.datetime,
    trigger_hour: int,
    trigger_minute: int,
    recurrence: str,
    week_day: str | None,
    month_day: int | None,
    year_day: int | None,
    year_month: str | None,
) -> tuple[bool, str]:
    """
    Check whether `now` satisfies the configured schedule.

    Returns:
        (True,  "Fired: ...")   — schedule condition is met, release execution
        (False, "Waiting: ...") — condition not yet met, keep waiting

    Time window: ±1 minute to absorb execution jitter and polling latency.
    This is a pure function — no side effects, safe to call repeatedly.
    """
    reasons: list[str] = []

    # ── Time check ────────────────────────────────────────────────────────────
    now_total    = now.hour * 60 + now.minute
    target_total = trigger_hour * 60 + trigger_minute
    time_matched = abs(now_total - target_total) <= 1

    if not time_matched:
        # Calculate minutes until trigger to give a useful waiting message
        mins_until = (target_total - now_total) % (24 * 60)
        reasons.append(
            f"current time {now.strftime('%H:%M')} — "
            f"trigger at {trigger_hour:02d}:{trigger_minute:02d} "
            f"({mins_until} min away)"
        )

    # ── Recurrence check ──────────────────────────────────────────────────────
    recurrence_matched = True

    if recurrence == "daily":
        pass  # only the time condition matters

    elif recurrence == "weekly":
        required_num = WEEKDAY_NAME_TO_NUMBER.get(week_day or "", -1)
        if now.weekday() != required_num:
            recurrence_matched = False
            # Show how many days until the required weekday
            days_until = (required_num - now.weekday()) % 7 or 7
            reasons.append(
                f"today is {now.strftime('%A')} — "
                f"trigger requires {week_day} ({days_until} day(s) away)"
            )

    elif recurrence == "monthly":
        last = _last_day_of_month(now.year, now.month)
        effective = min(month_day, last)  # type: ignore[arg-type]
        if now.day != effective:
            recurrence_matched = False
            clamp_note = (
                f" (day {month_day} clamped to {effective} — "
                f"this month has {last} days)"
            ) if effective != month_day else ""
            reasons.append(
                f"today is day {now.day} — "
                f"trigger requires day {effective}{clamp_note}"
            )

    elif recurrence == "yearly":
        req_month_num = MONTH_NAME_TO_NUMBER.get(year_month or "", -1)
        if now.month != req_month_num:
            recurrence_matched = False
            reasons.append(
                f"current month is {now.strftime('%B')} — "
                f"trigger requires {year_month}"
            )
        else:
            last = _last_day_of_month(now.year, req_month_num)
            effective = min(year_day, last)  # type: ignore[arg-type]
            if now.day != effective:
                recurrence_matched = False
                clamp_note = (
                    f" (day {year_day} clamped to {effective})"
                ) if effective != year_day else ""
                reasons.append(
                    f"today is day {now.day} — "
                    f"trigger requires day {effective}{clamp_note}"
                )

    # ── Build result ──────────────────────────────────────────────────────────
    matched = time_matched and recurrence_matched

    if matched:
        reason = (
            f"Fired: {recurrence} trigger at "
            f"{trigger_hour:02d}:{trigger_minute:02d} matched "
            f"{now.strftime('%H:%M on %A %d %B %Y')}."
        )
    else:
        reason = "Waiting: " + " | ".join(reasons) + "."

    return matched, reason


def _send_heartbeat(message: str) -> None:
    """
    Send a Temporal activity heartbeat if running inside a Temporal worker.
    Silently ignored when running outside Temporal (tests, local scripts, etc.).
    Heartbeats prevent Temporal from timing out long-running activities.
    """
    if not _HAS_TEMPORAL or not _temporal_activity.in_activity():
        return
    try:
        _temporal_activity.heartbeat(message)
    except Exception:
        pass  # heartbeat failure is non-fatal — never crash the timer for this


# ══════════════════════════════════════════════════════════════════════════════
# NODE REGISTRATION
# ══════════════════════════════════════════════════════════════════════════════

@register_action(
    type="timer_cron",
    label="Timer / Cron Trigger",
    icon="⏰",
    category="Control Flow",
    description=(
        "Blocks workflow execution and waits until the current time matches "
        "a recurring schedule (daily, weekly, monthly, or yearly), "
        "then passes control to the next node."
    ),
    config_schema={
        "trigger_time": {
            "type": "string",
            "label": "Trigger Time (HH:MM)",
            "required": True,
            "description": "Time of day to trigger in 24-hour format, e.g. 14:30.",
        },
        "recurrence": {
            "type": "select",
            "label": "Recurrence",
            "required": True,
            "options": ["daily", "weekly", "monthly", "yearly"],
            "description": "How often this timer should fire.",
        },
        "week_day": {
            "type": "select",
            "label": "Day of Week",
            "required": False,
            "options": [
                "Monday", "Tuesday", "Wednesday", "Thursday",
                "Friday", "Saturday", "Sunday",
            ],
            "description": "Required when recurrence is 'weekly'.",
            "visibleWhen": {"recurrence": "weekly"},
        },
        "month_day": {
            "type": "number",
            "label": "Day of Month",
            "required": False,
            "min": 1,
            "max": 31,
            "description": "Required when recurrence is 'monthly'. Enter a number 1–31.",
            "visibleWhen": {"recurrence": "monthly"},
        },
        "year_day": {
            "type": "number",
            "label": "Day (for Yearly)",
            "required": False,
            "min": 1,
            "max": 31,
            "description": "Required when recurrence is 'yearly'. Day of the month.",
            "visibleWhen": {"recurrence": "yearly"},
        },
        "year_month": {
            "type": "select",
            "label": "Month (for Yearly)",
            "required": False,
            "options": [
                "January", "February", "March", "April", "May", "June",
                "July", "August", "September", "October", "November", "December",
            ],
            "description": "Required when recurrence is 'yearly'.",
            "visibleWhen": {"recurrence": "yearly"},
        },
        "timezone": {
            "type": "string",
            "label": "Timezone",
            "required": False,
            "description": (
                "IANA timezone string. Defaults to UTC. "
                "Examples: Africa/Casablanca, Europe/Paris, America/New_York."
            ),
        },
        "output_mapping": {
            "type": "code",
            "label": "Output mapping",
            "required": False,
            "description": 'Rename output keys. JSON: {"new_key": "old_key"}',
        },
    },
    is_llm_type=False,
    is_loop_type=False,
)
async def handle_timer_cron(
    config: dict,
    input_data: dict,
    context: dict,
) -> dict:
    """
    Blocking timer node.

    Phase 1 — Validates all config fields. Returns __input_error__ immediately
              if anything is wrong. No waiting occurs if config is invalid.

    Phase 2 — Enters a non-blocking polling loop:
              • Checks if NOW matches the configured schedule via _check_schedule()
              • If YES → returns immediately with matched=True and metadata
              • If NO  → sends a Temporal heartbeat, sleeps TIMER_CHECK_INTERVAL_SECONDS,
                         then checks again
              • If waited > TIMER_MAX_WAIT_SECONDS → returns __service_error__

    This function never returns matched=False. The only return states are:
      - __input_error__: True  (bad config, instant)
      - __service_error__: True (safety cap exceeded)
      - matched: True          (schedule fired, execution continues)
    """

    # ══════════════════════════════════════════════════════════════════════════
    # PHASE 1 — VALIDATION (runs once before the wait loop)
    # ══════════════════════════════════════════════════════════════════════════

    # trigger_time
    trigger_time_raw = (config.get("trigger_time") or "").strip()
    if not trigger_time_raw:
        return {"error": "missing field: trigger_time", "__input_error__": True}
    if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", trigger_time_raw):
        return {
            "error": (
                f"invalid trigger_time '{trigger_time_raw}': "
                "must be HH:MM in 24-hour format (e.g. 14:30)"
            ),
            "__input_error__": True,
        }
    trigger_hour   = int(trigger_time_raw[:2])
    trigger_minute = int(trigger_time_raw[3:])

    # recurrence
    recurrence = (config.get("recurrence") or "").strip().lower()
    if recurrence not in ("daily", "weekly", "monthly", "yearly"):
        return {
            "error": (
                f"invalid recurrence '{recurrence}': "
                "must be one of daily, weekly, monthly, yearly"
            ),
            "__input_error__": True,
        }

    # conditional fields — only validated for the active recurrence mode
    week_day      = (config.get("week_day")   or "").strip() or None
    year_month    = (config.get("year_month") or "").strip() or None
    raw_month_day = config.get("month_day")
    raw_year_day  = config.get("year_day")
    month_day: int | None = None
    year_day:  int | None = None

    if recurrence == "weekly":
        if not week_day or week_day not in WEEKDAY_NAME_TO_NUMBER:
            return {
                "error": (
                    "missing or invalid field: week_day is required when "
                    f"recurrence is 'weekly'. Got: '{week_day}'. "
                    f"Must be one of: {list(WEEKDAY_NAME_TO_NUMBER.keys())}"
                ),
                "__input_error__": True,
            }

    elif recurrence == "monthly":
        if raw_month_day is None or str(raw_month_day).strip() in ("", "0", "null", "none"):
            return {
                "error": (
                    "missing field: month_day is required when "
                    "recurrence is 'monthly' (valid range: 1–31)"
                ),
                "__input_error__": True,
            }
        try:
            month_day = int(raw_month_day)
        except (TypeError, ValueError):
            return {
                "error": f"invalid field: month_day must be a number, got '{raw_month_day}'",
                "__input_error__": True,
            }
        if not (1 <= month_day <= 31):
            return {
                "error": f"invalid field: month_day must be between 1 and 31, got {month_day}",
                "__input_error__": True,
            }

    elif recurrence == "yearly":
        if raw_year_day is None or str(raw_year_day).strip() in ("", "0", "null", "none"):
            return {
                "error": (
                    "missing field: year_day is required when "
                    "recurrence is 'yearly' (valid range: 1–31)"
                ),
                "__input_error__": True,
            }
        if not year_month or year_month not in MONTH_NAME_TO_NUMBER:
            return {
                "error": (
                    "missing or invalid field: year_month is required when "
                    f"recurrence is 'yearly'. Got: '{year_month}'. "
                    f"Must be one of: {list(MONTH_NAME_TO_NUMBER.keys())}"
                ),
                "__input_error__": True,
            }
        try:
            year_day = int(raw_year_day)
        except (TypeError, ValueError):
            return {
                "error": f"invalid field: year_day must be a number, got '{raw_year_day}'",
                "__input_error__": True,
            }
        if not (1 <= year_day <= 31):
            return {
                "error": f"invalid field: year_day must be between 1 and 31, got {year_day}",
                "__input_error__": True,
            }

    # timezone
    tz_string = (config.get("timezone") or "").strip() or "UTC"
    tz_warning: str | None = None
    try:
        tz = ZoneInfo(tz_string)
    except (ZoneInfoNotFoundError, KeyError):
        tz = ZoneInfo("UTC")
        tz_warning = f"Unknown timezone '{tz_string}', fell back to UTC."

    # ══════════════════════════════════════════════════════════════════════════
    # PHASE 2 — WAIT LOOP
    #
    # Polls every TIMER_CHECK_INTERVAL_SECONDS seconds using asyncio.sleep()
    # which is non-blocking — the event loop remains free during the wait.
    #
    # On each cycle:
    #   1. Get current time in the configured timezone
    #   2. Call _check_schedule() — pure, instant, no I/O
    #   3. If matched → return result dict (execution continues to next node)
    #   4. If not matched → heartbeat + sleep + increment counter + repeat
    #   5. If waited > cap → return service error
    # ══════════════════════════════════════════════════════════════════════════

    waited_seconds: int = 0
    started_at = datetime.datetime.now(tz=tz)

    while True:
        now = datetime.datetime.now(tz=tz)

        matched, reason = _check_schedule(
            now,
            trigger_hour,
            trigger_minute,
            recurrence,
            week_day,
            month_day,
            year_day,
            year_month,
        )

        if matched:
            # ✅ Schedule condition met — build result and release execution
            result: dict = {
                "matched": True,
                "fired_at": now.isoformat(),
                "recurrence": recurrence,
                "trigger_time": trigger_time_raw,
                "timezone": tz_string,
                "reason": reason,
                "waited_seconds": waited_seconds,
                "started_waiting_at": started_at.isoformat(),
            }
            if tz_warning:
                result["tz_warning"] = tz_warning
            return result

        # ── Safety cap check ──────────────────────────────────────────────────
        if waited_seconds >= TIMER_MAX_WAIT_SECONDS:
            return {
                "error": (
                    f"Timer exceeded maximum wait of "
                    f"{TIMER_MAX_WAIT_SECONDS}s "
                    f"({TIMER_MAX_WAIT_SECONDS // 86400} days) without firing. "
                    f"Last check: {reason}"
                ),
                "recurrence": recurrence,
                "trigger_time": trigger_time_raw,
                "waited_seconds": waited_seconds,
                "__service_error__": True,
            }

        # ── Temporal heartbeat ────────────────────────────────────────────────
        # Tells the Temporal server "this activity is still alive".
        # Without this, Temporal cancels the activity after heartbeat_timeout.
        _send_heartbeat(
            f"timer_cron | recurrence={recurrence} "
            f"target={trigger_time_raw} ({tz_string}) | "
            f"now={now.strftime('%H:%M %a %d %b %Y')} | "
            f"waited={waited_seconds}s | "
            f"cap={TIMER_MAX_WAIT_SECONDS}s | "
            f"status: {reason}"
        )

        # ── Non-blocking sleep ────────────────────────────────────────────────
        await asyncio.sleep(TIMER_CHECK_INTERVAL_SECONDS)
        waited_seconds += TIMER_CHECK_INTERVAL_SECONDS


# ══════════════════════════════════════════════════════════════════════════════
# STANDALONE TESTS
# ══════════════════════════════════════════════════════════════════════════════
#
# ⚠️  Tests 1–4 will ACTUALLY WAIT until the configured time.
#     Set trigger_time to 1–2 minutes from NOW when running manually.
#     Test 5 validates error handling and returns instantly.
#
# import asyncio
# from nodes.timer_cron import handle_timer_cron
#
# # ── Test 1: Daily ────────────────────────────────────────────────────────────
# # Set trigger_time to now+1min before running:
# result = asyncio.run(handle_timer_cron(
#     config={
#         "trigger_time": "14:31",   # ← set to now+1 min
#         "recurrence": "daily",
#         "timezone": "Africa/Casablanca",
#     },
#     input_data={},
#     context={},
# ))
# print("Daily:", result)
# assert result["matched"] is True
# assert result["waited_seconds"] >= 0
#
# # ── Test 2: Weekly ───────────────────────────────────────────────────────────
# result = asyncio.run(handle_timer_cron(
#     config={
#         "trigger_time": "09:00",
#         "recurrence": "weekly",
#         "week_day": "Monday",
#         "timezone": "Europe/Paris",
#     },
#     input_data={},
#     context={},
# ))
# print("Weekly:", result)
# assert result["matched"] is True
#
# # ── Test 3: Monthly ──────────────────────────────────────────────────────────
# result = asyncio.run(handle_timer_cron(
#     config={
#         "trigger_time": "08:00",
#         "recurrence": "monthly",
#         "month_day": 17,
#         "timezone": "UTC",
#     },
#     input_data={},
#     context={},
# ))
# print("Monthly:", result)
# assert result["matched"] is True
#
# # ── Test 4: Yearly ───────────────────────────────────────────────────────────
# result = asyncio.run(handle_timer_cron(
#     config={
#         "trigger_time": "00:00",
#         "recurrence": "yearly",
#         "year_day": 1,
#         "year_month": "January",
#         "timezone": "UTC",
#     },
#     input_data={},
#     context={},
# ))
# print("Yearly:", result)
# assert result["matched"] is True
#
# # ── Test 5: Validation errors (all return instantly) ─────────────────────────
# error_cases = [
#     ({},                                                              "empty config"),
#     ({"trigger_time": "25:00", "recurrence": "daily"},               "bad time format"),
#     ({"trigger_time": "09:00", "recurrence": "unknown"},             "bad recurrence"),
#     ({"trigger_time": "09:00", "recurrence": "weekly"},              "missing week_day"),
#     ({"trigger_time": "09:00", "recurrence": "weekly",
#       "week_day": "Funday"},                                          "invalid week_day"),
#     ({"trigger_time": "09:00", "recurrence": "monthly",
#       "month_day": 0},                                               "month_day=0"),
#     ({"trigger_time": "09:00", "recurrence": "monthly",
#       "month_day": 32},                                              "month_day=32"),
#     ({"trigger_time": "09:00", "recurrence": "yearly",
#       "year_day": 1},                                                "missing year_month"),
#     ({"trigger_time": "09:00", "recurrence": "yearly",
#       "year_day": 1, "year_month": "Octember"},                     "invalid year_month"),
#     ({"trigger_time": "09:00", "recurrence": "daily",
#       "timezone": "Mars/Olympus"},                                   "bad timezone falls back"),
# ]
# for cfg, label in error_cases:
#     r = asyncio.run(handle_timer_cron(config=cfg, input_data={}, context={}))
#     if label == "bad timezone falls back":
#         # bad timezone is a warning, not an error — node still waits
#         assert "tz_warning" in r or r.get("matched") is True, f"Expected tz_warning for: {label}"
#     else:
#         assert r.get("__input_error__") is True, f"Expected __input_error__ for: {label}\nGot: {r}"
#     print(f"✓ '{label}': OK")
#
# ──────────────────────────────────────────────────────────────────────────────