"""A user's per-app / per-sender rules (``mute`` / ``important`` / ``alert``)
and how they treat each app (engagement), which feed the triage.

Changing a rule and re-bucketing what it covers is :mod:`.buckets`.
"""
from __future__ import annotations

import datetime
import sqlite3
import uuid
from typing import Any, Dict, List, Optional

from core import clock
from core.db import get_db_connection
from core.users import User
from addons.phone.notifications.values import RULE_ACTIONS, s


def list_rules(user: User) -> List[Dict[str, Any]]:
    with get_db_connection() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT id, package, sender, action, created_at FROM notification_rules WHERE user_id = ? "
            "ORDER BY created_at DESC", (user.id,))]


def set_rule(user: User, package: str, action: str, sender: str = "") -> Dict[str, Any]:
    package, sender = s(package, 200), s(sender, 200)
    if not package:
        raise ValueError("package is required")
    if action not in RULE_ACTIONS:
        raise ValueError("action must be mute, important, alert or none")
    with get_db_connection() as conn:
        if action == "none":
            conn.execute("DELETE FROM notification_rules WHERE user_id = ? AND package = ? AND sender = ?",
                         (user.id, package, sender))
            conn.commit()
            return {"package": package, "sender": sender, "action": "none"}
        conn.execute(
            "INSERT INTO notification_rules (id, user_id, package, sender, action) VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(user_id, package, sender) DO UPDATE SET action = excluded.action",
            (str(uuid.uuid4()), user.id, package, sender, action))
        conn.commit()
    return {"package": package, "sender": sender, "action": action}


def rule_for(conn: sqlite3.Connection, user: User, package: str, sender: str) -> Optional[str]:
    """The rule of ``user``'s that applies (a sender rule over the app's), or None."""
    row = None
    if sender:
        row = conn.execute("SELECT action FROM notification_rules WHERE user_id = ? AND package = ? AND sender = ?",
                           (user.id, package, sender)).fetchone()
    if not row:
        row = conn.execute("SELECT action FROM notification_rules WHERE user_id = ? AND package = ? AND sender = ''",
                           (user.id, package)).fetchone()
    return row["action"] if row else None


def app_rules(conn: sqlite3.Connection, user: User) -> Dict[str, str]:
    """package → ``user``'s app rule (sender rules left out)."""
    return {r["package"]: r["action"] for r in conn.execute(
        "SELECT package, action FROM notification_rules WHERE user_id = ? AND sender = ''", (user.id,))}


def engagement(conn: sqlite3.Connection, user: User, package: str, days: int = 30) -> Dict[str, int]:
    since = (clock.now() - datetime.timedelta(days=days)).isoformat(timespec="seconds")
    row = conn.execute(
        "SELECT SUM(removed_reason = 'opened') AS opened, SUM(removed_reason IN ('swiped', 'dana')) AS swiped "
        "FROM notifications WHERE user_id = ? AND package = ? AND posted_at >= ?", (user.id, package, since)).fetchone()
    return {"opened": int(row["opened"] or 0), "swiped": int(row["swiped"] or 0)}


def is_low_engagement(stats: Dict[str, int]) -> bool:
    total = stats["opened"] + stats["swiped"]
    return total >= 10 and stats["swiped"] / total >= 0.9


def apps_summary(user: User, days: int = 30) -> List[Dict[str, Any]]:
    """Per-app counts of ``user``'s notifications for the Settings / rules screen."""
    since = (clock.now() - datetime.timedelta(days=days)).isoformat(timespec="seconds")
    with get_db_connection() as conn:
        rules = app_rules(conn, user)
        rows = conn.execute(
            """SELECT package, MAX(app_name) AS app_name, COUNT(*) AS total,
                      SUM(removed_reason = 'opened') AS opened, SUM(removed_reason IN ('swiped','dana')) AS swiped,
                      SUM(bucket = 'dismiss') AS suggested_dismiss, MAX(posted_at) AS last_posted
               FROM notifications WHERE user_id = ? AND posted_at >= ? GROUP BY package ORDER BY total DESC""",
            (user.id, since)).fetchall()
    return [{"package": r["package"], "app_name": r["app_name"], "total": r["total"],
             "opened": r["opened"] or 0, "swiped": r["swiped"] or 0,
             "suggested_dismiss": r["suggested_dismiss"] or 0, "last_posted": r["last_posted"],
             "rule": rules.get(r["package"])} for r in rows]


def app_name(conn: sqlite3.Connection, user: User, package: str) -> str:
    """The name ``user``'s notifications give ``package`` (the package itself when none do)."""
    row = conn.execute("SELECT MAX(app_name) FROM notifications WHERE user_id = ? AND package = ?",
                       (user.id, package)).fetchone()
    return (row[0] if row else None) or package


def rule_label(app: str, sender: str, action: str) -> str:
    """What a rule change means, in the Notifications screen's words."""
    name = f"{sender} on {app}" if sender else app
    return {"mute": f"{name} will stay quiet", "alert": f"{name} will interrupt you",
            "important": f"{name} marked important"}.get(action, f"Dana decides for {name}")
