"""The ``cameras`` integration kind (Frigate): no tools, only the camera
endpoints (integrations/cameras/api.py over client.py)."""

from integrations.framework import ADDRESS, Field, Integration
from integrations.cameras import client
from integrations.cameras.config import KIND, URL

# No default address: the first admin's comes from the env import (integrations/bootstrap.py).
# Frigate has no authentication of its own, so whoever can set the address can
# watch those cameras: only a user with manage_users connects or changes this
# kind, for themselves or for someone else (docs/access.md, "Security rules").
cameras = Integration(
    KIND,
    settings=(Field(URL, "Frigate base URL", type=ADDRESS),),
    change_requires="manage_users",
    description="Frigate cameras (no tools: the dashboard's proxy endpoints)",
    check=client.check,
)
