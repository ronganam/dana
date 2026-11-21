"""The ``hello`` hooks: a daily clean-up job, an event handler, a Now card and its actions.

Each runs for (and as) one user who has the addon; nothing here runs for anyone else.
"""

import datetime
from typing import Any, Dict, List

from core import cards
from core.addons import Card, NowContext, hours
from core.users import User
from addons.hello import store
from addons.hello.addon import addon

KEEP_DONE = datetime.timedelta(days=30)
PREFIX = "Greet "


@addon.job(every=hours(24))
async def purge_old(user: User) -> int:
    """Forget greetings done more than 30 days ago."""
    return store.purge_done(user, KEEP_DONE)


@addon.on("tasks.task.created")
async def on_task(user: User, data: Dict[str, Any]) -> None:
    """A task titled "Greet <name>" also puts <name> on the Now screen."""
    title = str(data.get("title") or "")
    if title.startswith(PREFIX) and title[len(PREFIX):].strip():
        store.add(user, title[len(PREFIX):].strip())


@addon.now_cards
def greeting_cards(user: User, ctx: NowContext) -> List[Card]:
    """One card per person still to greet."""
    return [Card(id=f"hello:{g['id']}", kind="hello", source="hello", title=f"Greet {g['name']}",
                 why="You wanted to say hello", primary=cards.action("done", "Greeted"), schedulable=False)
            for g in store.pending(user)]


@addon.now_action("hello")
async def act(user: User, ref: str, action: str, args: Dict[str, Any], now: datetime.datetime) -> Dict[str, Any]:
    """``done`` on a card: that greeting is done."""
    if action != "done":
        raise cards.ActError(f"'{action}' isn't an action for a greeting")
    if not ref.isdigit() or not store.mark_done(user, int(ref)):
        raise cards.ActError("No such greeting.", 404)
    return cards.result("Greeted")
