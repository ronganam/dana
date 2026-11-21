"""Phone integration (docs/access.md, "Kinds")."""

from integrations.phone.kind import phone
from integrations.phone.calls import tools  # noqa: F401 — registers the phone tools (calls)
from integrations.phone import notification_tools  # noqa: F401 — then the notification tools

__all__ = ["phone"]
