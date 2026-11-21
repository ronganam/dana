"""Tools listed and executed for a user (docs/access.md, "Integration" → Rules).

A user's tools are the tools of the kinds they have connected, minus those
whose ``needs`` are not all connected and those whose availability rule
(``@kind.tool(available=...)``) refuses them, in the fixed kind order and then the
order the tools are defined in (so the tool block stays cacheable). The same
check runs again at execution, whoever asks: a tool the user does not have is
refused exactly like a tool that does not exist.
"""

import inspect
import logging
from typing import Any, Dict, FrozenSet, List, Mapping, Optional, Tuple

from core.users import User
from integrations.framework import store
from integrations.framework.kind import Tool, all_tools, find_tool, get_kind, kinds, version
from integrations.framework.schema import flatten_for_realtime

logger = logging.getLogger(__name__)


class ToolNotAvailable(ValueError):
    """The tool does not exist, or the user does not have it. The message is
    the same either way (``Tool 'x' is not registered.``), so a refusal never
    reveals whether someone else has the tool."""

    def __init__(self, name: str) -> None:
        super().__init__(f"Tool '{name}' is not registered.")
        self.name = name


_ListKey = Tuple[int, FrozenSet[str], FrozenSet[str]]
_lists: Dict[_ListKey, Tuple[Tuple[Tool, ...], List[Dict[str, Any]], List[Dict[str, Any]]]] = {}


def _passes(tool: Tool, user: User) -> bool:
    """The tool's own availability rule (if any) allows ``user`` now."""
    if tool.available is None:
        return True
    try:
        return bool(tool.available(user))
    except Exception:
        logger.warning(f"availability rule of {tool.name} failed; not offering it", exc_info=True)
        return False


_ruled: Tuple[int, Tuple[Tool, ...]] = (-1, ())


def _ruled_tools() -> Tuple[Tool, ...]:
    """Tools that have an availability rule (recomputed when tools change)."""
    global _ruled
    if _ruled[0] != version():
        _ruled = (version(), tuple(t for t in all_tools() if t.available is not None))
    return _ruled[1]


def _available(user: User) -> Tuple[Tuple[Tool, ...], List[Dict[str, Any]], List[Dict[str, Any]]]:
    connected = frozenset(store.connected_kinds(user))
    # Tools with an availability rule that refuses this user now (part of the cache key).
    refused = frozenset(t.name for t in _ruled_tools() if not _passes(t, user))
    key = (version(), connected, refused)
    hit = _lists.get(key)
    if hit is None:
        if len(_lists) > 64:
            _lists.clear()
        offered = tuple(t for k in kinds() if k.kind in connected for t in k.tools
                        if set(t.needs) <= connected and t.name not in refused)
        schemas = [t.schema for t in offered]
        realtime = [flatten_for_realtime(t.schema) for t in offered if get_kind(t.kind).realtime]
        hit = _lists[key] = (offered, schemas, realtime)
    return hit


def tools_for(user: User) -> Tuple[Tool, ...]:
    """The tools ``user`` has, in order."""
    return _available(user)[0]


def tool_schemas(user: User) -> List[Dict[str, Any]]:
    """``user``'s tool list for chat models (do not modify the result)."""
    return _available(user)[1]


def realtime_tool_schemas(user: User) -> List[Dict[str, Any]]:
    """``user``'s tool list in the flat shape of the OpenAI Realtime API,
    without the tools of kinds kept out of realtime voice
    (``Integration(realtime=False)``; do not modify the result)."""
    return _available(user)[2]


def has_tool(user: Optional[User], name: str) -> bool:
    """``user`` has tool ``name`` now: it exists, every kind it needs is
    connected for them and its availability rule (if any) allows them
    (False without a user)."""
    tool = find_tool(name)
    if user is None or tool is None:
        return False
    connected = store.connected_kinds(user)
    return all(k in connected for k in tool.kinds) and _passes(tool, user)


def in_realtime(user: Optional[User], name: str) -> bool:
    """``user`` has tool ``name`` and its kind is offered in realtime voice."""
    tool = find_tool(name)
    return tool is not None and get_kind(tool.kind).realtime and has_tool(user, name)


async def execute(user: Optional[User], name: str, arguments: Mapping[str, Any]) -> Any:
    """Run tool ``name`` for ``user`` with ``arguments``: the user must have
    the tool's kind and every kind it needs connected; the function gets the
    user's connection as ``conn``.

    When the kind says the connection is blocked (``Integration(blocked=)``)
    the result is ``"Error: <reason>"`` and the function is not called.

    Raises:
        RuntimeError: no user (a tool never runs without one).
        ToolNotAvailable: the user does not have the tool (or it does not
            exist); the message is the same either way.
    """
    if user is None:
        raise RuntimeError(f"Tool '{name}' was asked for outside any user's work; tools run only for a user.")
    tool = find_tool(name)
    if tool is None or not has_tool(user, name):
        if tool is not None:
            why = "not available to them now" if not _passes(tool, user) else f"needs {', '.join(tool.kinds)}"
            logger.warning(f"Refused tool {name} for user {user.id}: {why}")
        raise ToolNotAvailable(name)
    conn = store.require(user, tool.kind)
    integration = get_kind(tool.kind)
    reason = integration.blocked(conn) if integration.blocked is not None else None
    if reason:
        logger.info(f"Tool {name} not run for user {user.id}: the {tool.kind} connection is blocked")
        return f"Error: {reason}"
    if inspect.iscoroutinefunction(tool.func):
        return await tool.func(conn, **arguments)
    return tool.func(conn, **arguments)


def prompt_section(user: Optional[User], kind: str) -> str:
    """Kind ``kind``'s section of the agent's system prompt for ``user``
    (``Integration(prompt_section=)``): "" without a user, when the kind is
    unknown, has no section or is not connected for them, and when the
    section fails (logged). The caller decides where each kind's section goes."""
    if user is None:
        return ""
    try:
        integration = get_kind(kind)
    except KeyError:
        return ""
    if integration.prompt_section is None or not store.is_connected(user, kind):
        return ""
    try:
        text = integration.prompt_section(user)
    except Exception:
        logger.exception(f"prompt section of {kind} failed for user {user.id}; continuing without it")
        return ""
    return text if isinstance(text, str) else ""


def prompt_parts(user: Optional[User], slot: str) -> List[str]:
    """The texts of every kind's :class:`~integrations.framework.kind.PromptPart`
    for ``slot`` that applies to ``user`` (their kind and every kind it needs
    connected), ordered by rank, then the fixed kind order (empty without a user)."""
    if user is None:
        return []
    connected = set(store.connected_kinds(user))
    found = [(part.rank, i, part.text) for i, k in enumerate(kinds()) if k.kind in connected
             for part in k.prompt_parts if part.slot == slot and set(part.needs) <= connected]
    return [text for _, _, text in sorted(found, key=lambda f: (f[0], f[1]))]


def is_write_tool(name: str) -> bool:
    """The tool changes something (``is_write`` / ``confirm``)."""
    tool = find_tool(name)
    return bool(tool and tool.is_write)


def acts_outside(name: str) -> bool:
    """The tool acts outside Dana with no undo (``acts_outside`` flag)."""
    tool = find_tool(name)
    return bool(tool and tool.acts_outside)


def returns_untrusted(name: Optional[str]) -> bool:
    """The tool's result carries text written by other people (``untrusted_result`` flag)."""
    tool = find_tool(name or "")
    return bool(tool and tool.untrusted_result)


def needs_confirmation(name: str, arguments: Optional[Dict[str, Any]] = None, *, untrusted: bool = False) -> bool:
    """Whether this call must wait for the user's Approve (sends to another
    person or destroys something). ``arguments`` = None answers "can this
    tool ever ask" (used when no arguments are at hand).

    ``untrusted``: the turn carries content the user did not write (the phone
    screen they shared, an email a tool read). Then every ``acts_outside`` tool asks too, so text on
    a web page or in a chat can never open the gate on its own. It is per
    call, never global: the agent loop passes its run's flag."""
    tool = find_tool(name)
    if tool is None:
        return False
    if untrusted and tool.acts_outside:
        return True
    rule = tool.confirm
    if callable(rule):
        if arguments is None:
            return True
        try:
            return bool(rule(arguments or {}))
        except Exception:
            logger.warning(f"confirm rule of {name} failed; asking to be safe", exc_info=True)
            return True
    return bool(rule)
