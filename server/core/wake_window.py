"""Each user's wake window: the hours their morning wake is looked for
(docs/people.md, "Wake detection"; docs/signals.md).

The window is the one the user set (``PUT /api/profile {"wake_window":
{"start", "end"}}``), else the one learned from their first mornings, else
:data:`DEFAULT` (05:00–12:00). Learning: the first unlock of each day between
:data:`LEARN_FROM` and :data:`LEARN_UNTIL` is kept for the first
:data:`LEARN_DAYS` mornings; then the window runs from a little before the
second-earliest of them to a while after the second-latest (rounded to half
hours, at least :data:`MIN_HOURS` long), and stays. Setting ``null`` goes back
to the learned one. Stored in ``wake_windows`` (one row per user).
"""

import datetime
import json
import re
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

from core.db import get_db_connection
from core.users import User

DEFAULT = ("05:00", "12:00")
LEARN_FROM = datetime.time(3, 0)
LEARN_UNTIL = datetime.time(15, 0)
LEARN_DAYS = 7
BEFORE_MINUTES = 90
AFTER_MINUTES = 150
MIN_HOURS = 4
HHMM = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


@dataclass(frozen=True)
class Window:
    """A wake window: ``start`` ≤ time < ``end`` (``HH:MM``, same day)."""
    start: str
    end: str
    learned: bool

    def contains(self, t: datetime.time) -> bool:
        return _minutes(self.start) <= t.hour * 60 + t.minute < _minutes(self.end)

    @property
    def end_time(self) -> datetime.time:
        return datetime.time(*map(int, self.end.split(":")))

    def to_json(self) -> Dict[str, Any]:
        return {"start": self.start, "end": self.end, "learned": self.learned}


def _minutes(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def _hhmm(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def _row(user: User) -> Optional[Dict[str, Any]]:
    with get_db_connection() as conn:
        r = conn.execute("SELECT * FROM wake_windows WHERE user_id = ?", (user.id,)).fetchone()
    return dict(r) if r else None


def get(user: User) -> Window:
    """``user``'s wake window: set, else learned, else the default."""
    r = _row(user)
    if r and r["set_start"] and r["set_end"]:
        return Window(r["set_start"], r["set_end"], False)
    if r and r["learned_start"] and r["learned_end"]:
        return Window(r["learned_start"], r["learned_end"], True)
    return Window(DEFAULT[0], DEFAULT[1], False)


def check(value: Any) -> Tuple[str, str]:
    """``{"start": "HH:MM", "end": "HH:MM"}`` as (start, end), start before end.

    Raises:
        ValueError: not that shape.
    """
    if not isinstance(value, dict):
        raise ValueError('{"start": "HH:MM", "end": "HH:MM"} or null')
    start, end = value.get("start"), value.get("end")
    if not isinstance(start, str) or not isinstance(end, str) or not HHMM.match(start) or not HHMM.match(end):
        raise ValueError("start and end as HH:MM (00:00 to 23:59)")
    if _minutes(start) >= _minutes(end):
        raise ValueError("start must be before end, on the same day")
    return start, end


def set_window(user: User, value: Optional[Dict[str, Any]]) -> Window:
    """Set ``user``'s window (``None``: back to the learned one).

    Raises:
        ValueError: a malformed window.
    """
    start, end = check(value) if value is not None else (None, None)
    with get_db_connection() as conn:
        conn.execute("INSERT INTO wake_windows (user_id, set_start, set_end, updated_at) VALUES (?, ?, ?, ?) "
                     "ON CONFLICT(user_id) DO UPDATE SET set_start = excluded.set_start, set_end = excluded.set_end, "
                     "updated_at = excluded.updated_at", (user.id, start, end, _now()))
        conn.commit()
    return get(user)


def learn(mornings: Dict[str, str]) -> Tuple[str, str]:
    """The window learned from first unlocks ``{date: "HH:MM"}``: from
    :data:`BEFORE_MINUTES` before the second-earliest to :data:`AFTER_MINUTES`
    after the second-latest, on half hours, inside 03:00–15:00, at least
    :data:`MIN_HOURS` long."""
    times = sorted(_minutes(t) for t in mornings.values())
    low = times[1] if len(times) > 2 else times[0]
    high = times[-2] if len(times) > 2 else times[-1]
    lo_bound, hi_bound = LEARN_FROM.hour * 60, LEARN_UNTIL.hour * 60
    start = max(lo_bound, (low - BEFORE_MINUTES) // 30 * 30)
    end = min(hi_bound, -(-(high + AFTER_MINUTES) // 30) * 30)
    if end - start < MIN_HOURS * 60:
        end = min(hi_bound, start + MIN_HOURS * 60)
        start = min(start, end - MIN_HOURS * 60)
    return _hhmm(start), _hhmm(end)


def note_unlock(user: User, local: datetime.datetime) -> None:
    """An unlock of ``user``'s phone at ``local`` (their time): kept as that
    day's first morning unlock while the window is still being learned."""
    t = local.time()
    if not (LEARN_FROM <= t < LEARN_UNTIL):
        return
    r = _row(user)
    if r and r["learned_start"]:
        return
    mornings: Dict[str, str] = json.loads(r["mornings"]) if r else {}
    day, hhmm = local.date().isoformat(), f"{t.hour:02d}:{t.minute:02d}"
    if day in mornings and mornings[day] <= hhmm:
        return
    if day not in mornings and len(mornings) >= LEARN_DAYS:
        return
    mornings[day] = hhmm
    learned = learn(mornings) if len(mornings) >= LEARN_DAYS else (None, None)
    with get_db_connection() as conn:
        conn.execute("INSERT INTO wake_windows (user_id, learned_start, learned_end, mornings, updated_at) "
                     "VALUES (?, ?, ?, ?, ?) ON CONFLICT(user_id) DO UPDATE SET "
                     "learned_start = excluded.learned_start, learned_end = excluded.learned_end, "
                     "mornings = excluded.mornings, updated_at = excluded.updated_at",
                     (user.id, learned[0], learned[1], json.dumps(mornings, sort_keys=True), _now()))
        conn.commit()
