"""The knowledge addon's composer hooks: global search over projects and entries (docs/knowledge.md)."""

from typing import List

from core.addons import Hit, helpers
from core.users import User
from addons.knowledge import entries, projects
from addons.knowledge.addon import addon


@addon.search
def search_notes(user: User, q: str, limit: int) -> List[Hit]:
    """Entries and projects ``user`` can see: own and shared."""
    like = helpers.like_pattern(q)
    out: List[Hit] = []
    for r in entries.search_rows(user, entries.matching_ids(user, helpers.fts_query(q), like)):
        score = helpers.match_score(r["title"], q) if q.casefold() in (r["title"] or "").casefold() else 0.5
        out.append(Hit("note", r["id"], r["title"], r["project"] or (r["kind"] or "note").capitalize(),
                       score=score, extra={"project_id": r["project_id"]}))
    for r in projects.search_titles(user, like):
        out.append(Hit("note", f"project:{r['id']}", r["title"], "Project",
                       score=helpers.match_score(r["title"], q), extra={"project_id": r["id"]}))
    return sorted(out, key=lambda h: -h.score)[:limit]
