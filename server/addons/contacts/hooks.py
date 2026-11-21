"""The contacts addon's background work: every user's copy of their Google
Contacts brought up to date every ``CONTACTS_SYNC_MINUTES`` (60), their people
filled in from it (docs/contacts.md)."""

from typing import Dict, Optional

from core.addons import minutes
from core.users import User
from addons.contacts import sync
from addons.contacts.addon import addon
from addons.contacts.config import SYNC_MINUTES


@addon.job(every=minutes(SYNC_MINUTES), delay=sync.STARTUP_DELAY_SECONDS)
async def sync_contacts(user: User) -> Optional[Dict[str, int]]:
    return await sync.sync_job(user)
