"""Google Calendar tools: each runs on the user's own calendar (``conn``:
their refresh token, default calendar and selected calendars; addons/calendar/client.py).
Blocking (Google's client); the phone addon also calls ``add_event`` / ``delete_event``
directly in a worker thread and reads the ``ID: <id>.`` that ends their result."""

import datetime
import html
import logging
import re
from typing import Any, Dict, List, Optional, Tuple, Union

from core import clock
from core.undoable import UndoConflict, safe_record
from core.users import User
from core.addons import Approval, Connection, helpers, store
from core.addons.hooks import clean
from addons.calendar import client, today as calendar_today
from addons.calendar.config import ADDON
from addons.calendar.addon import addon
from addons.google_account import oauth

logger = logging.getLogger(__name__)

SERVICE = "Google Calendar"
MAX_RESULTS = 50
# Characters of an event's description in a listing (in full up to DESCRIPTION_ONE when one event is found).
DESCRIPTION_CHARS = 300
DESCRIPTION_ONE = 2000
RESULT_MAX_CHARS = 11_000  # under core/agent.py's MAX_TOOL_RESULT_CHARS
LIST_FIELDS = "items(id,summary,start,end,location,description,attendees(self,responseStatus))"

Moment = Union[datetime.date, datetime.datetime]


# --- Times ----------------------------------------------------------------------------------------

def _parse(value: Any, field: str) -> Moment:
    """``YYYY-MM-DD`` as a date, anything longer as an aware datetime (naive: the user's zone).

    Raises:
        ValueError: not an ISO date or time (the message names the field and the format).
    """
    raw = str(value or "").strip()
    try:
        if len(raw) == 10:
            return datetime.date.fromisoformat(raw)
        dt = datetime.datetime.fromisoformat(raw.replace("Z", "+00:00").replace(" ", "T", 1))
        return dt if dt.tzinfo else dt.replace(tzinfo=clock.get_timezone())
    except ValueError:
        raise ValueError(f"{field} '{clean(raw, 40)}' is not YYYY-MM-DDTHH:MM (a time) or YYYY-MM-DD (all day)")


def _instant(value: Moment, end: bool = False) -> datetime.datetime:
    """A search bound: a date is its local midnight (``end``: the midnight after it)."""
    if isinstance(value, datetime.datetime):
        return value
    day = value + datetime.timedelta(days=1) if end else value
    return datetime.datetime.combine(day, datetime.time(), tzinfo=clock.get_timezone())


def _field(value: Moment) -> Dict[str, str]:
    return {"dateTime": value.isoformat()} if isinstance(value, datetime.datetime) else {"date": value.isoformat()}


def _read(field: Dict[str, Any]) -> Optional[Moment]:
    try:
        if field.get("dateTime"):
            return _parse(field["dateTime"], "")
        return datetime.date.fromisoformat(field["date"]) if field.get("date") else None
    except (ValueError, TypeError):
        return None


def _end_for(start: Moment, end: Optional[Moment], length: Optional[datetime.timedelta] = None) -> Moment:
    """The end of an event starting at ``start``: ``end`` when given (an all-day
    one names its last day; Google's end is the day after), else ``start`` plus
    ``length`` (default an hour, or one day for an all-day event).

    Raises:
        ValueError: ``end`` before ``start``, or one is a date and the other a time.
    """
    timed = isinstance(start, datetime.datetime)
    if end is None:
        return start + (length or (datetime.timedelta(hours=1) if timed else datetime.timedelta(days=1)))
    if timed != isinstance(end, datetime.datetime):
        raise ValueError("start_time and end_time must both be times, or both dates (all day)")
    if end < start:  # type: ignore[operator]
        raise ValueError("end_time is before start_time")
    return end if timed else end + datetime.timedelta(days=1)


def _span(start: Dict[str, Any], end: Dict[str, Any]) -> str:
    """'Tue 2026-11-24 10:00–11:00' / 'Tue 2026-11-24 (all day)' in the user's zone."""
    a, b = _read(start), _read(end)
    if a is None:
        return "(no time)"
    if not isinstance(a, datetime.datetime):
        last = b - datetime.timedelta(days=1) if isinstance(b, datetime.date) and b > a else a
        return a.strftime("%a %Y-%m-%d") + (f" – {last.strftime('%a %Y-%m-%d')}" if last != a else "") + " (all day)"
    tz = clock.get_timezone()
    a = a.astimezone(tz)
    out = a.strftime("%a %Y-%m-%d %H:%M")
    if isinstance(b, datetime.datetime):
        b = b.astimezone(tz)
        out += f"–{b.strftime('%H:%M')}" if b.date() == a.date() else f" – {b.strftime('%a %Y-%m-%d %H:%M')}"
    return out


def _start_key(ev: Dict[str, Any]) -> float:
    moment = _read(ev.get("start") or {})
    return _instant(moment).timestamp() if moment is not None else float("inf")


# --- Calendars and failures -----------------------------------------------------------------------

def _missing(error: Exception) -> bool:
    status = getattr(getattr(error, "resp", None), "status", None)
    return status in (404, 410) or (status is None and ("404" in str(error) or "410" in str(error)))


def _failed(conn: Connection, error: Exception, doing: str) -> str:
    reason = oauth.failed(conn, error, SERVICE)
    logger.error(f"Calendar tool failed while {doing} for user {conn.user.id}: {reason}", exc_info=True)
    return f"Error {doing}: {reason}"


def _calendar(conn: Connection, svc: Any, wanted: Optional[str], names: Optional[Dict[str, str]] = None) -> str:
    """The id of the calendar ``wanted`` names (its id or name; none: the calendar new events go to).

    Raises:
        ValueError: no calendar of the account has that id or name (the message lists them).
    """
    wanted = str(wanted or "").strip()
    if not wanted:
        return client.default_calendar_id(conn)
    names = client.calendar_names(conn, svc) if names is None else names
    if wanted in names or wanted == "primary" or not names:
        return wanted
    by_name = [cid for cid, name in names.items() if name.strip().lower() == wanted.lower()]
    if len(by_name) == 1:
        return by_name[0]
    raise ValueError(f"no calendar is called '{clean(wanted, 80)}'. Calendars: "
                     + "; ".join(f"{name} (ID: {cid})" for cid, name in names.items()))


@addon.tool()
def list_calendars(conn: Connection) -> str:
    """List the user's Google Calendars: each one's name and ID, which are searched by default and where new
    events go. Needed only to pick another calendar; search_calendar already searches the default ones."""
    try:
        items = client.calendar_list(conn)
    except Exception as e:
        return _failed(conn, e, "listing the calendars")
    if not items:
        return "No calendars found."
    selected, default = set(client.selected_calendar_ids(conn)), client.default_calendar_id(conn)
    lines = []
    for cal in items:
        cid = cal.get("id")
        marks = [m for m, on in (("primary", cal.get("primary")), ("searched by default", cid in selected),
                                 ("new events go here", cid == default or (default == "primary" and cal.get("primary"))))
                 if on]
        lines.append(f"- {cal.get('summary') or 'Unnamed'} · ID: {cid}" + (f" · {', '.join(marks)}" if marks else ""))
    return "Calendars:\n" + "\n".join(lines)


# --- Searching ------------------------------------------------------------------------------------

def _description(text: Any, limit: int) -> str:
    plain = " ".join(html.unescape(re.sub(r"<[^>]{0,500}>", " ", str(text or ""))).split())
    return plain if len(plain) <= limit else plain[:limit].rstrip() + f"… [cut: {len(plain):,} characters]"


def _event_lines(ev: Dict[str, Any], calendar: str, description_chars: int) -> List[str]:
    head = f"- {ev.get('summary') or '(no title)'} · {_span(ev.get('start') or {}, ev.get('end') or {})}"
    lines = [head + f" · event ID: {ev.get('id')}" + (f" · calendar: {calendar}" if calendar else "")]
    rsvp = next((a.get("responseStatus") for a in ev.get("attendees") or [] if a.get("self")), None)
    extra = [f"Location: {clean(ev['location'], 200)}" if ev.get("location") else "",
             f"RSVP: {rsvp}" if rsvp and rsvp != "accepted" else ""]
    if any(extra):
        lines.append("  " + " · ".join(x for x in extra if x))
    if ev.get("description"):
        lines.append(f"  Description: {_description(ev['description'], description_chars)}")
    return lines


@addon.tool(untrusted_result=True)
def search_calendar(conn: Connection, query: Optional[str] = None, start_time: Optional[str] = None,
                    end_time: Optional[str] = None, limit: int = 20, calendar_id: Optional[str] = None) -> str:
    """Find events in the user's calendars (all the calendars they chose to see), soonest first, with each
    event's ID for update_event / delete_event.

    Args:
        query: Optional words to find in the title, description, location or attendees.
        start_time: From (YYYY-MM-DD or YYYY-MM-DDTHH:MM, the user's time). Default: today's start; with a
            query, a year ago.
        end_time: Until (same forms; a date includes that whole day). Default: 30 days after the start; with a
            query and no start_time, a year from now.
        limit: At most this many events (default 20, at most 50).
        calendar_id: Only this calendar (its name or ID from list_calendars).
    """
    try:
        lo = _instant(_parse(start_time, "start_time")) if start_time else None
        hi = _instant(_parse(end_time, "end_time"), end=True) if end_time else None
    except ValueError as e:
        return f"Error: {e}."
    span = datetime.timedelta(days=365 if query else 30)
    if lo is None:
        lo = clock.now() - span if query else clock.now().replace(hour=0, minute=0, second=0, microsecond=0)
        if hi is not None and hi <= lo:
            lo = hi - span
    hi = hi or lo + (2 * span if query and not start_time else span)
    limit = max(1, min(int(limit or 20), MAX_RESULTS))
    try:
        svc = client.service(conn)
        names = client.calendar_names(conn, svc)
        ids = [_calendar(conn, svc, calendar_id, names)] if calendar_id else client.selected_calendar_ids(conn)
    except ValueError as e:
        return f"Error: {e}."
    except Exception as e:
        return _failed(conn, e, "searching the calendar")
    found: List[Tuple[str, Dict[str, Any]]] = []
    failed: List[str] = []
    for cid in ids:
        asked: Dict[str, Any] = {"calendarId": cid, "maxResults": limit, "singleEvents": True, "orderBy": "startTime",
                                 "timeMin": lo.isoformat(), "timeMax": hi.isoformat(), "fields": LIST_FIELDS}
        if query:
            asked["q"] = query
        try:
            found += [(cid, ev) for ev in svc.events().list(**asked).execute().get("items", [])]
        except Exception as e:
            failed.append(f"{names.get(cid, cid)} ({oauth.failed(conn, e, SERVICE)})")
            logger.warning(f"Calendar '{cid}' of user {conn.user.id} could not be searched: {e}")
    if failed and len(failed) == len(ids):
        return f"Error searching the calendar: could not read {'; '.join(failed)}."
    window = f"between {_span({'dateTime': lo.isoformat()}, {})} and {_span({'dateTime': hi.isoformat()}, {})}"
    note = f" Could not read: {'; '.join(failed)}." if failed else ""
    if not found:
        return f"No events {window}{f' matching {query!r}' if query else ''}.{note}"
    found.sort(key=lambda f: _start_key(f[1]))
    shown = found[:limit]
    described = DESCRIPTION_ONE if len(shown) == 1 else DESCRIPTION_CHARS
    lines: List[str] = []
    for n, (cid, ev) in enumerate(shown):
        block = _event_lines(ev, names.get(cid, cid) if len(ids) > 1 else "", described)
        if sum(len(x) + 1 for x in lines + block) > RESULT_MAX_CHARS:
            shown = shown[:n]
            break
        lines += block
    more = (f" Showing the first {len(shown)}: narrow the time range or the query to see the rest."
            if len(shown) < len(found) or len(found) >= limit else "")
    return f"{len(shown)} event(s) {window}, soonest first.{more}{note}\n" + "\n".join(lines)


# --- Writing --------------------------------------------------------------------------------------

def _invites_people(args: Dict[str, Any]) -> bool:
    """add_event / update_event ask first only when they email invitations."""
    return bool(str(args.get("attendees") or args.get("add_attendees") or "").strip())


def _emails(value: Optional[str]) -> List[str]:
    """Comma-separated addresses, each checked.

    Raises:
        ValueError: one is not an email address.
    """
    out = [e.strip() for e in str(value or "").split(",") if e.strip()]
    bad = [e for e in out if not re.fullmatch(r"[^@\s,]+@[^@\s,]+\.[^@\s,]+", e)]
    if bad:
        raise ValueError(f"not an email address: {', '.join(bad)} (look it up with search_contacts)")
    return out


def _undo_service(user: User) -> Any:
    """The service of ``user``'s calendar at undo time (their current
    connection; NotConnected once they disconnected it)."""
    return client.service(store.require(user, ADDON))


def _undo_add_event(user: User, calendar_id: str, event_id: str, created_updated: Optional[str]):
    def _undo():
        service = _undo_service(user)
        try:
            current = service.events().get(calendarId=calendar_id, eventId=event_id).execute()
        except Exception as e:
            if _missing(e):
                return  # already gone
            raise
        if current.get("status") == "cancelled":
            return
        if created_updated and current.get("updated") != created_updated:
            raise UndoConflict("The event was edited since it was added, so it was kept.")
        service.events().delete(calendarId=calendar_id, eventId=event_id).execute()
    return _undo


def _undo_update_event(user: User, calendar_id: str, event_id: str, previous: Dict[str, Any],
                       updated_stamp: Optional[str]):
    def _undo():
        service = _undo_service(user)
        current = service.events().get(calendarId=calendar_id, eventId=event_id).execute()
        if current.get("status") == "cancelled":
            raise UndoConflict("The event was deleted in the meantime.")
        if updated_stamp and current.get("updated") != updated_stamp:
            raise UndoConflict("The event was changed again since, so it was left as it is.")
        service.events().patch(calendarId=calendar_id, eventId=event_id, body=previous).execute()
    return _undo


def _when(value: Any) -> str:
    raw = clean(value, 40)
    if not raw:
        return ""
    try:
        dt = datetime.datetime.fromisoformat(raw.replace("Z", "+00:00").replace(" ", "T", 1))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=clock.get_timezone())
        return helpers.when_phrase(dt)
    except Exception:
        return raw


def _approval_span(start: Any, end: Any) -> str:
    a, b = _when(start), _when(end)
    if a and b:
        head, _, a_hm = a.rpartition(" ")
        b_head, _, b_hm = b.rpartition(" ")
        if head == b_head and a_hm and b_hm:
            return f"{a}–{b_hm}"
        return f"{a} – {b}"
    return a or b


def _event_title(user: Optional[User], event_id: Any) -> str:
    try:
        for e in calendar_today.cached_events(user):
            if e.get("id") == event_id:
                return e.get("title") or ""
    except Exception:
        pass
    return ""


def _describe_add_event(user: Optional[User], a: Dict[str, Any]) -> Approval:
    name = a.get("summary") or ""
    invites = clean(a.get("attendees"), 300)
    q = f"“{clean(name or 'an event', 80)}”"
    title = f"Invite {invites} to {q}" if invites else f"Add {q} to the calendar"
    return Approval(title, "Calendar event", "📅", "Add this event?", body_label="Description",
                    body=a.get("description"),
                    facts=[("When", _approval_span(a.get("start_time"), a.get("end_time"))), ("Where", a.get("location"))])


def _describe_update_event(user: Optional[User], a: Dict[str, Any]) -> Approval:
    name = a.get("summary") or _event_title(user, a.get("event_id")) or ""
    invites = clean(a.get("attendees") or a.get("add_attendees"), 300)
    title = f"Change “{clean(name, 80)}” in the calendar" if name else "Change a calendar event"
    if invites:
        title += f" and invite {invites}"
    return Approval(title, "Change calendar event", "📅", "Make this change?", body_label="Description",
                    body=a.get("description"),
                    facts=[("When", _approval_span(a.get("start_time"), a.get("end_time"))), ("Where", a.get("location")),
                           ("New title", a.get("summary"))])


def _describe_delete_event(user: Optional[User], a: Dict[str, Any]) -> Approval:
    name = _event_title(user, a.get("event_id"))
    return Approval(f"Delete “{clean(name, 80)}” from the calendar" if name else "Delete an event from the calendar",
                    "Delete calendar event", "❌", "Delete this event?",
                    facts=[] if name else [("Event", a.get("event_id"))])


def _not_found(event_id: str, calendar_id: str) -> str:
    return (f"Error: no event with ID '{clean(event_id, 80)}' in calendar '{calendar_id}'. Find it with "
            "search_calendar, and pass calendar_id when it is in another calendar.")


@addon.tool(is_write=True, confirm=_invites_people, describe=_describe_add_event)
def add_event(conn: Connection, summary: str, start_time: str, end_time: Optional[str] = None,
              description: Optional[str] = None, location: Optional[str] = None, attendees: Optional[str] = None,
              calendar_id: Optional[str] = None) -> str:
    """Add an event to the user's calendar. Runs at once with Undo; asks first only when it invites people.

    Args:
        summary: The event's title.
        start_time: YYYY-MM-DDTHH:MM in the user's time (or with an offset); YYYY-MM-DD for an all-day event.
        end_time: The same form (all day: the last day). Default: an hour after the start (all day: one day).
        description: Optional notes.
        location: Optional place.
        attendees: Optional comma-separated email addresses to invite (Google emails them the invitation).
        calendar_id: Optional calendar name or ID (default: the calendar new events go to).
    """
    try:
        start = _parse(start_time, "start_time")
        end = _end_for(start, _parse(end_time, "end_time") if end_time else None)
        emails = _emails(attendees)
    except ValueError as e:
        return f"Error: {e}."
    body: Dict[str, Any] = {"summary": summary, "start": _field(start), "end": _field(end)}
    if description:
        body["description"] = description
    if location:
        body["location"] = location
    if emails:
        body["attendees"] = [{"email": e} for e in emails]
    try:
        svc = client.service(conn)
        cid = _calendar(conn, svc, calendar_id)
        created = svc.events().insert(calendarId=cid, body=body, **({"sendUpdates": "all"} if emails else {})).execute()
    except ValueError as e:
        return f"Error: {e}."
    except Exception as e:
        return _failed(conn, e, "adding the event")
    event_id = created.get("id")
    if event_id and not emails:
        safe_record(f"Added “{summary}” to the calendar", _undo_add_event(conn.user, cid, event_id, created.get("updated")),
                    "add_event")
    invited = f" Invitations were emailed to {', '.join(emails)}." if emails else ""
    return f"Added “{summary}” ({_span(body['start'], body['end'])}) to calendar '{cid}'.{invited} Event ID: {event_id}."


@addon.tool(is_write=True, confirm=_invites_people, describe=_describe_update_event)
def update_event(conn: Connection, event_id: str, summary: Optional[str] = None, start_time: Optional[str] = None,
                 end_time: Optional[str] = None, description: Optional[str] = None, location: Optional[str] = None,
                 add_attendees: Optional[str] = None, calendar_id: Optional[str] = None) -> str:
    """Change an event: only the fields given change; a new start_time alone keeps the event's length. Runs at
    once with Undo; asks first only when it invites people.

    Args:
        event_id: The event's ID from search_calendar.
        summary: A new title.
        start_time: A new start (YYYY-MM-DDTHH:MM, or YYYY-MM-DD for all day).
        end_time: A new end (same form; all day: the last day).
        description: New notes ("" clears them).
        location: A new place ("" clears it).
        add_attendees: Comma-separated email addresses to invite as well (Google emails them).
        calendar_id: The calendar the event is in (name or ID), when search_calendar showed another one.
    """
    try:
        start = _parse(start_time, "start_time") if start_time else None
        end = _parse(end_time, "end_time") if end_time else None
        emails = _emails(add_attendees)
    except ValueError as e:
        return f"Error: {e}."
    if all(v is None for v in (summary, start, end, description, location)) and not emails:
        return "Error: nothing to change; give at least one field."
    try:
        svc = client.service(conn)
        cid = _calendar(conn, svc, calendar_id)
        before = svc.events().get(calendarId=cid, eventId=event_id).execute()
    except ValueError as e:
        return f"Error: {e}."
    except Exception as e:
        return _not_found(event_id, calendar_id or client.default_calendar_id(conn)) if _missing(e) \
            else _failed(conn, e, "reading the event")
    body: Dict[str, Any] = {k: v for k, v in (("summary", summary), ("description", description),
                                              ("location", location)) if v is not None}
    if start is not None or end is not None:
        old_start, old_end = _read(before.get("start") or {}), _read(before.get("end") or {})
        length = None
        if start is not None and end is None and old_start is not None and old_end is not None \
                and type(old_start) is type(start):
            length = old_end - old_start  # type: ignore[operator]
        try:
            new_start = start if start is not None else old_start
            if new_start is None:
                raise ValueError("the event has no start time; give start_time too")
            body["start"], body["end"] = _field(new_start), _field(_end_for(new_start, end, length))
        except ValueError as e:
            return f"Error: {e}."
    if emails:
        current = before.get("attendees") or []
        have = {str(a.get("email") or "").lower() for a in current}
        body["attendees"] = current + [{"email": e} for e in emails if e.lower() not in have]
    try:
        updated = svc.events().patch(calendarId=cid, eventId=event_id, body=body,
                                     **({"sendUpdates": "all"} if emails else {})).execute()
    except Exception as e:
        return _failed(conn, e, "changing the event")
    title = updated.get("summary") or "(no title)"
    if not emails:  # invitations that went out cannot be called back
        previous = {k: before.get(k) if before.get(k) is not None else "" for k in body}
        safe_record(f"Changed “{title}” in the calendar",
                    _undo_update_event(conn.user, cid, event_id, previous, updated.get("updated")), "update_event")
    changed = ", ".join(sorted(body))
    return (f"Changed {changed} of “{title}” ({_span(updated.get('start') or {}, updated.get('end') or {})})."
            + (f" Invitations were emailed to {', '.join(emails)}." if emails else "") + f" Event ID: {event_id}.")


@addon.tool(is_write=True, confirm=True, describe=_describe_delete_event)
def delete_event(conn: Connection, event_id: str, calendar_id: Optional[str] = None) -> str:
    """Delete an event from the user's calendar (asks first).

    Args:
        event_id: The event's ID from search_calendar.
        calendar_id: The calendar the event is in (name or ID), when search_calendar showed another one.
    """
    try:
        svc = client.service(conn)
        cid = _calendar(conn, svc, calendar_id)
        svc.events().delete(calendarId=cid, eventId=event_id).execute()
    except ValueError as e:
        return f"Error: {e}."
    except Exception as e:
        return _not_found(event_id, calendar_id or client.default_calendar_id(conn)) if _missing(e) \
            else _failed(conn, e, "deleting the event")
    logger.info(f"Deleted calendar event {event_id} of user {conn.user.id}")
    return f"Deleted the event from calendar '{cid}'. Event ID: {event_id}."
