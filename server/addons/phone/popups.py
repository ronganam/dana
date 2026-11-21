"""The phone's full-screen suggestion review: popped when a call or a
notification just produced suggestions (direct push, ``suggestions`` event).

Calls pop it up; notification suggestions are accepted ~4% of the time, so by
default they wait quietly in Now instead (``SUGGESTION_POPUP_NOTIFICATIONS``).
Only sources that just happened pop it (``SUGGESTION_POPUP_MAX_AGE_HOURS``).
"""
from __future__ import annotations

import logging
import os

from core.db import get_db_connection
from core.users import User
from addons.phone import push
from addons.phone.suggestions import visible_sql

logger = logging.getLogger(__name__)

SUGGESTION_POPUP = os.getenv("SUGGESTION_POPUP", "1").strip().lower() not in ("0", "false", "no", "off")
SUGGESTION_POPUP_NOTIFICATIONS = os.getenv("SUGGESTION_POPUP_NOTIFICATIONS", "0").strip().lower() \
    in ("1", "true", "yes", "on")
SUGGESTION_POPUP_MAX_AGE_HOURS = float(os.getenv("SUGGESTION_POPUP_MAX_AGE_HOURS", "6"))


async def announce_suggestions(user: User, source_type: str, source_id: str, heading: str) -> bool:
    """Pop ``user``'s phone's suggestion review screen for the pending
    suggestions that just came from this call / notification of theirs (once
    per source). Notification-sourced ones only with
    ``SUGGESTION_POPUP_NOTIFICATIONS=1``."""
    if not SUGGESTION_POPUP or not source_id:
        return False
    if source_type == "notification" and not SUGGESTION_POPUP_NOTIFICATIONS:
        return False
    vis, vis_args = visible_sql(user)
    with get_db_connection() as conn:
        ids = [r["id"] for r in conn.execute(
            "SELECT s.id FROM call_suggestions s "
            f"WHERE s.call_id = ? AND COALESCE(s.source_type, 'call') = ? AND s.status = 'pending' AND {vis} "
            "ORDER BY s.created_at", (source_id, source_type, *vis_args)).fetchall()]
    if not ids:
        return False
    ok = await push.send(user, "suggestions", {"ids": ids, "source_type": source_type, "source_id": source_id,
                                               "heading": heading[:120]})
    logger.info(f"Suggestion pop-up for {source_type} {source_id}: {len(ids)} suggestion(s), "
                f"{'sent' if ok else 'not delivered'}")
    return ok
