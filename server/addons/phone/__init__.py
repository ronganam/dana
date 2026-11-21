"""Phone addon (docs/access.md, "Addons")."""

from addons.phone import addon  # noqa: F401 — declares the addon
from addons.phone.calls import tools  # noqa: F401 — registers the phone tools (calls)
from addons.phone import notification_tools  # noqa: F401 — then the notification tools
