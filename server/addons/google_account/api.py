"""HTTP routes of the Google sign-in (docs/access.md, "Google account").

- ``GET /api/google/connect`` — 302 to Google's consent page for the caller
  (503 ``google_not_configured`` without the server's OAuth client)
- ``GET /api/google/callback?code=&state=`` — Google comes back here: the
  refresh token is stored on each Google addon whose scopes were granted, then
  302 to the Addons page with ``?google=connected|denied|expired|failed``
- ``GET /api/google/status`` → ``{configured, redirect_uri, addons: [{name, connected, account}]}``

All three are the caller's own (their login cookie or token); the callback also
needs the one-time ``state`` that ``connect`` gave to the same user.
"""

import logging
import os

from aiohttp import web

from core import users
from core.auth import request_user
from core.addons import crypto, store
from addons.google_account import oauth

logger = logging.getLogger(__name__)

PAGE = "/?google={result}#addons"


def redirect_uri(request: web.Request) -> str:
    """Where Google sends the user back: ``PUBLIC_URL`` when set, else the
    address the request came to. It must be one of the OAuth client's
    authorised redirect URIs."""
    base = (os.getenv("PUBLIC_URL") or "").strip().rstrip("/")
    if not base:
        scheme = request.headers.get("X-Forwarded-Proto", request.scheme).split(",")[0].strip() or request.scheme
        base = f"{scheme}://{request.host}"
    return base + oauth.CALLBACK_PATH


def _back(result: str) -> web.HTTPFound:
    return web.HTTPFound(PAGE.format(result=result))


async def connect_api(request: web.Request) -> web.StreamResponse:
    """GET /api/google/connect"""
    user = request_user(request)
    if not oauth.configured():
        return web.json_response({"error": "google_not_configured",
                                  "message": "The server has no Google OAuth client."}, status=503)
    raise web.HTTPFound(oauth.authorize_url(user, redirect_uri(request)))


async def callback_api(request: web.Request) -> web.StreamResponse:
    """GET /api/google/callback"""
    user = request_user(request)
    pending = oauth.take_state(request.query.get("state") or "")
    if pending is None or pending[0] != user.id:
        raise _back("expired")
    code = request.query.get("code") or ""
    if request.query.get("error") or not code:
        raise _back("denied")
    try:
        grant = await oauth.exchange(code, pending[1])
        with users.acting_as(user):
            result = oauth.store_grant(user, grant)
    except oauth.ExchangeError as e:
        logger.warning(f"Google sign-in of user {user.id} failed: {e}")
        raise _back("failed")
    except crypto.CredentialsKeyError as e:
        logger.error(f"Storing the Google sign-in of user {user.id} failed: {e}")
        raise _back("failed")
    raise _back("connected" if any(v in ("connected", "updated") for v in result.values()) else "denied")


async def status_api(request: web.Request) -> web.Response:
    """GET /api/google/status"""
    user = request_user(request)
    rows = []
    for addon in oauth.google_addons():
        conn = store.get(user, addon)
        rows.append({"name": addon, "connected": conn is not None,
                     "account": (conn.account or None) if conn is not None else None})
    return web.json_response({"configured": oauth.configured(), "redirect_uri": redirect_uri(request),
                              "addons": rows})


APP_KEY = "google_account_routes"


def setup(app: web.Application) -> None:
    """Register the Google sign-in routes (once: every Google addon's ``api.py`` calls this)."""
    if app.get(APP_KEY):
        return
    app[APP_KEY] = True
    app.router.add_get(oauth.CONNECT_PATH, connect_api)
    app.router.add_get(oauth.CALLBACK_PATH, callback_api)
    app.router.add_get("/api/google/status", status_api)
