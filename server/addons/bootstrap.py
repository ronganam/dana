"""Startup for addons (docs/access.md, "Credentials"): the credentials key,
then the first admin's default addons, once.

- **The key**: ``CREDENTIALS_KEY`` when set; otherwise the key file in the data
  directory, generated at the first start while nothing is encrypted yet
  (core/addons/crypto.py). A key that cannot read the stored credentials stops
  the start.
- **The first admin's addons**: every ``default_on`` addon they may have, once
  (``system_marks['addons:first_admin_defaults']``), so one they turn off later
  stays off. Nothing is read from env: addons with credentials or settings are
  connected on the Addons page or with ``bin/users.py addons connect``.
"""

import logging
from typing import List

from core import users, user_rows
from core.addons import crypto, store
from core.db import get_db_connection

logger = logging.getLogger(__name__)

DEFAULTS_MARK = "addons:first_admin_defaults"


def _defaults_given() -> bool:
    with get_db_connection() as conn:
        return conn.execute("SELECT 1 FROM system_marks WHERE key = ?", (DEFAULTS_MARK,)).fetchone() is not None


def _mark_defaults_given(detail: str) -> None:
    now = user_rows.now_iso()
    with get_db_connection() as conn:
        conn.execute("INSERT OR IGNORE INTO system_marks (key, last_at, ok, last_ok_at, detail) VALUES (?, ?, 1, ?, ?)",
                     (DEFAULTS_MARK, now, now, detail[:300]))
        conn.commit()


def ensure_credentials_key() -> None:
    """Generate the key file when no key is configured and nothing is encrypted
    yet, then check the key against every stored credential; logs where the
    key comes from.

    Raises:
        CredentialsKeyError: the key is missing, malformed or wrong for the stored credentials.
    """
    created = crypto.ensure_key(stored_credentials=store.stored_credentials() > 0)
    if created is not None:
        logger.warning(f"CREDENTIALS_KEY is not set: generated the credentials key in {created} (mode 0600). "
                       "BACK IT UP with the database: without it the stored addon credentials cannot be read. "
                       "CREDENTIALS_KEY in the environment, when set, takes precedence.")
    elif crypto.key_source() == "file":
        logger.info(f"Credentials key: {crypto.key_file()} (keep a backup; CREDENTIALS_KEY would take precedence)")
    store.verify_key()


def bootstrap() -> List[str]:
    """Check the credentials key and give the first admin their default addons
    (once). Run at startup after ``users.bootstrap()``. Returns the addons turned on now.

    Raises:
        CredentialsKeyError: the key is missing, malformed or wrong (the message says what to do).
    """
    ensure_credentials_key()
    admin = users.first_admin()
    if admin is None or _defaults_given():
        return []
    turned_on = store.connect_defaults(admin)
    _mark_defaults_given(", ".join(turned_on) or "none")
    if turned_on:
        logger.info(f"Default addons turned on for {admin.name}: {', '.join(turned_on)}")
    return turned_on
