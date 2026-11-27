"""The watch trace (docs/watches.md, "Trace"): for each active watch, every event
of a type it listens to and what became of it, stored (``watch_trace``) so a
watch that never fires can be understood later, and past checks can improve the
compiler and its filters.

Volume: only watches that listen to the event's type are traced (others are
only counted by the pipeline); Home Assistant events arrive pre-narrowed by
the interest hints (``interest.py``). Past :data:`PER_HOUR` rows in one hour
for one watch, only near misses (at least one condition held) and fires are
stored; the rest are counted and written as one ``unstored`` row when that
watch's next hour starts. The daily prune keeps 30 days and at most 2000 rows
per watch (``store.prune_trace``).
"""

import datetime as dt
import logging
from typing import Any, Dict, List, Optional

from core import clock
from core.users import User
from addons.watches import store

logger = logging.getLogger(__name__)

PER_HOUR = 200
OUTCOMES = ("drop", "during", "outside_window", "semantic_no", "semantic_yes", "semantic_failed", "fired",
            "cooldown", "skipped", "unstored")

# Per watch id: [the hour ("YYYY-MM-DD HH"), rows stored in it, rows not stored in it].
_rate: Dict[str, List[Any]] = {}


def keep(rows: List[Dict[str, Any]], now: Optional[dt.datetime] = None) -> List[Dict[str, Any]]:
    """The rows to store now (the hourly cap applied), with an ``unstored`` row for a watch's previous hour when it
    dropped some."""
    hour = (now or clock.now()).strftime("%Y-%m-%d %H")
    out: List[Dict[str, Any]] = []
    for r in rows:
        wid = r["watch_id"]
        st = _rate.get(wid)
        if st is None or st[0] != hour:
            if st is not None and st[2]:
                out.append({"watch_id": wid, "outcome": "unstored", "event_type": r.get("event_type") or "",
                            "summary": f"{st[2]} more checks not stored ({st[0]}:00, over {PER_HOUR} an hour)",
                            "reason": "far misses over the hourly cap", "data": {}})
            st = _rate[wid] = [hour, 0, 0]
        if st[1] >= PER_HOUR and not (r.get("near") or r.get("outcome") == "fired"):
            st[2] += 1
            continue
        st[1] += 1
        out.append(r)
    return out


def unstored(watch_id: str) -> int:
    """How many of this hour's checks of the watch were not stored (over the cap)."""
    st = _rate.get(watch_id)
    return int(st[2]) if st and st[0] == clock.now().strftime("%Y-%m-%d %H") else 0


def record(user: User, rows: List[Dict[str, Any]]) -> None:
    """Store one event's trace rows for ``user`` (never raises: tracing must not stop a watch)."""
    try:
        store.add_trace(user, keep(rows))
    except Exception:
        logger.exception(f"Watch trace not stored for user {user.id}")


def reset() -> None:
    """Forget the hourly counters (tests)."""
    _rate.clear()
