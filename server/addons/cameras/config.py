"""Frigate configuration of one user: the addon's name and its setting
(docs/access.md, "Addons"). Nothing here reads env (the address is set on the
Addons page or with bin/dana); the tuning value
``FRIGATE_MOTION_THRESHOLD`` is server-wide (addons/cameras/client.py)."""

from core.addons import Connection

ADDON = "cameras"

URL = "url"  # Frigate's base URL (no default: whose Frigate it is is the user's)


def base_url(conn: Connection) -> str:
    """The connection's Frigate base URL, no trailing slash ("" when unset)."""
    return str(conn.settings.get(URL) or "").strip().rstrip("/")
