"""The shopping addon's sample data for the demo household (core/demo.py): items
written with their aisle (no aisle lookup, which would ask the model and fill
the shared aisle table)."""

import uuid

from core.addons import DemoContext
from core.db import get_db_connection
from core.users import User
from addons.shopping import lists
from addons.shopping.addon import addon
from addons.shopping.names import name_key
from addons.shopping.values import iso, utc_now

# (name, quantity, aisle, bought)
ITEMS = (
    ("Bananas", "6", "produce", False),
    ("Sourdough bread", "", "bakery", False),
    ("Oat milk", "2", "dairy", False),
    ("Eggs", "12", "dairy", False),
    ("Dog food", "1 bag", "pets", False),
    ("Dish soap", "", "household", False),
    ("Coffee beans", "", "pantry", True),
    ("Tomatoes", "1 kg", "produce", True),
)


@addon.demo
def demo_shopping(user: User, ctx: DemoContext) -> int:
    """Eight items on the first demo user's list (two bought), three on the others'."""
    list_id = lists.ensure_list(user)
    wanted = ITEMS if ctx.index == 0 else ITEMS[:3]
    now = iso(utc_now())
    with get_db_connection() as conn:
        for seq, (name, quantity, aisle, bought) in enumerate(wanted, start=1):
            conn.execute("INSERT INTO shopping_items (id, list_id, name, name_key, quantity, category, checked, "
                         "checked_at, checked_by, added_by, created_at, seq) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                         (uuid.uuid4().hex[:12], list_id, name, name_key(name), quantity, aisle, int(bought),
                          now if bought else None, user.id if bought else None, user.id, now, seq))
        # scoped: the demo user's own list made above
        conn.execute("UPDATE shopping_lists SET version = version + 1 WHERE id = ?", (list_id,))
        conn.commit()
    return len(wanted)
