"""The WAHA webhook (docs/whatsapp.md): ``POST /webhook/waha``, acknowledged at
once and handled as the user whose WhatsApp account the event names
(events.py). Accepted only when signed by WAHA with ``WAHA_WEBHOOK_SECRET``
(its HMAC: ``X-Webhook-Hmac``, SHA-512 of the raw body), or from a
``manage_server`` user (testing).
"""

import asyncio
import hashlib
import hmac
import json
import logging
import os

from aiohttp import web

from addons.whatsapp import events as whatsapp_events

logger = logging.getLogger(__name__)

SECRET_ENV = "WAHA_WEBHOOK_SECRET"
SIGNATURE_HEADER = "X-Webhook-Hmac"
ALGORITHM_HEADER = "X-Webhook-Hmac-Algorithm"


def signature(secret: str, body: bytes) -> str:
    """WAHA's signature of a webhook body: the hex HMAC-SHA512 of the raw bytes with ``secret``."""
    return hmac.new(secret.encode(), body, hashlib.sha512).hexdigest()


def signed_by_waha(request: web.Request, body: bytes) -> bool:
    """Whether ``body`` carries a valid WAHA signature made with ``WAHA_WEBHOOK_SECRET``
    (False when the secret is not set: an unsigned webhook is never trusted)."""
    secret = os.getenv(SECRET_ENV, "").strip()
    sent = request.headers.get(SIGNATURE_HEADER, "").strip().lower()
    algorithm = request.headers.get(ALGORITHM_HEADER, "sha512").strip().lower()
    if not secret or not sent or algorithm != "sha512":
        return False
    return hmac.compare_digest(sent, signature(secret, body))


async def handle_waha_webhook(request: web.Request) -> web.Response:
    """Handle incoming WAHA (WhatsApp) events — routed by account, transcribes incoming voice notes.

    Accepted when WAHA signed the body with ``WAHA_WEBHOOK_SECRET`` (the auth
    middleware lets a request without credentials through with no user), or
    from a user with ``manage_server`` (testing); anything else gets 403, so
    nobody can forge an event for someone's account (docs/access.md,
    "Security rules")."""
    body = await request.read()
    caller = request.get("user")
    if caller is None:
        if not signed_by_waha(request, body):
            why = "no WAHA_WEBHOOK_SECRET set" if not os.getenv(SECRET_ENV, "").strip() else "bad or missing signature"
            logger.warning(f"WAHA webhook refused from {request.remote}: {why}")
            return web.json_response({"error": "forbidden", "detail": "unsigned webhook"}, status=403)
    elif not caller.level.allows("manage_server"):
        logger.warning(f"WAHA webhook refused for user {caller.id}: needs manage_server")
        return web.json_response({"error": "forbidden", "needs": "manage_server"}, status=403)
    try:
        data = json.loads(body)
    except ValueError as e:
        logger.error(f"Failed to parse WAHA webhook JSON body: {e}")
        return web.Response(text="Malformed JSON", status=400)
    if not isinstance(data, dict):
        return web.Response(text="Malformed JSON", status=400)

    # Ack immediately; WAHA retries on non-2xx and transcription can be slow.
    # Processed as the user whose WhatsApp account the event names.
    asyncio.create_task(whatsapp_events.handle_webhook(data))
    return web.json_response({"status": "accepted"})


def setup(app: web.Application) -> None:
    """Register the WAHA webhook."""
    app.router.add_post("/webhook/waha", handle_waha_webhook)
    if not os.getenv(SECRET_ENV, "").strip():
        logger.info(f"{SECRET_ENV} is not set: every WAHA webhook is refused, so incoming WhatsApp messages and voice "
            f"notes are not received. Set it to WAHA's WHATSAPP_HOOK_HMAC_KEY (docs/whatsapp.md)")
