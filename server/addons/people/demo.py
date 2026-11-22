"""The people addon's sample data for the demo household (core/demo.py): made-up
people with fictional (555-01xx) numbers the demo calls and notifications use."""

from core.addons import DemoContext
from core.db import get_db_connection
from core.users import User
from addons.people import store as people_store
from addons.people.addon import addon

# (name, relation, notes, phone, pinned) for the first demo user; the second gets the first two.
PEOPLE = (
    ("Riley Brooks", "friend", "Climbing partner on Tuesdays. Allergic to nuts.", "+15555550101", True),
    ("Dr. Morgan Hale", "dentist", "Clinic on Pine Street; check-ups every six months.", "+15555550102", False),
    ("Taylor Quinn", "colleague", "Design lead; working together on the spring launch.", "+15555550103", False),
    ("Grandma Rose", "grandmother", "Loves crosswords; call on Sundays.", "+15555550104", True),
)


@addon.demo
def demo_people(user: User, ctx: DemoContext) -> int:
    """Made-up people (the first demo user gets four, the others two)."""
    wanted = PEOPLE if ctx.index == 0 else PEOPLE[:2]
    with get_db_connection() as conn:
        for name, relation, notes, phone, pinned in wanted:
            people_store.create_person(user, name, phones=[phone], relation=relation, notes=notes, pinned=pinned,
                                       conn=conn, recheck=False)
        conn.commit()
    return len(wanted)
