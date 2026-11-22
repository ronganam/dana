"""The ``showcase`` rows: a user's plants (when each was last watered) and the messages its channel kept."""

import datetime
from typing import Any, Dict, List, Optional

from core import clock
from core.addons import UserTable
from core.users import User

# --- reference: data ---
PLANTS = UserTable("showcase_plants")
INBOX = UserTable("showcase_inbox")


def add_plant(user: User, name: str, every_days: int) -> int:
    """Remember a plant of ``user``; returns its id."""
    return int(PLANTS.insert(user, {"name": name, "every_days": every_days,
                                    "created_at": clock.now().isoformat(timespec="seconds")}) or 0)


def plants(user: User) -> List[Dict[str, Any]]:
    """The user's plants, oldest first."""
    return PLANTS.list(user, order="id")
# --- end ---


def plant(user: User, plant_id: int) -> Optional[Dict[str, Any]]:
    """One of the user's plants (None: not theirs, or no such plant)."""
    return PLANTS.get(user, plant_id)


def water(user: User, plant_id: int, when: datetime.datetime) -> bool:
    """Mark a plant watered at ``when``; False when it is not the user's."""
    return PLANTS.update(user, plant_id, {"last_watered": when.isoformat(timespec="seconds")})


def unwater(user: User, plant_id: int, before: Optional[str]) -> bool:
    """Put back the watering time a plant had (an undo)."""
    return PLANTS.update(user, plant_id, {"last_watered": before})


def remove(user: User, plant_id: int) -> bool:
    """Forget one of the user's plants."""
    return PLANTS.delete(user, plant_id)


def due(user: User, now: datetime.datetime) -> List[Dict[str, Any]]:
    """The user's plants that need water at ``now`` (never watered, or watered ``every_days`` ago or more)."""
    out = []
    for p in plants(user):
        last = datetime.datetime.fromisoformat(p["last_watered"]) if p["last_watered"] else None
        if last is None or now - last >= datetime.timedelta(days=p["every_days"]):
            out.append(p)
    return out


def keep(user: User, text: str) -> None:
    """Keep a message the channel delivered."""
    INBOX.insert(user, {"text": text, "created_at": clock.now().isoformat(timespec="seconds")})


def inbox(user: User) -> List[str]:
    """The messages the channel kept for the user, oldest first."""
    return [r["text"] for r in INBOX.list(user, order="id")]
