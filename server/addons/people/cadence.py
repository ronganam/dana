"""How often a user talks to each of their people, who is drifting, and the People list.

Contact days are the days with a call or a message (last 365 days); with ≥ 4
contact days (3 gaps) the median gap is the typical gap; ``drifting`` when the
days since last contact exceed ``max(PEOPLE_DRIFT_MIN_DAYS, 2.5 × typical)``.
"""
from __future__ import annotations

import datetime
import re
import sqlite3
import statistics
from typing import Any, Dict, List, Optional

from core import clock
from core.db import get_db_connection
from core.users import User
from addons.people import activity, linking, person_tasks, store
from core.addons.helpers import ago
from addons.people.values import DRIFT_MIN_DAYS, norm_name, norm_phone, upcoming_birthday


def cadence(dates: List[datetime.date], today: Optional[datetime.date] = None) -> Dict[str, Any]:
    """How often we talk and whether it has been unusually long."""
    today = today or clock.now().date()
    if not dates:
        return {"contact_days": 0, "typical_gap_days": None, "days_since": None, "drifting": False}
    gaps = [(b - a).days for a, b in zip(dates, dates[1:]) if (b - a).days > 0]
    typical = statistics.median(gaps) if len(gaps) >= 3 else None
    since = (today - dates[-1]).days
    drifting = bool(typical is not None and since > max(DRIFT_MIN_DAYS, 2.5 * typical))
    return {"contact_days": len(dates), "typical_gap_days": round(typical, 1) if typical is not None else None,
            "days_since": since, "drifting": drifting}


def stats_from(act: Dict[str, Any], days: List[datetime.date]) -> Dict[str, Any]:
    """The ``stats`` block of a person from :func:`activity.last_activity` and their contact days."""
    call_t, calls = act.get("call", (None, 0))
    msg_t, msgs = act.get("message", (None, 0))
    last = max([t for t in (call_t, msg_t) if t], default=None)
    kind = None
    if last:
        kind = "call" if last == call_t else "message"
    return {"calls": calls, "messages": msgs, "last_call_at": call_t.isoformat() if call_t else None,
            "last_message_at": msg_t.isoformat() if msg_t else None,
            "last_contact_at": last.isoformat() if last else None, "last_contact_kind": kind,
            "last_contact_ago": ago(last), **cadence(days)}


def person_stats(conn: sqlite3.Connection, user: User, person_id: str) -> Dict[str, Any]:
    """The ``stats`` block of one of ``user``'s people."""
    act = activity.last_activity(conn, user, person_id).get(person_id, {})
    return stats_from(act, activity.contact_days(conn, user, person_id).get(person_id, []))


def list_people(user: User, *, q: str = "", sort: str = "recent", include_hidden: bool = False,
                limit: int = 300) -> List[Dict[str, Any]]:
    """``user``'s people with stats, open task counts and upcoming birthdays.

    ``sort``: ``recent`` (last contact first), ``name``, ``pinned`` (pinned
    first, then recent) or ``drifting`` (only drifting people). Empty without ``people``."""
    linking.link_pending(user)
    with get_db_connection() as conn:
        everyone = store.rows(conn, user, include_hidden=include_hidden)
        phone_rows = store.phones_by_person(conn, user)
        acts = activity.last_activity(conn, user)
        days = activity.contact_days(conn, user)
        open_tasks = person_tasks.open_task_counts(conn, user, everyone)
    qn = norm_name(q)
    qp = norm_phone(q) if re.search(r"\d{3}", q or "") else ""
    out = []
    for r in everyone:
        if qn and not any(qn in k for k in store.names_of(r)) \
                and not (qp and any(qp in p for p in phone_rows.get(r["id"], []))) \
                and qn not in norm_name(r["relation"]):
            continue
        p = store.to_api(r, phones=phone_rows.get(r["id"], []))
        p["stats"] = stats_from(acts.get(r["id"], {}), days.get(r["id"], []))
        p["open_tasks"] = open_tasks.get(r["id"], 0)
        p["birthday_in_days"] = upcoming_birthday(r["birthday"])
        out.append(p)

    def last(p):
        return p["stats"]["last_contact_at"] or ""
    if sort == "name":
        out.sort(key=lambda p: norm_name(p["name"]))
    elif sort == "drifting":
        out = [p for p in out if p["stats"]["drifting"]]
        out.sort(key=lambda p: (not p["pinned"], -(p["stats"]["days_since"] or 0)))
    elif sort == "pinned":
        out.sort(key=last, reverse=True)
        out = [p for p in out if p["pinned"]] + [p for p in out if not p["pinned"]]
    else:
        out.sort(key=last, reverse=True)
    return out[:limit]
