"""The tasks addon's sample data for the demo household (core/demo.py)."""

import datetime
import json

from core.addons import DemoContext
from core.users import User
from addons.tasks import groups, service
from addons.tasks.addon import addon

# (title, list key, priority, due in days or None, reminder in hours or None)
TASKS = (
    ("Renew the bike insurance", "personal", "high", 2, None),
    ("Book a vet visit for Biscuit", "home", "medium", 5, None),
    ("Send the launch slides to Taylor", "work", "high", 0, 3),
    ("Fix the dripping kitchen tap", "home", "low", None, None),
    ("Water the balcony plants", "home", "medium", None, 20),
    ("Pick up the dry cleaning", "general", "medium", 1, None),
)


@addon.demo
def demo_tasks(user: User, ctx: DemoContext) -> int:
    """Six open tasks for the first demo user (two with reminders), two for the others."""
    groups.ensure_default_groups(user)
    wanted = TASKS if ctx.index == 0 else TASKS[3:5]
    for title, key, priority, due_days, remind_hours in wanted:
        due = (ctx.now.date() + datetime.timedelta(days=due_days)).isoformat() if due_days is not None else None
        remind = ctx.now + datetime.timedelta(hours=remind_hours or 0)
        triggers = {"time": remind.strftime("%Y-%m-%d %H:%M")} if remind_hours else {}
        service.create_task(user, title=title, priority=priority, due_date=due, triggers=json.dumps(triggers),
                            group_id=groups.group_id_for(user, key))
    return len(wanted)
