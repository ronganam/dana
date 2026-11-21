"""HTTP routes for agent actions (docs/agent-actions.md).

Loaded by core/server.py through ``setup(app)``.

    GET    /api/shortcuts        the caller's learned phrases: [{id, phrase, does, active, times_used, last_used, ...}]
    DELETE /api/shortcuts/{id}   forget one of the caller's → {ok: true} | 404
    POST   /api/undo/{id}        reverse one of the caller's changes (core/undo.py) → {ok, label} | 404
                                 | 409 {ok: false, error} when it is refused (changed since, no longer allowed)

The thread (``session_id: "main"``) uses the ordinary assistant and session
routes.
"""
import logging

from aiohttp import web

from core import shortcuts, undo
from core.auth import request_user
from core.undoable import UndoConflict
from integrations.framework.store import NotConnected

logger = logging.getLogger(__name__)


async def list_shortcuts_api(request: web.Request) -> web.Response:
    """GET /api/shortcuts → {enabled, learn_after, shortcuts: [...]}"""
    user = request_user(request)
    try:
        return web.json_response({
            "enabled": shortcuts.enabled(),
            "learn_after": shortcuts.LEARN_AFTER,
            "shortcuts": shortcuts.list_all(user.id),
        })
    except Exception as e:
        logger.error(f"Listing shortcuts failed: {e}", exc_info=True)
        return web.json_response({"error": str(e)}, status=500)


async def delete_shortcut_api(request: web.Request) -> web.Response:
    """DELETE /api/shortcuts/{id} → {ok: true} | 404"""
    if not shortcuts.delete(request_user(request).id, request.match_info["id"]):
        return web.json_response({"ok": False, "error": "No such shortcut."}, status=404)
    return web.json_response({"ok": True})


async def undo_api(request: web.Request) -> web.Response:
    """POST /api/undo/{id} → {ok, label} | 404 when expired, already used or
    another user's (the auth middleware made the request's user current)."""
    undo_id = request.match_info["id"]
    try:
        label = await undo.run(undo_id)
    except KeyError:
        return web.json_response({"ok": False, "error": "This can no longer be undone."}, status=404)
    except NotConnected:
        raise  # answered by core.auth.integration_middleware
    except UndoConflict as e:
        logger.info(f"Undo {undo_id} refused: {e}")
        return web.json_response({"ok": False, "error": str(e)}, status=409)
    except Exception as e:
        logger.error(f"Undo {undo_id} failed: {e}", exc_info=True)
        return web.json_response({"ok": False, "error": f"Undo failed: {e}"}, status=500)
    return web.json_response({"ok": True, "label": label})


def setup(app: web.Application) -> None:
    app.router.add_get("/api/shortcuts", list_shortcuts_api)
    app.router.add_delete("/api/shortcuts/{id}", delete_shortcut_api)
    app.router.add_post("/api/undo/{id}", undo_api)
