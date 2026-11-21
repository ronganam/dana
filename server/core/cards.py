"""What every Now card is made of, for core/now.py and the addons' Now hooks
(``@addon.now_cards``, ``@addon.now_action``; docs/writing-an-addon.md).

- :func:`action` — a button of a card (``primary`` / ``secondary``);
- :class:`ActError` — refusing an action (message + HTTP status);
- :func:`result` — what an action answers (``{ok, message, undo_id?, …}``);
- :func:`hide_with_undo` — hide a card until a time, with an Undo;
- :func:`until_from` / :func:`slot_time` — the moment a snooze or a day slot means;
- small formatting helpers: :func:`clip`, :func:`loads`, :func:`parse_local`.

Nothing here imports an addon.
"""
from __future__ import annotations

import datetime
import json
import re
from typing import Any, Dict, Optional

from core import clock, now_hidden, undo
from core.users import User

# "Hidden for good" (a dismissed card): the far future.
FAR = datetime.datetime(9999, 12, 31, tzinfo=datetime.timezone.utc)


class ActError(Exception):
    """An action on a Now card that cannot be done: ``status`` is the HTTP answer (400, 404, 409…)."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def action(name: str, label: str, args: Optional[Dict[str, Any]] = None, client: bool = False) -> Dict[str, Any]:
    """A card's button: ``POST /api/now/act {id, action: name, args}`` (``client``: handled by the app itself)."""
    out: Dict[str, Any] = {"action": name, "label": label}
    if args:
        out["args"] = args
    if client:
        out["client"] = True
    return out


def result(message: str, undo_id: Optional[str] = None, **extra: Any) -> Dict[str, Any]:
    """The answer of an action: its message, the undo id and any extra keys (None values left out)."""
    out: Dict[str, Any] = {"ok": True, "message": message}
    if undo_id:
        out["undo_id"] = undo_id
    out.update({k: v for k, v in extra.items() if v is not None})
    return out


def hide_with_undo(user: User, item_id: str, until: datetime.datetime, label: str, reason: str = "snooze") -> str:
    """Hide ``user``'s card ``item_id`` until ``until`` (:data:`FAR`: for good); returns the undo id."""
    before = now_hidden.row(user, item_id)
    now_hidden.hide(user, item_id, until, reason)
    return undo.record(label, lambda: now_hidden.restore(user, item_id, before), source="now.hide")


def clip(text: Any, n: int) -> str:
    """``text`` on one line, cut at ``n`` characters with "…"."""
    s = re.sub(r"\s+", " ", str(text or "")).strip()
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"


def loads(raw: Any, default: Any) -> Any:
    """JSON text (a stored column) → value; ``default`` when empty or broken."""
    if isinstance(raw, (dict, list)):
        return raw
    try:
        v = json.loads(raw or "")
        return v if v is not None else default
    except (TypeError, ValueError):
        return default


def parse_local(value: Any) -> Optional[datetime.datetime]:
    """An ISO time (a naive one is local: trigger times, call timestamps) → aware local time; None when unparsable."""
    if not value:
        return None
    try:
        dt = datetime.datetime.fromisoformat(str(value).strip().replace("Z", "+00:00").replace(" ", "T", 1))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=clock.get_timezone())
    return dt.astimezone(clock.get_timezone())


def slot_time(slot: str, now: datetime.datetime) -> datetime.datetime:
    """The hour slot ``"14"`` of today as a moment (ActError when unknown or past)."""
    try:
        h = int(slot)
    except ValueError:
        raise ActError(f"unknown slot '{slot}'")
    if not 0 <= h <= 23:
        raise ActError(f"unknown slot '{slot}'")
    dt = now.replace(hour=h, minute=0, second=0, microsecond=0)
    if dt <= now:
        raise ActError("That hour has already passed")
    return dt


def until_from(args: Dict[str, Any], now: datetime.datetime) -> Optional[datetime.datetime]:
    """{minutes} | {until} | {slot: "<hour>"} → a future moment (None when none given)."""
    if args.get("minutes") is not None:
        try:
            m = float(args["minutes"])
        except (TypeError, ValueError):
            raise ActError("minutes must be a number")
        if m <= 0:
            raise ActError("minutes must be positive")
        return now + datetime.timedelta(minutes=m)
    if args.get("until"):
        dt = parse_local(args["until"])
        if not dt:
            raise ActError("until must be an ISO time")
        if dt <= now:
            raise ActError("until must be in the future")
        return dt
    slot = str(args.get("slot") or "")
    if slot.isdigit():
        return slot_time(slot, now)
    return None
