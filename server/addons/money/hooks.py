"""The money addon's background work (docs/money.md, "Schedule"):

- at start: end what a previous process left mid-sync, then classify what is
  still missing (after a migration cleared categories, or while the model was down);
- every minute (after 15 s): start every due source of the user (their
  slots, or earlier when a signal or "Sync now" pulled it).
"""

from typing import List

from core.addons import minutes
from core.users import User
from addons.money import classify, schedule, sync
from addons.money.addon import addon


@addon.job(at_start=True)
async def recover(user: User) -> None:
    sync.recover(user)
    await classify.catch_up_user(user)


@addon.job(every=minutes(schedule.INTERVAL_SECONDS / 60), delay=schedule.STARTUP_DELAY_SECONDS)
async def start_due_syncs(user: User) -> List[str]:
    return schedule.tick_user(user)
