"""HTTP routes of the watches addon (docs/watches.md, "API").

    GET    /api/watches                    → {watches: [Watch]}
    POST   /api/watches                    {text} → 201 {watch}; 400 {error, errors}
    GET    /api/watches/catalog            → {event_types, ops, action_types, tools}
    POST   /api/watches/dry-run            {spec, days} → {triggers: {id: {matches, per_day, over_budget, sample}}}
    GET    /api/watches/pipeline           → {stats, recent, watches_active, triggers_active}
    GET    /api/watches/pipeline/stream    server-sent events: one ``data: <PipelineEvent>`` per step
    GET    /api/watches/{id}               → {watch, fires} (newest first, last 50)
    GET    /api/watches/{id}/trace         ?limit=&outcome=all|near|<outcome> → {trace, unstored}
    PATCH  /api/watches/{id}               {spec} | {status} | {change} → {watch}; 400 {error, errors}
    DELETE /api/watches/{id}               → {ok, undo_id}
    GET    /watches                        the web app, opened on the Watches page

Every route answers for the request's user, who must have ``watches``
connected (403 ``not_connected``); another user's watch id answers 404 like
an unknown one.
"""

import asyncio
import json
from typing import Any, Dict, List

from aiohttp import web

from core import pages_api, users
from core.addons import registry
from core.addons import store as addon_store
from core.auth import request_user
from core.users import User
from addons.watches import compiler, engine, pipeline, service, store, trace
from addons.watches import spec as S
from addons.watches.addon import ADDON

PING_EVERY = 20.0
MAX_DRY_RUN_DAYS = 60
MAX_TRACE = 500


def watches_user(request: web.Request) -> User:
    """The request's user, who must have ``watches`` connected (``NotConnected`` → 403)."""
    user = request_user(request)
    addon_store.require(user, ADDON)
    return user


def _invalid(errors: List[str], status: int = 400) -> web.Response:
    return web.json_response({"error": "invalid", "errors": errors}, status=status)


def _not_found() -> web.Response:
    return web.json_response({"error": "not_found"}, status=404)


async def _body(request: web.Request) -> Dict[str, Any]:
    try:
        body = await request.json()
    except (ValueError, json.JSONDecodeError):
        return {}
    return body if isinstance(body, dict) else {}


async def list_api(request: web.Request) -> web.Response:
    user = watches_user(request)
    return web.json_response({"watches": [service.watch_json(user, r) for r in store.list_for(user)]})


async def create_api(request: web.Request) -> web.Response:
    user = watches_user(request)
    text = str((await _body(request)).get("text") or "").strip()
    if not text:
        return _invalid(["text is required"])
    with users.acting_as(user):
        try:
            row, _ = await service.create_from_text(user, text)
        except compiler.CompileError as e:
            return _invalid(e.errors)
    return web.json_response({"watch": service.watch_json(user, row)}, status=201)


async def get_api(request: web.Request) -> web.Response:
    user = watches_user(request)
    row = store.get(user, request.match_info["id"])
    if row is None:
        return _not_found()
    return web.json_response({"watch": service.watch_json(user, row),
                              "fires": [service.fire_json(f) for f in store.fires(user, row["id"], 50)]})


async def trace_api(request: web.Request) -> web.Response:
    """GET /api/watches/{id}/trace — the watch's recent checks, newest first (``limit`` ≤ 500, default 100;
    ``outcome``: all, near — near misses and fires —, or one outcome such as fired)."""
    user = watches_user(request)
    row = store.get(user, request.match_info["id"])
    if row is None:
        return _not_found()
    try:
        limit = max(1, min(int(request.query.get("limit") or 100), MAX_TRACE))
    except ValueError:
        return _invalid(["limit must be a number"])
    outcome = request.query.get("outcome") or "all"
    if outcome not in ("all", "near", *trace.OUTCOMES):
        return _invalid([f"outcome must be all, near or one of {', '.join(trace.OUTCOMES)}"])
    return web.json_response({"trace": [service.trace_json(t) for t in store.trace(user, row["id"], limit, outcome)],
                              "unstored": trace.unstored(row["id"])})


async def patch_api(request: web.Request) -> web.Response:
    user = watches_user(request)
    row = store.get(user, request.match_info["id"])
    if row is None:
        return _not_found()
    body = await _body(request)
    with users.acting_as(user):
        try:
            if "spec" in body:
                row = await service.set_spec(user, row, service.validate(user, body["spec"]))
            elif "status" in body:
                row = service.set_status(user, row, str(body["status"]))
            elif str(body.get("change") or "").strip():
                row = await service.change(user, row, str(body["change"]).strip())
            else:
                return _invalid(["give spec, status or change"])
        except service.Invalid as e:
            return _invalid(e.errors)
        except compiler.CompileError as e:
            return _invalid(e.errors)
    return web.json_response({"watch": service.watch_json(user, row)})


async def delete_api(request: web.Request) -> web.Response:
    user = watches_user(request)
    with users.acting_as(user):
        undo_id = service.delete(user, request.match_info["id"])
    if undo_id is None:
        return _not_found()
    return web.json_response({"ok": True, "undo_id": undo_id})


async def dry_run_api(request: web.Request) -> web.Response:
    user = watches_user(request)
    body = await _body(request)
    try:
        days = max(1, min(int(body.get("days") or S.DRY_RUN_DAYS), MAX_DRY_RUN_DAYS))
    except (TypeError, ValueError):
        return _invalid(["days must be a number"])
    try:
        sp = service.validate(user, body.get("spec"))
    except service.Invalid as e:
        return _invalid(e.errors)
    types = {t.get("event_type") for t in sp["triggers"] if t.get("kind") == "event"}
    hist = engine.history(user, days, only=types)
    return web.json_response({"triggers": S.dry_run(sp, hist, days)})


async def catalog_api(request: web.Request) -> web.Response:
    user = watches_user(request)
    return web.json_response({
        "event_types": [{"name": e.name, "about": e.about, "fields": dict(e.fields)}
                        for e in engine.catalog(user).values()],
        "ops": S.OPS, "action_types": S.ACTIONS, "tools": [t.name for t in registry.tools_for(user)]})


async def pipeline_api(request: web.Request) -> web.Response:
    user = watches_user(request)
    live = engine.active(user)
    return web.json_response({"stats": pipeline.stats(user.id), "recent": pipeline.recent(user.id, 100),
                              "watches_active": len(live),
                              "triggers_active": sum(len(w["spec"].get("triggers") or []) for w in live)})


async def stream_api(request: web.Request) -> web.StreamResponse:
    """GET /api/watches/pipeline/stream — every pipeline step of the caller, live; a comment line every 20 s."""
    user = watches_user(request)
    response = web.StreamResponse(headers={"Content-Type": "text/event-stream", "Cache-Control": "no-cache",
                                           "X-Accel-Buffering": "no"})
    await response.prepare(request)
    q = pipeline.subscribe(user.id)
    try:
        await response.write(b": connected\n\n")
        while True:
            try:
                ev = await asyncio.wait_for(q.get(), PING_EVERY)
            except asyncio.TimeoutError:
                if not addon_store.is_connected(user, ADDON):
                    break
                await response.write(b": ping\n\n")
                continue
            await response.write(f"data: {json.dumps(ev, ensure_ascii=False)}\n\n".encode("utf-8"))
    except ConnectionResetError:
        pass
    finally:
        pipeline.unsubscribe(user.id, q)
    return response


def setup(app: web.Application) -> None:
    r = app.router
    r.add_get("/api/watches", list_api)
    r.add_post("/api/watches", create_api)
    r.add_get("/api/watches/catalog", catalog_api)
    r.add_post("/api/watches/dry-run", dry_run_api)
    r.add_get("/api/watches/pipeline", pipeline_api)
    r.add_get("/api/watches/pipeline/stream", stream_api)
    r.add_get("/api/watches/{id}", get_api)
    r.add_get("/api/watches/{id}/trace", trace_api)
    r.add_patch("/api/watches/{id}", patch_api)
    r.add_delete("/api/watches/{id}", delete_api)
    r.add_get("/watches", pages_api.index_handler)
