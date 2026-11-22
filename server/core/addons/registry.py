"""Tools listed and executed for a user (docs/access.md, "Addon" → Rules), and
the addons' hooks collected for the core (../docs/addons.md, "The manifest").

A user's tools are the tools of the addons they have connected, minus those
whose ``needs`` are not all connected and those whose availability rule
(``@addon.tool(available=...)``) refuses them, in the fixed addon order and then the
order the tools are defined in (so the tool block stays cacheable). The same
check runs again at execution, whoever asks: a tool the user does not have is
refused exactly like a tool that does not exist.

Hooks (:func:`jobs`, :func:`services`, :func:`handlers`, :func:`channels`,
:func:`hooks`, :func:`hook_for`) come in the fixed addon order. Those asked
for a user come only from the addons the user has *active*
(:func:`active_addons`: connected, enabled on the server, and every addon the
addon ``depends`` on connected too); those asked without a user (``None``:
the server's own view, e.g. its self-checks) from every addon enabled on the
server. Composers look hooks up only through :func:`hooks` and
:func:`hook_for`; providers (``@addon.provide``) through :func:`providers`,
:func:`provider` and :func:`answer`, ranked: several addons may provide one
key. The scheduler checks jobs and services itself.
"""

import asyncio
import inspect
import logging
from typing import Any, Dict, FrozenSet, List, Mapping, Optional, Tuple

from core.users import User
from core.addons import state, store
from core.addons.addon import Addon, Tool, all_tools, find_tool, get_addon, all_addons, version
from core.addons.hooks import Channel, Handler, Hook, Job, Service
from core.addons.schema import flatten_for_realtime

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
    connected = frozenset(store.connected_addons(user))
    # Tools with an availability rule that refuses this user now (part of the cache key).
    refused = frozenset(t.name for t in _ruled_tools() if not _passes(t, user))
    key = (version(), connected, refused)
    hit = _lists.get(key)
    if hit is None:
        if len(_lists) > 64:
            _lists.clear()
        offered = tuple(t for k in all_addons() if k.name in connected for t in k.tools
                        if set(t.needs) <= connected and t.name not in refused)
        schemas = [t.schema for t in offered]
        realtime = [flatten_for_realtime(t.schema) for t in offered if get_addon(t.addon).realtime]
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
    without the tools of addons kept out of realtime voice
    (``Addon(realtime=False)``; do not modify the result)."""
    return _available(user)[2]


def has_tool(user: Optional[User], name: str) -> bool:
    """``user`` has tool ``name`` now: it exists, every addon it needs is
    connected for them and its availability rule (if any) allows them
    (False without a user)."""
    tool = find_tool(name)
    if user is None or tool is None:
        return False
    connected = store.connected_addons(user)
    return all(k in connected for k in tool.addons) and _passes(tool, user)


def in_realtime(user: Optional[User], name: str) -> bool:
    """``user`` has tool ``name`` and its addon is offered in realtime voice."""
    tool = find_tool(name)
    return tool is not None and get_addon(tool.addon).realtime and has_tool(user, name)


async def execute(user: Optional[User], name: str, arguments: Mapping[str, Any]) -> Any:
    """Run tool ``name`` for ``user`` with ``arguments``: the user must have
    the tool's addon and every addon it needs connected; the function gets the
    user's connection as ``conn``.

    When the addon says the connection is blocked (``Addon(blocked=)``)
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
            why = "not available to them now" if not _passes(tool, user) else f"needs {', '.join(tool.addons)}"
            logger.warning(f"Refused tool {name} for user {user.id}: {why}")
        raise ToolNotAvailable(name)
    conn = store.require(user, tool.addon)
    addon = get_addon(tool.addon)
    reason = addon.blocked(conn) if addon.blocked is not None else None
    if reason:
        logger.info(f"Tool {name} not run for user {user.id}: the {tool.addon} connection is blocked")
        return f"Error: {reason}"
    if inspect.iscoroutinefunction(tool.func):
        return await tool.func(conn, **arguments)
    return tool.func(conn, **arguments)


def prompt_section(user: Optional[User], name: str) -> str:
    """Addon ``name``'s section of the agent's system prompt for ``user``
    (``Addon(prompt_section=)``): "" without a user, when the addon is
    unknown, has no section or is not connected for them, and when the
    section fails (logged). The caller decides where each addon's section goes."""
    if user is None:
        return ""
    try:
        addon = get_addon(name)
    except KeyError:
        return ""
    if addon.prompt_section is None or not store.is_connected(user, name):
        return ""
    try:
        text = addon.prompt_section(user)
    except Exception:
        logger.exception(f"prompt section of {name} failed for user {user.id}; continuing without it")
        return ""
    return text if isinstance(text, str) else ""


def prompt_parts(user: Optional[User], slot: str) -> List[str]:
    """The texts of every addon's :class:`~core.addons.addon.PromptPart`
    for ``slot`` that applies to ``user`` (their addon and every addon it needs
    connected), ordered by rank, then the fixed addon order (empty without a user)."""
    if user is None:
        return []
    connected = set(store.connected_addons(user))
    found = [(part.rank, i, part.text) for i, k in enumerate(all_addons()) if k.name in connected
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


# --- Hooks ------------------------------------------------------------------------------

def jobs() -> List[Job]:
    """Every ``@addon.job`` of every addon."""
    return [j for a in all_addons() for j in a.jobs]


def job(key: str) -> Job:
    """The job ``<addon>.<name>`` (tests run one pass with ``scheduler.run_once``).

    Raises:
        KeyError: no such job.
    """
    found = next((j for j in jobs() if j.key == key), None)
    if found is None:
        raise KeyError(key)
    return found


def services() -> List[Service]:
    """Every ``@addon.service`` of every addon."""
    return [s for a in all_addons() for s in a.services]


def active_addons(user: User) -> Tuple[str, ...]:
    """The addons ``user`` has that are active for them: connected (or
    ``everyone``), enabled on the server, and with every addon they
    ``depends`` on connected too; in the fixed addon order."""
    connected = store.connected_addons(user)
    have = set(connected)
    return tuple(n for n in connected if set(get_addon(n).depends) <= have)


def handlers(event: str, user: User) -> List[Handler]:
    """The ``@addon.on(event)`` handlers of the addons active for ``user``."""
    active = set(active_addons(user))
    return [h for a in all_addons() if a.name in active for h in a.handlers if h.event == event]


def channels(user: User) -> List[Channel]:
    """The ``@addon.channel`` channels of the addons active for ``user``."""
    active = set(active_addons(user))
    return [c for a in all_addons() if a.name in active for c in a.channels]


def _scope(user: Optional[User]) -> List[Addon]:
    """The addons whose hooks ``user`` gets (None: every addon enabled on the server)."""
    if user is None:
        off = state.disabled()
        return [a for a in all_addons() if a.name not in off]
    active = set(active_addons(user))
    return [a for a in all_addons() if a.name in active]


def hooks(kind: str, user: Optional[User]) -> List[Hook]:
    """Every composer hook of ``kind`` (``now_cards``, ``briefing``…) of the
    addons active for ``user``, in addon order; ``user`` None: of every addon
    enabled on the server."""
    return [h for a in _scope(user) for h in a.hooks if h.kind == kind]


def hook_for(kind: str, key: str, user: Optional[User]) -> Optional[Hook]:
    """The hook of ``kind`` declared for ``key`` (a card prefix, a section or
    check key, an item type, a context kind) when it is ``user``'s (``user``
    None: of any addon enabled on the server); None when no enabled addon
    declares it.

    Raises:
        NotConnected: an enabled addon declares it, but it is not active for ``user``.
    """
    off = state.disabled()
    owner = next((h for a in all_addons() if a.name not in off for h in a.hooks if h.kind == kind and h.key == key),
                 None)
    if owner is None or user is None:
        return owner
    if owner.addon not in active_addons(user):
        raise store.NotConnected(user, owner.addon)
    return owner


def providers(key: str, user: Optional[User]) -> List[Hook]:
    """Every ``@addon.provide(key)`` hook of the addons active for ``user``
    (``user`` None: of every addon enabled on the server), ranked: lower
    ``rank`` first, ties in the fixed addon order."""
    found = [h for h in hooks("provide", user) if h.key == key]
    return sorted(found, key=lambda h: h.option("rank", 50))


def provider(key: str, user: Optional[User]) -> Optional[Hook]:
    """The highest-ranked ``@addon.provide(key)`` hook active for ``user``
    (None when there is none). A key several addons answer is read with
    :func:`answer`, which falls back to the next one."""
    ranked = providers(key, user)
    return ranked[0] if ranked else None


def source_of(hook: Hook) -> str:
    """The source a provider hook answers as (its ``source=``, else its addon's name)."""
    return str(hook.option("source", "") or hook.addon)


async def answer(key: str, user: User, *args: Any, **kwargs: Any) -> Tuple[Optional[Hook], Any]:
    """Ask ``user``'s providers of ``key`` in rank order and return the first
    that answers (anything but None) with its answer; (None, None) when none
    does. A sync provider runs in a worker thread; one that raises is logged
    and the next is asked."""
    for hook in providers(key, user):
        try:
            if inspect.iscoroutinefunction(hook.func):
                value = await hook.func(user, *args, **kwargs)
            else:
                value = await asyncio.to_thread(hook.func, user, *args, **kwargs)
        except Exception:
            logger.warning(f"provider {key} of {hook.addon} failed for user {user.id}; asking the next",
                           exc_info=True)
            continue
        if value is not None:
            return hook, value
    return None, None
