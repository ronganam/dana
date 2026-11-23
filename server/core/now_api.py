"""Routes for the Now screen, the self-check and search (contract: docs/now.md).

- ``GET /api/now`` — the queue, quiet suggestions, notification counts, lede, day slots
- ``POST /api/now/act {id, action, args?}`` → ``{ok, message, undo_id?, …}``
- ``GET /api/checks`` → ``{checks: [{key, label, ok, detail, since, ignored_until, title, why, blocks, fix, addon,
  server}]}`` (the caller's checks, their reconnects first: Settings → Needs attention)
- ``POST /api/checks/{key}/ignore {days?}`` → ``{ok, message, until, undo_id}`` (only for the caller)
- ``POST /api/checks/{key}/fix {fix}`` → ``{ok, message}`` (the check's server fix, for the caller only)
- ``GET /api/search?q=&limit=`` → ``{results: [{kind, id, title, subtitle}]}`` (the caller's items)

Every route answers for the request's user (core/now.py, core/checks.py, core/search.py).

The Notifications screen's endpoints (``/api/notifications/overview``,
``/api/notifications/rule-suggestions``) live in addons/phone/notifications/api.py
next to the other notification routes, so they are matched before ``/api/notifications/{id}``.
"""
import asyncio
import logging

from aiohttp import web

from core import cards, checks, now, search
from core.auth import request_user
from core.addons.store import NotConnected

logger = logging.getLogger(__name__)


async def _json(request: web.Request) -> dict:
    try:
        data = await request.json() if request.can_read_body else {}
    except Exception:
        raise web.HTTPBadRequest(text='{"error": "Invalid JSON"}', content_type="application/json")
    return data if isinstance(data, dict) else {}


async def now_api(request: web.Request) -> web.Response:
    return web.json_response(await now.build(request_user(request)))


async def now_act_api(request: web.Request) -> web.Response:
    data = await _json(request)
    try:
        result = await now.act(request_user(request), str(data.get("id") or ""), str(data.get("action") or ""),
                               data.get("args") or {})
    except cards.ActError as e:
        return web.json_response({"ok": False, "error": str(e)}, status=e.status)
    except NotConnected:
        raise  # answered by core.auth.addon_middleware
    except Exception as e:
        logger.error(f"Now action {data.get('action')} on {data.get('id')} failed: {e}", exc_info=True)
        return web.json_response({"ok": False, "error": f"That didn't work: {e}"}, status=500)
    return web.json_response(result)


async def checks_api(request: web.Request) -> web.Response:
    """The caller's checks: those of their own addons (the server's own only with ``see_everyone``)."""
    items = await asyncio.to_thread(checks.for_user, request_user(request))
    return web.json_response({"checks": [checks.public(c) for c in items]})


async def check_ignore_api(request: web.Request) -> web.Response:
    data = await _json(request)
    try:
        days = float(data.get("days", 7))
        return web.json_response(checks.ignore(request_user(request), request.match_info["key"], days))
    except LookupError as e:
        return web.json_response({"ok": False, "error": str(e)}, status=404)
    except (TypeError, ValueError):
        return web.json_response({"ok": False, "error": "days must be a number"}, status=400)


async def check_fix_api(request: web.Request) -> web.Response:
    """Run the server fix a failing check of the caller offers (core/checks.py ``run_fix``)."""
    data = await _json(request)
    try:
        return web.json_response(await checks.run_fix(request_user(request), request.match_info["key"],
                                                      str(data.get("fix") or "")))
    except LookupError as e:
        return web.json_response({"ok": False, "error": str(e)}, status=404)
    except NotConnected:
        raise  # answered by core.auth.addon_middleware
    except ValueError as e:
        return web.json_response({"ok": False, "error": str(e)}, status=409)
    except Exception as e:
        logger.error(f"Fix {data.get('fix')} of check {request.match_info['key']} failed: {e}", exc_info=True)
        return web.json_response({"ok": False, "error": f"That didn't work: {e}"}, status=500)


async def search_api(request: web.Request) -> web.Response:
    try:
        limit = int(request.query.get("limit", "5") or 5)
    except ValueError:
        limit = 5
    results = await asyncio.to_thread(search.search, request_user(request), request.query.get("q", ""), limit)
    return web.json_response({"results": results})


def setup(app: web.Application) -> None:
    app.router.add_get("/api/now", now_api)
    app.router.add_post("/api/now/act", now_act_api)
    app.router.add_get("/api/checks", checks_api)
    app.router.add_post("/api/checks/{key}/ignore", check_ignore_api)
    app.router.add_post("/api/checks/{key}/fix", check_fix_api)
    app.router.add_get("/api/search", search_api)
