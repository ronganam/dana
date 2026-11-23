"""The ``showcase`` composer hooks (a module the manifest names in ``modules=``): a Now card and
its action, a day item, a briefing section, a weekly-review section and its action, search
results, a self-check, context for a chat about a plant, a named capability and a
dashboard widget.

Composer hooks are plain functions (the composers call them in a worker thread);
the action hooks are async. Each runs only for a user who has the addon.
"""

import datetime
from typing import Any, Dict, List, Optional

from core import cards, clock
from core.addons import (BriefingContext, Card, Check, ContextRequest, DayItem, Field, Fix, Hit, NowContext, Section,
                         WidgetContext)
from core.addons.helpers import match_score
from core.users import User
from addons.showcase import store
from addons.showcase.addon import addon

# --- reference: now_cards ---
@addon.now_cards(order=60)
def thirsty_cards(user: User, ctx: NowContext) -> List[Card]:
    """A card for each plant that needs water now."""
    return [Card(id=f"plant:{p['id']}", kind="plant", source="Plants", title=f"Water {p['name']}",
                 why=f"Every {p['every_days']} days", primary=cards.action("water", "Watered"), schedulable=False)
            for p in store.due(user, ctx.now)]
# --- end ---


# --- reference: now_action ---
@addon.now_action("plant")
async def act(user: User, ref: str, action: str, args: Dict[str, Any], now: datetime.datetime) -> Dict[str, Any]:
    """``water`` on a card "plant:<id>": that plant is watered now."""
    if action != "water":
        raise cards.ActError(f"'{action}' isn't an action for a plant")
    if not ref.isdigit() or not store.water(user, int(ref), now):
        raise cards.ActError("No such plant.", 404)
    return cards.result("Watered")
# --- end ---


# --- reference: day_items ---
@addon.day_items
def watering_time(user: User, ctx: NowContext) -> List[DayItem]:
    """Today's watering, at the user's reminder hour, when a plant needs it."""
    due = store.due(user, ctx.now)
    if not due:
        return []
    hour = int(store_settings(user).get("remind_hour") or 9)
    return [DayItem(title=f"Water {len(due)} plant{'s' if len(due) > 1 else ''}", kind="plant",
                    at=ctx.now.replace(hour=hour, minute=0, second=0, microsecond=0))]
# --- end ---


def store_settings(user: User) -> Dict[str, Any]:
    """The user's showcase settings (defaults filled in)."""
    from core.addons import store as addon_store
    conn = addon_store.get(user, "showcase")
    return dict(conn.settings) if conn else {}


# --- reference: briefing ---
@addon.briefing("plants", counts=("thirsty_plants",), default=None)
def briefing_section(user: User, ctx: BriefingContext) -> Optional[Section]:
    """The morning briefing's plants: which need water (a count for the phone's push)."""
    due = [p["name"] for p in store.due(user, ctx.now)]
    if not due:
        return None
    return Section(data={"thirsty": due}, points=[f"Water {', '.join(due)}"], counts={"thirsty_plants": len(due)})
# --- end ---


# --- reference: review ---
@addon.review("plants")
def review_section(user: User, ctx: BriefingContext) -> Optional[Section]:
    """The weekly review: plants not watered for two weeks, to keep or forget."""
    cutoff = ctx.now - datetime.timedelta(days=14)
    stale = [p for p in store.plants(user)
             if p["last_watered"] and datetime.datetime.fromisoformat(p["last_watered"]) < cutoff]
    if not stale:
        return None
    return Section(data={"stale_plants": len(stale)},
                   items=[{"ref": str(p["id"]), "type": "plant", "title": p["name"],
                           "why": "Not watered for two weeks", "facts": {"every_days": p["every_days"]}}
                          for p in stale])


@addon.review_action("plant", ("forget",))
async def apply_review(user: User, item: Dict[str, Any], action: str, decision: Dict[str, Any],
                       today: datetime.date) -> str:
    """Apply the review's decision on a plant item: ``forget`` it (``keep`` changes nothing)."""
    if action == "forget" and str(item.get("ref", "")).isdigit() and store.remove(user, int(item["ref"])):
        return f"Forgot {item.get('title')}"
    return "Kept"
# --- end ---


# --- reference: search ---
@addon.search
def search_plants(user: User, query: str, limit: int) -> List[Hit]:
    """The user's plants whose name matches the global search."""
    hits = [Hit(kind="plant", id=str(p["id"]), title=p["name"], subtitle=f"Every {p['every_days']} days",
                score=match_score(p["name"], query)) for p in store.plants(user)]
    return sorted((h for h in hits if h.score > 1), key=lambda h: -h.score)[:limit]  # 0.5: no match at all
# --- end ---


# --- reference: selfcheck ---
def overdue(user: User, now: datetime.datetime) -> List[Dict[str, Any]]:
    """The plants that waited twice their interval for water."""
    return [p for p in store.plants(user) if p["last_watered"]
            and now - datetime.datetime.fromisoformat(p["last_watered"]) > datetime.timedelta(days=2 * p["every_days"])]


@addon.selfcheck("showcase_overdue", "Plants watered")
def overdue_check(user: User, now: datetime.datetime) -> Check:
    """Fails when a plant has waited twice its interval for water (a Now card says which, with a fix)."""
    late = [p["name"] for p in overdue(user, now)]
    if late:
        return Check(ok=False, detail=f"{len(late)} overdue", title="Plants are overdue",
                     why=f"{', '.join(late)} waited twice their interval",
                     fix=Fix.run("water_all", "Mark all watered"))
    return Check(ok=True, detail="All watered in time")
# --- end ---


# --- reference: fix ---
@addon.fix("showcase_overdue")
async def water_overdue(user: User, action: str) -> str:
    """The check's "Mark all watered" button: runs on the server for the user who pressed it."""
    late = overdue(user, clock.now())
    for p in late:
        store.water(user, p["id"], clock.now())
    return f"{len(late)} plant{'s' if len(late) != 1 else ''} marked watered"
# --- end ---


# --- reference: context ---
@addon.context("plant")
def plant_context(user: User, ref_id: str, req: ContextRequest) -> Optional[str]:
    """What the agent knows in a chat opened from a plant."""
    p = store.plant(user, int(ref_id)) if ref_id.isdigit() else None
    return f"This chat is about the plant {p['name']}, watered every {p['every_days']} days." if p else None


@addon.context_card("plant")
def plant_card(user: User, ref_id: str) -> Dict[str, Any]:
    """The card shown with that chat; LookupError when the plant is not the user's."""
    p = store.plant(user, int(ref_id)) if ref_id.isdigit() else None
    if p is None:
        raise LookupError(ref_id)
    return {"title": p["name"], "subtitle": f"Every {p['every_days']} days"}


@addon.context_block("plant")
def plant_block(user: User, ref_id: str) -> str:
    """A plant in a few words, for another addon's context."""
    p = store.plant(user, int(ref_id)) if ref_id.isdigit() else None
    return f"Plant: {p['name']}" if p else ""
# --- end ---


# --- reference: provide ---
@addon.provide("showcase.thirsty_count")
def thirsty_count(user: User) -> int:
    """How many of the user's plants need water now; others call it through registry.provider."""
    return len(store.due(user, clock.now()))
# --- end ---


# --- reference: widget ---
@addon.widget("plants_thirsty", title="Thirsty plants", script="widget.js", icon="sprout", refresh=300,
              sizes=("small", "medium"),
              settings=(Field("heading", "The words above the list", default="Needs water", required=False,
                              label="Heading", placeholder="Needs water"),),
              description="The plants that need water now")
def thirsty_widget(user: User, ctx: WidgetContext) -> Dict[str, Any]:
    """The widget's data for one dashboard: its heading (a setting) and the thirsty plants (fewer when small)."""
    names = [p["name"] for p in store.due(user, ctx.now)]
    return {"heading": ctx.settings["heading"], "plants": names[:3] if ctx.size == "small" else names}
# --- end ---
