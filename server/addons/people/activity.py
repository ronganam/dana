"""People's activity: every read and write of calls and notifications the addon does.

Activity is linked, not copied: ``phone_calls.person_id`` and
``notifications.person_id`` (NULL = not checked yet, '' = checked, no match).
A person id belongs to one user, and only that user's calls and
notifications are ever linked to it.

Whose calls and notifications are whose is decided in exactly two places,
:func:`calls_sql` and :func:`notifications_sql`; every query here goes through
them. Both are filtered by the row's ``user_id``, so nothing of anyone else's
is ever linked into a user's people, briefs or call card.
"""
from __future__ import annotations

import datetime
import re
import sqlite3
from typing import Any, Dict, List, Optional, Tuple

from core import clock
from core.users import User
from addons.people.values import loads, parse_time


# --- The two seams -------------------------------------------------------------------

def calls_sql(user: Optional[User], alias: str = "") -> Tuple[str, List[Any]]:
    """A WHERE condition (and its parameters) for the calls that belong to
    ``user`` (``alias``: the ``phone_calls`` table's alias, "" for none); "0"
    without a user."""
    if user is None:
        return "0", []
    return f"{alias + '.' if alias else ''}user_id = ?", [user.id]


def notifications_sql(user: Optional[User], alias: str = "") -> Tuple[str, List[Any]]:
    """A WHERE condition (and its parameters) for the notifications that belong
    to ``user`` (``alias``: the ``notifications`` table's alias, "" for none);
    "0" without a user."""
    if user is None:
        return "0", []
    return f"{alias + '.' if alias else ''}user_id = ?", [user.id]


# --- Linking ---------------------------------------------------------------------------

def unchecked_calls(conn: sqlite3.Connection, user: User) -> List[sqlite3.Row]:
    """``user``'s calls not matched to anyone yet, oldest first."""
    where, args = calls_sql(user)
    return conn.execute(
        "SELECT id, caller, receiver, phone_number FROM phone_calls WHERE person_id IS NULL "
        f"AND {where} ORDER BY call_timestamp", args).fetchall()


def unchecked_notifications(conn: sqlite3.Connection, user: User) -> List[sqlite3.Row]:
    """``user``'s notifications not matched to anyone yet."""
    where, args = notifications_sql(user)
    return conn.execute(
        f"SELECT id, sender, conversation, is_group FROM notifications WHERE person_id IS NULL AND {where}",
        args).fetchall()


def set_call_person(conn: sqlite3.Connection, call_id: str, person_id: str) -> None:
    """A call from :func:`unchecked_calls` checked: ``person_id`` or '' (no match)."""
    # scoped: a call from unchecked_calls (the user's)
    conn.execute("UPDATE phone_calls SET person_id = ? WHERE id = ?", (person_id, call_id))


def set_notification_person(conn: sqlite3.Connection, notification_id: str, person_id: str) -> None:
    """A notification from :func:`unchecked_notifications` checked: ``person_id`` or '' (no match)."""
    # scoped: a notification from unchecked_notifications (the user's)
    conn.execute("UPDATE notifications SET person_id = ? WHERE id = ?", (person_id, notification_id))


def recheck_unmatched(conn: sqlite3.Connection, user: User) -> None:
    """``user``'s names or numbers changed: look at their unmatched activity again."""
    where, args = calls_sql(user)
    conn.execute(f"UPDATE phone_calls SET person_id = NULL WHERE person_id = '' AND {where}", args)
    where, args = notifications_sql(user)
    conn.execute(f"UPDATE notifications SET person_id = NULL WHERE person_id = '' AND {where}", args)


def recheck_calls_of(conn: sqlite3.Connection, person_id: str) -> None:
    """A number of theirs was removed: their calls are matched again."""
    # scoped: a person id is one user's; the caller resolved it with store.row(user)
    conn.execute("UPDATE phone_calls SET person_id = NULL WHERE person_id = ?", (person_id,))


def recheck_notifications_of(conn: sqlite3.Connection, person_id: str) -> None:
    """Their names changed: anything linked by name is matched again."""
    # scoped: a person id is one user's; the caller resolved it with store.row(user)
    conn.execute("UPDATE notifications SET person_id = NULL WHERE person_id = ?", (person_id,))


def unlink_person(conn: sqlite3.Connection, person_id: str) -> None:
    """They were deleted: leave their activity unmatched ('') so a call from them doesn't recreate them."""
    # scoped: a person id is one user's; the caller resolved it with store.row(user)
    conn.execute("UPDATE phone_calls SET person_id = '' WHERE person_id = ?", (person_id,))
    # scoped: a person id is one user's; the caller resolved it with store.row(user)
    conn.execute("UPDATE notifications SET person_id = '' WHERE person_id = ?", (person_id,))


def move_person(conn: sqlite3.Connection, old_id: str, new_id: str) -> None:
    """Merged (both people are the same user's): ``old_id``'s activity becomes ``new_id``'s."""
    # scoped: both person ids are the user's (merge_people checked them)
    conn.execute("UPDATE phone_calls SET person_id = ? WHERE person_id = ?", (new_id, old_id))
    # scoped: both person ids are the user's (merge_people checked them)
    conn.execute("UPDATE notifications SET person_id = ? WHERE person_id = ?", (new_id, old_id))


def person_of_call(conn: sqlite3.Connection, user: User, call_id: str) -> Optional[str]:
    """The person ``user``'s call ``call_id`` is linked to (None: none, or not their call)."""
    where, args = calls_sql(user)
    r = conn.execute(f"SELECT person_id FROM phone_calls WHERE id = ? AND {where}", (call_id, *args)).fetchone()
    return (r["person_id"] or None) if r else None


# --- Per person ----------------------------------------------------------------------

def _person_filter(person_id: Optional[str]) -> Tuple[str, Tuple]:
    if person_id:
        return "person_id = ?", (person_id,)
    return "person_id IS NOT NULL AND person_id != ''", ()


def contact_days(conn: sqlite3.Connection, user: User, person_id: Optional[str] = None,
                 days: int = 365) -> Dict[str, List[datetime.date]]:
    """person id → the days with a call or a message (last ``days`` days), sorted."""
    since = (clock.now() - datetime.timedelta(days=days)).date().isoformat()
    who, args = _person_filter(person_id)
    out: Dict[str, set] = {}
    cw, ca = calls_sql(user)
    for r in conn.execute(f"SELECT person_id, substr(call_timestamp, 1, 10) AS d FROM phone_calls "
                          f"WHERE {who} AND {cw} AND substr(call_timestamp, 1, 10) >= ?", (*args, *ca, since)):
        out.setdefault(r["person_id"], set()).add(r["d"])
    nw, na = notifications_sql(user)
    for r in conn.execute(f"SELECT person_id, substr(posted_at, 1, 10) AS d FROM notifications "
                          f"WHERE {who} AND {nw} AND substr(posted_at, 1, 10) >= ?", (*args, *na, since)):
        out.setdefault(r["person_id"], set()).add(r["d"])
    res: Dict[str, List[datetime.date]] = {}
    for pid, ds in out.items():
        dates = []
        for d in ds:
            try:
                dates.append(datetime.date.fromisoformat(d))
            except (TypeError, ValueError):
                pass
        res[pid] = sorted(dates)
    return res


def last_activity(conn: sqlite3.Connection, user: User, person_id: Optional[str] = None) -> Dict[str, Dict[str, Any]]:
    """person id → {"call": (last time, count), "message": (last time, count)}."""
    who, args = _person_filter(person_id)
    out: Dict[str, Dict[str, Any]] = {}
    cw, ca = calls_sql(user)
    for r in conn.execute(f"SELECT person_id, MAX(call_timestamp) AS t, COUNT(*) AS n FROM phone_calls "
                          f"WHERE {who} AND {cw} GROUP BY person_id", (*args, *ca)):
        out.setdefault(r["person_id"], {})["call"] = (parse_time(r["t"]), r["n"])
    nw, na = notifications_sql(user)
    for r in conn.execute(f"SELECT person_id, MAX(posted_at) AS t, COUNT(*) AS n FROM notifications "
                          f"WHERE {who} AND {nw} GROUP BY person_id", (*args, *na)):
        out.setdefault(r["person_id"], {})["message"] = (parse_time(r["t"]), r["n"])
    return out


def call_ids(conn: sqlite3.Connection, user: User, person_id: str) -> List[str]:
    where, args = calls_sql(user)
    return [r["id"] for r in conn.execute(f"SELECT id FROM phone_calls WHERE person_id = ? AND {where}",
                                          (person_id, *args))]


def calls(conn: sqlite3.Connection, user: User, person_id: str, limit: int = 40) -> List[Dict[str, Any]]:
    """Their calls, newest first, as timeline entries."""
    where, args = calls_sql(user)
    out = []
    for r in conn.execute(
            "SELECT id, title, summary, call_timestamp, direction, duration_seconds, status, labels, importance "
            f"FROM phone_calls WHERE person_id = ? AND {where} ORDER BY call_timestamp DESC LIMIT ?",
            (person_id, *args, limit)):
        out.append({"type": "call", "id": r["id"], "at": (parse_time(r["call_timestamp"]) or clock.now()).isoformat(),
                    "title": r["title"], "summary": r["summary"] or "", "direction": r["direction"],
                    "duration_seconds": r["duration_seconds"] or 0, "status": r["status"],
                    "labels": loads(r["labels"], []), "importance": r["importance"]})
    return out


def last_ready_call(conn: sqlite3.Connection, user: User, person_id: str) -> Optional[sqlite3.Row]:
    where, args = calls_sql(user)
    return conn.execute(
        f"SELECT id, title, summary, call_timestamp FROM phone_calls WHERE person_id = ? AND status = 'ready' "
        f"AND {where} ORDER BY call_timestamp DESC LIMIT 1", (person_id, *args)).fetchone()


def recent_call_states(conn: sqlite3.Connection, user: User, person_id: str, limit: int = 8) -> List[sqlite3.Row]:
    """(id, status) of their last ``limit`` calls (what a brief's signature hashes)."""
    where, args = calls_sql(user)
    return conn.execute(f"SELECT id, status FROM phone_calls WHERE person_id = ? AND {where} "
                        "ORDER BY call_timestamp DESC LIMIT ?", (person_id, *args, limit)).fetchall()


def message_stats(conn: sqlite3.Connection, user: User, person_id: str, since: str) -> sqlite3.Row:
    """{n, t}: how many messages from them since ``since`` and the newest one's time."""
    where, args = notifications_sql(user)
    return conn.execute(f"SELECT COUNT(*) AS n, MAX(posted_at) AS t FROM notifications "
                        f"WHERE person_id = ? AND posted_at >= ? AND {where}", (person_id, since, *args)).fetchone()


def recent_messages(conn: sqlite3.Connection, user: User, person_id: str, limit: int = 40) -> List[sqlite3.Row]:
    where, args = notifications_sql(user)
    return conn.execute(
        f"SELECT posted_at, conversation, is_group, text FROM notifications WHERE person_id = ? AND {where} "
        "ORDER BY posted_at DESC LIMIT ?", (person_id, *args, limit)).fetchall()


def message_groups(conn: sqlite3.Connection, user: User, person_id: str, days: int = 30,
                   limit: int = 40) -> List[Dict[str, Any]]:
    """Messages grouped per day and chat: how many, and the latest text."""
    since = (clock.now() - datetime.timedelta(days=days)).isoformat(timespec="seconds")
    where, args = notifications_sql(user)
    groups: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for r in conn.execute(
            "SELECT id, app_name, conversation, is_group, title, text, posted_at FROM notifications "
            f"WHERE person_id = ? AND posted_at >= ? AND {where} ORDER BY posted_at DESC", (person_id, since, *args)):
        day = str(r["posted_at"])[:10]
        chat = r["conversation"] if r["is_group"] else ""
        g = groups.get((day, chat))
        if not g:
            g = groups[(day, chat)] = {"type": "messages", "id": r["id"], "at": r["posted_at"], "app": r["app_name"],
                                       "chat": chat, "count": 0, "texts": []}
        g["count"] += 1
        if len(g["texts"]) < 3 and r["text"]:
            g["texts"].append(re.sub(r"\s+", " ", r["text"])[:200])
    return sorted(groups.values(), key=lambda g: g["at"], reverse=True)[:limit]


def suggestions_for(conn: sqlite3.Connection, user: User, person_id: str) -> List[Dict[str, Any]]:
    """Pending suggestions from their calls and messages, newest first."""
    cw, ca = calls_sql(user, "c")
    nw, na = notifications_sql(user, "n")
    rows = conn.execute(
        "SELECT s.id, s.kind, s.payload, s.created_at FROM call_suggestions s "
        "LEFT JOIN phone_calls c ON (COALESCE(s.source_type, 'call') = 'call' AND c.id = s.call_id) "
        "LEFT JOIN notifications n ON (s.source_type = 'notification' AND n.id = s.call_id) "
        f"WHERE s.status = 'pending' AND ((c.person_id = ? AND {cw}) OR (n.person_id = ? AND {nw})) "
        "ORDER BY s.created_at DESC LIMIT 10",
        (person_id, *ca, person_id, *na)).fetchall()
    return [{"id": r["id"], "kind": r["kind"], "title": loads(r["payload"], {}).get("title") or "",
             "created_at": r["created_at"]} for r in rows]
