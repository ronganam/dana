"""The notifications digest: at ``NOTIFICATION_DIGEST_TIMES`` (once per slot
per day) to each user with ``phone``, on Telegram (core/notify.py) and their
phone (``notify`` event, ``digest`` channel), only when something needs them,
a suggestion waits, or ≥ 5 are suggested to dismiss.

When each user's slots last fired, and until when their last digest covered,
is kept in ``notification_digest_state`` (slot ``HHMM`` → date, ``last`` →
local ISO time).
"""
from __future__ import annotations

import asyncio
import datetime
import logging
import re
import sqlite3
from typing import Any, Dict, List, Optional, Tuple

from core import clock
from core.db import get_db_connection
from core.notify import notify
from core.users import User
from integrations.phone import push, suggestions
from integrations.phone.config import for_each_user
from integrations.phone.notifications import triage
from integrations.phone.notifications.values import DIGEST_TIMES, utc_now_iso

logger = logging.getLogger(__name__)


def build_digest(user: User, since_iso: str) -> Optional[Dict[str, Any]]:
    """What arrived on ``user``'s phone since ``since_iso`` (None when nothing did)."""
    with get_db_connection() as conn:
        rows = [dict(r) for r in conn.execute(
            "SELECT id, app_name, title, text, category, priority, bucket, status, snoozed_until FROM notifications "
            "WHERE user_id = ? AND posted_at >= ? ORDER BY priority_score DESC, posted_at DESC",
            (user.id, since_iso)).fetchall()]
    pending = suggestions.pending_count(user, source="notification")
    if not rows:
        return None
    now = utc_now_iso()
    # Snoozed ones are out of the way on purpose; they come back when due.
    active = [r for r in rows if r["status"] == "active" and not ((r.get("snoozed_until") or "") > now)]
    needs = [r for r in active if r["bucket"] == "needs_you"]
    dismiss = [r for r in active if r["bucket"] == "dismiss"]
    by_cat: Dict[str, int] = {}
    for r in rows:
        by_cat[r["category"] or "other"] = by_cat.get(r["category"] or "other", 0) + 1
    lines = [f"{len(rows)} notifications" + (f", {len(needs)} need you" if needs else "")
             + (f", {len(dismiss)} suggested to dismiss" if dismiss else "") + "."]
    for r in needs[:5]:
        em = triage.CATEGORY_EMOJI.get(r["category"] or "other", "🔔")
        lines.append(f"{em} {r['app_name']}: {(r['title'] or '')[:60]} — {(r['text'] or '')[:80]}".rstrip(" —"))
    if pending:
        lines.append(f"✅ {pending} suggested task(s) waiting in the Inbox.")
    top = sorted(by_cat.items(), key=lambda kv: -kv[1])[:4]
    lines.append("By kind: " + ", ".join(f"{triage.CATEGORY_EMOJI.get(k, '🔔')} {v}" for k, v in top))
    return {"text": "\n".join(lines), "total": len(rows), "needs_you": len(needs),
            "suggested_dismiss": len(dismiss), "pending_suggestions": pending}


def _digest_slots() -> List[Tuple[int, int]]:
    out = []
    for part in DIGEST_TIMES.split(","):
        part = part.strip()
        if re.fullmatch(r"\d{1,2}:\d{2}", part):
            h, m = map(int, part.split(":"))
            if 0 <= h < 24 and 0 <= m < 60:
                out.append((h, m))
    return out


def _state(conn: sqlite3.Connection, user: User, slot: str) -> Optional[str]:
    row = conn.execute("SELECT value FROM notification_digest_state WHERE user_id = ? AND slot = ?",
                       (user.id, slot)).fetchone()
    return row["value"] if row else None


def _set_state(conn: sqlite3.Connection, user: User, slot: str, value: str) -> None:
    conn.execute("INSERT INTO notification_digest_state (user_id, slot, value) VALUES (?, ?, ?) "
                 "ON CONFLICT(user_id, slot) DO UPDATE SET value = excluded.value", (user.id, slot, value))


async def send_digest(user: User, since_iso: str) -> bool:
    """Send ``user``'s digest since ``since_iso`` to them (Telegram) and their phone."""
    d = build_digest(user, since_iso)
    if not d or (d["needs_you"] == 0 and d["pending_suggestions"] == 0 and d["suggested_dismiss"] < 5):
        return False
    await notify(user, "🔔 Notifications digest\n\n" + d["text"])
    await push.notify(user, "🔔 Notifications digest", d["text"], tag="dana_digest", channel="digest")
    return True


async def digest_due(user: User, now: Optional[datetime.datetime] = None) -> int:
    """Send ``user``'s digest for each slot that is due (within 10 min of it,
    once per day). Returns how many slots fired."""
    now = now or clock.now()
    fired = 0
    for h, m in _digest_slots():
        slot = f"{h:02d}{m:02d}"
        slot_time = now.replace(hour=h, minute=m, second=0, microsecond=0)
        if not (slot_time <= now < slot_time + datetime.timedelta(minutes=10)):
            continue
        today = now.strftime("%Y-%m-%d")
        with get_db_connection() as conn:
            if _state(conn, user, slot) == today:
                continue
            since = _state(conn, user, "last") or (now - datetime.timedelta(hours=12)).isoformat(timespec="seconds")
            _set_state(conn, user, slot, today)
            _set_state(conn, user, "last", now.isoformat(timespec="seconds"))
            conn.commit()
        fired += 1
        await send_digest(user, since)
    return fired


async def digest_loop(interval_seconds: int = 60) -> None:
    """Every ``interval_seconds``: :func:`digest_due` for every user with ``phone``, as that user."""
    if not _digest_slots():
        return
    while True:
        try:
            await for_each_user("Notification digest", digest_due)
        except Exception as e:
            logger.error(f"Notification digest failed: {e}", exc_info=True)
        await asyncio.sleep(interval_seconds)
