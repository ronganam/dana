"""The events the ``phone`` addon emits for the event catalog (../docs/addons.md,
"Events"): a new notification, a new email (from an email app's notification)
and an analysed call, with the data each is emitted with and their history
from the user's own tables (dry runs of watches).

:func:`announce_notifications` is called with the rows :mod:`.notifications.ingest`
just inserted (the same dedupe as the Inbox: a notification seen before is not
new); :func:`announce_call` once a call's first analysis is stored.
"""
from __future__ import annotations

import datetime
import re
import sqlite3
from typing import Any, Dict, Iterable, List, Mapping, Optional

from core import clock, events
from core.addons import EventType
from core.addons.hooks import PastEvent
from core.db import get_db_connection
from core.users import User

# Email apps by package: their notifications are emails (phone.email.received), not notifications.
EMAIL_APPS = {"com.google.android.gm": "Gmail", "com.microsoft.office.outlook": "Outlook"}
# The Inbox's priority labels, lowest first: the index is the event's priority (0-4).
PRIORITIES = ("noise", "low", "normal", "important", "urgent")

_NUMBER = r"(?<![\d.,])(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)"
_SHEKEL = r"(?:₪|ש\"ח|ש״ח|ש''ח|(?<![A-Za-z])(?:NIS|ILS)(?![A-Za-z]))"
_ILS = re.compile(rf"{_SHEKEL}\s?{_NUMBER}|{_NUMBER}\s?{_SHEKEL}", re.IGNORECASE)


def ils_amount(text: Optional[str]) -> Optional[float]:
    """The first amount in shekels in ``text`` ("₪120", "1,250.50 ש״ח", "NIS 40",
    "99 ILS"), as a positive number; None when it names none."""
    m = _ILS.search(text or "")
    if not m:
        return None
    return float((m.group(1) or m.group(2)).replace(",", ""))


def email_account(package: str, app_name: str = "") -> str:
    """"Gmail" / "Outlook" when the notification is an email app's, else ""."""
    if package in EMAIL_APPS:
        return EMAIL_APPS[package]
    return next((name for name in EMAIL_APPS.values() if (app_name or "").strip().lower() == name.lower()), "")


def _priority(label: Any) -> Optional[int]:
    return PRIORITIES.index(label) if label in PRIORITIES else None


def notification_data(n: Mapping[str, Any]) -> Dict[str, Any]:
    """``phone.notification.received``'s data of a notification row (or the
    values ingest inserts): category and priority only once triaged."""
    text = n.get("text") or ""
    return {"notification_id": n.get("id") or "", "app": n.get("app_name") or n.get("package") or "",
            "sender": n.get("sender") or "", "conversation": n.get("conversation") or "",
            "is_group": bool(n.get("is_group")), "title": n.get("title") or "", "text": text,
            "category": n.get("category") or "", "priority": _priority(n.get("priority")),
            "amount": ils_amount(f"{n.get('title') or ''}\n{text}")}


def email_data(n: Mapping[str, Any]) -> Dict[str, Any]:
    """``phone.email.received``'s data of an email app's notification: the
    sender is its title (or sender), the subject the text's first line."""
    lines = (n.get("text") or "").strip().split("\n", 1)
    subject = lines[0].strip()
    body = lines[1].strip() if len(lines) > 1 else ""
    return {"notification_id": n.get("id") or "", "account": email_account(n.get("package") or "", n.get("app_name") or ""),
            "from": n.get("sender") or n.get("title") or "", "subject": subject, "text": body,
            "amount": ils_amount(f"{subject}\n{body}")}


def announce_notifications(user: User, rows: Iterable[Mapping[str, Any]]) -> int:
    """Emit ``phone.email.received`` / ``phone.notification.received`` for each
    of ``user``'s newly stored notifications; returns how many."""
    n = 0
    for row in rows:
        if email_account(row.get("package") or "", row.get("app_name") or ""):
            events.emit("phone.email.received", user, **email_data(row))
        else:
            events.emit("phone.notification.received", user, **notification_data(row))
        n += 1
    return n


def _parse(value: Any) -> Optional[datetime.datetime]:
    try:
        when = datetime.datetime.fromisoformat(str(value or "").strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return when.replace(tzinfo=clock.get_timezone()) if when.tzinfo is None else when


def _from(since: datetime.datetime) -> str:
    """A lower bound for a local ISO column (a day early: offsets differ around DST)."""
    return (since.astimezone(clock.get_timezone()) - datetime.timedelta(days=1)).isoformat(timespec="seconds")


def _notification_rows(user: User, since: datetime.datetime, email: bool) -> List[sqlite3.Row]:
    marks = ", ".join("?" * len(EMAIL_APPS))
    where = f"package IN ({marks})" if email else f"package NOT IN ({marks})"
    with get_db_connection() as conn:
        return conn.execute(
            f"SELECT id, package, app_name, title, text, sender, conversation, is_group, category, priority, posted_at "
            f"FROM notifications WHERE user_id = ? AND posted_at >= ? AND {where} ORDER BY posted_at",
            (user.id, _from(since), *EMAIL_APPS)).fetchall()


def _history(user: User, since: datetime.datetime, email: bool) -> List[PastEvent]:
    out: List[PastEvent] = []
    for row in _notification_rows(user, since, email):
        when = _parse(row["posted_at"])
        if when is not None and when >= since:
            out.append((when, email_data(dict(row)) if email else notification_data(dict(row))))
    out.sort(key=lambda item: item[0])
    return out


def notification_history(user: User, since: datetime.datetime) -> List[PastEvent]:
    """``user``'s notifications (not email apps') since ``since``, oldest first."""
    return _history(user, since, email=False)


def email_history(user: User, since: datetime.datetime) -> List[PastEvent]:
    """``user``'s email apps' notifications since ``since``, oldest first."""
    return _history(user, since, email=True)


# --- Calls ------------------------------------------------------------------------------


def call_data(row: Mapping[str, Any]) -> Dict[str, Any]:
    """``phone.call.ended``'s data of a ``phone_calls`` row."""
    caller, receiver = (row.get("caller") or "").strip(), (row.get("receiver") or "").strip()
    direction = row.get("direction") or ""
    if direction not in ("incoming", "outgoing"):
        direction = "outgoing" if caller == "Me" else "incoming"
    person = receiver if caller == "Me" else (caller or "Unknown")
    return {"call_id": row.get("id") or "", "person": person, "direction": direction,
            "duration_s": int(row.get("duration_seconds") or 0), "title": row.get("title") or "",
            "summary": row.get("summary") or ""}


def announce_call(user: User, call_id: str) -> bool:
    """Emit ``phone.call.ended`` for ``user``'s call ``call_id``; False when it is not theirs."""
    with get_db_connection() as conn:
        row = conn.execute("SELECT * FROM phone_calls WHERE id = ? AND user_id = ?", (call_id, user.id)).fetchone()
    if not row:
        return False
    events.emit("phone.call.ended", user, **call_data(dict(row)))
    return True


def call_history(user: User, since: datetime.datetime) -> List[PastEvent]:
    """``user``'s analysed calls since ``since``, oldest first."""
    with get_db_connection() as conn:
        rows = conn.execute("SELECT * FROM phone_calls WHERE user_id = ? AND status = 'ready' AND analysis IS NOT NULL "
                            "AND call_timestamp >= ?", (user.id, _from(since)[:10])).fetchall()
    out: List[PastEvent] = []
    for row in rows:
        when = _parse(row["call_timestamp"])
        if when is not None and when >= since:
            out.append((when, call_data(dict(row))))
    out.sort(key=lambda item: item[0])
    return out


PHONE_EVENTS = (
    EventType("phone.notification.received",
              "A new notification on the user's phone (chats, SMS, banks, apps; not email apps).",
              {"notification_id": "the Inbox notification's id",
               "app": "the app's name (\"WhatsApp\")",
               "sender": "who sent it, for chats (else empty)",
               "conversation": "the chat or group it is in (else empty)",
               "is_group": "true for a group chat",
               "title": "the notification's title",
               "text": "its text",
               "category": "the Inbox's category once sorted; empty when emitted (sorting runs after)",
               "priority": "0 (noise) to 4 (urgent) once sorted; null when emitted",
               "amount": "the first amount in shekels in the title or text (ILS, positive), else null"},
              history=notification_history),
    EventType("phone.email.received", "A new email, from the Gmail or Outlook app's notification on the phone "
                                         "(no recipients; \"from\" is \"me\" for mail to oneself): prefer "
                                         "gmail.email.received when the user has the gmail add-on.",
              {"notification_id": "the Inbox notification's id",
               "account": "Gmail | Outlook",
               "from": "the sender's name or address",
               "subject": "the subject",
               "text": "the start of the body the notification shows (may be empty)",
               "amount": "the first amount in shekels in the subject or text (ILS, positive), else null"},
              history=email_history),
    EventType("phone.call.ended", "A recorded phone call was transcribed and analysed.",
              {"call_id": "the call's id",
               "person": "the other side (a name or number)",
               "direction": "incoming | outgoing",
               "duration_s": "length in seconds",
               "title": "a short title of what it was about",
               "summary": "a summary of the call"},
              history=call_history),
)
