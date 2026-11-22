"""REST routes for the morning briefing and the weekly review (composers:
docs/people.md); ``setup(app)`` registers them. The people routes are the
people addon's (addons/people/api.py); the wake signal comes with the phone's
other signals (``POST /api/signals``, docs/signals.md).

Every route answers for the request's user. Read paths the clients poll
(``GET /api/briefing/today``, ``GET /api/review/latest``) answer null for a
user who gets no briefing / review; the other routes answer 403
``not_connected`` (``phone`` for the briefing, ``tasks`` for the review), and
another user's review id 404.
"""
from __future__ import annotations

import logging

from aiohttp import web

from core import briefing, weekly_review
from core.auth import request_user

logger = logging.getLogger(__name__)


async def _json(request: web.Request) -> dict:
    try:
        data = await request.json()
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


# --- Briefing ----------------------------------------------------------------

async def briefing_today_api(request: web.Request) -> web.Response:
    """GET /api/briefing/today?generate=1 — the caller's briefing of today; with
    generate=1 it's made now if missing (null for a user who gets none)."""
    user = request_user(request)
    if not briefing.available(user):
        return web.json_response({"briefing": None})
    b = briefing.get_today(user)
    if not b and request.query.get("generate") in ("1", "true"):
        try:
            b = await briefing.ensure_today(user, trigger="app_open")
        except Exception as e:
            logger.warning(f"Briefing on demand failed: {e}")
    return web.json_response({"briefing": b})


async def briefing_generate_api(request: web.Request) -> web.Response:
    """POST /api/briefing/generate {deliver?: bool} — rebuild today's briefing (and push it)."""
    user = request_user(request)
    briefing.require(user)
    d = await _json(request)
    b = await briefing.build_and_store(user, trigger="manual")
    if d.get("deliver"):
        await briefing.deliver(b, user)
    return web.json_response({"briefing": b})


# --- Weekly review -----------------------------------------------------------

async def review_latest_api(request: web.Request) -> web.Response:
    return web.json_response({"review": weekly_review.latest(request_user(request))})


async def review_get_api(request: web.Request) -> web.Response:
    user = request_user(request)
    weekly_review.require(user)
    r = weekly_review.get_review(user, request.match_info["id"])
    if not r:
        return web.json_response({"error": "Review not found"}, status=404)
    return web.json_response({"review": r})


async def review_generate_api(request: web.Request) -> web.Response:
    """POST /api/review/generate {deliver?: bool}"""
    user = request_user(request)
    weekly_review.require(user)
    d = await _json(request)
    r = await weekly_review.generate(user)
    if d.get("deliver"):
        await weekly_review.deliver(r, user)
    return web.json_response({"review": r})


async def review_apply_api(request: web.Request) -> web.Response:
    """POST /api/review/{id}/apply {items: [{ref, action, due_date?, group?, enabled}]}"""
    user = request_user(request)
    weekly_review.require(user)
    d = await _json(request)
    try:
        return web.json_response(await weekly_review.apply(user, request.match_info["id"], d.get("items")))
    except LookupError as e:
        return web.json_response({"error": str(e)}, status=404)
    except ValueError as e:
        return web.json_response({"error": str(e)}, status=400)


async def review_dismiss_api(request: web.Request) -> web.Response:
    user = request_user(request)
    weekly_review.require(user)
    if not weekly_review.dismiss(user, request.match_info["id"]):
        return web.json_response({"error": "Review not found"}, status=404)
    return web.json_response({"status": "dismissed"})


def setup(app: web.Application) -> None:
    r = app.router
    r.add_get("/api/briefing/today", briefing_today_api)
    r.add_post("/api/briefing/generate", briefing_generate_api)
    r.add_get("/api/review/latest", review_latest_api)
    r.add_post("/api/review/generate", review_generate_api)
    r.add_get("/api/review/{id}", review_get_api)
    r.add_post("/api/review/{id}/apply", review_apply_api)
    r.add_post("/api/review/{id}/dismiss", review_dismiss_api)
