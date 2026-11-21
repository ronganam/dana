"""The ``cameras`` addon (Frigate): no tools, only the camera
endpoints (addons/cameras/api.py over client.py)."""

from core.addons import ADDRESS, Field, Addon
from addons.cameras import client
from addons.cameras.config import ADDON, URL

# No default address: whose Frigate it is, an admin sets (Addons page, bin/users.py).
# Frigate has no authentication of its own, so whoever can set the address can
# watch those cameras: only a user with manage_users connects or changes this
# kind, for themselves or for someone else (docs/access.md, "Security rules").
addon = Addon(
    ADDON, order=150,
    settings=(Field(URL, "Frigate base URL", type=ADDRESS),),
    change_requires="manage_users",
    description="Frigate cameras (no tools: the dashboard's proxy endpoints)",
    test=client.check,
)
