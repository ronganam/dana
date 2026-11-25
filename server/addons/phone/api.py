"""HTTP routes of the phone addon: suggestions (from calls and notifications)
and direct push to the phone (docs/calls.md, docs/notifications.md).

- ``GET /api/suggestions``, ``POST /api/suggestions/bulk``, ``PUT /api/suggestions/{id}``,
  ``POST /api/suggestions/{id}/accept``, ``POST /api/suggestions/{id}/dismiss``
- ``POST /api/phone/register``, ``GET /api/phone/events``, ``GET /api/phone/devices``, ``POST /api/phone/test``
- ``/api/phone/app-icons*``: the phone's app icons (``app_icons.py``)
- ``POST /api/agent-sessions/hook``: coding-agent hook events, shown as live activities (``agent_sessions.py``)
- ``POST /api/signals``: the phone's signals (addons/phone/signals.py; ``GET
  /api/signals``, the overview, is core's: core/signals_api.py)

Every route answers for the request's user, who must have ``phone``
(``access.phone_user``; else 403 ``not_connected`` from the middleware). The
calls and notifications routes are in ``calls/api.py`` and ``notifications/api.py``.
"""

import asyncio
import logging
from typing import Set

from aiohttp import web

from core.addons import store
from core.auth import request_device, request_token_id
from core.addons.store import NotConnected
from addons.phone.access import phone_user
from addons.phone.calls import api as calls_api
from addons.phone.notifications import api as notifications_api
from addons.phone import (agent_sessions, app_icons, config as phone_config, push as phone_push, signals as phone_signals,
                          suggestions as phone_suggestions)
from addons.phone.calls import actions as suggestion_actions

logger = logging.getLogger(__name__)


async def get_suggestions_api(request: web.Request) -> web.Response:
    """GET /api/suggestions?status=pending|accepted|dismissed|all&call_id=&source=call|notification|all

    ``source`` defaults to ``call`` so app versions that predate notification
    suggestions keep seeing only call suggestions. Only the caller's own."""
    user = phone_user(request)
    status = request.query.get("status", "pending")
    source = request.query.get("source", "call")
    items = phone_suggestions.list_suggestions(user, None if status == "all" else status,
                                               call_id=request.query.get("call_id") or None,
                                               source=None if source == "all" else source,
                                               limit=int(request.query.get("limit", "100") or 100))
    return web.json_response(items)


async def bulk_suggestions_api(request: web.Request) -> web.Response:
    """POST /api/suggestions/bulk {ids: [...], action: "dismiss"} → {dismissed}"""
    user = phone_user(request)
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON"}, status=400)
    if (data or {}).get("action") != "dismiss":
        return web.json_response({"error": "action must be dismiss"}, status=400)
    ids = [i for i in (data.get("ids") or []) if isinstance(i, str)][:500]
    done = sum(1 for sid in ids if suggestion_actions.dismiss_suggestion(user, sid))
    return web.json_response({"dismissed": done})


async def accept_suggestion_api(request: web.Request) -> web.Response:
    """POST /api/suggestions/{id}/accept {payload overrides…} → creates the task/event, for the caller."""
    user = phone_user(request)
    sid = request.match_info.get("id")
    try:
        overrides = await request.json() if request.can_read_body else {}
    except Exception:
        overrides = {}
    try:
        result = await suggestion_actions.accept_suggestion(user, sid, overrides if isinstance(overrides, dict) else {})
        return web.json_response(result)
    except NotConnected:
        raise  # answered by core.auth.addon_middleware
    except LookupError as e:
        return web.json_response({"error": str(e)}, status=404)
    except ValueError as e:
        return web.json_response({"error": str(e)}, status=400)
    except Exception as e:
        logger.error(f"Accepting suggestion {sid} failed: {e}", exc_info=True)
        return web.json_response({"error": str(e)}, status=500)


async def update_suggestion_api(request: web.Request) -> web.Response:
    """PUT /api/suggestions/{id} {payload changes…} — edit the caller's pending suggestion."""
    user = phone_user(request)
    sid = request.match_info.get("id")
    try:
        changes = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON"}, status=400)
    updated = suggestion_actions.update_suggestion_payload(user, sid, changes if isinstance(changes, dict) else {})
    if not updated:
        return web.json_response({"error": "Suggestion not found or not pending"}, status=404)
    return web.json_response(updated)


async def dismiss_suggestion_api(request: web.Request) -> web.Response:
    """POST /api/suggestions/{id}/dismiss"""
    user = phone_user(request)
    sid = request.match_info.get("id")
    if not suggestion_actions.dismiss_suggestion(user, sid):
        return web.json_response({"error": "Suggestion not found"}, status=404)
    return web.json_response({"status": "dismissed", "id": sid})


async def phone_register_api(request: web.Request) -> web.Response:
    """POST /api/phone/register {transport: "fcm", token} | {transport:
    "unifiedpush", endpoint} (+ app_version?) — the push registration of the
    paired device making the request (400 ``device_required`` from a token of
    no device; 400 ``fcm_not_configured`` / ``invalid_endpoint`` / ``invalid``
    with a ``detail``)."""
    user = phone_user(request)
    device = request_device(request)
    if device is None:
        return web.json_response({"error": "device_required",
                                  "detail": "Register for push from a paired device (its own token)."}, status=400)
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON"}, status=400)
    if not isinstance(data, dict):
        return web.json_response({"error": "Invalid JSON"}, status=400)
    transport = str(data.get("transport") or "")
    address = str(data.get("endpoint" if transport == "unifiedpush" else "token") or "")
    try:
        return web.json_response(phone_push.register(user, device, transport, address,
                                                     str(data.get("app_version") or "")))
    except phone_push.Refused as e:
        return web.json_response({"error": e.code, "detail": str(e)}, status=400)
    except ValueError as e:
        return web.json_response({"error": "invalid", "detail": str(e)}, status=400)


async def phone_events_api(request: web.Request) -> web.Response:
    """GET /api/phone/events?after=<seq> — the caller's events the device hasn't
    handled yet (``registered``: whether the request's device is still
    registered for push; absent from a token of no device). ``device``: whether
    the request was made with a paired device's own token; false (an old token
    issued by hand) tells the app to pair again, and the fetch is recorded in
    the user's ``token_fetch`` mark (the token's id, never the token) for the
    ``push`` self-check."""
    user = phone_user(request)
    try:
        after = int(request.query.get("after", "0"))
    except ValueError:
        return web.json_response({"error": "after must be a number"}, status=400)
    device = request_device(request)
    res = phone_push.events_after(user, after, device)
    res["device"] = device is not None
    token_id = request_token_id(request)
    if device is None and token_id:
        phone_config.mark(phone_config.TOKEN_FETCH, user.id, True, token_id)
    return web.json_response(res)


async def post_signals_api(request: web.Request) -> web.Response:
    """POST /api/signals {events: [...]} — the caller's phone's signals (unlock,
    screen, car, zone, sleep), at most 200, oldest first → ``{accepted,
    rejected: [{index, error}]}``; a body that is not ``{"events": [...]}`` → 400."""
    user = phone_user(request)
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "invalid", "detail": "a JSON object is expected"}, status=400)
    events = data.get("events") if isinstance(data, dict) else None
    if not isinstance(events, list):
        return web.json_response({"error": "invalid", "detail": 'expected {"events": [...]}'}, status=400)
    if len(events) > phone_signals.MAX_EVENTS:
        return web.json_response({"error": "invalid", "detail": f"at most {phone_signals.MAX_EVENTS} events"},
                                 status=400)
    return web.json_response(await phone_signals.receive(user, events))


async def phone_devices_api(request: web.Request) -> web.Response:
    """GET /api/phone/devices — the caller's devices registered for push (transport, no tokens or endpoints) +
    whether FCM is configured."""
    user = phone_user(request)
    return web.json_response({"fcm_configured": phone_push.configured(), "latest_seq": phone_push.latest_seq(user),
                              "devices": phone_push.devices(user)})


async def phone_test_api(request: web.Request) -> web.Response:
    """POST /api/phone/test — a test notification on the caller's phone."""
    ok = await phone_push.notify(phone_user(request), "Dana push test", "Direct push works ✓", tag="dana_push_test")
    return web.json_response({"delivered": ok}, status=200 if ok else 502)


_hook_syncs: Set[asyncio.Task] = set()


async def agent_session_hook_api(request: web.Request) -> web.Response:
    """POST /api/agent-sessions/hook?agent=claude|codex&host=&open_url=&thread=&parent_thread=&title=
    — one coding-agent hook event as the agent gave it (JSON); the T3 Code
    thread it runs in, its root thread when it is a delegated child, and the
    thread's title. Answers at once; the session's live activity follows in
    the background (``agent_sessions.sync``)."""
    user = phone_user(request)
    try:
        event = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON"}, status=400)
    if not isinstance(event, dict):
        return web.json_response({"error": "Expected a JSON object"}, status=400)
    try:
        change = agent_sessions.record(user, request.query.get("agent", ""), request.query.get("host", ""), event,
                                       open_url=request.query.get("open_url", ""),
                                       thread=request.query.get("thread", ""),
                                       parent_thread=request.query.get("parent_thread", ""),
                                       title=request.query.get("title", ""))
    except ValueError as e:
        return web.json_response({"error": str(e)}, status=400)
    if change:
        task = asyncio.create_task(agent_sessions.sync(user))
        _hook_syncs.add(task)
        task.add_done_callback(_hook_syncs.discard)
    return web.json_response({"ok": True, "change": change})


def setup(app: web.Application) -> None:
    """Register the phone addon's routes: calls (``calls/api.py``), suggestions,
    push and app icons (``app_icons.py``), then notifications (``notifications/api.py``)."""
    calls_api.setup(app)
    app.router.add_get("/api/suggestions", get_suggestions_api)
    app.router.add_post("/api/suggestions/bulk", bulk_suggestions_api)
    app.router.add_post("/api/phone/register", phone_register_api)
    app.router.add_get("/api/phone/events", phone_events_api)
    app.router.add_post("/api/signals", post_signals_api)
    app.router.add_get("/api/phone/devices", phone_devices_api)
    app.router.add_post("/api/phone/test", phone_test_api)
    app_icons.setup(app)
    app.router.add_put("/api/suggestions/{id}", update_suggestion_api)
    app.router.add_post("/api/suggestions/{id}/accept", accept_suggestion_api)
    app.router.add_post("/api/suggestions/{id}/dismiss", dismiss_suggestion_api)
    app.router.add_post("/api/agent-sessions/hook", agent_session_hook_api)
    notifications_api.setup(app)
