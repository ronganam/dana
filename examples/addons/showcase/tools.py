"""The ``showcase`` tools. The schema the model sees comes from the type hints and
the docstring (every parameter under ``Args:``); ``conn`` is left out of it."""

import os
from typing import Any, Dict, List

from aiohttp import ClientSession, ClientTimeout

from core import clock, llm, undo
from core.addons import Approval, Connection
from core.users import User
from addons.showcase import service, store
from addons.showcase.addon import addon


# --- reference: tool ---
@addon.tool(is_write=True)
def showcase_add_plant(conn: Connection, name: str, every_days: int = 3) -> str:
    """Add a house plant to look after.

    Args:
        name: What the plant is called ("Basil", "the big fern").
        every_days: How many days it goes between waterings.
    """
    plant_id = store.add_plant(conn.user, name.strip(), every_days)
    undo.record(f"Add {name}", lambda: store.remove(conn.user, plant_id), source="showcase")
    return f"Added {name} (#{plant_id}), watered every {every_days} days."
# --- end ---


@addon.tool()
def showcase_plants(conn: Connection) -> List[Dict[str, Any]]:
    """List the user's house plants and when each was last watered."""
    return [{"id": p["id"], "name": p["name"], "every_days": p["every_days"], "last_watered": p["last_watered"]}
            for p in store.plants(conn.user)]


@addon.tool(is_write=True)
def showcase_water(conn: Connection, plant_id: int) -> str:
    """Mark a plant as watered now.

    Args:
        plant_id: The plant's id, from showcase_plants.
    """
    before = store.plant(conn.user, plant_id)
    if before is None:
        return "Error: no such plant."
    store.water(conn.user, plant_id, clock.now())
    undo.record(f"Water {before['name']}", lambda: store.unwater(conn.user, plant_id, before["last_watered"]),
                source="showcase")
    return f"Watered {before['name']}."


# --- reference: approval ---
def describe_forget(user: User, args: Dict[str, Any]) -> Approval:
    """The Approve card's words for one showcase_forget call."""
    p = store.plant(user, int(args.get("plant_id") or 0))
    return Approval(title=f"Forget {p['name'] if p else 'a plant'}", heading="Forget a plant", emoji="🪴",
                    question="Forget it and its watering history?")


@addon.tool(confirm=True, describe=describe_forget)
def showcase_forget(conn: Connection, plant_id: int) -> str:
    """Forget a plant for good (asks the user first).

    Args:
        plant_id: The plant's id, from showcase_plants.
    """
    return "Forgotten." if store.remove(conn.user, plant_id) else "Error: no such plant."
# --- end ---


@addon.tool()
async def showcase_tip(conn: Connection, plant: str) -> str:
    """A care tip for a kind of plant, from the plant service.

    Args:
        plant: The kind of plant ("fern", "basil").
    """
    try:
        return await service.tip(conn, plant)
    except service.ServiceError as e:
        return f"Error: {e}"


# --- reference: llm ---
@addon.tool()
async def showcase_poem(conn: Connection, plant_id: int) -> str:
    """A two-line poem about one of the user's plants.

    Args:
        plant_id: The plant's id, from showcase_plants.
    """
    p = store.plant(conn.user, plant_id)
    if p is None:
        return "Error: no such plant."
    return await llm.complete([{"role": "user", "content": f"Two short lines about a house plant called {p['name']}."}],
                              purpose="showcase_poem")
# --- end ---


@addon.tool()
async def showcase_worker_ping(conn: Connection) -> str:
    """Ask the showcase worker (its sidecar) whether it is up."""
    url = os.getenv("SHOWCASE_WORKER_URL", "http://127.0.0.1:8092").rstrip("/")
    async with ClientSession(timeout=ClientTimeout(total=5)) as session, session.get(f"{url}/ping") as r:
        return str((await r.json())["message"])
