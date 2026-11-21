"""HTTP routes of the shopping list (docs/shopping.md, "API").

    GET    /api/shopping                        the whole screen
    GET    /api/shopping/items                  the items only (an array)
    POST   /api/shopping/items                  {text} | {items: ["milk", {name, quantity, note, id}]}
    PATCH  /api/shopping/items/{id}             any of {checked, name, quantity, note, category}
    DELETE /api/shopping/items/{id}
    POST   /api/shopping/clear-bought
    GET    /api/shopping/suggestions?q=         → {suggestions: [{name, category, times}]}
    DELETE /api/shopping/suggestions            {name}
    GET    /api/shopping/events                 server-sent events: changed {list_id, version}, lists {}, end {}
    GET    /api/shopping/lists                  → {active, lists}
    PUT    /api/shopping/active                 {list_id}
    POST   /api/shopping/invites/{list_id}      {action: use | dismiss, move_items}

Every route answers for the request's user, who must have ``shopping``
connected (else 403 ``not_connected`` from the integration middleware), on
their active list; ``?list=<id>`` names another list they can see (one they
cannot see: 404 like an unknown one). A write on a list shared read-only:
403 ``{"error": "read_only"}``. Every write answers with the screen fields
plus ``undo`` (``{id, label}`` or null) and, for an add, ``results``.
Loaded by core/server.py (``setup(app)``).
"""

import asyncio
import json
import logging
from typing import Any, Dict, Optional

from aiohttp import web

from core.auth import request_user
from core.users import User
from integrations.framework import store
from integrations.shopping import events, history, invites, items, lists
from integrations.shopping.lists import ShoppingList
from integrations.shopping.names import TooMany, item_input, parse_text
from integrations.shopping.screen import screen
from integrations.shopping.values import KIND, MAX_ITEMS, Duplicate, NotFound, ReadOnly

logger = logging.getLogger(__name__)


def shopping_user(request: web.Request) -> User:
    """The request's user, who must have ``shopping`` connected (``NotConnected`` → 403)."""
    user = request_user(request)
    store.require(user, KIND)
    return user


class _Refused(Exception):
    def __init__(self, response: web.Response) -> None:
        super().__init__()
        self.response = response


def _error(status: int, error: str, message: str = "", **extra: Any) -> web.Response:
    body: Dict[str, Any] = {"error": error, **extra}
    if message:
        body["message"] = message
    return web.json_response(body, status=status)


def _target(request: web.Request, user: User) -> ShoppingList:
    """The list the request is about: ``?list=`` (one the user can see) or their active list."""
    wanted = request.query.get("list")
    if not wanted:
        return lists.active_list(user)
    lst = lists.get_list(user, wanted)
    if lst is None:
        raise _Refused(_error(404, "not_found", "Shopping list not found"))
    return lst


async def _body(request: web.Request) -> Dict[str, Any]:
    try:
        data = await request.json()
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
        raise _Refused(_error(400, "invalid", "The body must be JSON.")) from None
    if not isinstance(data, dict):
        raise _Refused(_error(400, "invalid", "The body must be a JSON object."))
    return data


def _answer(user: User, lst: ShoppingList, undo: Optional[Dict[str, str]], **extra: Any) -> web.Response:
    """A write's answer: the screen of ``lst`` (as the user sees it now), ``undo`` and ``extra``."""
    fresh = lists.get_list(user, lst.id) or lists.active_list(user)
    return web.json_response({**screen(user, fresh), "undo": undo, **extra})


def _route(handler):
    """Turn the service's refusals into their HTTP answers (``NotConnected`` passes through)."""
    async def wrapped(request: web.Request) -> web.StreamResponse:
        try:
            return await handler(request)
        except _Refused as r:
            return r.response
        except ReadOnly as e:
            return _error(403, "read_only", str(e))
        except NotFound as e:
            return _error(404, "not_found", str(e))
        except Duplicate as e:
            return _error(409, "duplicate", str(e))
        except TooMany as e:
            return _error(400, "too_many", str(e), max=MAX_ITEMS)
        except ValueError as e:
            return _error(400, "invalid", str(e))
    wrapped.__name__ = handler.__name__
    wrapped.__doc__ = handler.__doc__
    return wrapped


@_route
async def screen_api(request: web.Request) -> web.Response:
    """GET /api/shopping — the whole screen."""
    user = shopping_user(request)
    return web.json_response(screen(user, _target(request, user)))


@_route
async def items_api(request: web.Request) -> web.Response:
    """GET /api/shopping/items — the items only, in display order (a plain array)."""
    user = shopping_user(request)
    return web.json_response(items.list_items(_target(request, user)))


@_route
async def add_api(request: web.Request) -> web.Response:
    """POST /api/shopping/items {text} | {items: [...]} → screen + results + undo."""
    user = shopping_user(request)
    lst = _target(request, user)
    data = await _body(request)
    if isinstance(data.get("items"), list):
        parsed = [item_input(x) for x in data["items"]]
        wanted = [x for x in parsed if x is not None]
    elif isinstance(data.get("text"), str):
        wanted = parse_text(data["text"])
    else:
        return _error(400, "invalid", "Send {text} or {items: [...]}.")
    if not wanted:
        return _error(400, "invalid", "Nothing to add.")
    added = items.add(user, lst, wanted, source="api")
    return _answer(user, lst, added.undo, results=added.results)


@_route
async def update_api(request: web.Request) -> web.Response:
    """PATCH /api/shopping/items/{id} any of {checked, name, quantity, note, category}."""
    user = shopping_user(request)
    lst = _target(request, user)
    data = await _body(request)
    changes = {k: data[k] for k in ("checked", "name", "quantity", "note", "category") if k in data}
    if not changes:
        return _error(400, "invalid", "Nothing to change.")
    undo = items.update(user, lst, request.match_info["id"], changes, source="api")
    return _answer(user, lst, undo)


@_route
async def delete_api(request: web.Request) -> web.Response:
    """DELETE /api/shopping/items/{id}"""
    user = shopping_user(request)
    lst = _target(request, user)
    _, undo = items.remove(user, lst, [request.match_info["id"]], source="api")
    return _answer(user, lst, undo)


@_route
async def clear_bought_api(request: web.Request) -> web.Response:
    """POST /api/shopping/clear-bought"""
    user = shopping_user(request)
    lst = _target(request, user)
    _, undo = items.clear_bought(user, lst, source="api")
    return _answer(user, lst, undo)


@_route
async def suggestions_api(request: web.Request) -> web.Response:
    """GET /api/shopping/suggestions?q= → {suggestions} (no q: the usuals)."""
    user = shopping_user(request)
    lst = _target(request, user)
    return web.json_response({"suggestions": history.matching(lst.id, request.query.get("q", ""))})


@_route
async def forget_api(request: web.Request) -> web.Response:
    """DELETE /api/shopping/suggestions {name}"""
    user = shopping_user(request)
    lst = _target(request, user)
    data = await _body(request)
    name = str(data.get("name") or "").strip()
    if not name:
        return _error(400, "invalid", "name is required.")
    undo = items.forget_suggestion(user, lst, name, source="api")
    return _answer(user, lst, undo)


@_route
async def lists_api(request: web.Request) -> web.Response:
    """GET /api/shopping/lists → {active, lists: [{id, name, owner, access, active}]}"""
    return web.json_response(lists.lists_payload(shopping_user(request)))


@_route
async def active_api(request: web.Request) -> web.Response:
    """PUT /api/shopping/active {list_id} → the new active list's screen + undo."""
    user = shopping_user(request)
    data = await _body(request)
    list_id = str(data.get("list_id") or "").strip()
    if not list_id:
        return _error(400, "invalid", "list_id is required.")
    chosen, undo = lists.choose(user, list_id, source="api")
    return _answer(user, chosen, undo)


@_route
async def invite_api(request: web.Request) -> web.Response:
    """POST /api/shopping/invites/{list_id} {action: use | dismiss, move_items}"""
    user = shopping_user(request)
    data = await _body(request)
    action = data.get("action")
    list_id = request.match_info["list_id"]
    if action == invites.USE:
        _, undo = invites.use(user, list_id, move_items=bool(data.get("move_items")))
    elif action == invites.DISMISS:
        undo = invites.dismiss(user, list_id)
    else:
        return _error(400, "invalid", "action must be 'use' or 'dismiss'.")
    return _answer(user, lists.active_list(user), undo)


def _frame(event: str, data: Dict[str, Any]) -> bytes:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n".encode("utf-8")


async def events_api(request: web.Request) -> web.StreamResponse:
    """GET /api/shopping/events — server-sent events for the caller's active list (or ``?list=``).

    ``changed`` ``{list_id, version}`` first with the current version, then on
    every change (and when the followed active list switches); ``lists`` ``{}``
    when the caller's lists, invitations or active list changed (read the
    screen again); a comment line every 25 seconds. A ``?list=`` stream sends
    ``end`` ``{}`` and closes when the caller can no longer see that list."""
    user = shopping_user(request)
    wanted = request.query.get("list")
    fixed = lists.get_list(user, wanted) if wanted else None
    if wanted and fixed is None:
        return _error(404, "not_found", "Shopping list not found")
    response = web.StreamResponse(headers={"Content-Type": "text/event-stream", "Cache-Control": "no-cache",
                                           "X-Accel-Buffering": "no"})
    await response.prepare(request)
    listener = events.Listener(user.id)
    try:
        current = fixed or lists.active_list(user)
        listener.follow(current.id)
        await response.write(_frame("changed", {"list_id": current.id, "version": events.version_of(current.id)}))
        while True:
            try:
                message = await asyncio.wait_for(listener.queue.get(), events.PING_EVERY)
            except asyncio.TimeoutError:
                await response.write(b": ping\n\n")
                continue
            if message[0] == "changed":
                await response.write(_frame("changed", {"list_id": message[1], "version": message[2]}))
                continue
            if wanted:
                if lists.get_list(user, wanted) is None or not store.is_connected(user, KIND):
                    await response.write(_frame("end", {}))
                    break
            elif store.is_connected(user, KIND):
                now = lists.active_list(user)
                if now.id != listener.list_id:
                    listener.follow(now.id)
                    await response.write(_frame("changed", {"list_id": now.id, "version": events.version_of(now.id)}))
            await response.write(_frame("lists", {}))
    except ConnectionResetError:
        pass  # the client went away
    finally:
        listener.close()
    return response


def setup(app: web.Application) -> None:
    r = app.router
    r.add_get("/api/shopping", screen_api)
    r.add_get("/api/shopping/items", items_api)
    r.add_post("/api/shopping/items", add_api)
    r.add_patch("/api/shopping/items/{id}", update_api)
    r.add_delete("/api/shopping/items/{id}", delete_api)
    r.add_post("/api/shopping/clear-bought", clear_bought_api)
    r.add_get("/api/shopping/suggestions", suggestions_api)
    r.add_delete("/api/shopping/suggestions", forget_api)
    r.add_get("/api/shopping/events", events_api)
    r.add_get("/api/shopping/lists", lists_api)
    r.add_put("/api/shopping/active", active_api)
    r.add_post("/api/shopping/invites/{list_id}", invite_api)
