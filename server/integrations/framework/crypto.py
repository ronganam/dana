"""Encryption of integration credentials (docs/access.md, "Credentials").

Credentials are stored only as Fernet tokens of their JSON, with the key from
the env var ``CREDENTIALS_KEY`` (kept in your secret store). Settings are not secret
and are never encrypted.
"""

import json
import os
from typing import Dict, Mapping, Optional, Tuple

from cryptography.fernet import Fernet, InvalidToken

KEY_ENV = "CREDENTIALS_KEY"
GENERATE_HINT = (
    "Generate a key with: python -c \"from cryptography.fernet import Fernet; "
    "print(Fernet.generate_key().decode())\" and set it as CREDENTIALS_KEY (in production: in your secret store). "
    "Keep it: credentials stored with a key cannot be read without that same key."
)


class CredentialsKeyError(RuntimeError):
    """``CREDENTIALS_KEY`` is missing, malformed or not the key the stored credentials were encrypted with."""


_cached: Optional[Tuple[str, Fernet]] = None


def key_configured() -> bool:
    return bool(os.getenv(KEY_ENV, "").strip())


def _fernet() -> Fernet:
    """The cipher for the current ``CREDENTIALS_KEY``.

    Raises:
        CredentialsKeyError: the key is missing or malformed.
    """
    global _cached
    raw = os.getenv(KEY_ENV, "").strip()
    if not raw:
        raise CredentialsKeyError(f"{KEY_ENV} is not set, so integration credentials cannot be stored or read. "
                                  + GENERATE_HINT)
    if _cached is not None and _cached[0] == raw:
        return _cached[1]
    try:
        cipher = Fernet(raw.encode())
    except (ValueError, TypeError) as e:
        raise CredentialsKeyError(f"{KEY_ENV} is not a valid Fernet key ({e}). " + GENERATE_HINT) from None
    _cached = (raw, cipher)
    return cipher


def check_key() -> None:
    """Raise :class:`CredentialsKeyError` unless a well-formed key is set."""
    _fernet()


def encrypt(values: Mapping[str, str]) -> str:
    """The Fernet token of ``values`` (as JSON).

    Raises:
        CredentialsKeyError: no usable key.
    """
    return _fernet().encrypt(json.dumps(dict(values), sort_keys=True).encode()).decode()


def decrypt(token: str) -> Dict[str, str]:
    """The credentials in a token made by :func:`encrypt`.

    Raises:
        CredentialsKeyError: no usable key, or not the key the token was made with.
    """
    try:
        return json.loads(_fernet().decrypt(token.encode()).decode())
    except InvalidToken:
        raise CredentialsKeyError(f"{KEY_ENV} is not the key the stored integration credentials were encrypted "
                                  "with. Set the original key (your secret store keeps it); a new key cannot read them.") \
            from None


def generate_key() -> str:
    """A new random key for ``CREDENTIALS_KEY``."""
    return Fernet.generate_key().decode()
