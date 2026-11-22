"""The shopping addon's background work and hooks (docs/shopping.md):

- at start: every user with ``shopping`` gets their own list if they have none;
- global search over the user's active shopping list;
- the dashboard's Shopping widget (``shopping``: the active list's items to
  buy; ``web/widget.js`` follows the list's live stream and reads it again on
  each change).
"""

from typing import Any, Dict, List

from core.addons import Hit, WidgetContext, helpers
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


# How many items to buy each widget size lists.
WIDGET_ITEMS = {"small": 6, "medium": 12, "large": 30}


@addon.widget("shopping", title="Shopping list", script="widget.js", icon="shopping-cart", refresh=0,
              sizes=("large", "medium", "small"),
              description="Your active shopping list, live: what is left to buy, by aisle")
def shopping_widget(user: User, ctx: WidgetContext) -> Dict[str, Any]:
    """The active list (``{id, name}``), its items to buy in aisle order (``{id,
    name, quantity, category}``, at most the size's share) and ``to_buy`` (all of them)."""
    lst = lists.active_list(user)
    to_buy = [i for i in items.list_items(lst) if not i["checked"]]
    return {"list": {"id": lst.id, "name": lst.name}, "to_buy": len(to_buy),
            "items": [{"id": i["id"], "name": i["name"], "quantity": i["quantity"], "category": i["category"]}
                      for i in to_buy[:WIDGET_ITEMS.get(ctx.size, 12)]]}
