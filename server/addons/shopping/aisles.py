"""Aisles: the fixed categories in store order and how an item gets one
(docs/shopping.md, "Aisles").

An item's aisle is, in this order: what the list's people chose for that
name before (``shopping_aisle_choices``), what the model said about that
name before (``shopping_aisles``, one shared table: name key → aisle, no
user data), else ``other`` while a background model call
(``core/llm.complete_json``, job ``shopping``, purpose ``shopping_category``)
asks about every unknown name of one add at once. The same call also asks
about items already on the list to buy that still wait under ``other`` with
no known aisle (``waiting``: items from before the upgrade, or a failed
call), at most :data:`WAITING` of them, so nothing stays there for ever.
When the answer arrives the items still under ``other`` move and the list's
version rises. The call runs as the user who added (their usage row), never
blocks or fails the add, and is skipped without a model key; a failed call
leaves the items under ``other`` (the next add asks again).
"""

import asyncio
import logging
import sqlite3
from typing import Dict, List, Optional, Set

from core import llm, usage, users
from core.users import User
from addons.shopping import events
from addons.shopping.names import name_key
from addons.shopping.values import iso, utc_now

logger = logging.getLogger(__name__)

# (key, name) in walking order through a supermarket. Clients take this from
# every list payload (``categories``); nobody keeps a copy.
CATEGORIES = (
    ("produce", "Fruit & vegetables"),
    ("bakery", "Bread & bakery"),
    ("dairy", "Dairy & eggs"),
    ("meat", "Meat & fish"),
    ("pantry", "Pantry"),
    ("snacks", "Snacks & sweets"),
    ("drinks", "Drinks"),
    ("frozen", "Frozen"),
    ("household", "Cleaning & household"),
    ("care", "Personal care & pharmacy"),
    ("baby", "Baby"),
    ("pets", "Pets"),
    ("other", "Other"),
)
KEYS = tuple(k for k, _ in CATEGORIES)
OTHER = "other"
RANK = {k: i for i, k in enumerate(KEYS)}
NAMES = dict(CATEGORIES)
JOB = "shopping"
PURPOSE = "shopping_category"
WAITING = 30   # items waiting under "other" asked about along with one add


def payload() -> List[Dict[str, str]]:
    """``categories`` of a list payload: ``[{key, name}]`` in aisle order."""
    return [{"key": k, "name": n} for k, n in CATEGORIES]


def known(conn: sqlite3.Connection, list_id: str, key: str) -> Optional[str]:
    """The aisle a name already has: the list's choice, else the shared table (None: unknown)."""
    row = conn.execute("SELECT category FROM shopping_aisle_choices WHERE list_id = ? AND name_key = ?",
                       (list_id, key)).fetchone()
    if row is None:
        row = conn.execute("SELECT category FROM shopping_aisles WHERE name_key = ?", (key,)).fetchone()
    return row["category"] if row and row["category"] in RANK else None


def waiting(conn: sqlite3.Connection, list_id: str, skip: Set[str], limit: int = WAITING) -> Dict[str, str]:
    """Items to buy on the list still under ``other`` whose name has no known
    aisle (no choice of the list, nothing in the shared table), oldest first:
    ``{name key: name}``, at most ``limit``, leaving out the keys in ``skip``."""
    rows = conn.execute(
        "SELECT name_key, name FROM shopping_items i WHERE list_id = ? AND checked = 0 AND category = ? "
        "AND NOT EXISTS (SELECT 1 FROM shopping_aisle_choices c WHERE c.list_id = i.list_id AND c.name_key = i.name_key) "
        "AND NOT EXISTS (SELECT 1 FROM shopping_aisles a WHERE a.name_key = i.name_key) ORDER BY seq",
        (list_id, OTHER)).fetchall()
    found: Dict[str, str] = {}
    for r in rows:
        if r["name_key"] not in skip and r["name_key"] not in found:
            found[r["name_key"]] = r["name"]
            if len(found) >= limit:
                break
    return found


def remember_choice(conn: sqlite3.Connection, list_id: str, key: str, category: str) -> None:
    """A person on the list chose ``category`` for this name (used for the list from now on)."""
    conn.execute("INSERT INTO shopping_aisle_choices (list_id, name_key, category) VALUES (?, ?, ?) "
                 "ON CONFLICT(list_id, name_key) DO UPDATE SET category = excluded.category", (list_id, key, category))


# --- The model call -------------------------------------------------------------------

_tasks: Set["asyncio.Task[None]"] = set()


def _prompt(names: List[str]) -> str:
    aisles = "\n".join(f"- {k}: {n}" for k, n in CATEGORIES)
    listed = "\n".join(f"- {n}" for n in names)
    return ("You sort grocery shopping-list items into supermarket aisles. The names may be in Hebrew or "
            "English, with typos or brand names.\n\nAisles:\n" + aisles +
            "\n\nFor each item below give the one aisle it is usually found in; use 'other' only when none "
            "fits. Answer with every item exactly as written.\n\nItems:\n" + listed)


def _schema() -> Dict:
    return {"type": "object", "properties": {"items": {"type": "array", "items": {
        "type": "object", "properties": {"name": {"type": "string"}, "aisle": {"type": "string", "enum": list(KEYS)}},
        "required": ["name", "aisle"]}}}, "required": ["items"]}


def look_up_later(user: User, list_id: str, names: Dict[str, str]) -> None:
    """Ask the model about ``names`` (key → name as typed) in the background,
    as ``user``. Nothing happens without a running event loop or a model key."""
    if not names:
        return
    if not llm.configured(JOB):
        logger.info(f"Shopping aisles: no model key for {llm.model(JOB)}; {len(names)} item(s) stay under Other")
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    task = loop.create_task(_look_up(user, list_id, dict(names)))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


async def _look_up(user: User, list_id: str, names: Dict[str, str]) -> None:
    try:
        with users.acting_as(user), usage.scope(purpose=PURPOSE, ref=f"shopping_list:{list_id}"):
            data = await llm.complete_json(_prompt(list(names.values())), _schema(), job=JOB, purpose=PURPOSE,
                                           ref=f"shopping_list:{list_id}", source="background")
    except Exception as e:
        logger.warning(f"Shopping aisles: lookup of {len(names)} name(s) failed: {e}")
        return
    found: Dict[str, str] = {}
    for entry in data.get("items") or []:
        if isinstance(entry, dict) and entry.get("aisle") in RANK:
            key = name_key(str(entry.get("name") or ""))
            if key in names:
                found[key] = entry["aisle"]
    if found:
        settle(list_id, found)


def settle(list_id: str, found: Dict[str, str]) -> None:
    """Store the model's aisles for these names and move the list's items that
    still wait under ``other`` (the list's version rises when one moved)."""
    now = iso(utc_now())
    with events.change() as tx:
        for key, category in found.items():
            tx.conn.execute("INSERT INTO shopping_aisles (name_key, category, updated_at) VALUES (?, ?, ?) "
                            "ON CONFLICT(name_key) DO UPDATE SET category = excluded.category, "
                            "updated_at = excluded.updated_at", (key, category, now))
            if category == OTHER:
                continue
            moved = tx.conn.execute("UPDATE shopping_items SET category = ? WHERE list_id = ? AND name_key = ? "
                                    "AND category = ?", (category, list_id, key, OTHER)).rowcount
            if moved:
                tx.bump(list_id)


async def drain() -> None:
    """Wait for the aisle lookups started so far (tests, shutdown)."""
    while _tasks:
        await asyncio.gather(*list(_tasks), return_exceptions=True)
