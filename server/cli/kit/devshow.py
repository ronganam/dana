"""What one addon produces in a running Dana, as JSON: run inside the local test environment's
app container by ``bin/dana addons dev`` (``python -m cli.kit.devshow <name>``).

Prints ``{"loaded", "error", "tools", "cards", "day_items"}`` for the first admin: the
addon's tools they are offered, and the Now cards and day items its hooks make now.
"""

import json
import sys
from typing import Any, Dict

from core import clock, users
from core.addons import loader, registry
from core.addons.hooks import NowContext


def show(name: str) -> Dict[str, Any]:
    """The addon's state for the first admin (see the module docstring)."""
    loader.load()
    failed = loader.failed().get(name)
    out: Dict[str, Any] = {"loaded": name in loader.loaded(), "error": failed.error if failed else None,
                           "tools": [], "cards": [], "day_items": []}
    admin = users.first_admin()
    if not out["loaded"] or admin is None:
        return out
    ctx = NowContext(now=clock.now(), hidden={})
    with users.acting_as(admin):
        out["tools"] = [t.name for t in registry.tools_for(admin) if t.addon == name]
        for kind, key in (("now_cards", "cards"), ("day_items", "day_items")):
            for h in registry.hooks(kind, admin):
                if h.addon == name:
                    try:
                        out[key] += [x.to_json() for x in h.func(admin, ctx)]
                    except Exception as e:  # shown, not fatal: the author is iterating
                        out[key].append({"error": f"{h.func.__name__}: {type(e).__name__}: {e}"})
    return out


if __name__ == "__main__":
    print(json.dumps(show(sys.argv[1]), default=str))
