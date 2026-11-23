"""One user's Google Calendar, over that user's own connection: the refresh
token from the connection's credentials, the default calendar (new events go
there) and the calendars they chose to see from its settings. The OAuth client
(``GOOGLE_CLIENT_ID`` / ``GOOGLE_CLIENT_SECRET``) is the server's and stays in
env.

Every function takes the user's :class:`~core.addons.Connection`;
nothing is shared between connections: the OAuth credentials (and with them
the access token Google hands out) are cached per connection
(``addons/google_account/oauth.py``). A service object is built per call (they are not thread-safe and the callers run in
worker threads); building one is cheap.
"""

import asyncio
import logging
from typing import Any, Dict, List, Optional, Tuple

from googleapiclient.discovery import build

from core.users import User
from core.addons import Connection, store
from addons.calendar.config import DEFAULT_CALENDAR, ADDON, SELECTED
from addons.google_account import oauth

logger = logging.getLogger(__name__)

Signature = Tuple[str, str]


def default_calendar_id(conn: Connection) -> str:
    """The calendar new events go to (setting ``calendar_id``)."""
    return str(conn.settings.get(DEFAULT_CALENDAR) or "primary")


def selection(conn: Connection) -> List[str]:
    """The calendars the user chose to see (setting ``selected_calendars``; empty: none chosen)."""
    raw = conn.settings.get(SELECTED)
    return [str(c) for c in raw] if isinstance(raw, list) else []


def selected_calendar_ids(conn: Connection) -> List[str]:
    """Calendar ids to read and search: the user's selection, else their default calendar."""
    return selection(conn) or [default_calendar_id(conn)]


def save_selection(user: User, calendar_ids: List[Any]) -> Connection:
    """Store ``user``'s choice of calendars as given (an empty list: none chosen).

    Raises:
        NotConnected: ``user`` has no calendar.
    """
    return store.update(user, ADDON, settings={SELECTED: list(calendar_ids)})


def signature(conn: Connection) -> Signature:
    """What identifies one connection's Google account (caches key on it)."""
    return (conn.user.id, oauth.refresh_token(conn))


def service(conn: Connection) -> Any:
    """A Google Calendar API client for the connection (blocking calls: run it in a thread).

    Raises:
        ValueError: the server has no OAuth client or the connection no refresh token.
    """
    return build("calendar", "v3", credentials=oauth.credentials(conn))


def calendar_list(conn: Connection, svc: Optional[Any] = None) -> List[Dict[str, Any]]:
    """The connection's calendar list entries (Google's own dicts)."""
    return (svc or service(conn)).calendarList().list().execute().get("items", [])


def calendar_names(conn: Connection, svc: Optional[Any] = None) -> Dict[str, str]:
    """Calendar id → friendly name ({} when the list cannot be read)."""
    try:
        return {cal["id"]: cal.get("summary", cal["id"]) for cal in calendar_list(conn, svc)}
    except Exception:
        return {}


def _check_blocking(conn: Connection) -> Optional[str]:
    try:
        calendar_list_page = service(conn).calendarList().list(maxResults=1)
        calendar_list_page.execute()
    except Exception as e:
        return oauth.failed(conn, e, "Google Calendar")
    return None


async def check(conn: Connection) -> Optional[str]:
    """``Addon(check=)``: one cheap request (the first entry of the
    calendar list, refreshing the access token): None when it works, else a
    short reason (never a token)."""
    return await asyncio.to_thread(_check_blocking, conn)
