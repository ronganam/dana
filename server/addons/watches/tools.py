"""Dana's watch tools. Creating or changing a watch asks first in a turn that
carries text others wrote (``acts_outside``: a watch acts later on its own);
every write runs at once with an Undo."""

import logging
from typing import Any, Dict, Optional

from core import undo
from core.addons import Connection
from addons.watches import compiler, service, store
from addons.watches.addon import addon

logger = logging.getLogger(__name__)


def _understood(row: Dict[str, Any], compiled: Optional[compiler.Compiled] = None) -> str:
    text = service.describe(row)
    if compiled is not None and any(v.get("over_budget") for v in compiled.load.values()):
        text += "\n  note: a semantic check here may run often (over the daily budget in the last 2 weeks)."
    return text


@addon.tool(is_write=True, acts_outside=True)
async def create_watch(conn: Connection, request: str) -> str:
    """Set up a watch from the user's own words: Dana acts when something happens or by a deadline ('make sure the transfer from Maya arrives by tomorrow', 'when the dishwasher finishes ask Gal to empty it', 'remind me at 20:30, 30 min before, or when I get in the car'). A reminder also becomes a task.

    Args:
        request: What the user asked, in their own words and language (with the times and names they gave).
    """
    user = conn.user
    try:
        row, compiled = await service.create_from_text(user, request)
    except compiler.CompileError as e:
        return f"Error: could not build a watch from that: {e}"
    undo.record(f"Added watch “{row['title']}”", lambda: store.delete(user, row["id"]), source="create_watch")
    return "Watching: " + _understood(row, compiled)


@addon.tool()
async def list_watches(conn: Connection, include_closed: bool = False) -> str:
    """List the user's watches (active and paused; with include_closed also done and expired) with their triggers in words.

    Args:
        include_closed: Also list watches that are done or expired.
    """
    statuses = None if include_closed else [store.ACTIVE, store.PAUSED]
    rows = store.list_for(conn.user, statuses)
    if not rows:
        return "No watches."
    return "\n".join(service.describe(r) for r in rows[:30])


@addon.tool(is_write=True, acts_outside=True)
async def update_watch(conn: Connection, watch_id: str, change: str) -> str:
    """Change a watch in plain words ('until Friday instead', 'also when I leave work', 'send it to Gal too').

    Args:
        watch_id: The watch's ID (from list_watches).
        change: What to change, in the user's words.
    """
    user = conn.user
    row = store.get(user, watch_id)
    if row is None:
        return f"Error: no watch {watch_id}."
    try:
        after = await service.change(user, row, change)
    except compiler.CompileError as e:
        return f"Error: could not change it: {e}"
    undo.record(f"Changed watch “{after['title']}”",
                lambda: store.update(user, watch_id, spec=row["spec"], title=row["title"], kind=row["kind"],
                                     request=row["request"], status=row["status"], next_time=row["next_time"]),
                source="update_watch")
    return "Changed: " + _understood(after)


async def _status(conn: Connection, watch_id: str, status: str, verb: str) -> str:
    user = conn.user
    row = store.get(user, watch_id)
    if row is None:
        return f"Error: no watch {watch_id}."
    after = service.set_status(user, row, status)
    undo.record(f"{verb} watch “{row['title']}”",
                lambda: store.update(user, watch_id, status=row["status"], closed_at=row["closed_at"],
                                     next_time=row["next_time"]), source=f"{verb.lower()}_watch")
    return f"{verb} “{after['title']}” (ID: {watch_id})."


@addon.tool(is_write=True)
async def pause_watch(conn: Connection, watch_id: str) -> str:
    """Pause a watch: nothing fires until it is resumed.

    Args:
        watch_id: The watch's ID (from list_watches).
    """
    return await _status(conn, watch_id, store.PAUSED, "Paused")


@addon.tool(is_write=True)
async def resume_watch(conn: Connection, watch_id: str) -> str:
    """Resume a paused (or finished) watch.

    Args:
        watch_id: The watch's ID (from list_watches).
    """
    return await _status(conn, watch_id, store.ACTIVE, "Resumed")


@addon.tool(is_write=True)
async def delete_watch(conn: Connection, watch_id: str) -> str:
    """Delete a watch (with Undo).

    Args:
        watch_id: The watch's ID (from list_watches).
    """
    row = store.get(conn.user, watch_id)
    if row is None or service.delete(conn.user, watch_id) is None:
        return f"Error: no watch {watch_id}."
    return f"Deleted watch “{row['title']}”."
