"""The about addon's background work (docs/about.md):

- at start: the old memories copied into their owner's facts (once per key),
  then each user's prompt block built;
- every minute (after two): the learner's pass for each user whose pass is
  due (off with ``ABOUT_LEARN=0``).
"""

import datetime

from core.users import User
from addons.about import facts, learner, legacy
from addons.about.addon import addon


@addon.job(at_start=True)
async def load_prompt(user: User) -> None:
    if user.id == legacy.owner_id():
        legacy.migrate_legacy()
    facts.refresh_prompt(user)


@addon.job(every=datetime.timedelta(seconds=learner.LOOP_TICK_SECONDS), delay=120)
async def learn(user: User) -> bool:
    return await learner.run_if_due(user)
