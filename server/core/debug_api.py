"""Debug routes: the caller's sessions with their full history and environment,
and the server log buffer (``manage_server`` only).

- ``GET /api/debug/sessions``, ``GET|DELETE /api/debug/sessions/{session_id}`` — the caller's own
- ``GET|DELETE /api/debug/logs`` — the in-memory log (:class:`InMemoryLogHandler`, installed by core/server.py)

:class:`QuietAccessLog` (installed by main.py) keeps routine successful GETs out of the access log.
"""

import datetime
import logging
from typing import Optional

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

    # The web server's access lines (every request, the Debug screen's own polling included) stay in
    # the process log only; here they would push everything else out of the window.
    SKIP = ("aiohttp.access",)

    def emit(self, record):
        if record.name in self.SKIP:
            return
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


class QuietAccessLog(logging.Filter):
    """The ``aiohttp.access`` lines worth keeping: anything but GET / HEAD / OPTIONS, any 4xx / 5xx, and requests
    slower than ``SLOW_SECONDS``. The phone, the dashboards and the TV poll all day; their routine answers are
    dropped. The time taken is the line's ``%Tf`` / ``%D`` when the access format has one; with aiohttp's default
    format it is estimated from the start time it logs (whole seconds), so only requests that surely took
    ``SLOW_SECONDS`` or more count as slow (those of 1-2 s may not)."""

    SLOW_SECONDS = 1.0
    ROUTINE_METHODS = ("GET", "HEAD", "OPTIONS")

    def filter(self, record: logging.LogRecord) -> bool:
        line = getattr(record, "first_request_line", None)
        if not isinstance(line, str):
            return True  # not an access line (or a format without the request): keep
        if line.split(" ", 1)[0] not in self.ROUTINE_METHODS:
            return True
        try:
            if int(getattr(record, "response_status", 0)) >= 400:
                return True
        except (TypeError, ValueError):
            return True
        took = self.seconds(record)
        return took is not None and took >= self.SLOW_SECONDS

    @staticmethod
    def seconds(record: logging.LogRecord) -> Optional[float]:
        """How long the request took (None: unknown)."""
        try:
            if hasattr(record, "request_time_frac"):
                return float(getattr(record, "request_time_frac"))
            if hasattr(record, "request_time_micro"):
                return float(getattr(record, "request_time_micro")) / 1e6
            started = getattr(record, "request_start_time", None)
            if isinstance(started, str):
                start = datetime.datetime.strptime(started.strip("[]"), "%d/%b/%Y:%H:%M:%S %z").timestamp()
                # The start is cut to the second: subtract that second so a fast request never counts as slow.
                return max(0.0, record.created - start - 1.0)
        except (TypeError, ValueError):
            return None
        return None


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
