"""The Home Assistant home map: the entities exposed to the assistant (ids,
names, aliases; no state), cached per connection for the user's system
prompt (the kind's prompt section)."""

import asyncio
import logging
import os
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Set, Tuple

from core import users
from core.users import User
from integrations.framework import Connection, store
from integrations.homeassistant import client as ha_api
from integrations.homeassistant.config import KIND

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Home map — the exposed entities for the agent's system prompt
# ---------------------------------------------------------------------------
# A stable listing (entity_id, name, aliases; no state) so the model can call
# call_ha_service without a discovery round trip. Reading it never waits on HA:
# get_home_map() returns the cached text at once and refreshes in the
# background once it is older than HA_HOME_MAP_TTL_SECONDS. One cache per
# connection (user + host + token): a user never sees another user's map.

HOME_MAP_TTL_SECONDS = float(os.getenv("HA_HOME_MAP_TTL_SECONDS", "600"))
# After a failed refresh, wait at most this long before trying again.
HOME_MAP_RETRY_SECONDS = 60.0
# Upper bound for one refresh, so a hung HA connection cannot block later ones.
HOME_MAP_FETCH_TIMEOUT = 30.0


@dataclass
class HomeMapCache:
    """One connection's cached map."""
    text: str = ""
    next_refresh: float = 0.0  # time.monotonic(); 0 = refresh on first read
    task: Optional["asyncio.Task[str]"] = None


_caches: Dict[Tuple[str, str, str], HomeMapCache] = {}


def cache_for(conn: Connection) -> HomeMapCache:
    """The cache of ``conn``'s Home Assistant (a new token or host starts empty)."""
    key = ha_api.signature(conn)
    cache = _caches.get(key)
    if cache is None:
        for old in [k for k in _caches if k[0] == conn.user.id]:
            del _caches[old]  # the user's previous host / token
        cache = _caches[key] = HomeMapCache()
    return cache


def format_home_map(exposed_ids: Set[str], registry_map: Dict[str, Dict[str, Any]],
                    states_map: Optional[Dict[str, Dict[str, Any]]] = None) -> str:
    """Render exposed entities as `entity_id | name  aliases: ...` lines grouped
    by domain. States are used only for the friendly-name fallback, never shown."""
    states_map = states_map or {}
    by_domain: Dict[str, List[str]] = {}
    for eid in sorted(exposed_ids):
        reg = registry_map.get(eid, {"name": eid, "aliases": []})
        name = reg["name"]
        if name == eid:
            name = (states_map.get(eid) or {}).get("attributes", {}).get("friendly_name") or eid
        line = f"{eid} | {name}"
        if reg["aliases"]:
            line += f"  aliases: {', '.join(reg['aliases'])}"
        by_domain.setdefault(eid.split(".")[0], []).append(line)

    lines: List[str] = []
    for domain in sorted(by_domain):
        lines.append(f"[{domain}]")
        lines.extend(f"  {line}" for line in by_domain[domain])
    return "\n".join(lines)


async def refresh_home_map(conn: Connection) -> str:
    """Fetch ``conn``'s home map from its HA and cache it. A failure keeps the
    old map and is logged, never raised. Returns the cached map."""
    cache = cache_for(conn)
    started = time.monotonic()
    try:
        registry_result, states_result = await asyncio.wait_for(
            asyncio.gather(ha_api.fetch_registry(conn), ha_api.fetch_all_states(conn), return_exceptions=True),
            timeout=HOME_MAP_FETCH_TIMEOUT,
        )
        if isinstance(registry_result, BaseException):
            raise registry_result
        exposed_ids, registry_map = registry_result
        # States only supply friendly-name fallbacks; the map works without them.
        states_map = states_result if isinstance(states_result, dict) else None
        new_map = format_home_map(exposed_ids, registry_map, states_map)
    except asyncio.CancelledError:
        raise
    except Exception as e:
        cache.next_refresh = time.monotonic() + min(HOME_MAP_TTL_SECONDS, HOME_MAP_RETRY_SECONDS)
        err_msg = str(e).strip() or type(e).__name__
        logger.warning(f"[HA] Home map refresh for user {conn.user.id} failed, keeping the cached one: {err_msg}")
        return cache.text

    if new_map != cache.text:
        logger.info(f"[HA] Home map of user {conn.user.id} updated: {len(exposed_ids)} entities, "
                    f"{len(new_map)} chars ({time.monotonic() - started:.1f}s).")
    cache.text = new_map
    cache.next_refresh = time.monotonic() + HOME_MAP_TTL_SECONDS
    return cache.text


def _schedule_refresh(conn: Connection) -> Optional["asyncio.Task[str]"]:
    """Start a background refresh unless one is running or there is no loop."""
    cache = cache_for(conn)
    if cache.task is not None and not cache.task.done():
        return cache.task
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return None
    with users.acting_as(conn.user):
        cache.task = loop.create_task(refresh_home_map(conn))
    return cache.task


def get_home_map(conn: Connection) -> str:
    """``conn``'s cached home map ("" when nothing is cached yet). Never
    waits: a stale map is returned as is and refreshed in the background."""
    cache = cache_for(conn)
    if time.monotonic() >= cache.next_refresh:
        _schedule_refresh(conn)
    return cache.text


def cached_text(user: Optional[User]) -> str:
    """``user``'s cached home map ("" without the kind)."""
    conn = store.get(user, KIND) if user is not None else None
    return get_home_map(conn) if conn is not None else ""


def prompt_section(user: User) -> str:
    """The kind's section of ``user``'s system prompt: their home map ("" when
    nothing is cached yet). Never waits on HA."""
    text = cached_text(user)
    if not text:
        return ""
    return (
        "\n\n--- HOME DEVICES (Home Assistant) ---\n"
        "The smart-home entities exposed to you, as `entity_id | name  aliases`. These ids are verified: "
        "use them directly with call_ha_service. Call get_ha_exposed_entities only when you need a "
        "device's current state, or when the device asked for is not in this list.\n"
        f"{text}\n"
        "-----------------------------------"
    )


def warm_home_map(conn: Connection) -> Optional["asyncio.Task[str]"]:
    """Fill ``conn``'s cache in the background."""
    return _schedule_refresh(conn)


def warm_all() -> List["asyncio.Task[str]"]:
    """At startup: fill the cache of every user who has Home Assistant, in the background."""
    tasks: List["asyncio.Task[str]"] = []
    for uid in store.users_with(KIND):
        user = users.get(uid)
        conn = store.get(user, KIND) if user is not None else None
        if conn is None:
            continue
        task = warm_home_map(conn)
        if task is not None:
            tasks.append(task)
    if not tasks:
        logger.info("[HA] Nobody has Home Assistant connected; no home map in any prompt.")
    return tasks
