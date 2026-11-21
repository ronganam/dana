"""The trigger engine: when a task's reminder fires, for whom, and how often
(docs/tasks-and-triggers.md).

Triggers are stored on the task (``tasks.triggers`` JSON): ``time``,
``location`` (``{location, event}``), ``car`` (``enter`` / ``leave``), ``wake``.
A task's triggers fire for the user who created it (``tasks.created_by``),
also when the task is on someone else's shared list, while that user can still
see the task; the reminder goes to them.

Entry points:

- :func:`fire` — "fire the triggers of type T (and target) for user U": called
  by the addon's event handlers (hooks.py) for the Home Assistant watcher's
  ``homeassistant.location.changed`` / ``homeassistant.car.changed`` and the
  wake flow's ``core.user.woke``;
- :func:`check_time_triggers` — one user's due time triggers, run for every
  user with ``tasks`` by the addon's job (hooks.py).

A reminder goes out with ``core.notify.send``: the text to Telegram, the
``reminder`` event to every channel the user has (the phone's push).

Refire / flap / cooldown marks are per user (table ``task_trigger_marks``).
"""

import asyncio
import datetime
import json
import logging
import os
from typing import Any, Dict, List, Optional, Tuple

from core import clock, notify
from core.db import get_db_connection
from core.text import escape_markdown
from core.users import User
from addons.tasks import service

logger = logging.getLogger(__name__)

TRIGGER_TYPES = ("location_arrive", "location_leave", "car_enter", "car_leave", "time", "wake")

_OPPOSITE_TRIGGER = {
    "location_arrive": "location_leave",
    "location_leave": "location_arrive",
    "car_enter": "car_leave",
    "car_leave": "car_enter",
}

_TRIGGER_STYLE = {
    "time": ("⏰", "Time Reminder"),
    "location_arrive": ("📍", "Location Arrival Reminder"),
    "location_leave": ("📍", "Location Departure Reminder"),
    "car_enter": ("🚗", "Car Arrival Reminder"),
    "car_leave": ("🚗", "Car Departure Reminder"),
    "wake": ("☀️", "Wake Up Reminder"),
}


def _trigger_cooldown_minutes() -> int:
    try:
        return max(0, int(os.getenv("TRIGGER_REFIRE_COOLDOWN_MINUTES", "120")))
    except ValueError:
        return 120


def _flap_minutes() -> int:
    """Max leave->return gap still considered GPS jitter (not a real visit)."""
    try:
        return max(0, int(os.getenv("TRIGGER_FLAP_MINUTES", "3")))
    except ValueError:
        return 3


# --- Marks (per user) ------------------------------------------------------------------

def _fire_key(trigger_type: str, uid: str) -> str:
    return f"last_trigger_v2_{trigger_type}_{uid}"


def _event_key(trigger_type: str, target: str) -> str:
    return f"last_event_v2_{trigger_type}_{target or '-'}"


def _parse_mark(value: str) -> Optional[datetime.datetime]:
    try:
        dt = datetime.datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    return dt.replace(tzinfo=datetime.timezone.utc) if dt.tzinfo is None else dt


def _mark_get(user: User, key: str) -> Optional[datetime.datetime]:
    try:
        with get_db_connection() as conn:
            row = conn.execute("SELECT at FROM task_trigger_marks WHERE user_id = ? AND key = ?",
                               (user.id, key)).fetchone()
        return _parse_mark(row["at"]) if row else None
    except Exception as e:
        logger.warning(f"Trigger mark read failed for {key}: {e}")
        return None


def _mark_set_now(user: User, key: str) -> None:
    try:
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        with get_db_connection() as conn:
            conn.execute("INSERT OR REPLACE INTO task_trigger_marks (user_id, key, at) VALUES (?, ?, ?)",
                         (user.id, key, now))
            conn.commit()
    except Exception as e:
        logger.warning(f"Trigger mark write failed for {key}: {e}")


def should_skip_refire(user: User, uid: str, trigger_type: str, target: str) -> bool:
    """True when this firing would be an accidental repeat, not a genuine retrigger.

    Toggle triggers (arrive/leave, car enter/leave) refire only after the
    opposite transition was observed AND the return gap exceeds the flap window:
    - same event twice in a row (double-posted webhook) -> skip
    - leave + return inside the flap window (GPS jitter, never really left) -> skip
    - genuine leave + return, even minutes later -> fire
    Non-toggle triggers (time, wake) keep the plain cooldown window.
    """
    now = datetime.datetime.now(datetime.timezone.utc)
    last_fire = _mark_get(user, _fire_key(trigger_type, uid))
    if last_fire is None:
        return False
    opp = _OPPOSITE_TRIGGER.get(trigger_type)
    if opp is None:
        age_min = (now - last_fire).total_seconds() / 60
        return age_min < _trigger_cooldown_minutes()
    last_opp = _mark_get(user, _event_key(opp, target))
    if last_opp is None or last_opp <= last_fire:
        return True
    gap_min = (now - last_opp).total_seconds() / 60
    return gap_min < _flap_minutes()


def record_transition(user: User, trigger_type: str, action: str = "", target: str = "") -> None:
    """Record a zone / car transition of ``user`` (even with no matching task)
    so the refire logic can tell a genuine leave + return from GPS flapping."""
    _mark_set_now(user, _event_key(trigger_type, target))
    # The refire check looks up the opposite transition by its full type
    # (location_leave, car_enter…): record that key too, or a toggle
    # reminder (arrive / car) could only ever fire once per task.
    if trigger_type in ("location", "car") and action:
        _mark_set_now(user, _event_key(f"{trigger_type}_{action}", target))


def last_fired(user: Optional[User]) -> Dict[str, Tuple[datetime.datetime, str]]:
    """task id → (when its reminder last fired for ``user``, trigger type)."""
    out: Dict[str, Tuple[datetime.datetime, str]] = {}
    if user is None:
        return out
    with get_db_connection() as conn:
        rows = conn.execute("SELECT key, at FROM task_trigger_marks WHERE user_id = ? AND key LIKE 'last_trigger_v2_%'",
                            (user.id,)).fetchall()
    for r in rows:
        rest = r["key"][len("last_trigger_v2_"):]
        ttype = next((t for t in TRIGGER_TYPES if rest.startswith(t + "_")), None)
        at = _parse_mark(r["at"])
        if not ttype or not at:
            continue
        uid = rest[len(ttype) + 1:]
        if uid not in out or out[uid][0] < at:
            out[uid] = (at, ttype)
    return out


def last_transition(prefix: str, user: Optional[User] = None) -> Optional[datetime.datetime]:
    """When the latest ``prefix`` transition (``location``, ``car``) arrived,
    for ``user`` or for anyone (None)."""
    sql = "SELECT MAX(at) AS at FROM task_trigger_marks WHERE key LIKE ?"
    args: List[Any] = [f"last_event_v2_{prefix}%"]
    if user is not None:
        sql += " AND user_id = ?"
        args.append(user.id)
    with get_db_connection() as conn:
        row = conn.execute(sql, args).fetchone()
    return _parse_mark(row["at"]) if row and row["at"] else None


# --- Times -----------------------------------------------------------------------------

def parse_trigger_time(time_str: str) -> Optional[datetime.datetime]:
    """Parse trigger time format string (localized to system/HA timezone if naive)."""
    time_str = time_str.strip()
    tz = clock.get_timezone()
    for fmt in (
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M%z",
        "%Y-%m-%dT%H:%M",
        "%Y-%m-%d %H:%M:%S%z",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M%z",
        "%Y-%m-%d %H:%M",
        "%H:%M:%S",
        "%H:%M",
    ):
        try:
            dt = datetime.datetime.strptime(time_str, fmt)
            if fmt in ("%H:%M:%S", "%H:%M"):
                today = clock.now()
                dt = dt.replace(year=today.year, month=today.month, day=today.day)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=tz)
            return dt
        except ValueError:
            continue

    try:
        dt = datetime.datetime.fromisoformat(time_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=tz)
        return dt
    except ValueError:
        pass

    return None


# --- Firing ------------------------------------------------------------------------------

async def trigger_reminder(item: Dict[str, Any], trigger_type: str, user: User, target: str = "") -> None:
    """Fire one task's reminder for ``user`` (the task's creator): send it
    (Telegram and the phone, ``core.notify.send``) and clean up a time trigger."""
    summary = item.get("title") or item.get("summary", "Untitled Task")
    uid = item.get("id") or item.get("uid")

    # Dedup accidental repeats (a transition seen twice, GPS zone flapping)
    # while still allowing genuine retriggers (leave + come back).
    if uid and should_skip_refire(user, str(uid), trigger_type, target):
        logger.info(f"Skipping reminder for '{summary}' ({trigger_type}): accidental refire, no genuine retrigger.")
        return

    emoji, title = _TRIGGER_STYLE.get(trigger_type, ("🔔", "Reminder"))
    priority = (item.get("priority") or "medium").lower()
    data = {"task_id": str(uid or ""), "task_title": summary, "priority": priority, "trigger_type": trigger_type}
    # The text to Telegram (only for a task with an id), the full-screen
    # reminder to the phone app (the push channel: once queued it counts as
    # sent, a phone that missed the ping fetches it on its next sync).
    text = f"{emoji} *{title}*\n\nRemember to: *{escape_markdown(summary)}*" if uid else ""
    logger.info(f"Sending reminder for '{summary}'...")
    sent = await notify.send(user, notify.Message(text, parse_mode="Markdown", kind="reminder", data=data))
    if not any(sent.values()):
        logger.warning(f"Reminder for '{summary}' wasn't delivered now (queued for the phone's next sync, "
                       f"or no phone / chat)")

    if uid:
        _mark_set_now(user, _fire_key(trigger_type, str(uid)))

        # Strip the time trigger so it doesn't fire again (only time triggers).
        if trigger_type == "time":
            logger.info(f"Removing time trigger from task '{summary}' (UID: {uid}) in local DB...")
            try:
                with get_db_connection() as conn:
                    row = conn.execute("SELECT triggers FROM tasks WHERE id = ?", (uid,)).fetchone()
                    if row:
                        trig = json.loads(row["triggers"] or '{}')
                        trig.pop("time", None)
                        conn.execute("UPDATE tasks SET triggers = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                                     (json.dumps(trig), uid))
                        conn.commit()
            except Exception as e:
                logger.error(f"Failed to clear time trigger in local DB: {e}")


def _open_tasks_of(user: User) -> List[Dict[str, Any]]:
    """Open tasks ``user`` created and can still see (theirs to be reminded of)."""
    where, args = service.visible_sql(user, "")
    with get_db_connection() as conn:
        return [dict(r) for r in conn.execute(
            f"SELECT * FROM tasks WHERE status = 'needs_action' AND created_by = ? AND {where}",
            (user.id, *args)).fetchall()]


def _triggers_of(item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    try:
        trig = json.loads(item.get("triggers") or "{}")
    except Exception:
        return None
    return trig if isinstance(trig, dict) else None


async def fire(user: User, trigger_type: str, *, action: str = "", target: str = "") -> int:
    """Fire ``user``'s triggers of ``trigger_type``: ``location`` (``action``
    ``arrive`` / ``leave`` at zone ``target``), ``car`` (``action`` ``enter`` /
    ``leave``) or ``wake``. A location / car transition is recorded for the
    refire logic first, whether or not a task matches. Reminders of location /
    car tasks are sent in the background, wake reminders one after another.
    Returns how many tasks matched."""
    target = (target or "").strip().lower()
    action = (action or "").strip().lower()
    if trigger_type in ("location", "car"):
        record_transition(user, trigger_type, action, target)
    items = _open_tasks_of(user)
    matched = 0
    for item in items:
        trig = _triggers_of(item)
        if not trig:
            continue
        if trigger_type == "wake":
            if not trig.get("wake"):
                continue
            matched += 1
            logger.info(f"{user.name} is up: firing wake reminder '{item.get('title')}'")
            try:
                await trigger_reminder(item, "wake", user)
            except Exception as e:
                logger.error(f"Wake reminder '{item.get('title')}' failed: {e}", exc_info=True)
            continue
        fired_type = None
        if trigger_type == "location":
            loc = trig.get("location")
            if loc:
                zone = str(loc.get("location") or "").strip().lower()
                event = str(loc.get("event") or "").strip().lower()
                if zone == target and event == action:
                    fired_type = f"location_{event}"
        elif trigger_type == "car":
            car = trig.get("car")
            if car and str(car).strip().lower() == action:
                fired_type = f"car_{action}"
        if fired_type:
            matched += 1
            asyncio.create_task(trigger_reminder(item, fired_type, user, target))
    return matched


async def check_time_triggers(user: User) -> int:
    """Fire ``user``'s time triggers that are due. Returns how many fired."""
    now = clock.now()
    fired = 0
    for item in _open_tasks_of(user):
        trig = _triggers_of(item)
        time_str = trig.get("time") if trig else None
        if not time_str:
            continue
        trigger_dt = parse_trigger_time(time_str)
        if not trigger_dt:
            logger.warning(f"Could not parse trigger time '{time_str}' for task: '{item.get('title')}'")
            continue
        if now >= trigger_dt:
            logger.info(f"Triggering time reminder for task '{item.get('title')}' (due: {trigger_dt}, now: {now})")
            await trigger_reminder(item, "time", user)
            fired += 1
    return fired
