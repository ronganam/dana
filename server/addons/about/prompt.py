"""About you: the system-prompt block of one user's active facts, and the
per-user cache the agent reads it from.

:func:`section` is the addon's prompt section (``Addon(prompt_section=)``):
it only reads the cache, so it never touches the database, never blocks and
never raises. :mod:`addons.about.facts` rebuilds a user's entry after
every change to their facts and at startup (``facts.load_prompts``).
"""
from __future__ import annotations

from typing import Any, Dict, List

from core.users import User
from addons.about.values import MAX_CHARS, SECTION_KEYS, SECTION_LABELS

PROMPT_MAX_CHARS = MAX_CHARS + 800        # hard cap on the prompt block, header included

# user id → the rendered block ("" when they have no active facts).
_cache: Dict[str, str] = {}


def render(name: str, facts: List[Dict[str, Any]]) -> str:
    """Active facts about ``name`` → the system-prompt block ("" when there are none)."""
    if not facts:
        return ""
    head = (
        f"\n\n--- ABOUT {name.upper()} (what you know) ---\n"
        f"Things you know about {name}. Use them directly: resolve people, nicknames, places and devices "
        "from here instead of asking or looking them up, and follow the preferences. Never mention or "
        "recite this list unless asked what you know.\n"
    )
    tail = "-----------------------------------"
    budget = PROMPT_MAX_CHARS - len(head) - len(tail)
    by_section: Dict[str, List[str]] = {k: [] for k in SECTION_KEYS}
    for f in facts:
        by_section.setdefault(f["section"], []).append(f["text"])
    body: List[str] = []
    used = 0
    full = False
    for key in SECTION_KEYS:
        lines = by_section.get(key) or []
        if not lines or full:
            continue
        title = f"{SECTION_LABELS[key]}:"
        if used + len(title) + 1 > budget:
            break
        section_lines = [title]
        used += len(title) + 1
        for text in lines:
            line = f"- {text}"
            if used + len(line) + 1 > budget:
                full = True
                break
            section_lines.append(line)
            used += len(line) + 1
        if len(section_lines) > 1:
            body.extend(section_lines)
    if not body:
        return ""
    return head + "\n".join(body) + "\n" + tail


def put(user_id: str, text: str) -> None:
    """Store ``user_id``'s rendered block."""
    _cache[user_id] = text


def section(user: User) -> str:
    """``user``'s cached About block for the system prompt ("" when they have
    no facts or nothing is cached). Never blocks or raises."""
    return _cache.get(user.id, "")


def clear() -> None:
    """Forget every cached block (tests)."""
    _cache.clear()
