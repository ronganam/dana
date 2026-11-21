"""The environment section of a session's debug export (``GET
/api/debug/sessions/{id}``, Telegram ``/debug``; docs/access.md, "Security
rules").

Only a user with ``manage_server`` gets it at all — the process environment
is the server's, not the user's (another user's profile, service addresses,
keys). Everyone else gets an empty object, so the payload keeps its shape.

For an admin, a value is shown as ``******`` when any of these holds:

- its name contains ``key``, ``token``, ``password``, ``secret``, ``auth``,
  ``credential``, ``private``, ``cookie``, ``passwd``, ``session`` or
  ``service_account``, or ends in ``_json``;
- its value holds a PEM block (``-----BEGIN``), parses as JSON with a
  secret-looking key (``private_key``, ``client_secret``, ``token``, …) at any
  depth, or is a URL carrying a user name / password.
"""

import json
import os
import re
from typing import Any, Dict

from core.users import User

MASK = "******"
_NAME_MARKERS = ("key", "token", "password", "passwd", "secret", "auth", "credential", "private", "cookie",
                 "session", "service_account")
_URL_WITH_USERINFO = re.compile(r"[a-z][a-z0-9+.-]*://[^/\s@]+@", re.IGNORECASE)


def _secret_json(value: Any) -> bool:
    if isinstance(value, dict):
        return any(any(m in str(k).lower() for m in _NAME_MARKERS) or _secret_json(v) for k, v in value.items())
    if isinstance(value, list):
        return any(_secret_json(v) for v in value)
    return False


def masked(name: str, value: str) -> bool:
    """Whether the admin's export hides this env value."""
    lower = name.lower()
    if any(m in lower for m in _NAME_MARKERS) or lower.endswith("_json"):
        return True
    if "-----BEGIN" in value or _URL_WITH_USERINFO.search(value):
        return True
    text = value.strip()
    if text[:1] in ("{", "["):
        try:
            return _secret_json(json.loads(text))
        except ValueError:
            return False
    return False


def environment_for(user: User) -> Dict[str, str]:
    """The export's ``environment_variables`` for ``user``: ``{}`` without
    ``manage_server``, else the process environment with secrets masked."""
    if not user.level.allows("manage_server"):
        return {}
    return {k: MASK if masked(k, v) else v for k, v in os.environ.items()}
