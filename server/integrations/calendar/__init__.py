"""Calendar integration (docs/access.md, "Kinds")."""

from integrations.calendar.kind import calendar
from integrations.calendar import tools  # noqa: F401 — registers the calendar tools

__all__ = ["calendar"]
