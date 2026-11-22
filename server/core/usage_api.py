"""HTTP routes for the Usage screen (core/usage.py).

    GET /api/usage?range=30d&kind=&purpose=&source=&model=&user=
        → {range, from, to, filters, totals, previous, by_day: [{day, cost, calls}],
           breakdown: {kind|purpose|source|model: [{key, label, calls, failures, cost, unpriced,
                       input_tokens, output_tokens, cached_tokens, avg_ms}]}, raw_days}
        range: today · 7d · 30d · 90d · 1y · all. Costs are USD; ``unpriced`` counts successful
        calls whose price is unknown (not included in ``cost``). Plus ``budget``, the server's
        monthly budget (core/budget.py): ``{limit, spent, forecast, state: ok|warn|paused, month,
        resets, can_edit: true}`` for whoever may manage the server, ``{state, can_edit: false}``
        for anyone else (no amounts: the spend is everyone's).
    PUT /api/usage/budget {limit: <USD> | null} (``manage_server``) → the budget as above;
        null or 0 removes it; 400 for a negative or non-numeric limit.
    GET /api/usage/events?range=7d&sort=recent|cost&limit=50&before=<id>
                         &kind=&purpose=&source=&model=&ref=&ok=0|1&user=
        → {events: [{id, ts, kind, purpose, purpose_label, source, ref, provider, model, input_tokens,
                     output_tokens, cached_tokens, reasoning_tokens, units, cost, ms, ok, error}],
           next, raw_days}
        Individual calls are kept for ``USAGE_RAW_DAYS``; ``next`` is the cursor for the next
        page (recent order). ``ref`` is ``session:<id>`` · ``call:<id>`` · ``notification:<id>`` ·
        ``person:<id>`` · ``app:<package>``.

Both flush the write buffer first, so the screen is never behind. Shapes: docs/usage.md.

Both answer for the caller's own usage. An ``admin`` (access level with
``see_everyone``) may add ``user=<user id>`` for someone else's or ``user=all``
for everyone's (calls made for no user included); anyone else asking for more
than their own gets 403.
"""
import asyncio
import json
import logging
from typing import Optional

from aiohttp import web

from core import budget, usage, users
from core.auth import request_user, requires

logger = logging.getLogger(__name__)


def _err(message: str, status: int = 400) -> web.Response:
    return web.json_response({"ok": False, "error": message}, status=status)


async def _fresh() -> None:
    try:
        await usage.flush()
    except Exception as e:  # reads still work from what is stored
        logger.warning(f"Usage flush before read failed: {e}")


def _refuse(status: type, message: str) -> web.HTTPException:
    return status(text=json.dumps({"ok": False, "error": message}), content_type="application/json")


def _scope(request: web.Request) -> Optional[str]:
    """Whose usage to read: a user id, or None for everyone.

    Raises:
        web.HTTPForbidden: a non-admin asked for more than their own.
        web.HTTPNotFound: no such user.
    """
    me = request_user(request)
    wanted = (request.query.get("user") or "").strip()
    if not wanted or wanted == me.id:
        return me.id
    if not me.level.see_everyone:
        raise _refuse(web.HTTPForbidden, "You can only see your own usage.")
    if wanted == "all":
        return None
    if users.get(wanted) is None:
        raise _refuse(web.HTTPNotFound, f"No user {wanted!r}.")
    return wanted


def _budget(request: web.Request) -> dict:
    """The monthly budget as the caller may see it (amounts only for whoever may manage the server)."""
    st = budget.state()
    if request_user(request).level.allows("manage_server"):
        return {**st, "can_edit": True}
    return {"state": st["state"], "can_edit": False}


async def usage_api(request: web.Request) -> web.Response:
    q = request.query
    user_id = _scope(request)
    await _fresh()
    budget.forget_cache()  # the screen shows the spend as of now
    try:
        data = await asyncio.to_thread(usage.summary, q.get("range", "30d"), dict(q), user_id)
        data["budget"] = await asyncio.to_thread(_budget, request)
    except ValueError as e:
        return _err(str(e))
    except Exception as e:
        logger.error(f"GET /api/usage failed: {e}", exc_info=True)
        return _err(str(e), 500)
    return web.json_response(data)


@requires("manage_server")
async def budget_put_api(request: web.Request) -> web.Response:
    try:
        body = await request.json()
    except Exception:
        return _err("expected a JSON body {limit}")
    if not isinstance(body, dict) or "limit" not in body:
        return _err("expected a JSON body {limit}")
    await _fresh()
    try:
        st = await asyncio.to_thread(budget.set_limit, body["limit"], by=request_user(request).id)
    except ValueError as e:
        return _err(str(e))
    return web.json_response({**st, "can_edit": True})


async def usage_events_api(request: web.Request) -> web.Response:
    q = request.query
    sort = q.get("sort", "recent")
    if sort not in ("recent", "cost"):
        return _err("sort must be recent or cost")
    try:
        limit = int(q.get("limit", "50") or 50)
        before = int(q["before"]) if q.get("before") else None
        if q.get("ok") not in (None, "", "0", "1"):
            return _err("ok must be 0 or 1")
    except ValueError:
        return _err("limit and before must be numbers")
    user_id = _scope(request)
    await _fresh()
    try:
        data = await asyncio.to_thread(usage.events, limit, before, sort, q.get("range", "7d"), dict(q), user_id)
    except ValueError as e:
        return _err(str(e))
    except Exception as e:
        logger.error(f"GET /api/usage/events failed: {e}", exc_info=True)
        return _err(str(e), 500)
    return web.json_response(data)


async def _on_cleanup(app: web.Application) -> None:
    task = app.get("usage_task")
    if task:
        task.cancel()  # run_loop flushes once more on the way out
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass
    try:
        await usage.flush()
    except Exception as e:
        logger.warning(f"Final usage flush failed: {e}")


def setup(app: web.Application) -> None:
    app.router.add_get("/api/usage", usage_api)
    app.router.add_get("/api/usage/events", usage_events_api)
    app.router.add_put("/api/usage/budget", budget_put_api)
    app.on_cleanup.append(_on_cleanup)
