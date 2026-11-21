"""The shopping addon's background work and search hook (docs/shopping.md):

- at start: every user with ``shopping`` gets their own list if they have none;
- global search over the user's active shopping list.
"""

from typing import List

from core.addons import Hit, helpers
from core.users import User
from addons.shopping import items, lists
from addons.shopping.addon import addon


@addon.job(at_start=True)
async def ensure_list(user: User) -> str:
    return lists.ensure_list(user)


@addon.search
def search_shopping(user: User, q: str, limit: int) -> List[Hit]:
    rows = items.search(lists.active_list(user), helpers.like_pattern(q))
    return [Hit("shopping", r["id"], r["name"], "Bought" if r["checked"] else "On the list",
                score=helpers.match_score(r["name"], q) - (0.3 if r["checked"] else 0)) for r in rows][:limit]
