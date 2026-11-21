"""Audio retention: recordings older than ``CALL_AUDIO_RETENTION_DAYS`` are
deleted (transcript, analysis and links kept; ``/audio`` then answers 410),
every 6 h, for every user with ``phone``, each as that user."""
from __future__ import annotations

import asyncio
import datetime
import logging
import os

from core import clock
from core.db import get_db_connection
from core.users import User
from addons.phone import config as phone_config
from addons.phone.calls.store import RECORDINGS_DIR
from addons.phone.calls.values import AUDIO_RETENTION_DAYS

logger = logging.getLogger(__name__)


def purge_old_audio(user: User, days: int = AUDIO_RETENTION_DAYS) -> int:
    """Delete the audio files of ``user``'s calls uploaded more than ``days``
    ago (keeps the transcript, analysis and links)."""
    if days <= 0:
        return 0
    cutoff = (datetime.datetime.utcnow() - datetime.timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    removed = 0
    with get_db_connection() as conn:
        rows = conn.execute(
            "SELECT id, filename FROM phone_calls WHERE audio_deleted_at IS NULL AND created_at < ? AND user_id = ?",
            (cutoff, user.id)).fetchall()
        for r in rows:
            p = os.path.join(str(RECORDINGS_DIR), r["filename"] or "")
            try:
                if r["filename"] and os.path.exists(p):
                    os.remove(p)
                    removed += 1
            except OSError as e:
                logger.warning(f"Could not delete {p}: {e}")
                continue
            conn.execute("UPDATE phone_calls SET audio_deleted_at = ? WHERE id = ?",
                         (clock.now().isoformat(timespec="seconds"), r["id"]))
        conn.commit()
    if removed:
        logger.info(f"Audio retention: deleted {removed} recording(s) of user {user.id} older than {days} days")
    return removed


async def purge_all() -> dict:
    """:func:`purge_old_audio` for every user with ``phone`` (a failing user does not stop the next). Returns user id → how many."""
    async def job(user: User) -> int:
        return await asyncio.to_thread(purge_old_audio, user)
    return await phone_config.for_each_user("Audio retention", job)


async def retention_loop(interval_seconds: int = 6 * 3600) -> None:
    while True:
        try:
            await purge_all()
        except Exception as e:
            logger.error(f"Audio retention failed: {e}", exc_info=True)
        await asyncio.sleep(interval_seconds)
