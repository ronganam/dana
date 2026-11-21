"""Reading a user's notifications Inbox (the API shape, lists, FTS search,
counts) and forgetting notifications.

One row per distinct content per user (``content_hash``); a newer content
under the same shade key archives the older row (``removed_reason='updated'``,
kept and searchable). Full-text search uses ``notifications_fts`` (shared
index, keyed by notification id) and is always joined to the user's own rows,
so it never finds anyone else's.
"""
from __future__ import annotations

import json
import re
import sqlite3
from typing import Any, Dict, List, Optional

from core.db import get_db_connection
from core.users import User
from addons.phone import suggestions
from addons.phone.notifications import triage
from addons.phone.notifications.values import local_iso, utc_now_iso


def to_api(row, suggestions_of: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    d = dict(row)
    tri = {}
    try:
        tri = json.loads(d.get("triage") or "{}") or {}
    except (ValueError, TypeError):
        pass
    cat = d.get("category")
    return {
        "id": d["id"], "key": d.get("device_key"), "package": d["package"], "app_name": d.get("app_name"),
        "title": d.get("title"), "text": d.get("text"), "sub_text": d.get("sub_text"),
        "sender": d.get("sender"), "conversation": d.get("conversation"), "is_group": bool(d.get("is_group")),
        "posted_at": d.get("posted_at"), "status": d.get("status"), "removed_reason": d.get("removed_reason"),
        "triage_status": d.get("triage_status"), "category": cat,
        "category_emoji": triage.CATEGORY_EMOJI.get(cat or "", "🔔"),
        "priority": d.get("priority") or "normal", "priority_score": d.get("priority_score"),
        "bucket": d.get("bucket") or "normal", "escalated": bool(d.get("escalated_at")),
        "snoozed_until": local_iso(d.get("snoozed_until")) if (d.get("snoozed_until") or "") > utc_now_iso() else None,
        "signals": {k: tri[k] for k in ("needs_action", "from_person", "time_sensitive", "promotional") if k in tri},
        "actions": parse_actions(d.get("actions")),
        # Still in the phone's shade: its buttons can be pressed from here.
        "on_phone": d.get("status") == "active" and not d.get("removed_at") and bool(d.get("device_key"))
                    and not ((d.get("snoozed_until") or "") > utc_now_iso()),
        "suggestions": suggestions_of or [],
    }


def parse_actions(raw) -> List[Dict[str, Any]]:
    try:
        v = json.loads(raw or "[]")
        return v if isinstance(v, list) else []
    except (ValueError, TypeError):
        return []


def fts_query(q: str) -> str:
    terms = [t for t in re.findall(r"\w+", q or "", flags=re.UNICODE) if t]
    return " ".join(f'"{t}"*' for t in terms[:8])


def list_notifications(user: User, *, status: Optional[str] = "active", bucket: Optional[str] = None,
                       category: Optional[str] = None, package: Optional[str] = None, q: Optional[str] = None,
                       since: Optional[str] = None, limit: int = 200,
                       with_suggestions: bool = True) -> List[Dict[str, Any]]:
    """``user``'s notifications, newest first (``q``: full-text search)."""
    where, params = ["n.user_id = ?"], [user.id]
    now = utc_now_iso()
    if status == "snoozed":
        where.append("n.status = 'active' AND n.snoozed_until > ?")
        params.append(now)
    elif status and status != "all":
        where.append("n.status = ?")
        params.append(status)
        if status == "active":
            where.append("(n.snoozed_until IS NULL OR n.snoozed_until <= ?)")
            params.append(now)
    if bucket:
        where.append("n.bucket = ?")
        params.append(bucket)
    if category:
        where.append("n.category = ?")
        params.append(category)
    if package:
        where.append("n.package = ?")
        params.append(package)
    if since:
        where.append("n.posted_at >= ?")
        params.append(since)
    fts = fts_query(q or "")
    if q and not fts:
        return []
    if fts:
        where.append("n.id IN (SELECT id FROM notifications_fts WHERE notifications_fts MATCH ?)")
        params.append(fts)
    # scoped: where starts with n.user_id = ? (above)
    sql = f"SELECT n.* FROM notifications n WHERE {' AND '.join(where)} ORDER BY n.posted_at DESC LIMIT ?"
    params.append(max(1, min(int(limit or 200), 1000)))
    with get_db_connection() as conn:
        rows = conn.execute(sql, params).fetchall()
    sugg: Dict[str, List[Dict[str, Any]]] = {}
    if with_suggestions and rows:
        for sg in suggestions.list_suggestions(user, None, source="notification", limit=1000):
            sugg.setdefault(sg["source_id"], []).append(sg)
    return [to_api(r, sugg.get(r["id"])) for r in rows]


def get_notification(user: User, nid: str) -> Optional[Dict[str, Any]]:
    """``user``'s notification ``nid`` with its suggestions (None: not found or not theirs)."""
    with get_db_connection() as conn:
        row = conn.execute("SELECT * FROM notifications WHERE id = ? AND user_id = ?", (nid, user.id)).fetchone()
    if not row:
        return None
    return to_api(row, suggestions.list_suggestions(user, None, source="notification", source_id=nid))


def counts(user: User) -> Dict[str, Any]:
    """``user``'s Inbox counts per bucket, snoozed, untriaged, and the pending suggestions they can see."""
    with get_db_connection() as conn:
        now = utc_now_iso()
        rows = conn.execute("SELECT bucket, COUNT(*) AS n FROM notifications WHERE user_id = ? AND status = 'active' "
                            "AND (snoozed_until IS NULL OR snoozed_until <= ?) GROUP BY bucket", (user.id, now)).fetchall()
        snoozed = conn.execute("SELECT COUNT(*) FROM notifications WHERE user_id = ? AND status = 'active' "
                               "AND snoozed_until > ?", (user.id, now)).fetchone()[0]
        untriaged = conn.execute("SELECT COUNT(*) FROM notifications WHERE user_id = ? AND status = 'active' "
                                 "AND triage_status IN ('pending', 'failed')", (user.id,)).fetchone()[0]
    pending = suggestions.pending_count(user)
    by = {r["bucket"]: r["n"] for r in rows}
    return {"needs_you": by.get("needs_you", 0), "normal": by.get("normal", 0), "dismiss": by.get("dismiss", 0),
            "active": sum(by.values()), "snoozed": snoozed, "pending_suggestions": pending, "untriaged": untriaged}


def delete_package(user: User, package: str) -> int:
    """Forget everything from an app in ``user``'s Inbox (they stopped
    capturing it). Notifications a task was created from are kept."""
    with get_db_connection() as conn:
        ids = [r["id"] for r in conn.execute(
            "SELECT id FROM notifications WHERE user_id = ? AND package = ? AND id NOT IN "
            "(SELECT ref_id FROM task_links WHERE ref_type = 'notification')", (user.id, package)).fetchall()]
        delete_ids(conn, ids)
        conn.commit()
    return len(ids)


def delete_ids(conn: sqlite3.Connection, ids: List[str]) -> None:
    """Delete these notifications and their pending suggestions (no commit)."""
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        marks = ",".join("?" * len(chunk))
        # scoped: ids from the user's notifications (callers: purge_old, delete_package)
        conn.execute(f"DELETE FROM call_suggestions WHERE source_type = 'notification' AND status = 'pending' "
                     f"AND call_id IN ({marks})", chunk)
        # scoped: ids from the user's notifications (callers: purge_old, delete_package)
        conn.execute(f"DELETE FROM notifications WHERE id IN ({marks})", chunk)
