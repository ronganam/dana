"""Setup mode: a fresh server with no users and no ``API_TOKEN`` waits for
its first admin (docs/devices.md, "Setup mode").

At startup (main.py) :func:`begin` draws a one-time setup code and gives the
setup URL (``<public url>/setup?code=<code>``), which main.py logs once.
While :func:`required`, the web server answers only the setup page, its API,
``/api/health`` and static files (core/auth.py); everything else is 503
``setup_required``. :func:`create_admin` (``POST /api/setup/admin``) checks the
code, creates the first admin with their default addons and the browser they
use as their first device, and setup mode ends: users exist.

With ``API_TOKEN`` set (a headless install) or ``DANA_INSECURE_DEV=1``, there
is no setup mode: startup creates the first admin from env as before
(:func:`core.users.bootstrap`).
"""

import hashlib
import hmac
import logging
import secrets
from dataclasses import dataclass
from typing import Optional

from core import access, devices, user_rows, users
from core.addons import store
from core.devices import Device
from core.users import User

logger = logging.getLogger(__name__)

_code_hash: Optional[str] = None


class SetupError(Exception):
    """The first admin could not be created. ``error``: ``already_set_up``
    (users exist), ``invalid_code`` or ``invalid`` (with ``field``)."""

    def __init__(self, error: str, message: str, field: str = "") -> None:
        super().__init__(message)
        self.error = error
        self.field = field


@dataclass(frozen=True)
class Created:
    """What :func:`create_admin` made: the admin, the browser's device and its token (given once)."""
    user: User
    device: Device
    token: str


def _hash(code: str) -> str:
    return hashlib.sha256(code.encode()).hexdigest()


def required() -> bool:
    """The server waits for its first admin: no users, no ``API_TOKEN``, not local development."""
    return not users.env_token() and not users.insecure_dev() and not users.list_users()


def begin(base_url: str) -> Optional[str]:
    """Draw a new one-time setup code when :func:`required`; returns the
    setup URL on ``base_url`` (None: no setup needed). An earlier code stops
    working."""
    global _code_hash
    if not required():
        _code_hash = None
        return None
    code = secrets.token_urlsafe(18)
    _code_hash = _hash(code)
    return f"{base_url.rstrip('/')}/setup?code={code}"


def code_matches(code: str) -> bool:
    """Whether ``code`` is the setup code drawn by :func:`begin` (constant time)."""
    return _code_hash is not None and hmac.compare_digest(_hash((code or "").strip()), _code_hash)


def create_admin(code: str, name: str, device_name: str = "") -> Created:
    """Create the first admin (named ``name``) with their default addons and
    their first device, this browser (``device_name``; empty: "Browser"),
    whose token is returned once. The setup code stops working.

    Raises:
        SetupError: see the class.
    """
    global _code_hash
    if not required():
        raise SetupError("already_set_up", "Dana is already set up: sign in instead.")
    if not code_matches(code):
        raise SetupError("invalid_code", "This setup link is not valid. Open the newest one from the server's log.")
    name = " ".join((name or "").split())
    if not name:
        raise SetupError("invalid", "Your name is needed.", field="name")
    if len(name) > 80:
        raise SetupError("invalid", "A name is at most 80 characters.", field="name")
    profile = user_rows.first_user_profile()
    user = users.create(name, access.ADMIN, profile=profile)
    _code_hash = None
    store.connect_first_admin_defaults()
    token, device = devices.create(user, "browser", device_name)
    logger.info(f"Setup: created the first admin {user.id} and their first device {device.id}")
    return Created(user=user, device=device, token=token)
