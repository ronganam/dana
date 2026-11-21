"""Retention: each user's notifications older than ``NOTIFICATION_RETENTION_DAYS``
(30) are deleted every 6 h, except ones a task was created from (and the
accepted / dismissed suggestions, which only pending ones are not)."""
from __future__ import annotations

import asyncio
import datetime
import logging

from core import clock
from core.db import get_db_connection
from core.users import User
from integrations.phone.config import for_each_user
from integrations.phone.notifications.store import delete_ids
from integrations.phone.notifications.values import RETENTION_DAYS

logger = logging.getLogger(__name__)


def purge_old(user: User, days: int = RETENTION_DAYS) -> int:
    """Delete ``user``'s notifications older than ``days``. Returns how many."""
    if days <= 0:
        return 0
    cutoff = (clock.now() - datetime.timedelta(days=days)).isoformat(timespec="seconds")
    with get_db_connection() as conn:
        ids = [r["id"] for r in conn.execute(
            "SELECT id FROM notifications WHERE user_id = ? AND posted_at < ? AND id NOT IN "
            "(SELECT ref_id FROM task_links WHERE ref_type = 'notification')", (user.id, cutoff)).fetchall()]
        delete_ids(conn, ids)
        conn.commit()
    if ids:
        logger.info(f"Notification retention: deleted {len(ids)} of user {user.id} older than {days} days")
    return len(ids)


async def purge_all() -> dict:
    """:func:`purge_old` for every user with ``phone``. Returns user id → how many."""
    async def one(user: User) -> int:
        return await asyncio.to_thread(purge_old, user)
    return await for_each_user("Notification retention", one)


async def retention_loop(interval_seconds: int = 6 * 3600) -> None:
    while True:
        try:
            await purge_all()
        except Exception as e:
            logger.error(f"Notification retention failed: {e}", exc_info=True)
        await asyncio.sleep(interval_seconds)
