"""Status, snooze and "seen" of a user's notifications, and keeping the phone's shade in sync.

Nothing is dismissed automatically: the app shows "Suggested to dismiss" and
the user bulk-dismisses (``POST /api/notifications/status``), which the phone
mirrors by cancelling those notifications in the shade. Dismissals and
snoozes made elsewhere (web, agent) reach the phone in the next batch
response (:func:`keys_to_cancel`, :func:`keys_to_snooze`). Every function
works on the given user's notifications only; another user's ids are ignored
like ids that do not exist.
"""
from __future__ import annotations

import datetime
from typing import Any, Dict, Iterable, List, Optional

from core import clock
from core.db import get_db_connection
from core.users import User
from addons.phone.notifications.values import STATUSES, clean_ids, local_iso, to_utc_iso, utc_now_iso


def set_status(user: User, ids: List[str], status: str, reason: str = "dana") -> Dict[str, Any]:
    """Bulk dismiss / archive / restore. Returns the device keys to cancel."""
    if status not in STATUSES:
        raise ValueError(f"status must be one of {STATUSES}")
    ids = clean_ids(ids)
    if not ids:
        return {"updated": 0, "device_keys": []}
    marks = ",".join("?" * len(ids))
    with get_db_connection() as conn:
        keys = [r["device_key"] for r in conn.execute(
            f"SELECT device_key FROM notifications WHERE id IN ({marks}) AND user_id = ? AND device_key IS NOT NULL "
            f"AND status = 'active'", [*ids, user.id]).fetchall()]
        if status == "active":
            cur = conn.execute(
                f"UPDATE notifications SET status = 'active', removed_reason = NULL, removed_at = NULL, "
                f"snoozed_until = NULL, updated_at = CURRENT_TIMESTAMP WHERE id IN ({marks}) AND user_id = ?",
                [*ids, user.id])
        else:
            cur = conn.execute(
                f"UPDATE notifications SET status = ?, snoozed_until = NULL, removed_reason = COALESCE(removed_reason, ?), "
                f"removed_at = COALESCE(removed_at, ?), updated_at = CURRENT_TIMESTAMP "
                f"WHERE id IN ({marks}) AND user_id = ?",
                [status, reason, clock.now().isoformat(timespec="seconds"), *ids, user.id])
        conn.commit()
    return {"updated": cur.rowcount, "device_keys": keys if status != "active" else []}


_SNAPSHOT_COLS = ("status", "removed_reason", "removed_at", "snoozed_until")


def snapshot(user: User, ids: List[str]) -> List[Dict[str, Any]]:
    """The status fields of these notifications of ``user``, for :func:`restore` (undo)."""
    ids = clean_ids(ids)
    if not ids:
        return []
    marks = ",".join("?" * len(ids))
    with get_db_connection() as conn:
        return [dict(r) for r in conn.execute(
            f"SELECT id, {', '.join(_SNAPSHOT_COLS)} FROM notifications WHERE id IN ({marks}) AND user_id = ?",
            [*ids, user.id]).fetchall()]


def restore(user: User, snap: List[Dict[str, Any]]) -> int:
    """Put ``user``'s notifications back as :func:`snapshot` saw them (in Dana
    only: what the phone already cancelled in its shade stays gone there)."""
    n = 0
    with get_db_connection() as conn:
        for sn in snap:
            cur = conn.execute(
                f"UPDATE notifications SET {', '.join(c + ' = ?' for c in _SNAPSHOT_COLS)}, "
                f"updated_at = CURRENT_TIMESTAMP WHERE id = ? AND user_id = ?",
                (*[sn.get(c) for c in _SNAPSHOT_COLS], sn["id"], user.id))
            n += cur.rowcount
        conn.commit()
    return n


def snooze(user: User, ids: List[str], until: Optional[datetime.datetime]) -> Dict[str, Any]:
    """Snooze ``user``'s active notifications until ``until`` (None = wake them now).

    Returns ``device_keys`` [{key, until_ms}] the phone snoozes in its shade
    (Android brings them back there by itself)."""
    ids = clean_ids(ids)
    if not ids:
        return {"updated": 0, "until": None, "device_keys": []}
    if until is not None and to_utc_iso(until) <= utc_now_iso():
        raise ValueError("until must be in the future")
    value = to_utc_iso(until) if until is not None else None
    marks = ",".join("?" * len(ids))
    with get_db_connection() as conn:
        cur = conn.execute(
            f"UPDATE notifications SET snoozed_until = ?, updated_at = CURRENT_TIMESTAMP "
            f"WHERE id IN ({marks}) AND user_id = ? AND status = 'active'", [value, *ids, user.id])
        keys = [r["device_key"] for r in conn.execute(
            f"SELECT device_key FROM notifications WHERE id IN ({marks}) AND user_id = ? AND status = 'active' "
            f"AND device_key IS NOT NULL", [*ids, user.id]).fetchall()]
        conn.commit()
    until_ms = int(until.timestamp() * 1000) if until is not None else None
    return {"updated": cur.rowcount, "until": local_iso(value),
            "device_keys": [{"key": k, "until_ms": until_ms} for k in dict.fromkeys(keys)] if until_ms else []}


def keys_to_snooze(user: User, active_keys: Iterable[str]) -> List[Dict[str, Any]]:
    """Of the keys in ``user``'s phone's shade, the ones snoozed in Dana (web,
    agent) for at least another minute — the phone snoozes them there too."""
    keys = clean_ids(active_keys, 500)
    if not keys:
        return []
    marks = ",".join("?" * len(keys))
    soon = (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=60)).isoformat(timespec="seconds")
    with get_db_connection() as conn:
        rows = conn.execute(
            f"SELECT device_key, MAX(snoozed_until) AS until FROM notifications WHERE device_key IN ({marks}) "
            f"AND user_id = ? AND status = 'active' AND snoozed_until > ? GROUP BY device_key",
            [*keys, user.id, soon]).fetchall()
    out = []
    for r in rows:
        try:
            ms = int(datetime.datetime.fromisoformat(r["until"]).timestamp() * 1000)
        except (TypeError, ValueError):
            continue
        out.append({"key": r["device_key"], "until_ms": ms})
    return out


def keys_to_cancel(user: User, active_keys: Iterable[str]) -> List[str]:
    """Of the keys still in ``user``'s phone's shade, the ones they dismissed in Dana."""
    keys = clean_ids(active_keys, 500)
    if not keys:
        return []
    marks = ",".join("?" * len(keys))
    with get_db_connection() as conn:
        dismissed = {r["device_key"] for r in conn.execute(
            f"SELECT device_key FROM notifications WHERE device_key IN ({marks}) AND user_id = ? "
            f"AND status != 'active' AND removed_reason = 'dana'", [*keys, user.id]).fetchall()}
        # A key whose newest row is active again (restored / new content) stays.
        live = {r["device_key"] for r in conn.execute(
            f"SELECT device_key FROM notifications WHERE device_key IN ({marks}) AND user_id = ? AND status = 'active'",
            [*keys, user.id]).fetchall()}
    return [k for k in keys if k in dismissed and k not in live]


def mark_seen(user: User, ids: List[str]) -> int:
    """``user`` saw these (opened from the alert screen, asked Dana): stop nudging."""
    ids = clean_ids(ids, 200)
    if not ids:
        return 0
    marks = ",".join("?" * len(ids))
    with get_db_connection() as conn:
        cur = conn.execute(f"UPDATE notifications SET seen_at = COALESCE(seen_at, ?), updated_at = CURRENT_TIMESTAMP "
                           f"WHERE id IN ({marks}) AND user_id = ?",
                           [clock.now().isoformat(timespec="seconds"), *ids, user.id])
        conn.commit()
    return cur.rowcount
