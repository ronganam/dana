"""Announcing urgent notifications on their user's phone, and re-nudging until seen.

Urgent ones (and every one from an app / sender with the ``alert`` rule) are
pushed once each to the user's phone (full-screen alert or a plain
notification, ``NOTIFICATION_ALERT_STYLE``), at most
``NOTIFICATION_ESCALATIONS_PER_HOUR`` per user. The re-nudge loop sends the
alert again at ``NOTIFICATION_RENUDGE_MINUTES`` while the user hasn't seen it,
once per user with ``phone``.
"""
from __future__ import annotations

import asyncio
import datetime
import logging
import sqlite3
import time
from typing import Any, Dict, List, Optional

from core import clock
from core.db import get_db_connection
from core.users import User
from addons.phone import push
from addons.phone.config import for_each_user
from addons.phone.notifications import triage
from addons.phone.notifications.values import (ALERT_STYLE, ESCALATE, ESCALATION_PREFIX, ESCALATIONS_PER_HOUR,
                                                     RENUDGE_MINUTES, utc_now_iso)

logger = logging.getLogger(__name__)

# user id → times of the escalations sent in the last hour (the rate limit is per user).
_escalations: Dict[str, List[float]] = {}


def alert_data(n: Dict[str, Any], attempt: int = 1) -> Dict[str, Any]:
    """The phone's full-screen alert (``alert`` push event)."""
    return {
        "notification_id": n.get("id"), "key": n.get("device_key"), "package": n.get("package"),
        "app": n.get("app_name"), "title": (n.get("title") or "")[:200], "text": (n.get("text") or "")[:600],
        "priority": n.get("priority") or "urgent",
        "emoji": triage.CATEGORY_EMOJI.get(n.get("category") or "", "🔔"),
        "attempt": attempt,
    }


async def _alert_screen(user: User, n: Dict[str, Any], attempt: int = 1) -> bool:
    """Open the full-screen alert on ``user``'s phone (direct push)."""
    return await push.send(user, "alert", alert_data(n, attempt))


async def escalate(user: User, n: Dict[str, Any]) -> bool:
    """Push ``user``'s urgent notification (or one from an "alert" app) to
    their phone's full-screen alert once (rate limited per user)."""
    if not ESCALATE or n.get("escalated_at"):
        return False
    now = time.time()
    sent = _escalations.setdefault(user.id, [])
    sent[:] = [t for t in sent if now - t < 3600]
    if len(sent) >= ESCALATIONS_PER_HOUR:
        logger.info(f"Escalation of {n['id']} skipped: hourly limit reached")
        return False
    if ALERT_STYLE == "screen":
        ok = await _alert_screen(user, n)
    else:
        title = f"{ESCALATION_PREFIX} {n.get('app_name')}"
        body = " — ".join(x for x in (n.get("title"), n.get("text")) if x)[:300]
        ok = await push.notify(user, title, body, tag=f"dana_notif_{n['id']}", channel="important")
    if ok:
        sent.append(now)
        with get_db_connection() as conn:
            conn.execute("UPDATE notifications SET escalated_at = ? WHERE id = ? AND user_id = ?",
                         (clock.now().isoformat(timespec="seconds"), n["id"], user.id))
            conn.commit()
    return ok


def _unseen_latest(conn: sqlite3.Connection, user: User, n: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """The notification to nudge about, or None once the user has seen it.

    Seen = opened / swiped / dismissed / snoozed, marked seen from the alert
    screen, or gone from the shade because its app cleared it (they read the
    chat, maybe on another device). A newer message in the same chat replaces
    the escalated row; the nudge then shows the newest text.
    """
    if n.get("seen_at"):
        return None
    if n.get("device_key"):
        row = conn.execute(
            "SELECT * FROM notifications WHERE device_key = ? AND package = ? AND posted_at >= ? AND user_id = ? "
            "ORDER BY posted_at DESC LIMIT 1", (n["device_key"], n["package"], n["posted_at"], user.id)).fetchone()
    else:
        row = conn.execute("SELECT * FROM notifications WHERE id = ? AND user_id = ?", (n["id"], user.id)).fetchone()
    latest = dict(row) if row else None
    if not latest or latest["status"] != "active" or latest.get("removed_at") or latest.get("seen_at"):
        return None
    if (latest.get("snoozed_until") or "") > utc_now_iso():
        return None
    return latest


async def renudge_due(user: User, now: Optional[datetime.datetime] = None) -> int:
    """Re-send the alert for ``user``'s escalated notifications they haven't seen yet."""
    if not ESCALATE or not RENUDGE_MINUTES:
        return 0
    now = now or clock.now()
    horizon = (now - datetime.timedelta(minutes=max(RENUDGE_MINUTES) + 30)).isoformat(timespec="seconds")
    sent = 0
    with get_db_connection() as conn:
        rows = [dict(r) for r in conn.execute(
            "SELECT * FROM notifications WHERE user_id = ? AND escalated_at IS NOT NULL AND escalated_at >= ? "
            "AND COALESCE(nudges, 0) < ?", (user.id, horizon, len(RENUDGE_MINUTES))).fetchall()]
    for n in rows:
        done = int(n.get("nudges") or 0)
        try:
            first = datetime.datetime.fromisoformat(n["escalated_at"])
        except (TypeError, ValueError):
            continue
        if first.tzinfo is None:
            first = first.replace(tzinfo=now.tzinfo)
        if now < first + datetime.timedelta(minutes=RENUDGE_MINUTES[done]):
            continue
        with get_db_connection() as conn:
            target = _unseen_latest(conn, user, n)
            if target is None:
                # scoped: ids from the user's notifications (query above)
                conn.execute("UPDATE notifications SET nudges = ? WHERE id = ?", (len(RENUDGE_MINUTES), n["id"]))
                conn.commit()
                continue
            # scoped: ids from the user's notifications (query above)
            conn.execute("UPDATE notifications SET nudges = ? WHERE id = ?", (done + 1, n["id"]))
            conn.commit()
        target = {**target, "id": n["id"] if target["id"] == n["id"] else target["id"],
                  "priority": n.get("priority") or "urgent", "category": target.get("category") or n.get("category")}
        if ALERT_STYLE == "screen":
            ok = await _alert_screen(user, target, attempt=done + 2)
        else:
            body = " — ".join(x for x in (target.get("title"), target.get("text")) if x)[:300]
            ok = await push.notify(user, f"{ESCALATION_PREFIX} {target.get('app_name')} (still waiting)", body,
                                   tag=f"dana_notif_{n['id']}", channel="important")
        logger.info(f"Re-nudged {n['id']} (#{done + 1}): {'sent' if ok else 'failed'}")
        sent += int(ok)
    return sent
