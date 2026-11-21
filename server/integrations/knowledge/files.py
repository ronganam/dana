"""Files kept on an entry: a quote PDF, a voice note, a video, a photo
(docs/knowledge.md, "Files").

Stored under ``paths.KNOWLEDGE_FILES_DIR`` with a generated name (table
``entry_files``); served by ``GET /api/entries/{id}/files/{file_id}`` only to a
user who can see the entry. Adding or removing one is a change of the entry
(a ``read`` share is refused). An audio file is transcribed in the background
as the entry's owner (``transcript``), so Dana can read what it says.
"""

import asyncio
import logging
import mimetypes
import uuid
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set

from core import llm, paths, users
from core.db import get_db_connection
from core.users import User
from integrations.knowledge import entries
from integrations.knowledge.values import clean_str, serialize_file

logger = logging.getLogger(__name__)

MAX_BYTES = 60 * 1024 * 1024
# Audio up to this size is transcribed (llm.transcribe sends it inline).
MAX_TRANSCRIBE_BYTES = 14 * 1024 * 1024
SOURCES = ("upload", "whatsapp")

_tasks: Set["asyncio.Task[None]"] = set()


class TooLarge(ValueError):
    def __init__(self) -> None:
        super().__init__(f"The file is larger than {MAX_BYTES // (1024 * 1024)} MB.")


def _dir() -> Path:
    paths.KNOWLEDGE_FILES_DIR.mkdir(parents=True, exist_ok=True)
    return paths.KNOWLEDGE_FILES_DIR


def _clean_name(name: str, mime: str) -> str:
    name = clean_str(name).replace("/", "_").replace("\\", "_").strip(". ")[:160]
    if not name:
        name = "file" + (mimetypes.guess_extension(mime) or "")
    return name


def files_of(entry_ids: Iterable[str]) -> Dict[str, List[Dict[str, Any]]]:
    """Entry id → its files (API shape, oldest first). The caller has checked
    that the user can see these entries."""
    ids = [i for i in entry_ids if i]
    if not ids:
        return {}
    out: Dict[str, List[Dict[str, Any]]] = {}
    with get_db_connection() as conn:
        rows = conn.execute(f"SELECT * FROM entry_files WHERE entry_id IN ({','.join('?' * len(ids))}) "
                            "ORDER BY created_at, rowid", ids).fetchall()
    for row in rows:
        out.setdefault(row["entry_id"], []).append(serialize_file(row))
    return out


def add_file(user: User, entry_id: str, name: str, mime: str, data: bytes, *, source: str = "upload",
             note: str = "", transcript: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Keep ``data`` on an entry ``user`` may change; returns the file (API
    shape), or None when they cannot see the entry. Audio without a
    ``transcript`` is transcribed in the background.

    Raises:
        projects.ReadOnly: its project is shared with them read-only.
        TooLarge: over :data:`MAX_BYTES`.
    """
    try:
        row = entries.writable_entry(user, entry_id)
    except LookupError:
        return None
    if len(data) > MAX_BYTES:
        raise TooLarge()
    mime = clean_str(mime).split(";")[0].strip().lower() or mimetypes.guess_type(name)[0] or "application/octet-stream"
    name = _clean_name(name, mime)
    file_id = uuid.uuid4().hex
    suffix = Path(name).suffix.lower()[:10] or (mimetypes.guess_extension(mime) or "")
    stored = f"{file_id}{suffix}"
    (_dir() / stored).write_bytes(data)
    with get_db_connection() as conn:
        conn.execute(
            "INSERT INTO entry_files (id, entry_id, name, mime, size, stored, note, transcript, source, created_by) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (file_id, entry_id, name, mime, len(data), stored, clean_str(note), transcript or None,
             source if source in SOURCES else "upload", user.id),
        )
        conn.execute("UPDATE entries SET updated_at = CURRENT_TIMESTAMP WHERE id = ?", (entry_id,))
        conn.commit()
        saved = conn.execute("SELECT * FROM entry_files WHERE id = ?", (file_id,)).fetchone()
    if mime.startswith("audio/") and not transcript and len(data) <= MAX_TRANSCRIBE_BYTES:
        _start_transcript(file_id, data, mime, row["user_id"])
    return serialize_file(saved)


def _start_transcript(file_id: str, data: bytes, mime: str, owner_id: str) -> None:
    try:
        task = asyncio.get_running_loop().create_task(_transcribe(file_id, data, mime, owner_id))
    except RuntimeError:
        return  # no loop (a script): the file stays without a transcript
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


async def _transcribe(file_id: str, data: bytes, mime: str, owner_id: str) -> None:
    owner = users.get(owner_id)
    if owner is None:
        return
    try:
        with users.acting_as(owner):
            text = await llm.transcribe(data, mime, timeout=180.0, purpose="knowledge_file", ref=file_id)
    except Exception as e:
        logger.warning(f"Transcribing knowledge file {file_id} failed: {e}")
        return
    if text:
        with get_db_connection() as conn:
            conn.execute("UPDATE entry_files SET transcript = ? WHERE id = ?", (text, file_id))
            conn.commit()


def file_row(user: Optional[User], entry_id: str, file_id: str) -> Optional[Dict[str, Any]]:
    """The file row when ``user`` can see the entry it is on."""
    if entries.entry_row(user, entry_id) is None:
        return None
    with get_db_connection() as conn:
        row = conn.execute("SELECT * FROM entry_files WHERE id = ? AND entry_id = ?", (file_id, entry_id)).fetchone()
    return dict(row) if row else None


def file_path(user: Optional[User], entry_id: str, file_id: str) -> Optional[Path]:
    """Where the file is on disk, when ``user`` can see its entry (else None: 404)."""
    row = file_row(user, entry_id, file_id)
    if row is None:
        return None
    path = (_dir() / row["stored"]).resolve()
    if path.parent != _dir().resolve() or not path.is_file():
        return None
    return path


def update_file(user: User, entry_id: str, file_id: str, *, name: Optional[str] = None,
                note: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Rename a file or change its note, on an entry ``user`` may change.

    Raises:
        projects.ReadOnly: its project is shared with them read-only.
    """
    try:
        entries.writable_entry(user, entry_id)
    except LookupError:
        return None
    row = file_row(user, entry_id, file_id)
    if row is None:
        return None
    with get_db_connection() as conn:
        if name is not None and clean_str(name):
            conn.execute("UPDATE entry_files SET name = ? WHERE id = ?", (_clean_name(name, row["mime"]), file_id))
        if note is not None:
            conn.execute("UPDATE entry_files SET note = ? WHERE id = ?", (clean_str(note), file_id))
        conn.commit()
        saved = conn.execute("SELECT * FROM entry_files WHERE id = ?", (file_id,)).fetchone()
    return serialize_file(saved)


def delete_file(user: User, entry_id: str, file_id: str) -> bool:
    """Remove a file from an entry ``user`` may change (False: not found).

    Raises:
        projects.ReadOnly: its project is shared with them read-only.
    """
    try:
        entries.writable_entry(user, entry_id)
    except LookupError:
        return False
    row = file_row(user, entry_id, file_id)
    if row is None:
        return False
    with get_db_connection() as conn:
        conn.execute("DELETE FROM entry_files WHERE id = ?", (file_id,))
        conn.commit()
    _unlink([row["stored"]])
    return True


def forget_entries(conn: Any, entry_ids: List[str]) -> None:
    """Remove the files of entries about to be deleted (rows and disk), inside the caller's transaction."""
    if not entry_ids:
        return
    marks = ",".join("?" * len(entry_ids))
    stored = [r["stored"] for r in conn.execute(f"SELECT stored FROM entry_files WHERE entry_id IN ({marks})",
                                                entry_ids).fetchall()]
    conn.execute(f"DELETE FROM entry_files WHERE entry_id IN ({marks})", entry_ids)
    _unlink(stored)


def _unlink(stored: Iterable[str]) -> None:
    for name in stored:
        try:
            (_dir() / name).unlink(missing_ok=True)
        except OSError as e:
            logger.warning(f"Could not remove knowledge file {name}: {e}")
