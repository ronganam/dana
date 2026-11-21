"""The WhatsApp addon's background work: learn the account of a connection
that has none (WAHA being down only delays it), so inbound events find their
user (docs/whatsapp.md)."""

import datetime
from typing import Optional

from core.users import User
from addons.whatsapp import accounts
from addons.whatsapp.addon import addon


@addon.job(every=datetime.timedelta(seconds=accounts.REFRESH_SECONDS))
async def learn_account(user: User) -> Optional[str]:
    return await accounts.refresh_user(user)
