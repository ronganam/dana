"""The people addon's background work and composer hooks (docs/people.md):

- every ``PEOPLE_BRIEF_INTERVAL_SECONDS`` (600): refresh people briefs;
- context chat for ``person``;
- global search over the user's people.
"""

import datetime
import re
from typing import Any, Dict, List, Optional

from core.addons import ContextRequest, Hit, helpers
from core.db import get_db_connection
from core.users import User
from addons.people import briefs, detail, store as people_store
from addons.people.addon import addon
from addons.people.values import BRIEF_INTERVAL, norm_name, norm_phone


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
