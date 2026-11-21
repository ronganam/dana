"""Encryption of addon credentials (docs/access.md, "Credentials").

Credentials are stored only as Fernet tokens of their JSON. The key is the env
var ``CREDENTIALS_KEY`` when set (kept in your secret store); otherwise the key
file ``<data dir>/credentials.key``, which the first start writes (mode 0600)
while nothing is encrypted yet (:func:`ensure_key`). Back the file up with the
database: without its key the stored credentials cannot be read. Settings are
not secret and are never encrypted.
"""

import json
import os
from pathlib import Path
from typing import Dict, Mapping, Optional, Tuple

from cryptography.fernet import Fernet, InvalidToken

from core import paths

KEY_ENV = "CREDENTIALS_KEY"
GENERATE_HINT = (
    "Set the original key as CREDENTIALS_KEY (or put it back in <data dir>/credentials.key). A new key cannot read "
    "credentials stored with another one; to start over, disconnect the addons that hold credentials and connect "
    "them again. Generate a key with: python3 -c \"from cryptography.fernet import Fernet; "
    "print(Fernet.generate_key().decode())\""
)


class CredentialsKeyError(RuntimeError):
    """The credentials key is missing, malformed or not the key the stored credentials were encrypted with."""


_cached: Optional[Tuple[str, Fernet]] = None


def key_file() -> Path:
    """Where a generated key is kept: ``credentials.key`` in the data directory."""
    return paths.CREDENTIALS_KEY_FILE


def _file_key() -> str:
    try:
        return key_file().read_text().strip()
    except OSError:
        return ""


def key_source() -> str:
    """Where the key comes from: ``env`` (``CREDENTIALS_KEY``), ``file`` (the key file) or "" (none yet)."""
    if os.getenv(KEY_ENV, "").strip():
        return "env"
    return "file" if _file_key() else ""


def _raw_key() -> str:
    """The key: ``CREDENTIALS_KEY`` when set (env wins), else the key file's content ("" when neither)."""
    return os.getenv(KEY_ENV, "").strip() or _file_key()


def key_configured() -> bool:
    return bool(_raw_key())


def ensure_key(*, stored_credentials: bool) -> Optional[Path]:
    """Generate the key file when no key is configured and nothing is encrypted
    yet (a key made now could not read credentials stored with another one).
    Returns the file when it was written now (mode 0600), else None."""
    if key_configured() or stored_credentials:
        return None
    path = key_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(generate_key() + "\n")
    return path


def _fernet() -> Fernet:
    """The cipher for the current key.

    Raises:
        CredentialsKeyError: the key is missing or malformed.
    """
    global _cached
    raw = _raw_key()
    if not raw:
        raise CredentialsKeyError(f"{KEY_ENV} is not set and there is no key file ({key_file()}), so addon "
                                  "credentials cannot be stored or read. Restart the server to generate one, or "
                                  "set CREDENTIALS_KEY.")
    if _cached is not None and _cached[0] == raw:
        return _cached[1]
    try:
        cipher = Fernet(raw.encode())
    except (ValueError, TypeError) as e:
        where = KEY_ENV if key_source() == "env" else str(key_file())
        raise CredentialsKeyError(f"{where} is not a valid Fernet key ({e}). " + GENERATE_HINT) from None
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
        where = KEY_ENV if key_source() == "env" else f"the key in {key_file()}"
        raise CredentialsKeyError(f"{where} is not the key the stored addon credentials were encrypted with. "
                                  + GENERATE_HINT) from None


def generate_key() -> str:
    """A new random key."""
    return Fernet.generate_key().decode()
