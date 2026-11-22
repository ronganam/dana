"""The first-run wizard's progress, per user (docs/devices.md, "The wizard").

After setup mode makes the first admin, and on every new user's first sign-in,
the web app opens the wizard (static/setup.js) until the user has finished it.
Its steps are skippable; which ones a user finished or skipped is stored
(table ``user_wizard``, core migration ``m0006_zones_wizard``), so it picks up
where they left off. Users who existed before the wizard have it finished.

A step is *available* to a user when it means something for them: ``model``
only with ``manage_server`` (the LLM key is the server's), ``sample`` only when
the server has the demo endpoint (``POST /api/demo``).
"""

import datetime
import json
from typing import Any, Dict, List, Optional

from core.db import get_db_connection
from core.users import User

STEPS = ("you", "home", "model", "phone", "addons", "sample")
STATUSES = ("done", "skipped")


def _row(user: User) -> Optional[Dict[str, Any]]:
    with get_db_connection() as conn:
        r = conn.execute("SELECT steps, finished_at FROM user_wizard WHERE user_id = ?", (user.id,)).fetchone()
    if r is None:
        return None
    try:
        steps = json.loads(r["steps"] or "{}")
    except ValueError:
        steps = {}
    return {"steps": steps if isinstance(steps, dict) else {}, "finished_at": r["finished_at"]}


def available(user: User, step: str, *, demo: bool) -> bool:
    """Whether ``step`` means something for ``user`` (``demo``: the server has ``POST /api/demo``)."""
    if step == "model":
        return user.level.allows("manage_server")
    if step == "sample":
        return demo
    return step in STEPS


def state(user: User, *, demo: bool) -> Dict[str, Any]:
    """``{steps: [{id, status: pending|done|skipped, available}], finished, finished_at, next}``:
    ``next`` is the first available step not yet done or skipped (None when there is none)."""
    row = _row(user) or {"steps": {}, "finished_at": None}
    steps: List[Dict[str, Any]] = []
    for step in STEPS:
        status = row["steps"].get(step)
        steps.append({"id": step, "status": status if status in STATUSES else "pending",
                      "available": available(user, step, demo=demo)})
    pending = [s["id"] for s in steps if s["available"] and s["status"] == "pending"]
    return {"steps": steps, "finished": row["finished_at"] is not None, "finished_at": row["finished_at"],
            "next": pending[0] if pending else None}


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def mark(user: User, step: str, status: str) -> None:
    """Record that ``user`` finished (``done``) or skipped ``step``.

    Raises:
        ValueError: an unknown step or status.
    """
    if step not in STEPS or status not in STATUSES:
        raise ValueError(f"Unknown step {step!r} or status {status!r}")
    row = _row(user) or {"steps": {}, "finished_at": None}
    row["steps"][step] = status
    with get_db_connection() as conn:
        conn.execute("INSERT INTO user_wizard (user_id, steps) VALUES (?, ?) "
                     "ON CONFLICT(user_id) DO UPDATE SET steps = excluded.steps",
                     (user.id, json.dumps(row["steps"], sort_keys=True)))
        conn.commit()


def finish(user: User) -> None:
    """Record that ``user`` finished the wizard (the steps they did not reach stay pending)."""
    with get_db_connection() as conn:
        conn.execute("INSERT INTO user_wizard (user_id, finished_at) VALUES (?, ?) "
                     "ON CONFLICT(user_id) DO UPDATE SET finished_at = excluded.finished_at", (user.id, _now()))
        conn.commit()
