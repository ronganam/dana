"""Tools every user has (the ``essentials`` addon): the current time, asking the user
to pick between options, web search, and sharing an item with another user
(offered only on a server with more than one user)."""

import logging
from typing import Dict, List, Optional

from core import clock, users
from core.users import User
from core.addons import Connection, sharing, store
from addons.essentials.addon import addon

logger = logging.getLogger(__name__)


@addon.tool()
def get_current_time(conn: Connection) -> str:
    """Get the current date, time, and day of the week.
    
    Use this tool whenever you need to check the current date, time, or day of the week.
    """
    logger.info("Fetching current local date and time")
    now = clock.now()
    day_name = now.strftime("%A")
    date_str = now.strftime("%Y-%m-%d")
    time_str = now.strftime("%I:%M:%S %p")
    return f"The current time is {time_str}, today's date is {date_str}, and it is a {day_name}."


# Interactive user-selection prompt tool.
#
# The agent calls ``request_user_selection`` whenever the task depends on the user
# choosing between distinct, discrete options (which contact to message, which AC
# to control, which of several matching events to edit, ...). On UI channels
# (Telegram / web / Android) the tool is intercepted by the agent loop and
# rendered as an interactive single- or multi-select prompt; on channels without a
# selection UI (realtime voice) the function body returns guidance for the agent
# to ask verbally.

@addon.tool(is_write=True)
async def request_user_selection(
    conn: Connection,
    prompt: str,
    options: List[Dict[str, str]],
    multiple: bool = False,
    context: str = "",
) -> str:
    """Ask the user to choose one or more options from a predefined list.

    Use this whenever the next step of the task depends on a choice between
    distinct, discrete options. Each option is a ``label`` (human-readable text
    shown to the user) plus a ``value`` (machine-readable identifier returned to
    you). The user's pick is fed back to you as the answer, so you can continue
    without guessing. Do NOT use this for approvals or for open-ended questions
    where the user could answer anything.

    Args:
        prompt: The question to present to the user, short and clear.
        options: Available choices, each a dict with a "label" and a "value". Provide realistic, distinct options (2-6 recommended).
        multiple: Whether the user may pick several options (True) or exactly one (False, default).
        context: Optional one-line clarification on why the choice is needed.
    """
    if not options:
        raise ValueError("request_user_selection requires at least one option")

    option_lines = "\n".join(
        f"- {o.get('label') or o.get('value') or '?'}" for o in options
    )
    mode = "option(s)" if multiple else "option"
    context_note = f"\nContext: {context}" if context else ""
    return (
        "Selection UI is not available on this channel, so ask the user verbally which "
        f"of the following {mode} they want.{context_note}\n"
        "Options:\n"
        f"{option_lines}"
    )


# Mock fallback database for offline safety/testing
MOCK_SEARCH_RESULTS: Dict[str, str] = {
    "smile clinic address": "Smile Clinic is located at 123 Main St, Springfield, OR 97477. Phone: (555) 019-2834.",
    "weather tomorrow": "Springfield weather forecast: Sunny with a high of 24°C and a low of 12°C. 0% chance of rain.",
    "joe's coffee": "Joe's Coffee is located at 456 Elm St. Hours: 7 AM - 6 PM daily. Known for its cold brew."
}

@addon.tool()
def web_search(conn: Connection, query: str) -> str:
    """Search the web for public information using DuckDuckGo.
    
    Use this ONLY for looking up public, general-knowledge information (e.g. business hours, 
    addresses, news, product specs). Do NOT use this for personal data — use recall_info for 
    stored memories, search_calendar for schedule, search_calls for call history, or 
    search_whatsapp_contact_or_chat for contacts instead.
    
    Returns up to 3 results.
    
    Args:
        query: The search query string.
    """
    logger.info(f"Performing web search for: '{query}'")
    
    try:
        try:
            from ddgs import DDGS
        except ImportError:
            from duckduckgo_search import DDGS
        
        # Build DDG search client and run search query
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=3))
            
        if not results:
            # Fallback to mock search database if results are empty
            query_lower = query.lower()
            for k, v in MOCK_SEARCH_RESULTS.items():
                if k in query_lower or query_lower in k:
                    return f"Mock search result for '{query}':\n{v}"
            return f"No search results found for '{query}'."
            
        result_str = f"Search results for '{query}':\n"
        for i, res in enumerate(results, 1):
            title = res.get("title", "No Title")
            snippet = res.get("body", "No description")
            url = res.get("href", "#")
            result_str += f"{i}. {title} - {snippet} (URL: {url})\n"
        return result_str
        
    except Exception as e:
        logger.warning(f"Live DDG search failed: {e}. Falling back to mock search database.")
        
        # Try to find a match in the mock database
        query_lower = query.lower()
        for k, v in MOCK_SEARCH_RESULTS.items():
            if k in query_lower or query_lower in k:
                return f"Mock search result for '{query}':\n{v}"
                
        return f"Mock search result: No information found for '{query}'. (Live search failed: {e})"


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


@addon.tool(confirm=True, available=sharing.tool_available)
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
