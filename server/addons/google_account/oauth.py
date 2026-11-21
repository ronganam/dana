"""Google OAuth shared by the addons that use a Google account (``calendar``,
``contacts``, ``gmail``): which scopes each needs, the consent flow that
connects them in one go, and the credentials their clients are built with.

The OAuth client (``GOOGLE_CLIENT_ID`` / ``GOOGLE_CLIENT_SECRET``) is the
server's and stays in env. A user's refresh token is the credential
``refresh_token`` of each Google addon they have: one consent stores the same
token on every addon whose scopes Google granted (:func:`store_grant`), and each
addon is still disconnected on its own.

Flow: ``GET /api/google/connect`` → :func:`authorize_url` (a one-time ``state``
bound to the user) → Google → ``GET /api/google/callback`` → :func:`take_state`,
:func:`exchange`, :func:`store_grant`.
"""

import base64
import json
import logging
import os
import secrets
import threading
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlencode

import httpx
from google.oauth2.credentials import Credentials

from core.users import User
from core.addons import Connection, store
from core.addons.addon import get_addon, all_addons

logger = logging.getLogger(__name__)

AUTH_URI = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URI = "https://oauth2.googleapis.com/token"
CONNECT_PATH = "/api/google/connect"
CALLBACK_PATH = "/api/google/callback"
# Asked with every consent: tells which Google account was connected (the addons' ``account``).
IDENTITY_SCOPES = ("openid", "email")
STATE_TTL_SECONDS = 600
TOKEN_TIMEOUT_SECONDS = 20.0
CREDENTIAL = "refresh_token"

# addon → the scopes its tools need (:func:`register`).
_scopes: Dict[str, Tuple[str, ...]] = {}


def register(addon: str, scopes: Tuple[str, ...]) -> None:
    """Declare that ``addon`` uses the user's Google account with ``scopes``
    (its credential is ``refresh_token``)."""
    _scopes[addon] = tuple(scopes)


def google_addons() -> List[str]:
    """The addons that use a Google account, in the fixed addon order."""
    return [k.name for k in all_addons() if k.name in _scopes]


def scopes_of(addon: str) -> Tuple[str, ...]:
    return _scopes.get(addon, ())


def oauth_client() -> Tuple[str, str]:
    """The server's OAuth client id and secret ("" when not set)."""
    return os.getenv("GOOGLE_CLIENT_ID") or "", os.getenv("GOOGLE_CLIENT_SECRET") or ""


def configured() -> bool:
    return all(oauth_client())


# --- Credentials for a connection -----------------------------------------------------------------

Signature = Tuple[str, str, str]  # user id, addon, refresh token

_credentials: Dict[Signature, Credentials] = {}
_lock = threading.Lock()


def refresh_token(conn: Connection) -> str:
    return (conn.credentials.get(CREDENTIAL) or "").strip()


def credentials(conn: Connection) -> Credentials:
    """The connection's OAuth credentials (cached per connection, so the access
    token is reused until it expires; a new refresh token starts over).

    Raises:
        ValueError: the server has no OAuth client or the connection no refresh token.
    """
    client_id, client_secret = oauth_client()
    token = refresh_token(conn)
    if not client_id or not client_secret or not token:
        raise ValueError(f"Missing Google credentials (the server's GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET, "
                         f"or the {conn.addon} connection's refresh token).")
    sig = (conn.user.id, conn.addon, token)
    with _lock:
        creds = _credentials.get(sig)
        if creds is None:
            for old in [s for s in _credentials if s[:2] == sig[:2]]:
                _credentials.pop(old, None)  # the connection's previous refresh token
            creds = _credentials[sig] = Credentials(token=None, refresh_token=token, token_uri=TOKEN_URI,
                                                    client_id=client_id, client_secret=client_secret)
    return creds


def failure_reason(error: Exception, service: str) -> str:
    """A short reason (never a token) for a failed request to ``service``
    ("Google Calendar"), for an addon's connection check."""
    if isinstance(error, ValueError):  # no OAuth client / no refresh token (names no secret)
        return str(error)
    status = getattr(getattr(error, "resp", None), "status", None)
    if status == 403:
        return f"{service} refused the request (HTTP 403): connect Google again to grant this access."
    if status:
        return f"{service} answered HTTP {status}."
    if type(error).__name__ == "RefreshError":
        return "Google refused the refresh token (revoked or expired); connect Google again."
    return f"Could not reach {service} ({type(error).__name__})."


# --- The consent flow -----------------------------------------------------------------------------

@dataclass(frozen=True)
class _Pending:
    user_id: str
    redirect_uri: str
    expires: float


_states: Dict[str, _Pending] = {}


def wanted_scopes(user: User) -> List[str]:
    """Every scope to ask ``user`` for: those of each Google addon their access
    level allows, plus who they are."""
    out: List[str] = []
    for addon in google_addons():
        if get_addon(addon).allowed_for(user):
            out.extend(s for s in _scopes[addon] if s not in out)
    return [*out, *IDENTITY_SCOPES]


def authorize_url(user: User, redirect_uri: str) -> str:
    """Google's consent page for ``user``, coming back to ``redirect_uri``
    with a one-time state (good for :data:`STATE_TTL_SECONDS`).

    Raises:
        ValueError: the server has no OAuth client.
    """
    client_id, _ = oauth_client()
    if not configured():
        raise ValueError("The server has no Google OAuth client (GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET).")
    now = time.monotonic()
    with _lock:
        for old in [s for s, p in _states.items() if p.expires < now]:
            _states.pop(old, None)
        state = secrets.token_urlsafe(32)
        _states[state] = _Pending(user.id, redirect_uri, now + STATE_TTL_SECONDS)
    return AUTH_URI + "?" + urlencode({
        "client_id": client_id, "redirect_uri": redirect_uri, "response_type": "code",
        "scope": " ".join(wanted_scopes(user)), "state": state,
        # offline + consent: Google returns a refresh token every time, not only on the first consent.
        "access_type": "offline", "prompt": "consent", "include_granted_scopes": "true"})


def take_state(state: str) -> Optional[Tuple[str, str]]:
    """``(user id, redirect uri)`` of a state :func:`authorize_url` gave out,
    once; None when unknown, used or expired."""
    with _lock:
        pending = _states.pop(state or "", None)
    if pending is None or pending.expires < time.monotonic():
        return None
    return pending.user_id, pending.redirect_uri


@dataclass(frozen=True)
class Grant:
    """What a consent gave: the refresh token, the scopes granted and the account's email."""
    refresh_token: str
    scopes: Tuple[str, ...]
    email: Optional[str]


class ExchangeError(Exception):
    """Google did not turn the code into a refresh token (the message names no secret)."""


async def _post_token(data: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    async with httpx.AsyncClient(timeout=TOKEN_TIMEOUT_SECONDS) as client:
        resp = await client.post(TOKEN_URI, data=data)
    try:
        body = resp.json()
    except ValueError:
        body = {}
    return resp.status_code, body if isinstance(body, dict) else {}


def _email_of(id_token: Any) -> Optional[str]:
    """The email in an id token that came straight from Google's token
    endpoint (so its signature is not checked)."""
    try:
        payload = str(id_token).split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        email = claims.get("email")
        return str(email) if email else None
    except Exception:
        return None


async def exchange(code: str, redirect_uri: str) -> Grant:
    """Turn the ``code`` Google sent to the callback into a :class:`Grant`.

    Raises:
        ExchangeError: Google refused the code, or returned no refresh token.
    """
    client_id, client_secret = oauth_client()
    status, body = await _post_token({"code": code, "client_id": client_id, "client_secret": client_secret,
                                      "redirect_uri": redirect_uri, "grant_type": "authorization_code"})
    if status != 200:
        raise ExchangeError(f"Google refused the sign-in (HTTP {status}, {body.get('error') or 'no reason given'}).")
    token = str(body.get("refresh_token") or "").strip()
    if not token:
        raise ExchangeError("Google returned no refresh token.")
    return Grant(token, tuple(str(body.get("scope") or "").split()), _email_of(body.get("id_token")))


def store_grant(user: User, grant: Grant) -> Dict[str, str]:
    """Give ``grant``'s refresh token to every Google addon whose scopes were
    all granted: connect it, or replace the token of one already connected.
    An addon whose scopes were not granted is left as it is.

    Returns:
        addon → ``connected`` | ``updated`` | ``not_granted`` | ``not_allowed``.
    """
    granted = set(grant.scopes)
    out: Dict[str, str] = {}
    for addon in google_addons():
        if not get_addon(addon).allowed_for(user):
            out[addon] = "not_allowed"
        elif not set(_scopes[addon]) <= granted:
            out[addon] = "not_granted"
        elif store.is_connected(user, addon):
            store.update(user, addon, credentials={CREDENTIAL: grant.refresh_token}, account=grant.email, by=user)
            out[addon] = "updated"
        else:
            store.connect(user, addon, credentials={CREDENTIAL: grant.refresh_token}, account=grant.email, by=user)
            out[addon] = "connected"
    logger.info(f"Google consent stored for user {user.id}: {out}")
    return out
