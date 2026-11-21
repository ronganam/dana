"""Tuning values (server-wide env) and small helpers shared by the notifications modules."""
from __future__ import annotations

import datetime
import os
import sqlite3
from typing import Any, Dict, Iterable, List, Optional

from core import clock
from core.users import User

RETENTION_DAYS = int(os.getenv("NOTIFICATION_RETENTION_DAYS", "30"))
CONCURRENCY = int(os.getenv("NOTIFICATION_TRIAGE_CONCURRENCY", "4"))
ESCALATE = os.getenv("NOTIFICATION_ESCALATE", "1").strip().lower() not in ("0", "false", "no", "off")
ESCALATIONS_PER_HOUR = int(os.getenv("NOTIFICATION_ESCALATIONS_PER_HOUR", "6"))
DIGEST_TIMES = os.getenv("NOTIFICATION_DIGEST_TIMES", "08:30,20:30")
ESCALATION_PREFIX = "⚡ Dana:"
# Only fresh notifications are announced / turned into suggestions.
ESCALATE_MAX_AGE_SECONDS = int(os.getenv("NOTIFICATION_ESCALATE_MAX_AGE_SECONDS", "900"))
EXTRACT_MAX_AGE_HOURS = float(os.getenv("NOTIFICATION_EXTRACT_MAX_AGE_HOURS", "12"))
MAX_BATCH = 200
STATUSES = ("active", "dismissed", "archived")
BUCKETS = ("needs_you", "normal", "dismiss")
RULE_ACTIONS = ("mute", "important", "alert", "none")
# How an urgent notification is pushed to the phone: "screen" = the app's
# full-screen alert, "notify" = a plain notification (both via addons/phone/push.py).
ALERT_STYLE = os.getenv("NOTIFICATION_ALERT_STYLE", "screen").strip().lower()
# After the first alert, nudge again at these minutes while the user hasn't
# seen it (still in the shade, not opened / answered / snoozed / dismissed).
RENUDGE_MINUTES = [float(x) for x in os.getenv("NOTIFICATION_RENUDGE_MINUTES", "3,10").split(",") if x.strip()]


def s(v, limit: int = 4000) -> str:
    """``v`` as a stripped string of at most ``limit`` characters ("" for None)."""
    return str(v).strip()[:limit] if v is not None else ""


def clean_ids(ids: Optional[Iterable[Any]], limit: int = 1000) -> List[str]:
    """Distinct non-empty string ids, in order, at most ``limit``."""
    return [i for i in dict.fromkeys(ids or []) if isinstance(i, str) and i][:limit]


def utc_now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def to_utc_iso(dt: datetime.datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=clock.now().tzinfo)
    return dt.astimezone(datetime.timezone.utc).isoformat(timespec="seconds")


def local_iso(utc_iso: Optional[str]) -> Optional[str]:
    if not utc_iso:
        return None
    try:
        return clock.from_timestamp(datetime.datetime.fromisoformat(utc_iso).timestamp()).isoformat(timespec="seconds")
    except (TypeError, ValueError):
        return utc_iso


def posted_iso(value) -> str:
    """Epoch millis / seconds or an ISO string → local ISO timestamp."""
    try:
        if isinstance(value, (int, float)) or (isinstance(value, str) and value.strip().isdigit()):
            ts = float(value)
            if ts > 1e11:
                ts /= 1000.0
            return clock.from_timestamp(ts).isoformat(timespec="seconds")
        if isinstance(value, str) and value.strip():
            dt = datetime.datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=clock.get_timezone())
            return dt.astimezone(clock.get_timezone()).isoformat(timespec="seconds")
    except (ValueError, OverflowError, OSError):
        pass
    return clock.now().isoformat(timespec="seconds")


def age_seconds(posted_at: Optional[str]) -> float:
    """Seconds since the notification was posted (0 when unknown)."""
    if not posted_at:
        return 0.0
    try:
        t = datetime.datetime.fromisoformat(str(posted_at).replace("Z", "+00:00"))
    except ValueError:
        return 0.0
    if t.tzinfo is None:
        t = t.replace(tzinfo=clock.now().tzinfo)
    return max(0.0, (clock.now() - t).total_seconds())


def row(conn: sqlite3.Connection, user: User, nid: str) -> Optional[Dict[str, Any]]:
    """``user``'s notification ``nid`` as a dict (None: no such notification, or not theirs)."""
    r = conn.execute("SELECT * FROM notifications WHERE id = ? AND user_id = ?", (nid, user.id)).fetchone()
    return dict(r) if r else None
