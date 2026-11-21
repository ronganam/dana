"""The calendar addon's composer hooks: the ``calendar`` self-check."""

import datetime

from core.addons import Check, helpers
from core.db import get_db_connection
from core.users import User
from addons.calendar import today as calendar_today
from addons.calendar.addon import addon


@addon.selfcheck("calendar", "Calendar")
def check_calendar(user: User, now: datetime.datetime) -> Check:
    with get_db_connection() as conn:
        m = dict(conn.execute("SELECT * FROM system_marks WHERE key = ?",
                              (calendar_today.mark_key(user.id),)).fetchone() or {})
    ok_at, fail_at = helpers.parse_time(m.get("last_ok_at")), helpers.parse_time(m.get("last_fail_at"))
    if not ok_at and not fail_at:
        return Check(True, "not checked yet")
    if m.get("ok") == 0:
        return Check(False, f"failing since {helpers.ago(fail_at, now) if fail_at else 'never'}", ok_at,
                     title="The calendar can't be read",
                     why="Events are missing from Now and the briefing.",
                     blocks=[{"type": "text", "label": "Error", "text": m.get("detail") or "Unknown error"}])
    return Check(True, f"read {helpers.ago(ok_at, now)}", ok_at)
