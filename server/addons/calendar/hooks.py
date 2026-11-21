"""The calendar addon's composer hooks:

- self-check: ``calendar``;
- morning briefing: ``events`` (today's events);
- providers: ``events_today`` (Now screen cache) and ``events_between`` (meeting cards, weekly review).
"""

import asyncio
import datetime
import logging
from typing import Any, Dict, List, Optional

from core import clock
from core.addons import BriefingContext, Check, Section, helpers, store
from core.db import get_db_connection
from core.users import User
from addons.calendar import config, events as calendar_events, today as calendar_today
from addons.calendar.addon import addon

logger = logging.getLogger(__name__)


def _hhmm(iso: Optional[str]) -> str:
    try:
        dt = datetime.datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
        if dt.tzinfo:
            dt = dt.astimezone(clock.get_timezone())
        return dt.strftime("%H:%M")
    except (TypeError, ValueError):
        return ""


@addon.selfcheck("calendar", "Calendar")
def check_calendar(user: User, now: datetime.datetime) -> Check:
    with get_db_connection() as conn:
        m = dict(conn.execute("SELECT * FROM system_marks WHERE key = ?",
                              (calendar_today.mark_key(user.id),)).fetchone() or {})
    ok_at, fail_at = helpers.parse_time(m.get("last_ok_at")), helpers.parse_time(m.get("last_fail_at"))
    if not ok_at and not fail_at:
        return Check(True, "not checked yet")
    if m.get("ok") == 0:
        return Check(False, f"failing since {helpers.ago(fail_at, now) if fail_at else 'never'}", ok_at,
                     title="The calendar can't be read",
                     why="Events are missing from Now and the briefing.",
                     blocks=[{"type": "text", "label": "Error", "text": m.get("detail") or "Unknown error"}])
    return Check(True, f"read {helpers.ago(ok_at, now)}", ok_at)


@addon.briefing("events", default=[], rank=20)
def briefing_events(user: User, ctx: BriefingContext) -> Optional[Section]:
    conn = store.get(user, config.ADDON)
    if conn is None:
        return None
    try:
        start = ctx.now.replace(hour=0, minute=0, second=0, microsecond=0)
        evs = calendar_events.list_events(conn, start, start + datetime.timedelta(days=1))
    except Exception as e:
        logger.info(f"Briefing: calendar unavailable ({e})")
        return None
    out = []
    for e in evs:
        if e["all_day"] and str(e["start"])[:10] != start.date().isoformat():
            continue
        out.append({"title": e["title"], "time": "all day" if e["all_day"] else _hhmm(e["start"]),
                    "end": "" if e["all_day"] else _hhmm(e["end"]), "location": e["location"][:80]})
    points = []
    if out:
        first = out[0]
        more = f" + {len(out) - 1} more" if len(out) > 1 else ""
        points.append(f"{first['time']} {first['title']}{more}")
    return Section(out, points=points)


@addon.provide("events_today")
async def provide_events_today(user: User, wait: float = 1.5) -> Optional[List[Dict[str, Any]]]:
    return await calendar_today.events_today(user, wait)


@addon.provide("events_between")
async def provide_events_between(user: User, start: datetime.datetime, end: datetime.datetime,
                                 limit: int = 50) -> Optional[List[Dict[str, Any]]]:
    conn = store.get(user, config.ADDON)
    if conn is None:
        return None
    return await asyncio.to_thread(calendar_events.list_events, conn, start, end, limit)
