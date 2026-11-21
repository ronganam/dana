"""The WAHA webhook (docs/whatsapp.md): ``POST /webhook/waha``, acknowledged at
once and handled as the user whose WhatsApp account the event names
(events.py). Only WAHA itself (an ``INTERNAL_HOSTS`` caller without
credentials) or a ``manage_server`` user may post to it.
"""

import asyncio
import logging

from aiohttp import web

from addons.whatsapp import events as whatsapp_events

logger = logging.getLogger(__name__)


async def handle_waha_webhook(request: web.Request) -> web.Response:
    """Handle incoming WAHA (WhatsApp) events — routed by account, transcribes incoming voice notes.

    Accepted only from WAHA itself — an ``INTERNAL_HOSTS`` host without
    credentials (the auth middleware lets that through with no user) — or
    from a user with ``manage_server`` (testing); anyone else gets 403, so no
    user can forge an event for someone else's account (docs/access.md,
    "Security rules")."""
    caller = request.get("user")
    if caller is not None and not caller.level.allows("manage_server"):
        logger.warning(f"WAHA webhook refused for user {caller.id}: needs manage_server")
        return web.json_response({"error": "forbidden", "needs": "manage_server"}, status=403)
    try:
        data = await request.json()
    except Exception as e:
        logger.error(f"Failed to parse WAHA webhook JSON body: {e}")
        return web.Response(text="Malformed JSON", status=400)

    # Ack immediately; WAHA retries on non-2xx and transcription can be slow.
    # Processed as the user whose WhatsApp account the event names.
    asyncio.create_task(whatsapp_events.handle_webhook(data))
    return web.json_response({"status": "accepted"})


def setup(app: web.Application) -> None:
    """Register the WAHA webhook."""
    app.router.add_post("/webhook/waha", handle_waha_webhook)
