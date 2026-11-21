"""The people addon's background work and composer hooks (docs/people.md):

- every ``PEOPLE_BRIEF_INTERVAL_SECONDS`` (600): refresh people briefs;
- context chat for ``person``;
- global search over the user's people;
- morning briefing (birthdays, reach out) and weekly review (drifting people + ``reach_out``);
- provider: ``person_cards_for_text`` (meeting context cards).
"""

import datetime
import logging
import re
from typing import Any, Dict, List, Optional, Sequence

from core.addons import BriefingContext, ContextRequest, Hit, Section, helpers, store
from core.db import get_db_connection
from core.users import User
from addons.people import briefs, cadence, detail, store as people_store
from addons.people.addon import addon
from addons.people.values import BRIEF_INTERVAL, norm_name, norm_phone

logger = logging.getLogger(__name__)


@addon.job(every=datetime.timedelta(seconds=BRIEF_INTERVAL), delay=30)
async def refresh_briefs(user: User) -> int:
    return await briefs.refresh_briefs(user)


@addon.context_card("person")
def person_card(user: User, ref_id: str) -> Dict[str, Any]:
    return detail.chat_context(user, ref_id)


@addon.context("person")
def person_context(user: User, ref_id: str, req: ContextRequest) -> Optional[str]:
    return detail.context_prompt(user, ref_id)


@addon.search
def search_people(user: User, q: str, limit: int) -> List[Hit]:
    qn = norm_name(q)
    qp = norm_phone(q) if re.search(r"\d{3}", q or "") else ""
    out: List[Hit] = []
    with get_db_connection() as conn:
        phones: Dict[str, List[str]] = people_store.phones_by_person(conn, user) if qp else {}
        for r in people_store.rows(conn, user, include_hidden=False):
            names = people_store.names_of(r)
            hit_name = qn and any(qn in n for n in names)
            hit_phone = qp and any(qp in p for p in phones.get(r["id"], []))
            hit_rel = qn and qn in norm_name(r["relation"])
            if not (hit_name or hit_phone or hit_rel):
                continue
            score = max((helpers.match_score(n, qn) for n in names), default=0.5) if hit_name else 1.0
            out.append(Hit("person", r["id"], r["name"], r["relation"] or "Person", score=score + 0.1))
    return sorted(out, key=lambda h: -h.score)[:limit]


@addon.briefing("people", default={"birthdays": [], "reach_out": []}, rank=50)
def briefing_people(user: User, ctx: BriefingContext) -> Section:
    try:
        everyone = cadence.list_people(user, sort="recent")
    except Exception as e:
        logger.debug(f"Briefing people failed: {e}")
        return Section({"birthdays": [], "reach_out": []})
    bdays = [{"id": p["id"], "name": p["name"], "in_days": p["birthday_in_days"]}
             for p in everyone if p.get("birthday_in_days") is not None and p["birthday_in_days"] <= 3]
    bdays.sort(key=lambda b: b["in_days"])
    drifting = [p for p in everyone if p["stats"]["drifting"]]
    drifting.sort(key=lambda p: (not p["pinned"], -(p["stats"]["contact_days"] or 0)))
    reach = [{"id": p["id"], "name": p["name"], "days_since": p["stats"]["days_since"],
              "typical_gap_days": p["stats"]["typical_gap_days"]} for p in drifting[:2]]
    points = []
    for b in bdays[:1]:
        points.append(f"{b['name']}'s birthday " + ("today" if b["in_days"] == 0 else f"in {b['in_days']} days"))
    for r in reach[:1]:
        points.append(f"Haven't talked to {r['name']} in {r['days_since']} days")
    return Section({"birthdays": bdays, "reach_out": reach}, points=points)


@addon.review("people")
def review_people(user: User, ctx: BriefingContext) -> Section:
    items: List[Dict[str, Any]] = []
    try:
        for p in cadence.list_people(user, sort="drifting")[:3]:
            st = p["stats"]
            items.append({"ref": p["id"], "type": "person", "title": p["name"], "why": "drifting",
                          "facts": [f"last contact {st['days_since']} days ago",
                                    f"usually every ~{st['typical_gap_days']} days", p.get("relation") or ""]})
    except Exception as e:
        logger.debug(f"Review people failed: {e}")
    return Section(items=items)


@addon.review_action("person", ("reach_out", "keep"), primary=False)
async def apply_review_person(user: User, item: Dict[str, Any], action: str, decision: Dict[str, Any],
                              today: datetime.date) -> str:
    from addons.tasks import groups as task_groups, service as tasks_service, titles as task_titles
    store.require(user, tasks_service.ADDON)
    due = helpers.valid_future_date(decision.get("due_date"), today) or helpers.next_weekday(today, 6).isoformat()
    title = await task_titles.sanitize_task_title(f"📞 Reach out to {item['title']}")
    g = task_groups.find_group(user, "Personal")
    group_id = g["id"] if g else await task_titles.resolve_task_group(user, "Personal", title)
    return tasks_service.create_task(user, title=title, priority="low", due_date=due, triggers="{}",
                                     group_id=group_id, task_links=[("person", item["ref"], "origin")])


@addon.provide("person_cards_for_text")
def person_cards_for_text(user: User, texts: Sequence[str], limit: int = 2) -> List[Dict[str, Any]]:
    pids: List[str] = []
    for text in texts:
        for pid in people_store.mentioned_in(user, text):
            if pid not in pids:
                pids.append(pid)
    out = []
    for pid in pids[:limit]:
        c = detail.card(user, pid)
        if c and (c["last_call"] or c["points"] or c["open"]):
            out.append(c)
    return out
