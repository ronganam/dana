"""The money addon's background work and composer hooks (docs/money.md):

- at start: end what a previous process left mid-sync, then classify what is
  still missing;
- every minute (after 15 s): start every due source of the user;
- context chats for ``transaction`` and ``saving``;
- self-check: ``money`` (each bank/card source syncs cleanly).
"""

import datetime
from typing import Any, Dict, List, Optional, Tuple

from core.addons import Check, ContextRequest, helpers, minutes
from core.db import get_db_connection
from core.users import User
from addons.money import classify, config, providers, queries, savings, schedule, sync
from addons.money.addon import addon


@addon.job(at_start=True)
async def recover(user: User) -> None:
    sync.recover(user)
    await classify.catch_up_user(user)


@addon.job(every=minutes(schedule.INTERVAL_SECONDS / 60), delay=schedule.STARTUP_DELAY_SECONDS)
async def start_due_syncs(user: User) -> List[str]:
    return schedule.tick_user(user)


@addon.context_card("transaction")
def transaction_card(user: User, ref_id: str) -> Dict[str, Any]:
    return queries.chat_context(user, ref_id)


@addon.context("transaction")
def transaction_context(user: User, ref_id: str, req: ContextRequest) -> Optional[str]:
    return queries.context_prompt(user, ref_id)


@addon.context_card("saving")
def saving_card(user: User, ref_id: str) -> Dict[str, Any]:
    return savings.chat_context(user, ref_id)


@addon.context("saving")
def saving_context(user: User, ref_id: str, req: ContextRequest) -> Optional[str]:
    return savings.context_prompt(user, ref_id)


@addon.selfcheck("money", "Bank and card syncs", after="briefing")
def check_money(user: User, now: datetime.datetime) -> Check:
    with get_db_connection() as conn:
        rows = [dict(r) for r in conn.execute(
            "SELECT company, label, state, state_reason, last_ok_at, created_at FROM money_sources WHERE user_id = ? "
            "ORDER BY created_at, id", (user.id,)).fetchall()]
    if not rows:
        return Check(True, "no bank or card added")
    problems: List[Tuple[str, str]] = []
    for r in rows:
        name = (r.get("label") or "").strip() or providers.name_of(r["company"])
        last_ok = helpers.parse_time(r.get("last_ok_at"))
        if r["state"] == "needs_code":
            problems.append((f"{name} is waiting for a sign-in code",
                             f"{name}: waiting for the SMS sign-in code (enter it on the Money page or in Telegram)"))
        elif r["state"] == "needs_attention":
            problems.append((f"{name} needs its login fixed", f"{name}: {r.get('state_reason') or 'the login failed'}"))
        else:
            since = last_ok or helpers.parse_time(r.get("created_at"))
            if since and (now - since).total_seconds() > config.STALE_HOURS * 3600:
                quiet = helpers.span((now - since).total_seconds())
                why = f" ({r['state_reason']})" if r.get("state_reason") else ""
                problems.append((f"{name} hasn't synced for {quiet}",
                                 f"{name}: no successful sync for {quiet}{why}" if last_ok
                                 else f"{name}: never synced{why}"))
    ok_times = [t for r in rows for t in (helpers.parse_time(r.get("last_ok_at")),) if t]
    latest = max(ok_times) if ok_times else None
    if problems:
        detail = problems[0][1] + (f" (+{len(problems) - 1} more)" if len(problems) > 1 else "")
        return Check(False, detail, latest, title=problems[0][0],
                     why="New transactions are missing from the Money page and Dana's answers.",
                     blocks=[{"type": "facts", "label": "Banks and cards", "items": [p[1] for p in problems]}])
    return Check(True, f"synced {helpers.ago(latest, now) if latest else 'never'}", latest)
