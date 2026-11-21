"""HTTP routes of the calendar addon: the caller's own Google Calendar.

- ``GET /api/calendar/calendars`` — their calendars and which are selected
- ``POST /api/calendar/selections {calendar_ids}``
- ``GET /api/calendar/events?start_date=&end_date=&calendars=``

403 ``not_connected`` without ``calendar``. The full-screen ``/calendar`` page
is in core/pages_api.py.
"""

import asyncio
import logging

from aiohttp import web

from core.auth import request_user
from addons.calendar import client as calendar_client, config as calendar_config, events as calendar_events
from core.addons import store
from core.addons.store import NotConnected

logger = logging.getLogger(__name__)


async def get_calendar_calendars_api(request: web.Request) -> web.Response:
    """GET /api/calendar/calendars - List the caller's Google Calendars (which ones are selected)."""
    conn = store.require(request_user(request), calendar_config.ADDON)
    try:
        return web.json_response(await asyncio.to_thread(calendar_events.calendars_payload, conn))
    except Exception as e:
        logger.error(f"Failed to list calendars: {e}", exc_info=True)
        return web.json_response({"error": str(e)}, status=500)


async def save_calendar_selections_api(request: web.Request) -> web.Response:
    """POST /api/calendar/selections - Save the caller's selected calendar IDs."""
    user = request_user(request)
    store.require(user, calendar_config.ADDON)
    try:
        data = await request.json()
        calendar_ids = data.get("calendar_ids", [])

        if not isinstance(calendar_ids, list):
            return web.json_response({"error": "calendar_ids must be a list"}, status=400)

        calendar_client.save_selection(user, calendar_ids)
        logger.info(f"Saved calendar selections: {calendar_ids}")
        return web.json_response({"status": "saved", "calendar_ids": calendar_ids})

    except NotConnected:
        raise  # answered by core.auth.addon_middleware
    except Exception as e:
        logger.error(f"Failed to save calendar selections: {e}", exc_info=True)
        return web.json_response({"error": str(e)}, status=500)


async def get_calendar_events_api(request: web.Request) -> web.Response:
    """GET /api/calendar/events - Events of the caller's selected calendars for a date range."""
    conn = store.require(request_user(request), calendar_config.ADDON)
    try:
        return web.json_response(await asyncio.to_thread(
            calendar_events.events_payload, conn, request.query.get("start_date"), request.query.get("end_date"),
            request.query.get("calendars")))
    except Exception as e:
        logger.error(f"Failed to fetch calendar events: {e}", exc_info=True)
        return web.json_response({"error": str(e)}, status=500)


def setup(app: web.Application) -> None:
    """Register the calendar routes."""
    app.router.add_get("/api/calendar/calendars", get_calendar_calendars_api)
    app.router.add_post("/api/calendar/selections", save_calendar_selections_api)
    app.router.add_get("/api/calendar/events", get_calendar_events_api)
