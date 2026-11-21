"""HTTP routes for "About you" and the learner's startup.

    GET    /api/about                 → {sections: [{key, label, facts: [...]}], used_chars, max_chars}
    POST   /api/about/facts           {text, section?}   → 201 {ok, fact, undo_id}
    PUT    /api/about/facts/{id}      {text?, section?}  → {ok, fact, undo_id}
    DELETE /api/about/facts/{id}                          → {ok, undo_id}

Every route answers for the request's user, who must have ``about``
connected (else 403 ``not_connected`` from the integration middleware);
another user's fact id is 404 like an unknown one. Undo ids go to
``POST /api/undo/{id}`` (core/undo.py). Shapes: docs/about.md. Loaded by
core/server.py (``setup(app)``). The old ``/api/memories`` routes (old
clients, the old shape) are here too:

    GET    /api/memories              → [{key: "fact_<id>", value, category, updated_at}]
    POST   /api/memories              {key, value, category?} → {status, key}
    DELETE /api/memories/{key}                            → {status, key, undo_id?}
"""
import asyncio
import logging
from typing import Any, Dict, Optional

from aiohttp import web

from core import undo
from core.auth import request_user
from core.users import User
from integrations.about import facts, learner, legacy
from integrations.about.values import SECTION_KEYS
from integrations.framework import store

logger = logging.getLogger(__name__)


def about_user(request: web.Request) -> User:
    """The request's user, who must have ``about`` connected (``store.NotConnected``
    → 403 ``not_connected`` from the middleware)."""
    user = request_user(request)
    store.require(user, facts.KIND)
    return user


def _err(message: str, status: int = 400) -> web.Response:
    return web.json_response({"ok": False, "error": message}, status=status)


async def _body(request: web.Request) -> Optional[Dict[str, Any]]:
    try:
        data = await request.json()
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def _public(fact: Dict[str, Any]) -> Dict[str, Any]:
    return {k: fact.get(k) for k in ("id", "text", "section", "source", "source_kind", "source_ref",
                                     "learned_at", "updated_at")}


async def get_about_api(request: web.Request) -> web.Response:
    user = about_user(request)
    try:
        return web.json_response(facts.grouped(user))
    except Exception as e:
        logger.error(f"GET /api/about failed: {e}", exc_info=True)
        return _err(str(e), 500)


async def add_fact_api(request: web.Request) -> web.Response:
    user = about_user(request)
    data = await _body(request)
    if data is None:
        return _err("Expected a JSON object.")
    section = data.get("section")
    if section is not None and section not in SECTION_KEYS:
        return _err(f"section must be one of: {', '.join(SECTION_KEYS)}")
    try:
        fact, created = facts.add_fact(user, str(data.get("text") or ""), section, source_kind="owner")
    except ValueError as e:
        return _err(str(e))
    undo_id = None
    if created:
        fid = fact["id"]
        undo_id = undo.record(f"Added: {fact['text']}", lambda: facts.forget_fact(user, fid), source="about_api")
    return web.json_response({"ok": True, "fact": _public(fact), "created": created, "undo_id": undo_id},
                             status=201 if created else 200)


async def update_fact_api(request: web.Request) -> web.Response:
    user = about_user(request)
    fact_id = request.match_info["id"]
    data = await _body(request)
    if data is None:
        return _err("Expected a JSON object.")
    if "text" not in data and "section" not in data:
        return _err("Send text and/or section.")
    snap = facts.snapshot(user, fact_id)
    try:
        fact = facts.update_fact(user, fact_id, text=data.get("text"), section=data.get("section"))
    except LookupError:
        return _err("No such fact.", 404)
    except ValueError as e:
        return _err(str(e))
    undo_id = undo.record(f"Changed back: {snap['text']}", lambda: facts.restore_snapshot(user, snap),
                          source="about_api") if snap else None
    return web.json_response({"ok": True, "fact": _public(fact), "undo_id": undo_id})


async def delete_fact_api(request: web.Request) -> web.Response:
    user = about_user(request)
    fact_id = request.match_info["id"]
    current = facts.get_fact(user, fact_id)
    if not current or current["status"] != "active" or not facts.forget_fact(user, fact_id):
        return _err("No such fact.", 404)
    undo_id = undo.record(f"Forgot: {current['text']}", lambda: facts.restore_fact(user, fact_id),
                          source="about_api")
    return web.json_response({"ok": True, "undo_id": undo_id})


# --- Old /api/memories endpoints -------------------------------------------------

def legacy_delete(user: User, key: str) -> Optional[Dict[str, Any]]:
    """``DELETE /api/memories/{key}`` for a ``fact_<id>`` key of ``user``: forget it.
    Returns the response body, or None when the key is not one of their active facts."""
    if not key.startswith("fact_"):
        return None
    fact_id = key[5:]
    current = facts.get_fact(user, fact_id)
    if not current or current["status"] != "active" or not facts.forget_fact(user, fact_id):
        return None
    undo_id = undo.record(f"Forgot: {current['text']}", lambda: facts.restore_fact(user, fact_id),
                          source="api_memories")
    return {"status": "deleted", "key": key, "undo_id": undo_id}


async def get_memories_api(request: web.Request) -> web.Response:
    """GET /api/memories — old clients: "About you" facts in the old shape
    ({key: "fact_<id>", value, category: section, updated_at}); docs/about.md."""
    user = about_user(request)
    try:
        memories = legacy.legacy_list(user)
        return web.json_response(memories)
    except Exception as e:
        logger.error(f"API Error fetching memories: {e}", exc_info=True)
        return web.json_response({"error": str(e)}, status=500)


async def create_memory_api(request: web.Request) -> web.Response:
    """POST /api/memories — old clients: key "fact_<id>" edits that fact, any
    other key adds a fact ("key: value")."""
    user = about_user(request)
    try:
        data = await request.json()
        key = (data.get("key") or "").strip()
        value = (data.get("value") or "").strip()
        category = (data.get("category") or "general").strip()

        if not key or not value:
            return web.json_response({"error": "Key and Value are required"}, status=400)

        try:
            fact = legacy.legacy_upsert(user, key, value, category)
        except ValueError as e:
            return web.json_response({"error": str(e)}, status=400)
        return web.json_response({"status": "created", "key": f"fact_{fact['id']}"})
    except Exception as e:
        logger.error(f"API Error creating memory: {e}", exc_info=True)
        return web.json_response({"error": str(e)}, status=500)


async def delete_memory_api(request: web.Request) -> web.Response:
    """DELETE /api/memories/{key} — "fact_<id>" forgets that fact (+ undo_id);
    any other key deletes an old memories row as before (only for the user who
    owns the old memories, docs/about.md)."""
    user = about_user(request)
    key = request.match_info.get("key")
    if key is None:
        return web.json_response({"error": "Key is required"}, status=400)
    try:
        forgotten = legacy_delete(user, key)
        if forgotten:
            return web.json_response(forgotten)
        success = legacy.delete_old_memory(user, key)
        if not success:
            return web.json_response({"error": "Memory not found"}, status=404)
        return web.json_response({"status": "deleted", "key": key})
    except Exception as e:
        logger.error(f"API Error deleting memory {key}: {e}", exc_info=True)
        return web.json_response({"error": str(e)}, status=500)


# --- Learner -----------------------------------------------------------------------

async def _start_learner(app: web.Application) -> None:
    if not learner.LEARN_ENABLED:
        logger.info("About learner off (ABOUT_LEARN=0)")
        return
    app["about_learner_task"] = asyncio.create_task(learner.learner_loop())
    logger.info(f"About learner on: every {learner.LEARN_INTERVAL_MINUTES} min per user with About you, "
                f"conversations quiet for {learner.LEARN_QUIET_MINUTES} min")


async def _stop_learner(app: web.Application) -> None:
    task = app.get("about_learner_task")
    if task:
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass


def setup(app: web.Application) -> None:
    app.router.add_get("/api/about", get_about_api)
    app.router.add_post("/api/about/facts", add_fact_api)
    app.router.add_put("/api/about/facts/{id}", update_fact_api)
    app.router.add_delete("/api/about/facts/{id}", delete_fact_api)
    app.router.add_get("/api/memories", get_memories_api)
    app.router.add_post("/api/memories", create_memory_api)
    app.router.add_delete("/api/memories/{key}", delete_memory_api)
    # Started with the app (runner.setup() in core/server.py fires on_startup),
    # next to the other background loops.
    app.on_startup.append(_start_learner)
    app.on_cleanup.append(_stop_learner)
