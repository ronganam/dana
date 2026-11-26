"""The calendar addon's composer hooks:

- self-check: ``calendar``;
- morning briefing: ``events`` (today's events);
- providers: ``events_today`` (Now screen cache) and ``events_between`` (meeting cards, weekly review);
- the dashboard's Calendar widget (``calendar``: today and tomorrow; drawn by ``web/widget.js``);
- jobs: ``starting`` (``calendar.event.starting`` before each event: event_types.py) and its daily prune.
"""

import asyncio
import datetime
import logging
from typing import Any, Dict, List, Optional

from core import clock
from core.addons import BriefingContext, Check, Section, WidgetContext, helpers, store
from core.db import get_db_connection
from core.users import User
from addons.calendar import config, event_types, events as calendar_events, today as calendar_today
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


@addon.job(every=datetime.timedelta(minutes=1), timeout=50.0)
async def starting(user: User) -> int:
    """Announce the user's events that start in the next few minutes (``calendar.event.starting``)."""
    return await event_types.announce_starting(user)


@addon.job(daily="03:50")
async def prune_starting(user: User) -> int:
    """Forget announced event starts older than 30 days."""
    return event_types.prune(user)


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


@addon.widget("calendar", title="Calendar", script="widget.js", icon="calendar-days", refresh=300,
              sizes=("medium", "small", "large"),
              description="Today's and tomorrow's events from your selected calendars")
def calendar_widget(user: User, ctx: WidgetContext) -> Dict[str, Any]:
    """``days``: today and tomorrow (``{date, label, events}``; an event is
    ``{id, title, start, end, all_day, location, calendar_id}``)."""
    conn = store.require(user, config.ADDON)
    start = ctx.now.replace(hour=0, minute=0, second=0, microsecond=0)
    found = calendar_events.list_events(conn, start, start + datetime.timedelta(days=2), limit=60)
    days = []
    for offset, label in ((0, "Today"), (1, "Tomorrow")):
        day = (start + datetime.timedelta(days=offset)).date()
        days.append({"date": day.isoformat(), "label": label,
                     "events": [{k: e.get(k) for k in ("id", "title", "start", "end", "all_day", "location",
                                                       "calendar_id")}
                                for e in found if _on_day(e, day, ctx.now.tzinfo)]})
    return {"days": days}


def _local_date(value: Any, tz: Any, end: bool = False) -> Optional[datetime.date]:
    """The local date of an event's start / end (an end is exclusive: an all-day one, or one at midnight, is
    the day before's)."""
    text = str(value or "")
    if not text:
        return None
    if len(text) == 10:
        d = datetime.date.fromisoformat(text)
        return d - datetime.timedelta(days=1) if end else d
    moment = datetime.datetime.fromisoformat(text)
    moment = (moment if moment.tzinfo is not None else moment.replace(tzinfo=tz)).astimezone(tz)
    if end and moment.time() == datetime.time(0, 0):
        return moment.date() - datetime.timedelta(days=1)   # ends at midnight: not on the next day
    return moment.date()


def _on_day(event: Dict[str, Any], day: datetime.date, tz: Any) -> bool:
    """Whether ``event`` takes part of ``day`` (local)."""
    try:
        first = _local_date(event.get("start"), tz)
        last = _local_date(event.get("end"), tz, end=True) or first
    except ValueError:
        return False
    return first is not None and first <= day <= max(first, last or first)
