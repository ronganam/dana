import json
import logging
import datetime
from typing import List, Any, Mapping, Optional, Dict
from core import thread
from core.db import get_db_connection
from core.history import prune_history
from core.addons import registry

logger = logging.getLogger(__name__)

def _is_thread(session_id: Any) -> bool:
    return thread.is_thread(session_id)


# Every session belongs to a user: the key is (user_id, session_id), so each
# user has their own ``main`` thread and their own sessions.

def _loads(raw: Optional[str], default: Any) -> Any:
    return json.loads(raw) if raw else default


def session_exists(user_id: str, session_id: str) -> bool:
    with get_db_connection() as conn:
        return conn.execute("SELECT 1 FROM sessions WHERE user_id = ? AND session_id = ?",
                            (user_id, session_id)).fetchone() is not None


def owned_by_someone_else(user_id: str, session_id: str) -> bool:
    """True when ``session_id`` is another user's session and not this user's
    (the endpoints answer 404). Never for the shared thread id, which every
    user has."""
    if _is_thread(session_id):
        return False
    with get_db_connection() as conn:
        rows = conn.execute("SELECT user_id FROM sessions WHERE session_id = ?", (session_id,)).fetchall()
    owners = {r["user_id"] for r in rows}
    return bool(owners) and user_id not in owners


def _upsert(conn, user_id: str, session_id: str, column: str, value: Optional[str]) -> None:
    """Set one column, creating the session row (idle, untitled) when missing."""
    cur = conn.execute(f"UPDATE sessions SET {column} = ?, updated_at = CURRENT_TIMESTAMP "
                       "WHERE user_id = ? AND session_id = ?", (value, user_id, session_id))
    if not cur.rowcount:
        history = value if column == "history" else "[]"
        pending = value if column == "pending_approval" else None
        conn.execute("INSERT INTO sessions (user_id, session_id, history, pending_approval, status, title, "
                     "debug_info, updated_at) VALUES (?, ?, ?, ?, 'idle', '', '{}', CURRENT_TIMESTAMP)",
                     (user_id, session_id, history, pending))


# --- History --------------------------------------------------------------------

def get_history(user_id: str, session_id: str) -> List[Dict[str, Any]]:
    """The conversation the model sees (the thread: its model window,
    core/thread.py; an ordinary session: its stored history)."""
    if _is_thread(session_id):
        return thread.model_window(user_id, session_id)
    with get_db_connection() as conn:
        row = conn.execute("SELECT history FROM sessions WHERE user_id = ? AND session_id = ?",
                           (user_id, session_id)).fetchone()
    return list(_loads(row["history"], [])) if row else []


def append_history(user_id: str, session_id: str, entries: List[Dict[str, Any]], device: str = "",
                   prune: bool = True) -> None:
    """Store a turn's new messages. The thread keeps them (and their device /
    time) in its own table and prunes only what the model sees; an ordinary
    session (created if missing) is pruned to the history limits
    (:func:`core.history.prune_history`) unless ``prune`` is False."""
    entries = [e for e in (entries or []) if isinstance(e, dict)]
    if not entries:
        return
    if _is_thread(session_id):
        thread.append(user_id, entries, device=device, thread_id=session_id)
        return
    history = get_history(user_id, session_id) + entries
    if prune:
        history = prune_history(history)
    with get_db_connection() as conn:
        _upsert(conn, user_id, session_id, "history", json.dumps(history, default=str))
        conn.commit()


def clear_history(user_id: str, session_id: str) -> None:
    """Empty an ordinary session's history (created if missing). The shared
    thread cannot be cleared."""
    if _is_thread(session_id):
        raise ValueError("The main conversation cannot be cleared.")
    with get_db_connection() as conn:
        _upsert(conn, user_id, session_id, "history", "[]")
        conn.commit()


# --- Pending approval / selection / step-limit prompt ------------------------------

def get_pending(user_id: str, session_id: str) -> Optional[Dict[str, Any]]:
    """The suspended run waiting for the user (approval, selection, step limit), or None."""
    with get_db_connection() as conn:
        row = conn.execute("SELECT pending_approval FROM sessions WHERE user_id = ? AND session_id = ?",
                           (user_id, session_id)).fetchone()
    pending = _loads(row["pending_approval"], None) if row else None
    return pending if isinstance(pending, dict) else None


def waiting_for_someone_else(user_id: str, session_id: str) -> bool:
    """True when ``user_id`` has nothing pending in ``session_id`` but another user
    does (a shared Telegram group: the buttons belong to whoever triggered them)."""
    if get_pending(user_id, session_id) is not None:
        return False
    with get_db_connection() as conn:
        row = conn.execute("SELECT 1 FROM sessions WHERE session_id = ? AND user_id != ? "
                           "AND pending_approval IS NOT NULL LIMIT 1", (session_id, user_id)).fetchone()
    return row is not None


def set_pending(user_id: str, session_id: str, payload: Dict[str, Any]) -> None:
    with get_db_connection() as conn:
        _upsert(conn, user_id, session_id, "pending_approval", json.dumps(payload, default=str))
        conn.commit()


def pop_pending(user_id: str, session_id: str) -> Optional[Dict[str, Any]]:
    """Take the pending prompt off the session (None when there was none)."""
    pending = get_pending(user_id, session_id)
    if pending is not None:
        with get_db_connection() as conn:
            conn.execute("UPDATE sessions SET pending_approval = NULL, updated_at = CURRENT_TIMESTAMP "
                         "WHERE user_id = ? AND session_id = ?", (user_id, session_id))
            conn.commit()
    return pending


# --- Session rows -------------------------------------------------------------------

def get_session_state(user_id: str, session_id: str) -> Optional[Dict[str, Any]]:
    """Gets the complete session state dictionary from the database."""
    with get_db_connection() as conn:
        row = conn.execute(
            "SELECT session_id, history, pending_approval, status, title, debug_info, context, updated_at "
            "FROM sessions WHERE user_id = ? AND session_id = ?",
            (user_id, session_id)
        ).fetchone()
    if not row:
        return None

    history = _loads(row["history"], [])
    if _is_thread(session_id):
        # Recent messages with seq/ts/device (display form, not for the model).
        history = thread.display_page(user_id, session_id)["entries"]

    return {
        "session_id": row["session_id"],
        "history": history,
        "pending_approval": _loads(row["pending_approval"], None),
        "status": row["status"] or "idle",
        "title": row["title"] or "",
        "debug_info": _loads(row["debug_info"], {}),
        "context": _load_json(row["context"]),
        "updated_at": row["updated_at"]
    }


def list_all_sessions(user_id: str) -> List[Dict[str, Any]]:
    """The user's sessions, sorted by updated_at descending."""
    with get_db_connection() as conn:
        cursor = conn.execute(
            "SELECT session_id, status, title, history, updated_at FROM sessions WHERE user_id = ? "
            "ORDER BY updated_at DESC", (user_id,)
        )
        sessions = []
        for row in cursor.fetchall():
            if _is_thread(row["session_id"]):
                continue  # the shared thread is not an ordinary session
            history = json.loads(row["history"]) if row["history"] else []
            # Calculate snippet
            snippet = ""
            if history:
                # Find last user/assistant message text
                for msg in reversed(history):
                    text, _ = _extract_text_and_images(msg.get("content"))
                    if text:
                        snippet = text[:60] + ("..." if len(text) > 60 else "")
                        break

            sessions.append({
                "session_id": row["session_id"],
                "status": row["status"] or "idle",
                "title": row["title"] or snippet or "New Session",
                "snippet": snippet or "Empty chat",
                "updated_at": row["updated_at"]
            })
        return sessions

def _load_json(raw):
    if not raw:
        return None
    try:
        return json.loads(raw)
    except Exception:
        return None


def create_new_session_db(user_id: str, session_id: str, title: str = "New Chat",
                          context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Creates a new session for the user (no-op if it already exists)."""
    if _is_thread(session_id):
        title, context = thread.THREAD_TITLE, None
    with get_db_connection() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO sessions (user_id, session_id, history, pending_approval, status, title, "
            "debug_info, context, updated_at) VALUES (?, ?, '[]', NULL, 'idle', ?, '{}', ?, CURRENT_TIMESTAMP)",
            (user_id, session_id, title, json.dumps(context, ensure_ascii=False) if context else None)
        )
        conn.commit()
    state = get_session_state(user_id, session_id)
    assert state is not None
    return state

def set_session_status(user_id: str, session_id: str, status: str):
    """Updates only the status column of a session."""
    with get_db_connection() as conn:
        conn.execute(
            "UPDATE sessions SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE user_id = ? AND session_id = ?",
            (status, user_id, session_id)
        )
        conn.commit()

def set_session_title(user_id: str, session_id: str, title: str):
    """Updates only the title column of a session."""
    with get_db_connection() as conn:
        conn.execute(
            "UPDATE sessions SET title = ?, updated_at = CURRENT_TIMESTAMP WHERE user_id = ? AND session_id = ?",
            (title, user_id, session_id)
        )
        conn.commit()

def delete_session_from_db(user_id: str, session_id: str):
    """Deletes one of the user's sessions.

    Raises:
        ValueError: for the shared thread (``main``), which cannot be deleted.
    """
    if _is_thread(session_id):
        raise ValueError("The main conversation cannot be deleted.")
    with get_db_connection() as conn:
        conn.execute("DELETE FROM sessions WHERE user_id = ? AND session_id = ?", (user_id, session_id))
        conn.commit()


def delete_session_row(user_id: str, session_id: str) -> bool:
    """Debug: delete the session row whatever it is (the thread's row too; its
    messages stay). False when the user has no such session."""
    with get_db_connection() as conn:
        n = conn.execute("DELETE FROM sessions WHERE user_id = ? AND session_id = ?",
                         (user_id, session_id)).rowcount
        conn.commit()
    return n > 0


def list_debug_sessions(user_id: str) -> List[Dict[str, Any]]:
    """Debug: every session row of the user (the thread's too), newest first."""
    with get_db_connection() as conn:
        rows = conn.execute("SELECT session_id, history, pending_approval, updated_at FROM sessions "
                            "WHERE user_id = ? ORDER BY updated_at DESC", (user_id,)).fetchall()
    return [{
        "session_id": r["session_id"],
        "history_length": len(json.loads(r["history"] or "[]")),
        "has_pending_approval": r["pending_approval"] is not None,
        "updated_at": str(r["updated_at"]),
    } for r in rows]

def save_session_debug(user_id: str, session_id: str, error_context: Optional[str] = None, agent = None):
    """Record debug metadata for a session's latest run.

    Only the run metadata is stored; history and pending state already live in
    their own columns and are joined in by :func:`build_session_debug_payload`.
    """
    try:
        debug_info_str = json.dumps({
            "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "agent_model": getattr(agent, "model", "unknown") if agent else "unknown",
            "error_context": error_context,
        })
        with get_db_connection() as conn:
            _upsert(conn, user_id, session_id, "debug_info", debug_info_str)
            conn.commit()
    except Exception as e:
        logger.error(f"Failed to save session debug info: {e}", exc_info=True)


def build_session_debug_payload(user_id: str, session_id: str,
                                environment: Mapping[str, str]) -> Optional[Dict[str, Any]]:
    """Full debug view of one of the user's sessions: run metadata plus
    history, pending state and ``environment`` (``core.debug_env.environment_for``:
    empty unless the user may see the server's). Returns None if the user has
    no such session."""
    with get_db_connection() as conn:
        row = conn.execute(
            "SELECT history, pending_approval, debug_info FROM sessions WHERE user_id = ? AND session_id = ?",
            (user_id, session_id),
        ).fetchone()
    if row is None:
        return None
    meta = json.loads(row["debug_info"] or "{}")
    history = json.loads(row["history"] or "[]")
    return {
        "timestamp": meta.get("timestamp"),
        "session_id": session_id,
        "agent_model": meta.get("agent_model", "unknown"),
        "error_context": meta.get("error_context"),
        "history_length": len(history),
        "history": history,
        "pending_approval": json.loads(row["pending_approval"]) if row["pending_approval"] else None,
        "environment_variables": dict(environment),
    }

def _extract_text_and_images(content):
    """Extract text and image URLs from a message content field (string or content array)."""
    if isinstance(content, list):
        text_parts = [p["text"] for p in content if isinstance(p, dict) and p.get("type") == "text"]
        image_urls = [p["image_url"]["url"] for p in content if isinstance(p, dict) and p.get("type") == "image_url" and isinstance(p.get("image_url"), dict)]
        return " ".join(text_parts), image_urls
    return content or "", []


def format_session_history(history: List[Dict[str, Any]],
                           pending_call_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """Converts LiteLLM history list to custom UI message list expected by Android/Web clients.

    ``pending_call_id``: the tool call the session's pending approval waits
    on; it gets ``requiresApproval`` even when the tool asks only in this
    turn (an ``acts_outside`` tool in a turn with the shared phone screen)."""
    formatted = []
    
    # Track tool messages by call ID to associate them with the tool calls
    tool_responses = {}
    for entry in history:
        if entry.get("role") == "tool":
            tc_id = entry.get("tool_call_id")
            if tc_id:
                tool_responses[tc_id] = entry.get("content") or ""

    for pos, entry in enumerate(history):
        role = entry.get("role")
        content = entry.get("content") or ""
        # Thread messages carry a stable seq (ids stay the same across pages)
        # plus when and from which device they were sent.
        idx = entry.get("seq", pos)
        meta = {k: entry[k] for k in ("ts", "device") if k in entry}
        
        if role == "user":
            text, image_urls = _extract_text_and_images(content)
            formatted.append({
                "id": f"msg_user_{idx}",
                "sender": "You",
                "message": text,
                "isUser": True,
                "toolCall": None,
                "images": image_urls,
                **meta,
            })
        elif role == "assistant":
            # 1. Add the text message from the assistant if it has content
            if isinstance(content, str) and content.strip():
                formatted.append({
                    "id": f"msg_assistant_{idx}",
                    "sender": "Assistant",
                    "message": content,
                    "isUser": False,
                    "toolCall": None,
                    **meta,
                })
            
            # 2. Add the tool calls
            tool_calls = entry.get("tool_calls")
            if tool_calls:
                for tc in tool_calls:
                    # A malformed entry must not break the whole state payload.
                    # This formatter runs on the SSE `state` frame, so a single
                    # bad entry would otherwise silently drop the authoritative
                    # state for every connected client. The agent loop applies
                    # the same defensive check (core/agent.py).
                    if not isinstance(tc, dict):
                        logger.warning("Skipping non-dict tool_call entry in history: %r", tc)
                        continue
                    tc_id = tc.get("id") or ""
                    fn = tc.get("function", {})
                    name = fn.get("name") or ""
                    args_str = fn.get("arguments", "{}")
                    
                    try:
                        args = json.loads(args_str) if isinstance(args_str, str) else args_str
                        pretty_args = json.dumps(args, indent=2, ensure_ascii=False)
                    except Exception:
                        args = args_str
                        pretty_args = str(args_str)
                    
                    response = tool_responses.get(tc_id, "")
                    has_response = tc_id in tool_responses

                    # A selection prompt that hasn't been answered yet renders as
                    # an interactive card (selection_request), not a tool card —
                    # and must not look like a pending write-tool approval.
                    if name == "request_user_selection":
                        if not has_response:
                            continue
                        requires_approval = False
                    else:
                        # Waiting for Approve: no response yet and a tool that asks first.
                        requires_approval = not has_response and (
                            (pending_call_id is not None and tc_id == pending_call_id)
                            or registry.needs_confirmation(name, args if isinstance(args, dict) else None))
                    
                    formatted.append({
                        "id": f"msg_tool_{idx}_{tc_id}",
                        "sender": "Tool",
                        "message": "",
                        "isUser": False,
                        "toolCall": {
                            "toolName": name,
                            "toolSummary": name,
                            "parameters": pretty_args,
                            "response": response,
                            "requiresApproval": requires_approval,
                            "toolCallId": tc_id,
                            "toolArgs": json.dumps(args, ensure_ascii=False) if isinstance(args, (dict, list)) else str(args),
                            "isCollapsed": has_response
                        },
                        **meta,
                    })
    return formatted


def thread_state_fields(user_id: str, thread_id: str = thread.THREAD_ID, before: Optional[int] = None,
                        limit: int = thread.PAGE_DEFAULT, pending_call_id: Optional[str] = None) -> Dict[str, Any]:
    """Thread-only keys merged into the session state payload: the page of
    messages (client format), ``has_more``, ``before`` and ``thread: true``."""
    page = thread.display_page(user_id, thread_id, before=before, limit=limit)
    return {
        "messages": format_session_history(page["entries"], pending_call_id=pending_call_id),
        "has_more": page["has_more"],
        "before": page["before"],
        "thread": True,
        "gap_minutes": thread.THREAD_GAP_MINUTES,
        **thread.context_info(user_id, thread_id),
    }
