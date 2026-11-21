"""Frigate configuration of one user: the kind's name and its setting
(docs/access.md, "Kinds"). Nothing here reads env: the first admin's address
is imported once by ``integrations/bootstrap.py``; the tuning value
``FRIGATE_MOTION_THRESHOLD`` is server-wide (integrations/cameras/client.py)."""

from integrations.framework import Connection

KIND = "cameras"

URL = "url"  # Frigate's base URL (no default: whose Frigate it is is the user's)


def base_url(conn: Connection) -> str:
    """The connection's Frigate base URL, no trailing slash ("" when unset)."""
    return str(conn.settings.get(URL) or "").strip().rstrip("/")
