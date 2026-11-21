"""The ``cameras`` addon (Frigate): no tools, only the camera
endpoints (addons/cameras/api.py over client.py)."""

from core.addons import ADDRESS, Field, Addon
from addons.cameras import client
from addons.cameras.config import ADDON, URL

# No default address: whose Frigate it is, an admin sets (Addons page, bin/dana).
# Frigate has no authentication of its own, so whoever can set the address can
# watch those cameras: only a user with manage_users connects or changes this
# kind, for themselves or for someone else (docs/access.md, "Security rules").
addon = Addon(
    ADDON, order=150,
    title="Cameras", icon="cctv",
    settings=(Field(URL, "Frigate base URL", type=ADDRESS, label="Frigate address",
                    help="The address Frigate's web page opens at.", url="https://docs.frigate.video/",
                    placeholder="http://frigate:5000"),),
    change_requires="manage_users",
    description="Your Frigate cameras on the dashboard",
    features=("Live camera views and recent events on the dashboard and the TV",),
    test=client.check,
)
