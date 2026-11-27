"""The gmail addon's jobs: ``inbox`` (``gmail.email.received`` for new mail,
about every minute: event_types.py) and its daily prune."""

import datetime

from core.users import User
from addons.gmail import event_types
from addons.gmail.addon import addon


@addon.job(every=datetime.timedelta(minutes=1), delay=60, timeout=50.0)
async def inbox(user: User) -> int:
    """Announce the messages added to the user's Gmail inbox since the last poll."""
    return await event_types.poll(user)


@addon.job(daily="03:55")
async def prune_received(user: User) -> int:
    """Forget announced emails older than 30 days."""
    return event_types.prune(user)
