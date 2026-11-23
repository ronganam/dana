"""The one conversation with Dana that every device shares (session id ``main``).

Phone, watch, tablet, TV, Mac, desktop (the Omarchy plugin) and web all post
to ``session_id: "main"`` with their ``device``. Every user has their own: the
wire id is ``main`` for everyone and resolves to the user's thread (rows carry
``user_id``). It always exists (created on first use), cannot be deleted, and
is not listed among the ordinary sessions; per-device sessions keep working as
before.

Storage: one row per message in ``thread_messages`` (the LiteLLM message JSON
in ``message``; ``ts`` and ``device`` in their own columns, so they can never
reach the model). The user's ``sessions`` row ``main`` still holds status,
pending approval and title like any session; its ``history`` column stays
empty (core/session.py creates it and builds the client state).

Two views of the same messages:

* **model** (:func:`model_window`): only the messages after the last quiet gap
  of ``THREAD_GAP_MINUTES`` (default 20) before a user message and after the
  user's last "new topic" (:func:`clear`, ``sessions.cleared_seq``), then
  bounded by the usual history limits (40 messages / ``MAX_HISTORY_CHARS``). A
  command sent after a pause starts fresh instead of dragging in the morning's
  chat. :func:`context_info` tells clients where that window starts.
* **display** (:func:`display_page`): pages of up to a few hundred messages
  with ``seq`` / ``ts`` / ``device``, newest last, paged backwards with
  ``before=<seq>``. The oldest rows beyond ``THREAD_KEEP_MESSAGES`` (default
  500) are deleted.
"""
import json
import logging
import os
import time
from typing import Any, Dict, List, Optional, Sequence

from core.db import get_db_connection
from core.history import prune_history

logger = logging.getLogger(__name__)

THREAD_ID = "main"
THREAD_TITLE = "Dana"
DEVICES = ("phone", "watch", "tablet", "tv", "mac", "desktop", "web")

THREAD_GAP_MINUTES = float(os.getenv("THREAD_GAP_MINUTES", "20") or 20)
THREAD_KEEP_MESSAGES = int(os.getenv("THREAD_KEEP_MESSAGES", "500") or 500)
PAGE_DEFAULT = 60
PAGE_MAX = 300
# Rows read to build the model window (it never needs more than this).
_MODEL_SCAN_ROWS = 400
# Keys that live in columns, never inside the stored message.
_META_KEYS = ("seq", "ts", "device")

def is_thread(session_id: Any) -> bool:
    return str(session_id) == THREAD_ID


def normalize_device(value: Any) -> str:
    """One of :data:`DEVICES`, ``other`` for anything else, ``""`` when absent."""
    v = str(value or "").strip().lower()
    if not v:
        return ""
    return v if v in DEVICES else "other"


def clean(message: Dict[str, Any]) -> Dict[str, Any]:
    """The message without display metadata (safe to send to LiteLLM)."""
    return {k: v for k, v in message.items() if k not in _META_KEYS}


def append(user_id: str, entries: Sequence[Dict[str, Any]], device: str = "", ts: Optional[float] = None,
           thread_id: str = THREAD_ID) -> None:
    """Store messages at the end of the user's thread and drop the oldest
    beyond ``THREAD_KEEP_MESSAGES``."""
    entries = [e for e in (entries or []) if isinstance(e, dict)]
    if not entries:
        return
    when = time.time() if ts is None else ts
    dev = normalize_device(device)
    with get_db_connection() as conn:
        for entry in entries:
            conn.execute(
                "INSERT INTO thread_messages (user_id, thread_id, ts, device, message) VALUES (?, ?, ?, ?, ?)",
                (user_id, thread_id, when, dev, json.dumps(clean(entry), ensure_ascii=False, default=str)))
        conn.execute(
            "DELETE FROM thread_messages WHERE user_id = ? AND thread_id = ? AND seq <= ("
            " SELECT seq FROM thread_messages WHERE user_id = ? AND thread_id = ? ORDER BY seq DESC LIMIT 1 OFFSET ?)",
            (user_id, thread_id, user_id, thread_id, THREAD_KEEP_MESSAGES))
        conn.execute("UPDATE sessions SET updated_at = CURRENT_TIMESTAMP WHERE user_id = ? AND session_id = ?",
                     (user_id, thread_id))
        conn.commit()


def _rows(user_id: str, thread_id: str, before: Optional[int] = None,
          limit: int = PAGE_DEFAULT) -> List[Dict[str, Any]]:
    """Up to ``limit`` of the user's rows with ``seq < before``, oldest first."""
    sql = "SELECT seq, ts, device, message FROM thread_messages WHERE user_id = ? AND thread_id = ?"
    args: List[Any] = [user_id, thread_id]
    if before is not None:
        sql += " AND seq < ?"
        args.append(int(before))
    sql += " ORDER BY seq DESC LIMIT ?"
    args.append(int(limit))
    with get_db_connection() as conn:
        rows = conn.execute(sql, args).fetchall()
    out = []
    for r in reversed(rows):
        try:
            msg = json.loads(r["message"])
        except (TypeError, ValueError):
            continue
        out.append({"seq": r["seq"], "ts": r["ts"], "device": r["device"], "message": msg})
    return out


def _cleared_seq(user_id: str, thread_id: str) -> int:
    with get_db_connection() as conn:
        row = conn.execute("SELECT cleared_seq FROM sessions WHERE user_id = ? AND session_id = ?",
                           (user_id, thread_id)).fetchone()
    return int(row["cleared_seq"] or 0) if row else 0


def _window_rows(user_id: str, thread_id: str, gap_minutes: Optional[float] = None) -> List[Dict[str, Any]]:
    """The stored rows of the model window (see :func:`model_window`)."""
    cleared = _cleared_seq(user_id, thread_id)
    rows = [r for r in _rows(user_id, thread_id, limit=_MODEL_SCAN_ROWS) if r["seq"] > cleared]
    # Never start mid-turn (a clear can land between a tool call and its result).
    first_user = next((i for i, r in enumerate(rows) if r["message"].get("role") == "user"), None)
    rows = rows[first_user:] if first_user is not None else []
    gap = 60.0 * (THREAD_GAP_MINUTES if gap_minutes is None else gap_minutes)
    start = 0
    for i in range(1, len(rows)):
        if rows[i]["message"].get("role") == "user" and rows[i]["ts"] - rows[i - 1]["ts"] >= gap:
            start = i
    rows = rows[start:]
    kept = prune_history([r["message"] for r in rows])  # a suffix of the list
    return rows[len(rows) - len(kept):]


def model_window(user_id: str, thread_id: str = THREAD_ID,
                 gap_minutes: Optional[float] = None) -> List[Dict[str, Any]]:
    """What the model sees: the messages after the last quiet gap before a
    user message and after the last :func:`clear`, bounded by the history
    limits. Clean LiteLLM dicts."""
    return [r["message"] for r in _window_rows(user_id, thread_id, gap_minutes)]


def context_info(user_id: str, thread_id: str = THREAD_ID) -> Dict[str, Any]:
    """Where Dana's memory of the thread starts, for clients to draw it.

    ``context_from_seq``: the ``seq`` of the oldest message the model would
    see now, ``None`` when it sees none (just cleared, or empty).
    ``context_expires_at``: epoch seconds when the quiet gap runs out and the
    next message starts fresh (``None`` with nothing in memory).
    """
    rows = _window_rows(user_id, thread_id)
    if not rows:
        return {"context_from_seq": None, "context_expires_at": None}
    return {"context_from_seq": rows[0]["seq"],
            "context_expires_at": round(rows[-1]["ts"] + 60.0 * THREAD_GAP_MINUTES, 3)}


def clear(user_id: str, thread_id: str = THREAD_ID) -> int:
    """Start a new topic: the model forgets every message stored so far
    (they stay stored and shown). Returns the new mark."""
    with get_db_connection() as conn:
        row = conn.execute("SELECT MAX(seq) AS m FROM thread_messages WHERE user_id = ? AND thread_id = ?",
                           (user_id, thread_id)).fetchone()
        mark = int(row["m"] or 0)
        conn.execute("UPDATE sessions SET cleared_seq = ?, updated_at = CURRENT_TIMESTAMP"
                     " WHERE user_id = ? AND session_id = ?", (mark, user_id, thread_id))
        conn.commit()
    return mark


def display_page(user_id: str, thread_id: str = THREAD_ID, before: Optional[int] = None,
                 limit: int = PAGE_DEFAULT) -> Dict[str, Any]:
    """A page of the user's thread for clients, oldest first.

    Returns ``{"entries", "has_more", "before"}``: entries are the stored
    messages plus ``seq``, ``ts`` (epoch seconds) and ``device``; a page never
    starts in the middle of a turn (it is extended back to the user message);
    ``before`` is the cursor for the next older page (None when there is none).
    """
    limit = max(1, min(int(limit or PAGE_DEFAULT), PAGE_MAX))
    rows = _rows(user_id, thread_id, before=before, limit=limit)
    # Extend back to the turn's user message so tool cards keep their call.
    extra = 0
    while rows and rows[0]["message"].get("role") != "user" and extra < 100:
        older = _rows(user_id, thread_id, before=rows[0]["seq"], limit=10)
        if not older:
            break
        rows = older + rows
        extra += len(older)
    if rows and rows[0]["message"].get("role") != "user":
        # Retention cut off this turn's start: drop the orphaned tail.
        first_user = next((i for i, r in enumerate(rows) if r["message"].get("role") == "user"), None)
        rows = rows[first_user:] if first_user is not None else rows
    has_more = bool(rows) and bool(_rows(user_id, thread_id, before=rows[0]["seq"], limit=1))
    entries = [{**r["message"], "seq": r["seq"], "ts": round(r["ts"], 3), "device": r["device"]} for r in rows]
    return {"entries": entries, "has_more": has_more, "before": rows[0]["seq"] if rows and has_more else None}
