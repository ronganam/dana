"""The people addon's background work: every user's people briefs refreshed
every ``PEOPLE_BRIEF_INTERVAL_SECONDS`` (600) by the model."""

import datetime

from core.users import User
from addons.people import briefs
from addons.people.addon import addon
from addons.people.values import BRIEF_INTERVAL


@addon.job(every=datetime.timedelta(seconds=BRIEF_INTERVAL), delay=30)
async def refresh_briefs(user: User) -> int:
    return await briefs.refresh_briefs(user)
