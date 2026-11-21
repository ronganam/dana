"""Google Calendar tools: each runs on the user's own calendar (``conn``:
their refresh token, default calendar and selected calendars; addons/calendar/client.py)."""

import datetime
import logging
from typing import Optional, Any, Dict

from core import clock
from core.undoable import UndoConflict, safe_record
from core.users import User
from core.addons import Connection, store
from addons.calendar import client
from addons.calendar.config import ADDON
from addons.calendar.addon import addon

logger = logging.getLogger(__name__)


def format_datetime(dt_str: str) -> str:
    try:
        dt_str_clean = dt_str.strip()
        if len(dt_str_clean) == 10:
            return dt_str_clean
        dt = datetime.datetime.fromisoformat(dt_str_clean)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=clock.get_timezone())
        return dt.isoformat()
    except Exception as e:
        logger.warning(f"Error formatting datetime '{dt_str}': {e}")
        return dt_str


@addon.tool()
def list_calendars(conn: Connection) -> str:
    """List all available Google Calendars with their IDs and which is the default (personal)."""
    logger.info("Listing available calendars")
    try:
        service = client.service(conn)
        cal_list = service.calendarList().list().execute()
        items = cal_list.get('items', [])

        if not items:
            return "No calendars found."

        selected_ids = set(client.selected_calendar_ids(conn))

        result = "Available calendars:\n"
        for cal in items:
            cal_id = cal.get('id')
            summary = cal.get('summary', 'Unnamed')
            primary = cal.get('primary', False)
            selected = cal_id in selected_ids
            result += f"- {summary}\n"
            result += f"  ID: {cal_id}\n"
            result += f"  Primary (personal): {'Yes' if primary else 'No'}\n"
            result += f"  Searched by default: {'Yes' if selected else 'No'}\n"
        return result
    except Exception as e:
        logger.error(f"Failed to list calendars: {e}", exc_info=True)
        return f"Error listing calendars: {e}"


@addon.tool()
def search_calendar(
    conn: Connection,
    query: Optional[str] = None,
    start_time: Optional[str] = None,
    end_time: Optional[str] = None,
    limit: int = 20,
    calendar_id: Optional[str] = None
) -> str:
    """Search your calendars for events. By default searches all your selected calendars (personal, work, etc.).
    Use 'calendar_id' to search only one calendar (pass the exact ID from list_calendars()).

    Args:
        query: Optional text to search for in event summaries, descriptions, or locations.
        start_time: Optional start time in ISO format (YYYY-MM-DDTHH:MM:SS). Defaults to 30 days ago.
        end_time: Optional end time in ISO format (YYYY-MM-DDTHH:MM:SS). Defaults to 30 days in the future.
        limit: The maximum number of results to return (default 20).
        calendar_id: Optional specific calendar ID (from list_calendars()). If omitted, searches all selected calendars.
    """
    logger.info(f"Searching calendar(s): query='{query}', start_time='{start_time}', end_time='{end_time}', calendar_id='{calendar_id}'")
    try:
        service = client.service(conn)

        if start_time:
            time_min = format_datetime(start_time)
        else:
            time_min = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=30)).isoformat()

        time_max = format_datetime(end_time) if end_time else None

        if calendar_id:
            calendar_ids = [calendar_id]
        else:
            calendar_ids = client.selected_calendar_ids(conn)

        cal_name_map = client.calendar_names(conn, service)

        all_results = []
        for cid in calendar_ids:
            try:
                kwargs = {
                    'calendarId': cid,
                    'maxResults': limit,
                    'singleEvents': True,
                    'orderBy': 'startTime',
                    'timeMin': time_min,
                }
                if time_max:
                    kwargs['timeMax'] = time_max
                if query:
                    kwargs['q'] = query

                events_result = service.events().list(**kwargs).execute()
                events = events_result.get('items', [])
                for ev in events:
                    all_results.append((cid, cal_name_map.get(cid, cid), ev))
            except Exception as e:
                logger.warning(f"Failed to search calendar '{cid}': {e}")
                continue

        if not all_results:
            return "No calendar events found."

        all_results.sort(key=lambda x: x[2].get('start', {}).get('dateTime') or x[2].get('start', {}).get('date', ''))

        if len(all_results) > limit:
            all_results = all_results[:limit]

        result = "Found the following calendar events:\n"
        for cid, cal_name, ev in all_results:
            ev_id = ev.get('id')
            summary = ev.get('summary', '(No Title)')
            start = ev.get('start', {})
            end = ev.get('end', {})

            start_time_val = start.get('dateTime') or start.get('date')
            end_time_val = end.get('dateTime') or end.get('date')

            location = ev.get('location')
            description = ev.get('description')

            response_status = None
            for att in ev.get('attendees', []):
                if att.get('self'):
                    response_status = att.get('responseStatus')
                    break

            result += f"- [{ev_id}] {summary}\n"
            result += f"  Calendar: {cal_name} (ID: {cid})\n"
            result += f"  Time: {start_time_val} to {end_time_val}\n"
            if response_status:
                result += f"  RSVP: {response_status}\n"
            if location:
                result += f"  Location: {location}\n"
            if description:
                result += f"  Description: {description}\n"
        return result

    except Exception as e:
        logger.error(f"Failed to search calendar: {e}", exc_info=True)
        return f"Error searching Google Calendar: {e}"


def _invites_people(args: Dict[str, Any]) -> bool:
    """add_event / update_event ask first only when they email invitations."""
    return bool(str(args.get("attendees") or args.get("add_attendees") or "").strip())


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
            if "404" in str(e) or "410" in str(e):
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


@addon.tool(is_write=True, confirm=_invites_people)
def add_event(
    conn: Connection,
    summary: str,
    start_time: str,
    end_time: str,
    description: Optional[str] = None,
    location: Optional[str] = None,
    attendees: Optional[str] = None,
) -> str:
    """Add a new event to your default personal calendar.

    Only creates events on the default calendar configured via GOOGLE_CALENDAR_ID.
    Use list_calendars to see available calendars.

    Args:
        summary: The title or summary of the event.
        start_time: Start time in ISO format (YYYY-MM-DDTHH:MM:SS).
        end_time: End time in ISO format (YYYY-MM-DDTHH:MM:SS).
        description: Optional description of the event.
        location: Optional location for the event.
        attendees: Optional comma-separated attendee emails to invite.
    """
    logger.info(f"Adding calendar event: '{summary}'")
    try:
        service = client.service(conn)

        start_formatted = format_datetime(start_time)
        end_formatted = format_datetime(end_time)

        start_key = 'date' if len(start_formatted) == 10 else 'dateTime'
        end_key = 'date' if len(end_formatted) == 10 else 'dateTime'

        event_body: Dict[str, Any] = {
            'summary': summary,
            'start': {start_key: start_formatted},
            'end': {end_key: end_formatted},
        }

        if description:
            event_body['description'] = description
        if location:
            event_body['location'] = location
        if attendees:
            emails = [email.strip() for email in attendees.split(",") if email.strip()]
            event_body['attendees'] = [{'email': email} for email in emails]

        kwargs = {
            'calendarId': client.default_calendar_id(conn),
            'body': event_body
        }
        if attendees:
            kwargs['sendUpdates'] = 'all'

        created_event = service.events().insert(**kwargs).execute()

        event_id = created_event.get('id')
        logger.info(f"Successfully added event: {summary} (ID: {event_id})")
        if event_id and not attendees:
            safe_record(f"Added “{summary}” to the calendar",
                        _undo_add_event(conn.user, client.default_calendar_id(conn), event_id,
                                        created_event.get("updated")), "add_event")
        return f"Successfully added event '{summary}' to your personal calendar with ID: {event_id}."

    except Exception as e:
        logger.error(f"Failed to add event: {e}", exc_info=True)
        return f"Error adding event to Google Calendar: {e}"


@addon.tool(is_write=True, confirm=_invites_people)
def update_event(
    conn: Connection,
    event_id: str,
    summary: Optional[str] = None,
    start_time: Optional[str] = None,
    end_time: Optional[str] = None,
    description: Optional[str] = None,
    location: Optional[str] = None,
    add_attendees: Optional[str] = None,
) -> str:
    """Update an existing event in your personal calendar.

    Args:
        event_id: The ID of the event to update.
        summary: New summary (if updating).
        start_time: New start time in ISO format (if updating).
        end_time: New end time in ISO format (if updating).
        description: New description (if updating).
        location: New location (if updating).
        add_attendees: Comma-separated new attendee emails to invite.
    """
    logger.info(f"Updating calendar event: {event_id}")
    try:
        service = client.service(conn)

        body: Dict[str, Any] = {}
        updates = []

        if summary is not None:
            body['summary'] = summary
            updates.append("summary")

        if start_time is not None:
            start_formatted = format_datetime(start_time)
            start_key = 'date' if len(start_formatted) == 10 else 'dateTime'
            body['start'] = {start_key: start_formatted}
            updates.append("start_time")

        if end_time is not None:
            end_formatted = format_datetime(end_time)
            end_key = 'date' if len(end_formatted) == 10 else 'dateTime'
            body['end'] = {end_key: end_formatted}
            updates.append("end_time")

        if description is not None:
            body['description'] = description
            updates.append("description")

        if location is not None:
            body['location'] = location
            updates.append("location")

        if add_attendees is not None:
            existing_event = service.events().get(
                calendarId=client.default_calendar_id(conn),
                eventId=event_id
            ).execute()

            current_attendees = existing_event.get('attendees', [])
            existing_emails = {att.get('email').strip().lower() for att in current_attendees if att.get('email')}

            new_emails = [email.strip() for email in add_attendees.split(",") if email.strip()]

            updated_attendees = list(current_attendees)
            for email in new_emails:
                if email.lower() not in existing_emails:
                    updated_attendees.append({'email': email})

            body['attendees'] = updated_attendees
            updates.append("attendees")

        if not updates:
            return f"No changes specified for event ID: {event_id}."

        # What the changed fields held before, for Undo (not offered when
        # invitations went out: those cannot be called back).
        previous: Optional[Dict[str, Any]] = None
        if add_attendees is None:
            try:
                before = service.events().get(calendarId=client.default_calendar_id(conn), eventId=event_id).execute()
                previous = {k: before.get(k) for k in body}
                for k in ("description", "location"):
                    if k in previous and previous[k] is None:
                        previous[k] = ""
            except Exception as e:
                logger.warning(f"Could not read event {event_id} before updating; no undo: {e}")

        kwargs = {
            'calendarId': client.default_calendar_id(conn),
            'eventId': event_id,
            'body': body
        }
        if add_attendees is not None:
            kwargs['sendUpdates'] = 'all'

        updated_event = service.events().patch(**kwargs).execute()

        updated_summary = updated_event.get('summary', '(No Title)')
        logger.info(f"Successfully updated event {event_id}: {', '.join(updates)}")
        if previous and all(v is not None for v in previous.values()):
            safe_record(f"Changed “{updated_summary}” in the calendar",
                        _undo_update_event(conn.user, client.default_calendar_id(conn), event_id, previous,
                                           updated_event.get("updated")), "update_event")
        return f"Successfully updated the following fields for event '{updated_summary}' (ID: {event_id}): {', '.join(updates)}."

    except Exception as e:
        logger.error(f"Failed to update event {event_id}: {e}", exc_info=True)
        return f"Error updating Google Calendar event with ID '{event_id}': {e}"


@addon.tool(is_write=True, confirm=True)
def delete_event(conn: Connection, event_id: str) -> str:
    """Delete an existing event from your personal calendar by its event ID.

    Args:
        event_id: The ID of the event to delete.
    """
    logger.info(f"Deleting calendar event: {event_id}")
    try:
        service = client.service(conn)
        service.events().delete(
            calendarId=client.default_calendar_id(conn),
            eventId=event_id
        ).execute()
        logger.info(f"Successfully deleted event: {event_id}")
        return f"Successfully deleted the event with ID: {event_id}."
    except Exception as e:
        logger.error(f"Failed to delete event {event_id}: {e}", exc_info=True)
        return f"Error deleting Google Calendar event with ID '{event_id}': {e}"
