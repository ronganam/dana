"""Live activities over HTTP (docs/live.md; core/live.py).

    GET    /api/live                                   → {activities: [<wire>, ...]} (newest-updated first)
    POST   /api/live/{id}/act {action, choice?}        → {activity: <wire> | null} (null: it ended)
    POST   /api/live/{id}/select {choice}              → {activity: <wire> | null}
    DELETE /api/live/{id}                              → {ok: true} (stop; an ended one is dismissed)

``{id}`` is ``<addon>:<key>``. Every signed-in user, for their own
activities only: another user's id answers 404 ``not_found`` like a missing
one. An action the activity does not have: 400 ``unknown_action``; a choice it
does not have: 400 ``unknown_choice``; a body that is not a JSON object: 400
``invalid``. The phone redraws from these replies at once; the ``live`` push
follows for the user's other devices.
"""

import json
from typing import Any, Dict, Optional, Union

from aiohttp import web

from core import live
from core.auth import request_user

NOT_FOUND = {"error": "not_found"}


async def _body(request: web.Request) -> Union[Dict[str, Any], web.Response]:
    try:
        body = await request.json()
    except (json.JSONDecodeError, ValueError):
        body = None
    if not isinstance(body, dict):
        return web.json_response({"error": "invalid", "detail": "a JSON object is expected"}, status=400)
    return body


def _text(value: Any) -> Optional[str]:
    return value if isinstance(value, str) and value else None


def _reply(current: Optional[live.Current]) -> web.Response:
    return web.json_response({"activity": live.to_json(current) if current is not None else None})


async def list_api(request: web.Request) -> web.Response:
    """GET /api/live"""
    return web.json_response(live.payload(request_user(request)))


async def act_api(request: web.Request) -> web.Response:
    """POST /api/live/{id}/act"""
    body = await _body(request)
    if isinstance(body, web.Response):
        return body
    action = _text(body.get("action"))
    if action is None:
        return web.json_response({"error": "unknown_action"}, status=400)
    try:
        current = await live.act(request_user(request), request.match_info["id"], action, _text(body.get("choice")))
    except LookupError:
        return web.json_response(NOT_FOUND, status=404)
    except live.UnknownAction:
        return web.json_response({"error": "unknown_action"}, status=400)
    except live.UnknownChoice:
        return web.json_response({"error": "unknown_choice"}, status=400)
    return _reply(current)


async def select_api(request: web.Request) -> web.Response:
    """POST /api/live/{id}/select"""
    body = await _body(request)
    if isinstance(body, web.Response):
        return body
    try:
        current = await live.select(request_user(request), request.match_info["id"], _text(body.get("choice")) or "")
    except LookupError:
        return web.json_response(NOT_FOUND, status=404)
    except live.UnknownChoice:
        return web.json_response({"error": "unknown_choice"}, status=400)
    return _reply(current)


async def stop_api(request: web.Request) -> web.Response:
    """DELETE /api/live/{id}"""
    if not await live.stop(request_user(request), request.match_info["id"]):
        return web.json_response(NOT_FOUND, status=404)
    return web.json_response({"ok": True})


def setup(app: web.Application) -> None:
    """Register the live activity routes."""
    app.router.add_get("/api/live", list_api)
    app.router.add_post("/api/live/{id}/act", act_api)
    app.router.add_post("/api/live/{id}/select", select_api)
    app.router.add_delete("/api/live/{id}", stop_api)
