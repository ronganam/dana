"""Shopping constants and small helpers shared by the shopping modules (docs/shopping.md)."""

import datetime

KIND = "shopping"
ITEM_TYPE = "shopping_list"
DEFAULT_NAME = "Shopping list"
ACTIVE_SETTING = "active_list"

MAX_ITEMS = 30          # items in one add (typed text or a list)
MAX_NAME = 200
MAX_QUANTITY = 40
MAX_NOTE = 200
CLEAR_AFTER = datetime.timedelta(hours=12)   # a bought item leaves the list this long after it was ticked
USUALS = 8              # usuals shown as chips
SUGGESTIONS = 8         # matches while typing


class ReadOnly(PermissionError):
    """The list is shared with the user read-only."""


class NotFound(LookupError):
    """No such item / list for this user (another user's answers the same)."""


class Duplicate(ValueError):
    """Renaming would make a second item to buy with the same name."""


def utc_now() -> datetime.datetime:
    """Now, in UTC (stored timestamps)."""
    return datetime.datetime.now(datetime.timezone.utc)


def iso(dt: datetime.datetime) -> str:
    """A stored timestamp: ISO 8601 with its zone, seconds."""
    return dt.astimezone(datetime.timezone.utc).isoformat(timespec="seconds")


def parse_iso(value: str) -> datetime.datetime:
    """A stored timestamp back as an aware datetime (UTC when it has no zone)."""
    dt = datetime.datetime.fromisoformat(value)
    return dt if dt.tzinfo else dt.replace(tzinfo=datetime.timezone.utc)
