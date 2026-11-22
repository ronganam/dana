"""Startup for addons (docs/access.md, "Credentials"): the credentials key,
then the first admin's default addons, once.

- **The key**: ``CREDENTIALS_KEY`` when set; otherwise the key file in the data
  directory, generated at the first start while nothing is encrypted yet
  (core/addons/crypto.py). A key that cannot read the stored credentials stops
  the start.
- **The first admin's addons**: every ``default_on`` addon they may have, once
  (``system_marks['addons:first_admin_defaults']``), so one they turn off later
  stays off; with no admin yet (setup mode) the setup page gives them when it
  creates the admin (core/setup.py). Nothing is read from env: addons with credentials or settings are
  connected on the Addons page or with ``bin/dana addons connect``.
"""

import logging
from typing import List

from core.addons import crypto, store

logger = logging.getLogger(__name__)


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
    (once: :func:`core.addons.store.connect_first_admin_defaults`). Run at
    startup after ``users.bootstrap()``. Returns the addons turned on now.

    Raises:
        CredentialsKeyError: the key is missing, malformed or wrong (the message says what to do).
    """
    ensure_credentials_key()
    return store.connect_first_admin_defaults()
