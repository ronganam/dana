"""Tools every user has (the ``essentials`` addon): the current time, asking the user
to pick between options, web search, and sharing an item with another user
(offered only on a server with more than one user)."""

import asyncio
import logging
from typing import Any, Dict, List, Optional

from core import clock, users
from core.users import User
from core.addons import Approval, Connection, sharing, store
from core.addons.hooks import clean
from addons.essentials.addon import addon

logger = logging.getLogger(__name__)

SEARCH_RESULTS = 5
SEARCH_TIMEOUT = 20  # seconds for the whole search (each engine gets half)


@addon.tool()
def get_current_time(conn: Connection) -> str:
    """Get the current date, time, day of the week and timezone: the base for any date or time you compute."""
    now = clock.now()
    return f"The current time is {now.strftime('%Y-%m-%d %H:%M (%A)')}, timezone {now.tzinfo}."


# The agent loop shows this tool as a single- or multi-select prompt on channels
# with a selection UI (Telegram / web / Android: core/agent.py, SELECTION_TOOL_NAME);
# the body runs only where there is none (realtime voice).
@addon.tool(is_write=True)
async def request_user_selection(
    conn: Connection,
    prompt: str,
    options: List[Dict[str, str]],
    multiple: bool = False,
    context: str = "",
) -> str:
    """Ask the user to pick one or more options from a list; their pick comes back to you as the answer.

    Args:
        prompt: The question, short and clear.
        options: 2-6 distinct choices, each {"label": "shown to the user", "value": "returned to you"}.
        multiple: True when the user may pick several.
        context: Optional one line on why the choice is needed.
    """
    if not options:
        return "Error: give at least one option, each {\"label\": ..., \"value\": ...}."
    option_lines = "\n".join(f"- {o.get('label') or o.get('value') or '?'}" for o in options)
    context_note = f"\nContext: {context}" if context else ""
    return ("Selection UI is not available on this channel, so ask the user verbally which "
            f"of the following {'option(s)' if multiple else 'option'} they want.{context_note}\n"
            f"Options:\n{option_lines}")


def _search(query: str) -> List[Dict[str, Any]]:
    from ddgs import DDGS

    with DDGS(timeout=SEARCH_TIMEOUT // 2) as ddgs:
        return list(ddgs.text(query, max_results=SEARCH_RESULTS))


@addon.tool(untrusted_result=True)
async def web_search(conn: Connection, query: str) -> str:
    """Search the web (DuckDuckGo) for public, general information: business hours, addresses, news, product specs. Never for the user's own data (memories, calendar, calls, contacts have their own tools).

    Args:
        query: The search query string.
    """
    if not (query or "").strip():
        return "Error: the search query is empty."
    try:
        results = await asyncio.wait_for(asyncio.to_thread(_search, query), SEARCH_TIMEOUT)
    except Exception as e:
        logger.warning(f"Web search failed: {e!r}", exc_info=True)
        return f"Error: the web search failed ({str(e)[:200] or type(e).__name__}). Rephrase the query or try later."
    if not results:
        return f"No search results for '{query}'."
    lines = [f"Search results for '{query}':"]
    for i, res in enumerate(results, 1):
        lines.append(f"{i}. {res.get('title') or 'No title'} - {res.get('body') or ''} (URL: {res.get('href') or '-'})")
    return "\n".join(lines)


def _find_user(owner: User, name: str) -> Optional[User]:
    """The other user called ``name`` (case-insensitive; a unique first name
    or start of a name also matches), None when unknown or ambiguous."""
    wanted = " ".join((name or "").split()).casefold()
    others = [u for u in users.list_users() if u.id != owner.id]
    exact = [u for u in others if u.name.casefold() == wanted]
    if len(exact) == 1:
        return exact[0]
    partial = [u for u in others if wanted and (u.name.casefold().split()[:1] == [wanted]
                                                or u.name.casefold().startswith(wanted))]
    return partial[0] if len(partial) == 1 else None


def _describe_share_item(user: Optional[User], a: Dict[str, Any]) -> Approval:
    what = str(a.get("item_type") or "item").replace("_", " ")
    level = str(a.get("level") or "").strip().lower()
    item = f"“{clean(a.get('item'), 80)}”"
    who = clean(a.get("user"), 80)
    if level == "none":
        return Approval(f"Stop sharing the {what} {item} with {who}", "Stop sharing", "🔒", "Stop sharing it?")
    return Approval(f"Share the {what} {item} with {who}", "Share", "🤝", "Share it?",
                    facts=[("They can", {"read": "see it", "edit": "see and change it"}.get(level, level))])


@addon.tool(confirm=True, available=sharing.tool_available, describe=_describe_share_item)
def share_item(conn: Connection, item_type: str, item: str, user: str, level: str) -> str:
    """Share one of the user's own items with another person who uses Dana, change what they may do with it, or stop sharing it.

    Only the owner of an item can share it. What is inside a shared item comes with it (the tasks of a shared task list, the items of a shared shopping list, the entries of a shared project; sharing a shopping list sends them an invitation and never switches their list: with 'edit' they can use it from the invitation or with choose_shopping_list).

    Args:
        item_type: What kind of item: 'task_list' (a task group), 'shopping_list' (the user's shopping list) or 'project' (a knowledge project).
        item: The item's name or id (e.g. the task group 'Home' or the project 'Wedding'; a user has one shopping list, any name finds it).
        user: The other person's name.
        level: 'read' (they can see it), 'edit' (they can also change what is in it) or 'none' (stop sharing it with them).
    """
    owner = conn.user
    types = [t for t in sharing.item_types() if store.is_connected(owner, t.addon)]
    t = next((t for t in types if t.name == (item_type or "").strip().lower()), None)
    if t is None:
        return (f"Error: '{item_type}' can't be shared. Items you can share: "
                f"{', '.join(f'{x.name} ({x.label})' for x in types) or 'none'}.")
    item_id = t.find_owned(owner, item)
    if item_id is None:
        return f"Error: you have no {t.label} called '{item}' (only the owner of a {t.label} can share it)."
    other = _find_user(owner, user)
    if other is None:
        names = ", ".join(u.name for u in users.list_users() if u.id != owner.id)
        return f"Error: there is no single user called '{user}'. The people you can share with: {names}."
    level = (level or "").strip().lower()
    if level not in (*sharing.LEVELS, sharing.NONE):
        return "Error: level must be 'read', 'edit' or 'none'."
    try:
        before = sharing.set_level(owner, t.name, item_id, other.id, level)
    except (LookupError, ValueError) as e:
        return f"Error: {e}"
    title = t.title_of(item_id) or item
    if level == sharing.NONE:
        if before is None:
            return f"The {t.label} '{title}' was not shared with {other.name}."
        return f"Stopped sharing the {t.label} '{title}' with {other.name}."
    msg = (f"Shared the {t.label} '{title}' with {other.name} "
           f"({'read-only' if level == sharing.READ else 'they can edit it'}).")
    if not store.is_connected(other, t.addon):
        msg += f" They will see it once the {t.addon} addon is connected for them."
    return msg
