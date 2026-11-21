"""Batch ingest from a user's phone: validation, OTP redaction, loop guards,
dedupe per user (``content_hash``) and the shade's removals.

The Android ``NotificationListenerService`` batches what the phone shows
(minus denied apps, OTPs redacted) to ``POST /api/notifications/batch``
together with removals from the shade (``{key, reason}``); everything is
stored for the user whose token sent it. The same content on two people's
phones is two rows, one each.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import sqlite3
import uuid
from typing import Any, Dict, Iterable, List, Optional

from core import clock
from core.db import get_db_connection
from core.users import User
from addons.phone.notifications.values import ESCALATION_PREFIX, MAX_BATCH, posted_iso, s

logger = logging.getLogger(__name__)

# Our own notifications and the escalation re-posts must never loop back in.
_OWN_PACKAGES = ("app.ganam.assistant",)

_OTP_CONTEXT = re.compile(r"(code|otp|password|passcode|verification|verify|pin|קוד|סיסמ|אימות)", re.I)
_OTP_NUMBER = re.compile(r"(?<!\d)(\d[\d\- ]{2,8}\d)(?!\d)")


def redact(text: str) -> str:
    """Hide one-time codes (the phone already does this; this is the safety net)."""
    if not text or not _OTP_CONTEXT.search(text):
        return text
    return _OTP_NUMBER.sub(lambda m: "[code]" if sum(c.isdigit() for c in m.group(1)) >= 4 else m.group(1), text)


def content_hash(n: Dict[str, Any]) -> str:
    raw = "\x1f".join([n.get("package") or "", n.get("title") or "", n.get("text") or "",
                       n.get("sender") or "", n.get("conversation") or ""])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def normalize_item(raw: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Validate one posted notification; None when it should be dropped."""
    if not isinstance(raw, dict):
        return None
    package = s(raw.get("package"), 200)
    if not package or package.startswith(_OWN_PACKAGES):
        return None
    title = redact(s(raw.get("title"), 500))
    text = redact(s(raw.get("big_text") or raw.get("text")))
    if not title and not text:
        return None
    if title.startswith(ESCALATION_PREFIX) or text.startswith(ESCALATION_PREFIX):
        return None
    n = {
        "device_key": s(raw.get("key"), 500) or None,
        "package": package,
        "app_name": s(raw.get("app_name"), 200) or package,
        "title": title,
        "text": text,
        "sub_text": redact(s(raw.get("sub_text"), 500)),
        "sender": s(raw.get("sender"), 200),
        "conversation": s(raw.get("conversation"), 200),
        "is_group": 1 if raw.get("is_group") else 0,
        "android_category": s(raw.get("category"), 50) or None,
        "channel": s(raw.get("channel"), 200) or None,
        "posted_at": posted_iso(raw.get("posted_at")),
        "actions": _actions(raw.get("actions")),
    }
    n["content_hash"] = content_hash(n)
    return n


def _actions(raw) -> Optional[str]:
    """The notification's buttons: [{"title", "reply"}] (max 6), as JSON."""
    if not isinstance(raw, list):
        return None
    out = []
    for a in raw[:6]:
        if isinstance(a, dict) and s(a.get("title"), 60):
            out.append({"title": s(a.get("title"), 60), "reply": bool(a.get("reply"))})
    return json.dumps(out, ensure_ascii=False) if out else None


def ingest(user: User, posted: Iterable[Dict[str, Any]], removed: Iterable[Dict[str, Any]] = ()) -> Dict[str, Any]:
    """Store ``user``'s new notifications and apply their shade's removals. Returns ids to triage."""
    new_ids: List[str] = []
    duplicates = dropped = 0
    items = list(posted or [])[:MAX_BATCH]
    named: set = set()
    with get_db_connection() as conn:
        for raw in items:
            n = normalize_item(raw)
            if not n:
                dropped += 1
                continue
            if n["app_name"] != n["package"] and n["package"] not in named:
                # Older app versions couldn't read other apps' labels.
                named.add(n["package"])
                conn.execute("UPDATE notifications SET app_name = ? WHERE package = ? AND app_name = package "
                             "AND user_id = ?", (n["app_name"], n["package"], user.id))
            existing = conn.execute("SELECT id, device_key FROM notifications WHERE content_hash = ? AND user_id = ?",
                                    (n["content_hash"], user.id)).fetchone()
            if existing:
                duplicates += 1
                if n["device_key"] and existing["device_key"] != n["device_key"]:
                    conn.execute("UPDATE notifications SET device_key = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                                 (n["device_key"], existing["id"]))
                if n["actions"]:
                    conn.execute("UPDATE notifications SET actions = ? WHERE id = ?", (n["actions"], existing["id"]))
                continue
            nid = str(uuid.uuid4())
            if n["device_key"]:
                # Same shade entry with new content (a chat getting another
                # message): the older version leaves the Inbox but is kept.
                conn.execute(
                    "UPDATE notifications SET status = 'archived', removed_reason = 'updated', removed_at = ?, "
                    "updated_at = CURRENT_TIMESTAMP WHERE device_key = ? AND package = ? AND status = 'active' "
                    "AND user_id = ?", (n["posted_at"], n["device_key"], n["package"], user.id))
            conn.execute(
                """INSERT INTO notifications (id, user_id, device_key, package, app_name, title, text, sub_text, sender,
                       conversation, is_group, android_category, channel, posted_at, content_hash, actions)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (nid, user.id, n["device_key"], n["package"], n["app_name"], n["title"], n["text"], n["sub_text"],
                 n["sender"], n["conversation"], n["is_group"], n["android_category"], n["channel"],
                 n["posted_at"], n["content_hash"], n["actions"]),
            )
            new_ids.append(nid)
        cleared = _apply_removals(conn, user, removed or [])
        conn.commit()
    return {"accepted": len(new_ids), "duplicates": duplicates, "dropped": dropped,
            "removed": cleared, "ids": new_ids}


# Android NotificationListenerService REASON_* → what the user did.
_REASONS = {"click": "opened", "cancel": "swiped", "cancel_all": "swiped", "app": "app", "dana": "dana",
            "timeout": "timeout", "snoozed": "snoozed", "other": "other"}


def _apply_removals(conn: sqlite3.Connection, user: User, removed: Iterable[Dict[str, Any]]) -> int:
    n = 0
    for r in list(removed)[:MAX_BATCH]:
        if not isinstance(r, dict) or not r.get("key"):
            continue
        reason = _REASONS.get(s(r.get("reason"), 30).lower(), "other")
        if reason == "snoozed":
            continue  # the user snoozed it; it comes back to the shade by itself
        if reason == "other" and r.get("code") is not None:
            logger.info("Notification removed with unmapped Android reason %s", s(r.get("code"), 10))
        when = posted_iso(r.get("at")) if r.get("at") else clock.now().isoformat(timespec="seconds")
        if reason in ("opened", "swiped", "dana"):
            cur = conn.execute(
                "UPDATE notifications SET status = 'dismissed', removed_reason = ?, removed_at = ?, "
                "updated_at = CURRENT_TIMESTAMP WHERE device_key = ? AND status = 'active' AND user_id = ?",
                (reason, when, s(r["key"], 500), user.id))
        elif reason == "app":
            # The app cleared it: nearly always because the user read it there
            # (on this phone or another device), and chat apps often cancel
            # their own notification a moment before the system logs the tap.
            # Out of the Inbox, kept (archived, searchable, restorable).
            cur = conn.execute(
                "UPDATE notifications SET status = 'archived', removed_reason = 'app', removed_at = ?, "
                "updated_at = CURRENT_TIMESTAMP WHERE device_key = ? AND status = 'active' AND user_id = ?",
                (when, s(r["key"], 500), user.id))
        else:
            # Timed out (or unknown): gone from the shade but still unread in
            # the Inbox — only remember why.
            cur = conn.execute(
                "UPDATE notifications SET removed_reason = COALESCE(removed_reason, ?), removed_at = COALESCE(removed_at, ?), "
                "updated_at = CURRENT_TIMESTAMP WHERE device_key = ? AND status = 'active' AND user_id = ?",
                (reason, when, s(r["key"], 500), user.id))
        n += cur.rowcount
    return n
