"""Tools every user has (the ``essentials`` addon): the current time, asking the user
to pick between options, the weather at Home, web search, sharing an item with another user
(offered only on a server with more than one user) and running Python in the sandbox
(``run_code``: core/sandbox.py, docs/sandbox.md)."""

import asyncio
import json
import logging
from typing import Any, Dict, List, Optional

from core import clock, sandbox, users
from core.text import untrusted
from core.users import User
from core.addons import Approval, Connection, registry, sharing, store
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


@addon.tool()
async def get_weather(conn: Connection) -> str:
    """The weather now and today at the user's Home zone (condition, temperature, high / low, rain chance). Use this for any "what's the weather" — not web search."""
    from core import briefing
    w = await briefing.weather(conn.user)
    if not w or w.get("temperature") is None:
        return "No weather: the user has no Home zone (Settings → Zones) or the weather service did not answer."
    unit = w.get("unit") or "°"
    parts = [f"{(w.get('condition') or '').capitalize()}, {w['temperature']}{unit}"]
    if w.get("high") is not None:
        parts.append(f"high {w['high']}{unit}, low {w.get('low')}{unit}")
    if w.get("rain_chance") is not None:
        parts.append(f"rain chance {w['rain_chance']}%")
    return "Weather at Home: " + "; ".join(parts) + "."


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


CODE_TIMEOUT = 30  # seconds, default
CODE_MAX_TIMEOUT = 120
# Never from a script: itself, and the pick prompt (its UI belongs to the agent loop).
NOT_FROM_CODE = ("run_code", "request_user_selection")


def _code_refusal(user: User, name: str, arguments: Dict[str, Any]) -> Optional[str]:
    """Why a script may not call ``name`` (None: it may). Scripts get reads and changes Dana can
    undo; a tool that asks the user first or acts outside Dana stays with the agent."""
    if name in NOT_FROM_CODE or not registry.has_tool(user, name):
        return f"no tool '{name}' for scripts"
    if registry.acts_outside(name) or registry.needs_confirmation(name, arguments):
        return f"{name} acts outside Dana or asks the user first: call it yourself, outside run_code"
    return None


@addon.tool(is_write=True)
async def run_code(conn: Connection, code: str, timeout: Optional[int] = None) -> str:
    """Run Python 3 in a sandbox and get back what it prints. For calculations, parsing or reshaping data, many similar tool calls in a loop, and edge cases no tool covers. Prefer a dedicated tool when one fits.

    The script calls your tools as `tools.NAME(arg=...)` (or `call("NAME", arg=...)`); each returns the tool's text. Reads and changes Dana can undo are allowed; tools that ask first or act outside Dana (sending, paying, deleting) raise ToolError: call those yourself. `/work` is your scratch folder, kept a day. Standard library only, no network, no input().

    Args:
        code: The Python code; print() what you need back.
        timeout: Seconds (default 30, max 120).
    """
    user = conn.user
    untrusted_seen = False

    async def call_tool(name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        nonlocal untrusted_seen
        refusal = _code_refusal(user, name, arguments)
        if refusal:
            return {"error": refusal}
        try:
            result = await registry.execute(user, name, arguments)
        except registry.ToolNotAvailable:
            return {"error": f"no tool '{name}' for scripts"}
        except TypeError as e:
            return {"error": f"{name}: {e}"}
        except Exception as e:
            logger.warning(f"run_code: {name} failed: {e}", exc_info=True)
            return {"error": f"{name} failed: {e}"}
        if registry.returns_untrusted(name):
            untrusted_seen = True
        return {"result": result if isinstance(result, str) else json.dumps(result, default=str, ensure_ascii=False)}

    seconds = max(1, min(int(timeout or CODE_TIMEOUT), CODE_MAX_TIMEOUT))
    try:
        run = await sandbox.run(code, user_id=user.id, call_tool=call_tool, timeout=seconds)
    except RuntimeError as e:
        return f"Error: {e}"
    parts = []
    if run.timed_out:
        parts.append(f"Error: stopped after {seconds} s (raise timeout, up to {CODE_MAX_TIMEOUT}, or do less per run).")
    elif run.exit_code:
        parts.append(f"Error: exit code {run.exit_code}.")
    if run.stdout.strip():
        parts.append(run.stdout.rstrip())
    if run.stderr.strip():
        parts.append(run.stderr.rstrip() if run.exit_code else f"stderr:\n{run.stderr.rstrip()}")
    if run.files:
        parts.append("/work: " + ", ".join(f"{name} ({size:,} B)" for name, size in run.files[:20])
                     + (f" …and {len(run.files) - 20} more" if len(run.files) > 20 else ""))
    out = "\n\n".join(parts) or "(no output: print() what you need)"
    # A tool that returned other people's text fed the script: its output is data, never instructions.
    return untrusted("a run_code script that read text written by other people", out) if untrusted_seen else out
