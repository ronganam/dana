"""The about addon's sample data for the demo household (core/demo.py)."""

from core.addons import DemoContext
from core.users import User
from addons.about import facts
from addons.about.addon import addon

# (section, text)
FACTS = (
    ("people", "Lives with Jamie and their dog Biscuit"),
    ("people", "Riley Brooks is a climbing partner"),
    ("habits", "Cycles to work most days"),
    ("habits", "Prefers reminders in the evening, not the morning"),
    ("home", "The balcony plants need water twice a week"),
)


@addon.demo
def demo_about(user: User, ctx: DemoContext) -> int:
    """Five facts for the first demo user, one for the others."""
    wanted = FACTS if ctx.index == 0 else FACTS[2:3]
    for section, text in wanted:
        facts.add_fact(user, text, section)
    return len(wanted)
