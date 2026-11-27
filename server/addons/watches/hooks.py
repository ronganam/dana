"""The watches addon's background work and Now card: the 30-second time pass,
the daily prune of old fires and trace rows, the interest hints at start-up, and "Done?" cards when evidence suggests a
reminder's task got done by itself (closing it is always the user's call)."""

import datetime as dt
from typing import Any, Dict, List

from core import cards, clock, live
from core.addons import Card, NowContext
from core.users import User
from addons.watches import actions, engine, interest, store
from addons.watches import spec as S
from addons.watches.addon import addon


@addon.job(every=dt.timedelta(seconds=30), timeout=25.0)
async def tick(user: User) -> int:
    """Fire due time triggers, deadlines and window ends for the user."""
    return await engine.tick(user)


@addon.job(daily="03:40")
async def prune(user: User) -> int:
    """Forget fires older than 90 days and trace rows older than 30 days or past 2000 per watch."""
    now = clock.now()
    return store.prune(user, now) + store.prune_trace(user, now)


@addon.job(at_start=True)
async def hint_interest(user: User) -> int:
    """Tell the event sources which values the user's active watches can fire on (``interest.py``)."""
    interest.update(user)
    return 0


def _held_evidence(user: User) -> List[Dict[str, Any]]:
    """(watch, fire) pairs of open reminders whose evidence waits for the user's answer."""
    out = []
    for w in store.list_for(user, [store.ACTIVE, store.PAUSED]):
        if w["kind"] != "reminder":
            continue
        for f in store.fires(user, w["id"], 50):
            if f["trigger_id"] == "evidence" and f["actions"] and f["actions"][0].get("status") == actions.HELD:
                out.append({"watch": w, "fire": f})
                break
    return out


@addon.now_cards(order=40)
def evidence_cards(user: User, ctx: NowContext) -> List[Card]:
    """A "Done?" card per reminder whose task an event suggests is done."""
    out = []
    for item in _held_evidence(user):
        w, f = item["watch"], item["fire"]
        out.append(Card(id=f"watch:{f['id']}", kind="watch", source="Watches",
                        title=f"Done? {w['spec'].get('task_title') or w['title']}",
                        why=f"Looks done: {S.summary(f['event_type'], f['event'])}",
                        primary=cards.action("close", "Done"), secondary=[cards.action("dismiss", "Not yet")],
                        schedulable=False))
    return out


def _set_evidence(user: User, fire: Dict[str, Any], status: str, detail: str) -> None:
    acts = fire["actions"] or [actions.result("suggest_complete", status)]
    acts[0]["status"], acts[0]["detail"] = status, detail
    store.set_fire_actions(user, fire["id"], acts)


@addon.now_action("watch")
async def act(user: User, ref: str, action: str, args: Dict[str, Any], now: dt.datetime) -> Dict[str, Any]:
    """``close`` on a "Done?" card completes the reminder's task and ends the watch; ``dismiss`` keeps it."""
    fire = store.get_fire(user, ref)
    watch = store.get(user, fire["watch_id"]) if fire else None
    if fire is None or watch is None:
        raise cards.ActError("No such watch.", 404)
    if action == "dismiss":
        _set_evidence(user, fire, actions.SKIPPED, "you said not yet")
        return cards.result("Kept open")
    if action != "close":
        raise cards.ActError(f"'{action}' isn't an action for a watch")
    if watch.get("task_id"):
        await actions.complete_task(user, watch["task_id"])
    store.close(user, watch["id"], store.DONE)
    _set_evidence(user, fire, actions.DONE, "closed by you")
    return cards.result(f"Closed “{watch['title']}”")


@addon.live_action
async def live_act(user: User, current: live.Current, action: str, choice: str) -> live.HookResult:
    """Approve / Deny on a watch agent run's live activity: answer it; the resumed run gets its own activity."""
    if action not in ("approve", "deny"):
        return None
    why = await actions.answer(user, current, action)
    if why == "busy":
        return None  # Dana is busy in that chat: the card stays, try again
    return live.END
