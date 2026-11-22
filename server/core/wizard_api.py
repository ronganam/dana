"""The first-run wizard over HTTP (docs/devices.md, "The wizard"; core/wizard.py).

    GET  /api/wizard                         → <state>
    POST /api/wizard/steps/{step} {status: done | skipped} → <state>
    POST /api/wizard/finish                  → <state>
    PUT  /api/wizard/home {latitude, longitude, radius_m?, timezone?, currency?}
         → {zone, timezone: {value, applied}, currency, state}: the caller's Home zone
           there (core/zones.py); ``timezone`` becomes the server's when the caller has
           ``manage_server`` (``applied``: false while ``TIMEZONE`` in env wins);
           ``currency`` becomes theirs when they have chosen none. Marks ``home`` done.

``<state>`` = ``{steps: [{id, status, available}], finished, finished_at, next,
demo, timezone}``: ``demo`` says whether the server has the sample data
endpoint (``POST /api/demo``); ``timezone`` is the server's now. Every
signed-in user, for themselves. An unknown step 404; a step that is not
available to them (``model`` without ``manage_server``, ``sample`` without the
demo endpoint) 400 ``unavailable``.
"""

import json
import logging
from typing import Any, Dict

from aiohttp import web

from core import clock, user_locale, users, wizard, zones
from core.auth import request_user
from core.users import User

logger = logging.getLogger(__name__)

DEMO_PATH = "/api/demo"


def has_demo(app: web.Application) -> bool:
    """Whether a ``POST /api/demo`` route is registered (the sample data, when its feature is merged)."""
    for resource in app.router.resources():
        if resource.canonical == DEMO_PATH and any(route.method == "POST" for route in resource):
            return True
    return False


def _state(request: web.Request, user: User) -> Dict[str, Any]:
    demo = has_demo(request.app)
    return {**wizard.state(user, demo=demo), "demo": demo, "timezone": str(clock.get_timezone())}


async def get_wizard_api(request: web.Request) -> web.Response:
    """GET /api/wizard"""
    return web.json_response(_state(request, request_user(request)))


async def mark_step_api(request: web.Request) -> web.Response:
    """POST /api/wizard/steps/{step}"""
    user = request_user(request)
    step = request.match_info["step"]
    if step not in wizard.STEPS:
        return web.json_response({"error": "not_found"}, status=404)
    try:
        body = await request.json()
    except (json.JSONDecodeError, ValueError):
        body = None
    status = body.get("status") if isinstance(body, dict) else None
    if status not in wizard.STATUSES:
        return web.json_response({"error": "invalid", "field": "status", "detail": "done or skipped"}, status=400)
    if not wizard.available(user, step, demo=has_demo(request.app)):
        return web.json_response({"error": "unavailable", "detail": f"The {step} step is not for you."}, status=400)
    wizard.mark(user, step, status)
    return web.json_response(_state(request, user))


async def finish_api(request: web.Request) -> web.Response:
    """POST /api/wizard/finish"""
    user = request_user(request)
    wizard.finish(user)
    return web.json_response(_state(request, user))


async def home_api(request: web.Request) -> web.Response:
    """PUT /api/wizard/home"""
    user = request_user(request)
    try:
        body = await request.json()
    except (json.JSONDecodeError, ValueError):
        body = None
    if not isinstance(body, dict):
        return web.json_response({"error": "invalid", "detail": "a JSON object is expected"}, status=400)
    tz = str(body.get("timezone") or "").strip()
    currency = str(body.get("currency") or "").strip()
    if tz and not clock.valid_timezone(tz):
        return web.json_response({"error": "invalid", "field": "timezone", "detail": "Not a timezone."}, status=400)
    try:
        currency = user_locale.check_currency(currency)
    except ValueError as e:
        return web.json_response({"error": "invalid", "field": "currency", "detail": str(e)}, status=400)
    try:
        zone = zones.set_home(user, body.get("latitude"), body.get("longitude"),
                              body.get("radius_m", zones.DEFAULT_RADIUS))
    except zones.Invalid as e:
        return web.json_response({"error": "invalid", "field": e.field, "detail": e.detail}, status=400)
    applied = False
    if tz and user.level.allows("manage_server"):
        applied = clock.set_server_timezone(tz, by=user.id)
        logger.info(f"{user.name} set the server's timezone to {tz}")
    if currency and not user.currency:
        user = users.update(user.id, currency=currency)
    wizard.mark(user, "home", "done")
    return web.json_response({"zone": zone.to_dict(), "timezone": {"value": str(clock.get_timezone()),
                                                                   "applied": applied},
                              "currency": user_locale.currency(user), "state": _state(request, user)})


def setup(app: web.Application) -> None:
    """Register the wizard routes."""
    app.router.add_get("/api/wizard", get_wizard_api)
    app.router.add_post("/api/wizard/steps/{step}", mark_step_api)
    app.router.add_post("/api/wizard/finish", finish_api)
    app.router.add_put("/api/wizard/home", home_api)
