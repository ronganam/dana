"""Acting on a notification from the web: the user's phone carries it out.

``POST /api/notifications/{id}/act`` stores a request in
``notification_action_requests`` and pushes a ``notification_action`` event
carrying **only** the request id to the user's phone; the phone fetches the
request (and any reply text) with its own token, presses the button and
posts the result — the reply text never goes through Google. Requests expire
after ``ACTION_REQUEST_TTL_SECONDS``; a done one counts as seen. A request
belongs to the user who made it; another user's id answers like one that
does not exist.
"""
from __future__ import annotations

import datetime
import uuid
from typing import Any, Dict, Optional

from core.db import get_db_connection
from core.users import User
from addons.phone import push
from addons.phone.notifications.store import to_api
from addons.phone.notifications.values import utc_now_iso

ACTION_REQUEST_TTL_SECONDS = 120


async def request_action(user: User, nid: str, action: str, text: str = "") -> Dict[str, Any]:
    """Ask ``user``'s phone to open their notification ``nid`` (``action='open'``),
    press one of its buttons (by title) or reply (a button with ``reply``; ``text``).

    Raises LookupError (not found / not theirs) or ValueError for a bad request."""
    action = (action or "").strip()
    text = (text or "").strip()
    with get_db_connection() as conn:
        row = conn.execute("SELECT * FROM notifications WHERE id = ? AND user_id = ?", (nid, user.id)).fetchone()
    if not row:
        raise LookupError("notification not found")
    n = to_api(row)
    if not n["on_phone"]:
        raise ValueError("this notification is no longer on the phone")
    if action != "open":
        match = next((a for a in n["actions"] if a["title"].lower() == action.lower()), None)
        if not match:
            raise ValueError(f"no button named '{action}'")
        action = match["title"]
        if match.get("reply") and not text:
            raise ValueError("a reply needs text")
        if not match.get("reply"):
            text = ""
    rid = str(uuid.uuid4())
    with get_db_connection() as conn:
        conn.execute("INSERT INTO notification_action_requests (id, user_id, notification_id, device_key, action, "
                     "text, status, created_at) VALUES (?, ?, ?, ?, ?, ?, 'pending', ?)",
                     (rid, user.id, nid, row["device_key"], action, text[:2000] or None, utc_now_iso()))
        conn.commit()
    if not await push.send(user, "notification_action", {"request_id": rid}):
        finish_request(user, rid, False, "couldn't reach the phone")
    return get_request(user, rid)


def get_request(user: User, rid: str) -> Optional[Dict[str, Any]]:
    """``user``'s request ``rid`` (None: not found or not theirs); a pending one past its TTL expires."""
    with get_db_connection() as conn:
        row = conn.execute("SELECT * FROM notification_action_requests WHERE id = ? AND user_id = ?",
                           (rid, user.id)).fetchone()
        if not row:
            return None
        d = dict(row)
        if d["status"] == "pending":
            created = datetime.datetime.fromisoformat(d["created_at"])
            if created.tzinfo is None:
                created = created.replace(tzinfo=datetime.timezone.utc)
            age = (datetime.datetime.now(datetime.timezone.utc) - created).total_seconds()
            if age > ACTION_REQUEST_TTL_SECONDS:
                # scoped: the user's request (query above)
                conn.execute("UPDATE notification_action_requests SET status = 'expired', done_at = ? "
                             "WHERE id = ? AND status = 'pending'", (utc_now_iso(), rid))
                conn.commit()
                d["status"] = "expired"
    return {"id": d["id"], "notification_id": d["notification_id"], "key": d["device_key"],
            "action": d["action"], "text": d["text"] or "", "status": d["status"], "error": d["error"],
            "created_at": d["created_at"], "done_at": d["done_at"]}


def finish_request(user: User, rid: str, ok: bool, error: str = "") -> Optional[Dict[str, Any]]:
    """The phone's answer. A done request also counts as seen (no more re-nudges)."""
    with get_db_connection() as conn:
        row = conn.execute("SELECT notification_id, status FROM notification_action_requests WHERE id = ? "
                           "AND user_id = ?", (rid, user.id)).fetchone()
        if not row:
            return None
        if row["status"] == "pending":
            # scoped: the user's request (query above)
            conn.execute("UPDATE notification_action_requests SET status = ?, error = ?, done_at = ? WHERE id = ?",
                         ("done" if ok else "failed", (error or None) if not ok else None, utc_now_iso(), rid))
            if ok:
                conn.execute("UPDATE notifications SET seen_at = COALESCE(seen_at, ?) WHERE id = ? AND user_id = ?",
                             (utc_now_iso(), row["notification_id"], user.id))
            conn.commit()
    return get_request(user, rid)
