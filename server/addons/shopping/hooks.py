"""The shopping addon's background work: at start, every user with
``shopping`` gets their own list if they have none (new users get it from
``on_connect``)."""

from core.users import User
from addons.shopping import lists
from addons.shopping.addon import addon


@addon.job(at_start=True)
async def ensure_list(user: User) -> str:
    return lists.ensure_list(user)
