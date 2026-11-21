"""Reading suggestions (table ``call_suggestions``): the API shape, lists and lookups.

Suggestions come from calls (``source_type='call'``, ``call_id`` = the call)
and from notifications (``source_type='notification'``, ``call_id`` = the
notification id); creating, accepting and dismissing them is the call
package's (integrations/phone/calls/: ``suggest``, ``actions``). They are read
here so the notifications Inbox, which attaches its suggestions to each
notification, and the call pipeline both depend on this module instead of on
each other.

Whose suggestion is whose: every suggestion has its user (``user_id``: the
user of the call or notification it came from, :func:`visible_sql`); another
user's suggestion answers like one that does not exist.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Tuple

from core.db import get_db_connection
from core.users import User
from integrations.phone.calls.labels import label_details


def _loads(raw, default):
    if raw is None or raw == "":
        return default
    if isinstance(raw, (dict, list)):
        return raw
    try:
        return json.loads(raw)
    except Exception:
        return default


def visible_sql(user: Optional[User], s: str = "s") -> Tuple[str, List[Any]]:
    """A WHERE condition (and its parameters) for the suggestions ``user`` may
    see, on ``call_suggestions`` aliased ``s``: their own (none without a user)."""
    if user is None:
        return "0", []
    return f"{s}.user_id = ?", [user.id]


def suggestion_to_api(user: Optional[User], row) -> Dict[str, Any]:
    d = dict(row)
    source = d.get("source_type") or "call"
    out = {
        "id": d["id"],
        # call_id stays the call (None for other sources) so older clients keep working.
        "call_id": d["call_id"] if source == "call" else None,
        "source_type": source,
        "source_id": d["call_id"],
        "kind": d["kind"],
        "status": d["status"],
        "payload": _loads(d.get("payload"), {}),
        "result_ref": d.get("result_ref"),
        "created_at": d.get("created_at"),
        "resolved_at": d.get("resolved_at"),
    }
    if source == "notification":
        out["notification"] = {
            "id": d["call_id"],
            "app_name": d.get("notif_app"),
            "package": d.get("notif_package"),
            "title": d.get("notif_title"),
            "text": d.get("notif_text"),
            "sender": d.get("notif_sender"),
            "posted_at": d.get("notif_posted_at"),
        }
    elif "call_title" in d:
        labels = _loads(d.get("call_labels"), [])
        out["call"] = {
            "id": d["call_id"],
            "title": d.get("call_title"),
            "contact": d.get("call_receiver") if d.get("call_caller") == "Me" else d.get("call_caller"),
            "call_timestamp": d.get("call_timestamp"),
            "summary": (d.get("call_summary") or "")[:1500] or None,
            "labels": labels,
            "label_details": label_details(user, labels),
        }
    return out


_SUGGESTION_SELECT = """
    SELECT s.*, c.title AS call_title, c.caller AS call_caller, c.receiver AS call_receiver, c.summary AS call_summary,
           c.call_timestamp AS call_timestamp, c.labels AS call_labels,
           n.app_name AS notif_app, n.package AS notif_package, n.title AS notif_title, n.text AS notif_text,
           n.sender AS notif_sender, n.posted_at AS notif_posted_at,
           COALESCE(c.call_timestamp, n.posted_at) AS source_time
    FROM call_suggestions s
    LEFT JOIN phone_calls c ON s.source_type = 'call' AND c.id = s.call_id
    LEFT JOIN notifications n ON s.source_type = 'notification' AND n.id = s.call_id
"""


def list_suggestions(user: Optional[User], status: Optional[str] = "pending", call_id: Optional[str] = None,
                     limit: int = 100, source: Optional[str] = None,
                     source_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """The suggestions ``user`` may see. ``source``: 'call' | 'notification' |
    None (all). ``call_id`` implies source 'call'."""
    where, params = [], []
    vis, vis_args = visible_sql(user)
    where.append(vis)
    params += vis_args
    if status:
        where.append("s.status = ?")
        params.append(status)
    if call_id:
        source, source_id = "call", call_id
    if source:
        where.append("s.source_type = ?")
        params.append(source)
    if source_id:
        where.append("s.call_id = ?")
        params.append(source_id)
    sql = _SUGGESTION_SELECT + f" WHERE {' AND '.join(where)}"
    # Alerts first, then newest sources first.
    sql += " ORDER BY (s.kind = 'alert') DESC, source_time DESC, s.created_at DESC LIMIT ?"
    params.append(limit)
    with get_db_connection() as conn:
        return [suggestion_to_api(user, r) for r in conn.execute(sql, params).fetchall()]


def get_suggestion(user: Optional[User], suggestion_id: str) -> Optional[Dict[str, Any]]:
    """One suggestion ``user`` may see (None: no such suggestion, or not theirs)."""
    vis, vis_args = visible_sql(user)
    with get_db_connection() as conn:
        row = conn.execute(_SUGGESTION_SELECT + f" WHERE s.id = ? AND {vis}", (suggestion_id, *vis_args)).fetchone()
    return suggestion_to_api(user, row) if row else None


def pending_titles(user: Optional[User]) -> List[str]:
    """Titles of the pending suggestions ``user`` may see (to avoid suggesting one twice)."""
    vis, vis_args = visible_sql(user)
    out = []
    with get_db_connection() as conn:
        for r in conn.execute(f"SELECT s.payload FROM call_suggestions s WHERE s.status = 'pending' AND {vis}",
                              vis_args):
            try:
                out.append(json.loads(r["payload"]).get("title") or "")
            except (ValueError, TypeError):
                pass
    return out


def pending_count(user: Optional[User], source: Optional[str] = None) -> int:
    """How many pending suggestions ``user`` may see (``source``: only that kind)."""
    vis, vis_args = visible_sql(user)
    extra, args = ("AND s.source_type = ?", [source]) if source else ("", [])
    with get_db_connection() as conn:
        return conn.execute(f"SELECT COUNT(*) FROM call_suggestions s WHERE s.status = 'pending' AND {vis} {extra}",
                            (*vis_args, *args)).fetchone()[0]
