"""Debug routes: the caller's sessions with their full history and environment,
and the server log buffer (``manage_server`` only).

- ``GET /api/debug/sessions``, ``GET|DELETE /api/debug/sessions/{session_id}`` — the caller's own
- ``GET|DELETE /api/debug/logs`` — the in-memory log (:class:`InMemoryLogHandler`, installed by core/server.py)
"""

import datetime
import logging

from aiohttp import web

from core import debug_env
from core.auth import request_user, requires
from core.session import build_session_debug_payload, delete_session_row, list_debug_sessions

logger = logging.getLogger(__name__)


async def get_debug_sessions_api(request: web.Request) -> web.Response:
    """GET /api/debug/sessions — the caller's sessions."""
    user = request_user(request)
    try:
        return web.json_response(list_debug_sessions(user.id))
    except Exception as e:
        logger.error(f"Error fetching debug sessions: {e}", exc_info=True)
        return web.json_response({"error": str(e)}, status=500)


class InMemoryLogHandler(logging.Handler):
    def __init__(self, limit=2000):
        super().__init__()
        self.limit = limit
        self.logs = []

    def emit(self, record):
        try:
            msg = self.format(record)
            self.logs.append({
                "timestamp": datetime.datetime.fromtimestamp(record.created).isoformat(),
                "name": record.name,
                "levelname": record.levelname,
                "message": record.getMessage(),
                "formatted": msg
            })
            if len(self.logs) > self.limit:
                self.logs.pop(0)
        except Exception:
            self.handleError(record)


@requires("manage_server")
async def get_system_logs_api(request: web.Request) -> web.Response:
    """GET /api/debug/logs"""
    app = request.app
    log_handler = app.get("log_handler")
    if not log_handler:
        return web.json_response([])
    return web.json_response(log_handler.logs)


@requires("manage_server")
async def clear_system_logs_api(request: web.Request) -> web.Response:
    """DELETE /api/debug/logs"""
    app = request.app
    log_handler = app.get("log_handler")
    if log_handler:
        log_handler.logs.clear()
        logger.info("System log buffer cleared by administrator.")
    return web.json_response({"status": "cleared"})


async def get_debug_session_detail_api(request: web.Request) -> web.Response:
    """GET /api/debug/sessions/{session_id}"""
    user = request_user(request)
    session_id = request.match_info.get("session_id")
    try:
        payload = build_session_debug_payload(user.id, session_id, debug_env.environment_for(user))
        if payload is None:
            return web.json_response({"error": "Session not found"}, status=404)
        return web.json_response(payload)
    except Exception as e:
        logger.error(f"Error fetching debug session detail for {session_id}: {e}", exc_info=True)
        return web.json_response({"error": str(e)}, status=500)


async def delete_debug_session_api(request: web.Request) -> web.Response:
    """DELETE /api/debug/sessions/{session_id}"""
    user = request_user(request)
    session_id = request.match_info.get("session_id")
    try:
        if not delete_session_row(user.id, session_id):
            return web.json_response({"error": "Session not found"}, status=404)
        return web.json_response({"status": "deleted", "session_id": session_id})
    except Exception as e:
        logger.error(f"Error deleting session {session_id}: {e}", exc_info=True)
        return web.json_response({"error": str(e)}, status=500)


def setup(app: web.Application) -> None:
    """Register the debug routes."""
    app.router.add_get("/api/debug/sessions", get_debug_sessions_api)
    app.router.add_get("/api/debug/sessions/{session_id}", get_debug_session_detail_api)
    app.router.add_delete("/api/debug/sessions/{session_id}", delete_debug_session_api)
    app.router.add_get("/api/debug/logs", get_system_logs_api)
    app.router.add_delete("/api/debug/logs", clear_system_logs_api)
