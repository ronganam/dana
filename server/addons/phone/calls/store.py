"""A user's calls (table ``phone_calls``): reads, writes and the API shape.

Every call belongs to the user who uploaded it (``user_id``). Reads and
writes here take that user; another user's call id answers like an id that
does not exist (None / False). :func:`owner_of` is the one unscoped lookup:
the pipeline uses it to know whom to process a call as.
"""
from __future__ import annotations

import datetime
import logging
import os
import sqlite3
from typing import Any, Dict, List, Optional, Tuple

from core import clock, paths, users
from core.db import get_db_connection
from core.users import User
from addons.phone.calls.labels import label_details
from addons.phone.calls.values import loads
from addons.tasks import links as task_links

logger = logging.getLogger(__name__)

RECORDINGS_DIR = paths.RECORDINGS_DIR


def owned_sql(user: Optional[User], alias: str = "") -> Tuple[str, List[Any]]:
    """A WHERE condition (and its parameters) for ``user``'s own calls (or
    suggestions; ``alias``: the table's alias, "" for none); "0" without a user."""
    if user is None:
        return "0", []
    return f"{alias + '.' if alias else ''}user_id = ?", [user.id]


def contact_of(row) -> str:
    caller = (row["caller"] or "").strip()
    receiver = (row["receiver"] or "").strip()
    return receiver if caller == "Me" else (caller or "Unknown")


def parse_call_time(value: str) -> Optional[datetime.datetime]:
    if not value:
        return None
    try:
        dt = datetime.datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        try:
            dt = datetime.datetime.fromisoformat(value.strip()[:19])
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=clock.get_timezone())
    return dt


def audio_path(row) -> str:
    return os.path.join(str(RECORDINGS_DIR), row["filename"])


def audio_available(row) -> bool:
    try:
        return bool(row["filename"]) and os.path.exists(audio_path(row))
    except Exception:
        return False


def call_dict(row) -> Dict[str, Any]:
    """A call row as the analysis / suggestion code reads it (with ``contact`` and ``when``)."""
    d = dict(row)
    d["contact"] = contact_of(row)
    d["when"] = parse_call_time(d.get("call_timestamp") or "")
    return d


# --- Reads -------------------------------------------------------------------------------

def get_row(user: Optional[User], call_id: Optional[str]) -> Optional[sqlite3.Row]:
    """``user``'s call ``call_id`` (None: no such call, or not theirs)."""
    where, args = owned_sql(user)
    with get_db_connection() as conn:
        return conn.execute(f"SELECT * FROM phone_calls WHERE id = ? AND {where}", (call_id, *args)).fetchone()


def owner_of(call_id: str) -> Optional[User]:
    """The user a call belongs to (None: no such call, or its user is gone)."""
    with get_db_connection() as conn:
        row = conn.execute("SELECT user_id FROM phone_calls WHERE id = ?", (call_id,)).fetchone()
    return users.get(row["user_id"]) if row else None


def list_rows(user: Optional[User]) -> List[sqlite3.Row]:
    """``user``'s calls, newest first."""
    where, args = owned_sql(user)
    with get_db_connection() as conn:
        return conn.execute(f"SELECT * FROM phone_calls WHERE {where} ORDER BY call_timestamp DESC", args).fetchall()


def find_duplicate(user: User, sha: str, source_filename: str) -> Optional[Dict[str, Any]]:
    """``user``'s call with the same audio bytes or the same original file name."""
    with get_db_connection() as conn:
        row = conn.execute(
            "SELECT * FROM phone_calls WHERE user_id = ? AND (audio_sha256 = ? OR (source_filename = ? AND ? != '')) "
            "ORDER BY created_at LIMIT 1",
            (user.id, sha, source_filename, source_filename),
        ).fetchone()
    return dict(row) if row else None


def pending_suggestion_counts(user: Optional[User]) -> Dict[str, int]:
    """call id → how many pending suggestions ``user``'s call has."""
    where, args = owned_sql(user)
    with get_db_connection() as conn:
        return {r["call_id"]: r["n"] for r in conn.execute(
            "SELECT call_id, COUNT(*) AS n FROM call_suggestions WHERE status = 'pending' AND source_type = 'call' "
            f"AND {where} GROUP BY call_id", args)}


# --- Writes ------------------------------------------------------------------------------

def update_fields(user: User, call_id: str, fields: Dict[str, Any]) -> bool:
    """Set ``fields`` (column → value) on ``user``'s call. False: not their call."""
    sets = ", ".join(f"{k} = ?" for k in fields)
    with get_db_connection() as conn:
        cur = conn.execute(f"UPDATE phone_calls SET {sets} WHERE id = ? AND user_id = ?",
                           [*fields.values(), call_id, user.id])
        conn.commit()
    return cur.rowcount > 0


def set_status(call_id: str, status: str, error: Optional[str] = None, **fields: Any) -> None:
    """The pipeline's progress on a call it is processing (for its owner)."""
    sets = ["status = ?", "error = ?"] + [f"{k} = ?" for k in fields]
    with get_db_connection() as conn:
        conn.execute(f"UPDATE phone_calls SET {', '.join(sets)} WHERE id = ?",
                     [status, error, *fields.values(), call_id])
        conn.commit()


def delete_call(user: User, call_id: str) -> bool:
    """Delete ``user``'s call: the row (and its FTS entry), its suggestions,
    its task links and the recording. False: not their call."""
    with get_db_connection() as conn:
        row = conn.execute("SELECT filename FROM phone_calls WHERE id = ? AND user_id = ?",
                           (call_id, user.id)).fetchone()
        if not row:
            return False
        filename = row["filename"]
        conn.execute("DELETE FROM phone_calls WHERE id = ?", (call_id,))
        conn.execute("DELETE FROM call_suggestions WHERE call_id = ? AND source_type = 'call'", (call_id,))
        task_links.forget_ref("call", call_id, conn=conn)
        conn.commit()
    file_path = os.path.join(str(RECORDINGS_DIR), filename)
    if os.path.exists(file_path):
        try:
            os.remove(file_path)
        except Exception as fe:
            logger.error(f"Failed to delete audio file {file_path}: {fe}")
    return True


# --- API shape ---------------------------------------------------------------------------

def call_to_api(user: Optional[User], row, *, include_transcript: bool = True,
                pending_counts: Optional[Dict[str, int]] = None) -> Dict[str, Any]:
    d = dict(row)
    labels = loads(d.get("labels"), [])
    analysis = loads(d.get("analysis"), None)
    out = {
        "id": d["id"],
        "filename": d.get("filename"),
        "caller": d.get("caller"),
        "receiver": d.get("receiver"),
        "contact": contact_of(row),
        "call_timestamp": d.get("call_timestamp"),
        "created_at": d.get("created_at"),
        "title": d.get("title"),
        "summary": d.get("summary"),
        "duration_seconds": d.get("duration_seconds") or 0,
        "status": d.get("status") or "ready",
        "error": d.get("error"),
        "labels": labels,
        "label_details": label_details(user, labels),
        "importance": d.get("importance") or "normal",
        "direction": d.get("direction"),
        "phone_number": d.get("phone_number"),
        "project_id": d.get("project_id"),
        "audio_available": audio_available(row),
        "audio_deleted_at": d.get("audio_deleted_at"),
        "participants": (analysis or {}).get("participants") or [],
        "key_facts": (analysis or {}).get("key_facts") or [],
        "action_items": (analysis or {}).get("action_items") or [],
        "security_alert": (analysis or {}).get("security_alert"),
        "pending_suggestions": (pending_counts or {}).get(d["id"], 0),
    }
    if include_transcript:
        out["transcript"] = d.get("transcript")
    return out


def call_payload(user: User, call_id: str, include_transcript: bool = True) -> Dict[str, Any]:
    """``user``'s call as the API returns it (``{id, status: "missing"}`` when it is not theirs)."""
    row = get_row(user, call_id)
    if not row:
        return {"id": call_id, "status": "missing"}
    return call_to_api(user, row, include_transcript=include_transcript,
                       pending_counts=pending_suggestion_counts(user))


def list_calls(user: User, label: str = "", include_duplicates: bool = False,
               include_transcripts: bool = True) -> List[Dict[str, Any]]:
    """``GET /api/calls``: ``user``'s calls, newest first (duplicates left out unless asked)."""
    counts = pending_suggestion_counts(user)
    calls = []
    for row in list_rows(user):
        if row["status"] == "duplicate" and not include_duplicates:
            continue
        c = call_to_api(user, row, include_transcript=include_transcripts, pending_counts=counts)
        if label and label not in c["labels"]:
            continue
        calls.append(c)
    return calls
