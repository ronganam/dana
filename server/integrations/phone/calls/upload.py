"""A call recording uploaded from the phone (``POST /api/calls/upload``): it
belongs to the request's user (their API token)."""
from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import re
import uuid
from typing import Any, Dict, Optional, Tuple

from core import clock
from core.db import get_db_connection
from core.users import User
from integrations.phone.calls import store as calls_store
from integrations.phone.calls.processing import schedule_processing

logger = logging.getLogger(__name__)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _write_bytes(path: str, data: bytes) -> None:
    with open(path, "wb") as f:
        f.write(data)


async def receive(user: User, audio: bytes, filename: str, mime_type: str,
                  fields: Dict[str, str]) -> Tuple[str, bool]:
    """Store ``user``'s recording and queue it for processing. Returns
    ``(call id, duplicate)``: re-uploading the same recording (same bytes or
    same original file name, among the user's own calls) returns their existing
    call, with the call-log details it lacked filled in.

    ``fields``: caller, receiver, call_timestamp, duration_seconds, direction
    (incoming|outgoing|missed), phone_number, instructions.
    """
    caller = fields.get("caller") or "Unknown"
    receiver = fields.get("receiver") or "Me"
    call_timestamp = fields.get("call_timestamp") or clock.now().isoformat(timespec="seconds")
    try:
        duration_seconds = int(float(fields.get("duration_seconds") or 0))
    except ValueError:
        duration_seconds = 0
    direction: Optional[str] = (fields.get("direction") or "").lower() or None
    if direction not in (None, "incoming", "outgoing", "missed"):
        direction = None
    phone_number = fields.get("phone_number") or None
    instructions = fields.get("instructions") or ""
    source_filename = os.path.basename(filename)

    sha = sha256_bytes(audio)
    existing = calls_store.find_duplicate(user, sha, source_filename)
    if existing:
        logger.info(f"Call upload '{source_filename}' is a duplicate of {existing['id']}")
        # Fill in call-log details the first upload didn't have.
        updates: Dict[str, Any] = {k: v for k, v in (("direction", direction), ("phone_number", phone_number),
                                                     ("duration_seconds", duration_seconds or None))
                                   if v and not existing.get(k)}
        if updates:
            calls_store.update_fields(user, existing["id"], updates)
        return existing["id"], True

    call_id = str(uuid.uuid4())
    safe_name = re.sub(r"[\\/\x00]", "_", source_filename)
    stored_filename = f"{call_id}_{safe_name}"
    file_path = os.path.join(str(calls_store.RECORDINGS_DIR), stored_filename)
    await asyncio.to_thread(_write_bytes, file_path, audio)

    contact = receiver if caller == "Me" else caller
    with get_db_connection() as conn:
        conn.execute(
            """
            INSERT INTO phone_calls (id, user_id, filename, caller, receiver, call_timestamp, transcript, summary,
                                     title, duration_seconds, status, audio_sha256, source_filename, direction,
                                     phone_number, mime_type, instructions)
            VALUES (?, ?, ?, ?, ?, ?, NULL, NULL, ?, ?, 'processing', ?, ?, ?, ?, ?, ?)
            """,
            (call_id, user.id, stored_filename, caller, receiver, call_timestamp, f"Call with {contact}",
             duration_seconds, sha, source_filename, direction, phone_number, mime_type, instructions),
        )
        conn.commit()

    schedule_processing(call_id)
    return call_id, False
