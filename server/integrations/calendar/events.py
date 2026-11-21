"""Reading one user's calendars: the plain event list the composers use (Now,
the briefing, meeting cards, the weekly review) and the payloads of
``GET /api/calendar/calendars`` / ``GET /api/calendar/events`` (handlers in
integrations/calendar/api.py). Blocking (Google's client): call from a thread.
"""

import datetime
import logging
from typing import Any, Dict, List, Optional

from core import clock
from integrations.framework import Connection
from integrations.calendar import client

logger = logging.getLogger(__name__)


def list_events(conn: Connection, time_min: datetime.datetime, time_max: datetime.datetime,
                limit: int = 50) -> List[Dict[str, Any]]:
    """Events from the connection's selected calendars as plain dicts (blocking — call via asyncio.to_thread).

    Each: id, title, start / end (ISO; a date for all-day), all_day, location, attendees (names or emails), calendar_id.
    Cancelled events and ones the user declined are left out.

    Raises:
        ValueError: no OAuth client / refresh token (:func:`client.credentials`).
    """
    service = client.service(conn)
    out: List[Dict[str, Any]] = []
    for cid in client.selected_calendar_ids(conn):
        try:
            items = service.events().list(calendarId=cid, timeMin=time_min.isoformat(), timeMax=time_max.isoformat(),
                                          singleEvents=True, orderBy="startTime", maxResults=limit).execute().get("items", [])
        except Exception as e:
            logger.warning(f"Calendar '{cid}' couldn't be read: {e}")
            continue
        for ev in items:
            if ev.get("status") == "cancelled":
                continue
            me = next((a for a in ev.get("attendees", []) if a.get("self")), None)
            if me and me.get("responseStatus") == "declined":
                continue
            start, end = ev.get("start", {}), ev.get("end", {})
            out.append({
                "id": ev.get("id"), "title": ev.get("summary") or "(No title)",
                "start": start.get("dateTime") or start.get("date"), "end": end.get("dateTime") or end.get("date"),
                "all_day": "dateTime" not in start, "location": ev.get("location") or "",
                "attendees": [a.get("displayName") or a.get("email", "") for a in ev.get("attendees", []) if not a.get("self")],
                "calendar_id": cid,
            })
    out.sort(key=lambda e: e["start"] or "")
    return out


def calendars_payload(conn: Connection) -> List[Dict[str, Any]]:
    """``GET /api/calendar/calendars``: every calendar of the account, with
    whether it is selected (with nothing chosen: the primary one)."""
    selected = set(client.selection(conn))
    out = []
    for cal in client.calendar_list(conn):
        cal_id = cal.get("id")
        out.append({
            "id": cal_id,
            "summary": cal.get("summary", "Unnamed Calendar"),
            "description": cal.get("description"),
            "primary": cal.get("primary", False),
            "selected": cal_id in selected or (not selected and cal.get("primary", False)),
        })
    return out


def _bound(raw: Optional[str], tz: Any) -> Optional[datetime.datetime]:
    if not raw:
        return None
    value = datetime.datetime.fromisoformat(raw)
    return value if value.tzinfo is not None else value.replace(tzinfo=tz)


def events_payload(conn: Connection, start_date: Optional[str], end_date: Optional[str],
                   calendars: Optional[str]) -> List[Dict[str, Any]]:
    """``GET /api/calendar/events``: events of ``calendars`` (comma-separated
    ids; default: the selected ones, else the default calendar) between
    ``start_date`` and ``end_date`` (ISO; default: Monday of this week through
    Sunday of next week), sorted by their real start instant.

    Raises:
        ValueError: a malformed date, or no OAuth client / refresh token.
    """
    today = clock.now()
    tz = today.tzinfo
    if calendars:
        calendar_ids = [c.strip() for c in calendars.split(",") if c.strip()]
    else:
        calendar_ids = client.selected_calendar_ids(conn)
    if not calendar_ids:
        calendar_ids = [client.default_calendar_id(conn)]

    time_min = _bound(start_date, tz)
    if time_min is None:
        monday = today - datetime.timedelta(days=today.weekday())
        time_min = monday.replace(hour=0, minute=0, second=0, microsecond=0)
    time_max = _bound(end_date, tz)
    if time_max is None:
        next_sunday = time_min + datetime.timedelta(days=13)
        time_max = next_sunday.replace(hour=23, minute=59, second=59, microsecond=999999)

    service = client.service(conn)
    all_events = []
    for cal_id in calendar_ids:
        try:
            events = service.events().list(calendarId=cal_id, timeMin=time_min.isoformat(),
                                           timeMax=time_max.isoformat(), maxResults=100, singleEvents=True,
                                           orderBy="startTime").execute().get("items", [])
        except Exception as cal_err:
            logger.warning(f"Failed to fetch events from calendar '{cal_id}': {cal_err}")
            continue
        for ev in events:
            start = ev.get("start", {})
            end = ev.get("end", {})
            all_events.append({
                "id": ev.get("id"),
                "calendar_id": cal_id,
                "summary": ev.get("summary", "(No Title)"),
                "start": start.get("dateTime") or start.get("date"),
                "end": end.get("dateTime") or end.get("date"),
                "all_day": "date" in start and "dateTime" not in start,
                "location": ev.get("location"),
                "description": ev.get("description"),
                "attendees": [
                    {"email": a.get("email"), "name": a.get("displayName"), "responseStatus": a.get("responseStatus")}
                    for a in (ev.get("attendees") or [])
                ],
            })

    # Sort by the real start instant. Calendars can return mixed timezone
    # offsets (e.g. "...Z" vs "...+03:00"), so a plain string comparison puts
    # events from one calendar after another even when they start sooner.
    # All-day events (date only) parse as local midnight, so they land first
    # on their day.
    def _event_sort_key(e: Dict[str, Any]) -> float:
        raw_start = e.get("start") or ""
        try:
            start_dt = datetime.datetime.fromisoformat(raw_start.replace("Z", "+00:00"))
        except ValueError:
            return float("inf")
        if start_dt.tzinfo is None:
            start_dt = start_dt.replace(tzinfo=tz)
        return start_dt.timestamp()

    all_events.sort(key=_event_sort_key)
    return all_events
