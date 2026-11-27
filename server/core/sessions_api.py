"""Sessions: the SSE relay every assistant stream goes through, the session
CRUD routes, reattaching to a run, and context chats (docs/agent-actions.md).

- ``GET /api/sessions`` — the caller's sessions
- ``GET /api/sessions/{session_id}`` — the session state (``main``: ``?before=&limit=`` pages backwards)
- ``GET /api/sessions/{session_id}/stream?after=&run_id=`` → SSE (reattach to the current run)
- ``POST /api/sessions``, ``DELETE /api/sessions/{session_id}``
- ``POST /api/chat/context {kind, id}`` → ``{session_id, title, context}``

Another user's session id answers 404 like an unknown one. :func:`relay_run`
and :func:`build_state_payload` are shared with core/assistant_api.py.
"""

import asyncio
import logging
import time
import uuid
from typing import Any, Dict, Optional

from aiohttp import web

from core import context_chats, thread
from core.agent import AgentOrchestrator
from core.auth import request_user
from core.runs import Run, live_undos, runs, sse_frame
from core.session import (
    create_new_session_db,
    delete_session_from_db,
    format_session_history,
    get_session_state,
    list_all_sessions,
    owned_by_someone_else,
    thread_state_fields,
)
from core.users import User

logger = logging.getLogger(__name__)


# Gap between SSE frames while a long tool call or LLM turn runs. Keeps the
# connection demonstrably alive so idle-timeouts in the path cannot silently
# kill a task the user is still waiting on.
SSE_HEARTBEAT_SECONDS = 5.0


# Runaway guard only — a legitimate multi-extension run can legitimately run for
# many minutes, so this is set well above any real task.
SSE_MAX_STREAM_SECONDS = 1800.0


def _selection_request_for(state: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Derive the client-facing selection prompt payload for a session state.

    Returns None when the session is not awaiting a user selection.
    """
    if not state or state["status"] != "awaiting_selection" or not state.get("pending_approval"):
        return None
    pending = state["pending_approval"]
    return {
        "prompt": pending.get("prompt", ""),
        "options": pending.get("options", []),
        "multiple": bool(pending.get("multiple", False)),
    }


def _with_card(pending: Any) -> Any:
    """A waiting approval with its words for the app's card (``card``: core/approval_text.py's
    ``describe``, the same as Telegram's and Now's); other states as they are."""
    if not isinstance(pending, dict) or pending.get("status") != "pending_approval" or not pending.get("tool_name"):
        return pending
    from core import approval_text
    return {**pending, "card": approval_text.describe(str(pending["tool_name"]), pending.get("tool_args"))}


def _fresh_context(user: User, ctx: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Re-resolve the attachment card so it shows the current status/title."""
    if not ctx:
        return None
    try:
        return context_chats.build_chat_context(user, ctx.get("kind"), ctx.get("ref_id"))
    except Exception:
        return ctx


def build_state_payload(user: User, session_id: str, transcription: Optional[str] = None,
                         before: Optional[int] = None, limit: Optional[int] = None) -> Dict[str, Any]:
    """Build the authoritative session state a client should apply at stream end.

    This is the single shape every assistant endpoint streams as its final
    ``state`` event. Clients feed it to their existing session-state parser, so
    terminal handling (approval / selection / iteration prompt) needs no special
    casing per transport.
    """
    state = get_session_state(user.id, session_id)
    if not state:
        return {"session_id": session_id, "status": "error", "error": "Session state not found"}

    active = runs.active(user.id, session_id)
    status = state["status"]
    if active is not None:
        # The run may not have written its status yet; it is running.
        status = "running"
    elif status == "running":
        # Left over from a run that died with the process (restart, crash):
        # nothing is working on it, so don't make clients wait forever.
        status = "idle"

    pending = state.get("pending_approval")
    pending_call_id = None
    if isinstance(pending, dict) and pending.get("status") in ("pending_approval", "tool_call"):
        pending_call_id = pending.get("tool_call_id") or None
    payload: Dict[str, Any] = {
        "session_id": state["session_id"],
        "status": status,
        "title": state["title"],
        "pending_approval": _with_card(state["pending_approval"]),
        "selection_request": _selection_request_for(state),
        "messages": format_session_history(state["history"], pending_call_id=pending_call_id),
        # What the chat was opened from (call suggestion / call / task), or None.
        "context": _fresh_context(user, state.get("context")),
        # Attach with GET /api/sessions/{id}/stream to follow it live.
        "active_run": active.info() if active else None,
    }
    # Undo entries of the latest run (one Undo button under the reply) and
    # whether a learned phrase answered it (docs/agent-actions.md).
    latest = runs.latest(user.id, session_id)
    payload["undos"] = live_undos(latest)
    payload["shortcut"] = bool(latest and latest.shortcut)
    if thread.is_thread(session_id):
        # The shared thread: a page of messages with ts/device, pageable backwards.
        payload.update(thread_state_fields(user.id, session_id, before=before, limit=limit or thread.PAGE_DEFAULT,
                                           pending_call_id=pending_call_id))
    if transcription is not None:
        payload["transcription"] = transcription
    if state["status"] == "iterations_exceeded" and state.get("pending_approval"):
        payload["extend_summary"] = AgentOrchestrator.summarize_state(state["pending_approval"])
    return payload


def _sse_headers() -> Dict[str, str]:
    return {
        "Content-Type": "text/event-stream; charset=utf-8",
        "Cache-Control": "no-cache, no-transform",
        # Belt-and-braces against any buffering intermediary in front of us.
        "X-Accel-Buffering": "no",
        "Connection": "keep-alive",
    }


async def relay_run(
    request: web.Request,
    user: User,
    session_id: str,
    run: Optional[Run],
    *,
    after_seq: int = 0,
    transcription: Optional[str] = None,
) -> web.StreamResponse:
    """Stream a run's events to the client as Server-Sent Events.

    Replays every event with ``seq > after_seq`` and then follows the run live
    until its ``run_end``. The stream always closes with a ``state`` frame
    (no ``seq``) carrying the authoritative session state. With ``run=None``
    only the ``state`` frame is sent.

    A client disconnect never cancels the run: it keeps going, persists its
    result, and the client can reattach via ``GET /api/sessions/{id}/stream``.
    """
    response = web.StreamResponse(status=200, headers=_sse_headers())
    await response.prepare(request)

    try:
        if run is not None:
            seq = max(0, after_seq)
            deadline = time.monotonic() + SSE_MAX_STREAM_SECONDS
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    logger.warning("SSE stream for %s hit the %ss cap", session_id, SSE_MAX_STREAM_SECONDS)
                    break
                events = await run.wait_for_events(seq, min(SSE_HEARTBEAT_SECONDS, remaining))
                if not events:
                    if run.finished:
                        break
                    # Keepalive comment — clients ignore it, intermediaries don't time out.
                    await response.write(b": heartbeat\n\n")
                    continue
                for event in events:
                    await response.write(sse_frame(event))
                seq = events[-1]["seq"]

        # Must happen before write_eof() — the connection closes after it.
        await response.write(sse_frame({
            "type": "state",
            "state": build_state_payload(user, session_id, transcription),
        }))
    except (ConnectionResetError, asyncio.CancelledError):
        logger.info("SSE client for %s disconnected; the run continues in the background", session_id)
    except Exception as e:
        logger.error(f"SSE stream error for {session_id}: {e}", exc_info=True)
    finally:
        try:
            await response.write_eof()
        except (ConnectionResetError, ConnectionError, RuntimeError):
            pass

    return response


def not_found() -> web.Response:
    return web.json_response({"error": "Session not found"}, status=404)


def session_of(request: web.Request, session_id: str) -> Optional[User]:
    """The request's user, or None when ``session_id`` is another user's
    session (the caller answers 404, as for any unknown id)."""
    user = request_user(request)
    return None if owned_by_someone_else(user.id, session_id) else user


async def session_stream_api(request: web.Request) -> web.StreamResponse:
    """GET /api/sessions/{session_id}/stream?after=N&run_id=X → SSE

    Attach to the session's current (or just-finished) run. ``after`` is the
    last ``seq`` the client already rendered for ``run_id``; if a different run
    is now current, it is replayed from the start. With no run to follow, only
    the closing ``state`` frame is sent.
    """
    session_id = request.match_info.get("session_id")
    user = session_of(request, session_id)
    if user is None:
        return not_found()
    asked = request.query.get("run_id")
    if asked and runs.owner_of(asked) not in (None, user.id):
        return not_found()  # someone else's run (also on the shared `main` id)
    run = runs.latest(user.id, session_id)
    after = 0
    if run is not None and request.query.get("run_id") == run.run_id:
        try:
            after = max(0, int(request.query.get("after", "0")))
        except ValueError:
            after = 0
    return await relay_run(request, user, session_id, run, after_seq=after)


async def get_sessions_api(request: web.Request) -> web.Response:
    """GET /api/sessions — the caller's sessions."""
    user = request_user(request)
    try:
        sessions = list_all_sessions(user.id)
        return web.json_response(sessions)
    except Exception as e:
        logger.error(f"Error listing sessions: {e}", exc_info=True)
        return web.json_response({"error": str(e)}, status=500)


async def get_session_detail_api(request: web.Request) -> web.Response:
    """GET /api/sessions/{session_id}

    For the shared thread (``main``, always exists): ``?before=<seq>&limit=N``
    pages backwards; messages carry ``ts`` and ``device``.
    """
    session_id = request.match_info.get("session_id")
    user = session_of(request, session_id)
    if user is None:
        return not_found()
    try:
        before = limit = None
        if thread.is_thread(session_id):
            create_new_session_db(user.id, session_id)
            try:
                before = int(request.query["before"]) if request.query.get("before") else None
                limit = int(request.query["limit"]) if request.query.get("limit") else None
            except ValueError:
                return web.json_response({"error": "before and limit must be integers"}, status=400)
        state = get_session_state(user.id, session_id)
        if not state:
            return web.json_response({"error": "Session not found"}, status=404)

        resp = build_state_payload(user, session_id, before=before, limit=limit)
        resp["updated_at"] = str(state["updated_at"])
        return web.json_response(resp)
    except Exception as e:
        logger.error(f"Error fetching session detail for {session_id}: {e}", exc_info=True)
        return web.json_response({"error": str(e)}, status=500)


async def create_session_api(request: web.Request) -> web.Response:
    """POST /api/sessions"""
    request_user(request)
    try:
        try:
            data = await request.json()
        except Exception:
            data = {}
            
        session_id = data.get("session_id")
        if not session_id:
            session_id = str(uuid.uuid4())
        user = session_of(request, session_id)
        if user is None:
            return not_found()
            
        title = data.get("title") or "New Chat"
        
        state = create_new_session_db(user.id, session_id, title)
        return web.json_response({
            "session_id": state["session_id"],
            "status": state["status"],
            "title": state["title"],
            "pending_approval": state["pending_approval"],
            "messages": []
        })
    except Exception as e:
        logger.error(f"Error creating session: {e}", exc_info=True)
        return web.json_response({"error": str(e)}, status=500)


async def delete_session_api(request: web.Request) -> web.Response:
    """DELETE /api/sessions/{session_id}"""
    session_id = request.match_info.get("session_id")
    user = session_of(request, session_id)
    if user is None:
        return not_found()
    if thread.is_thread(session_id):
        return web.json_response({"error": "The main conversation cannot be deleted.",
                                  "code": "thread_not_deletable"}, status=400)
    try:
        delete_session_from_db(user.id, session_id)
        return web.json_response({"status": "deleted", "session_id": session_id})
    except Exception as e:
        logger.error(f"Error deleting session: {e}", exc_info=True)
        return web.json_response({"error": str(e)}, status=500)


async def clear_thread_api(request: web.Request) -> web.Response:
    """POST /api/sessions/main/clear {device?} — "new topic": Dana forgets the
    thread so far (messages stay shown); an open prompt counts as declined.
    Returns the thread state (``context_from_seq`` is then null). Only the
    shared thread (400 ``not_a_thread``); 409 ``session_busy`` while a run goes."""
    from core.runs import SessionBusyError
    from core.session_runner import clear_thread

    session_id = request.match_info.get("session_id")
    user = session_of(request, session_id)
    if user is None:
        return not_found()
    if not thread.is_thread(session_id):
        return web.json_response({"error": "Only the shared conversation can be cleared; start a new chat instead.",
                                  "code": "not_a_thread"}, status=400)
    try:
        data = await request.json()
    except Exception:
        data = {}
    try:
        clear_thread(user, session_id, device=str((data or {}).get("device") or ""))
    except SessionBusyError as busy:
        return web.json_response({"error": "This chat is still working on the previous request.",
                                  "code": "session_busy", "active_run": busy.run.info()}, status=409)
    return web.json_response(build_state_payload(user, session_id))


async def start_context_chat_api(request: web.Request) -> web.Response:
    """POST /api/chat/context {kind: suggestion|call|task|notification|person|review, id} → {session_id, title, context}

    Opens a new chat with the item attached: the agent gets it as system
    context on every turn and clients render ``context`` as a card with
    quick prompts.
    """
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON"}, status=400)
    try:
        return web.json_response(context_chats.start_context_session(request_user(request), data.get("kind"),
                                                                     data.get("id")))
    except LookupError as e:
        return web.json_response({"error": str(e)}, status=404)
    except ValueError as e:
        return web.json_response({"error": str(e)}, status=400)


def setup(app: web.Application) -> None:
    """Register the session and context-chat routes."""
    app.router.add_post("/api/chat/context", start_context_chat_api)
    app.router.add_get("/api/sessions", get_sessions_api)
    app.router.add_get("/api/sessions/{session_id}", get_session_detail_api)
    app.router.add_get("/api/sessions/{session_id}/stream", session_stream_api)
    app.router.add_post("/api/sessions", create_session_api)
    app.router.add_delete("/api/sessions/{session_id}", delete_session_api)
    app.router.add_post("/api/sessions/{session_id}/clear", clear_thread_api)
