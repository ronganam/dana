"""One search box over what Dana keeps for one user: what each active addon's
``@addon.search`` finds (reminders, calls, people, notes/projects, active
notifications, active shopping list, and any other addon). An addon they have
not connected adds nothing. Plain SQLite (FTS where it exists, LIKE elsewhere),
no model, Hebrew and English.

``search(user, q, limit)`` → ``[{kind, id, title, subtitle, …}]``, at most
``limit`` per addon (notifications add ``package``, notes ``project_id``), best
matches first across kinds (exact title > title prefix > a word of the title
starts with it > contains > matched only in the body).
"""
from __future__ import annotations

from typing import Any, Dict, List

from core.addons import registry
from core.users import User


def search(user: User, q: str, limit: int = 5) -> List[Dict[str, Any]]:
    """``user``'s items matching ``q`` across their addons."""
    q = (q or "").strip()[:100]
    if not q:
        return []
    limit = max(1, min(int(limit or 5), 20))
    results: List[Dict[str, Any]] = []
    kinds: List[str] = []
    for hook in registry.hooks("search", user):
        try:
            hits = hook.func(user, q, limit) or []
        except Exception:
            continue  # one addon's search failing leaves the others
        results += [{**h.to_json(), "_score": h.score} for h in hits]
        kinds += [h.kind for h in hits if h.kind not in kinds]
    order = {k: i for i, k in enumerate(kinds)}
    results.sort(key=lambda r: (-r["_score"], order.get(r["kind"], 9)))
    return [{k: v for k, v in r.items() if not k.startswith("_")} for r in results]
