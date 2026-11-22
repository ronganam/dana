"""Authentication for the web server: every request resolves to a user (and,
when its token is a device's, to that device).

Trust model
-----------
* **Apps / scripts** send ``Authorization: Bearer <token>``: a user's token,
  looked up hashed in the DB (core/users.py): each app instance has its own,
  made by pairing (core/devices.py), and a script may have one issued with
  ``bin/dana users tokens issue``. ``API_TOKEN`` (env), when set, is the first
  admin's (a headless install).
* **Browsers** sign in once at ``/login`` with a pairing code (the browser
  becomes a device) or any valid token; a long-lived HttpOnly cookie
  (``dana_session``) holding an HMAC of the token (never the token itself) is
  set.
* **Inbound webhooks** (:data:`INBOUND_PATHS`: WAHA) may be called without
  credentials; their handler proves the sender itself (WAHA's HMAC signature
  with ``WAHA_WEBHOOK_SECRET``: addons/whatsapp/api.py) and finds its user
  from the payload, so auth sets no user. Every other path needs credentials.
* **Setup mode** (core/setup.py): no users and no ``API_TOKEN``: only the
  setup page, its API, ``/api/health`` and static files answer; everything
  else is 503 ``setup_required``.

An authenticated request carries its :class:`core.users.User` in
``request["user"]`` (handlers read it with :func:`request_user`) and its
:class:`core.devices.Device` in ``request["device"]`` (None for a token of no
device: :func:`request_device`); the device's ``last_seen_at`` is refreshed.
The handler runs with that user current (:func:`core.users.acting_as`), so
undo entries, usage records and tools inside the request know whose they
are. ``DANA_INSECURE_DEV=1`` without ``API_TOKEN`` (local development): a
request without credentials is the first admin.

Access (docs/access.md, "Enforcement"): endpoints about the whole server are
wrapped in :func:`requires` (403 ``{"error": "forbidden", "needs": <capability>}``
for a level without it), and :func:`addon_middleware` answers a handler
that needed an addon the user has not connected
(``core.addons.store.NotConnected``) with 403
``{"error": "not_connected", "addon": <name>}``.
"""

import functools
import logging
from pathlib import Path
from typing import Awaitable, Callable, Optional

from aiohttp import web
from yarl import URL

from core import devices, ratelimit, setup, users
from core.access import CAPABILITIES
from core.devices import Device, PairingError
from core.users import Identity, User
from core.addons.store import NotConnected

Handler = Callable[[web.Request], Awaitable[web.StreamResponse]]

logger = logging.getLogger(__name__)

SESSION_COOKIE = "dana_session"
# Browsers cap cookie lifetime at 400 days.
SESSION_MAX_AGE = 400 * 24 * 3600

_STATIC_DIR = Path(__file__).resolve().parent.parent / "static"

# Reachable without credentials. The app-update endpoints stay public so an
# installed app can always fetch an update (the APK holds no secrets);
# /api/app/upload is guarded by its own APP_PUBLISH_TOKEN. /api/pair and the
# setup API prove themselves with a code (rate-limited).
_PUBLIC_PATHS = frozenset({
    "/login",
    "/api/health",
    "/api/app/version",
    "/api/app/upload",
    "/api/pair",
    "/api/setup",
    "/api/setup/admin",
    "/sw.js",
    "/manifest.json",
})
_PUBLIC_PREFIXES = ("/static/", "/api/app/download/")

# What answers while the server waits for its first admin (core/setup.py).
_SETUP_PATHS = frozenset({"/setup", "/api/setup", "/api/setup/admin", "/api/health", "/sw.js", "/manifest.json"})

# Inbound webhooks reachable without credentials: the handler verifies the
# sender's signature (WAHA; Home Assistant posts nothing: Dana watches it).
INBOUND_PATHS = frozenset({"/webhook/waha"})

# Paths answered with 401 rather than a redirect to the login page.
_API_PREFIXES = ("/api/", "/webhook", "/media/")


def bind_host() -> str:
    """Where the web server listens: 127.0.0.1 in insecure local development
    (``DANA_INSECURE_DEV=1`` without ``API_TOKEN``), every interface otherwise."""
    return "127.0.0.1" if users.insecure_dev() else "0.0.0.0"


def _is_public(path: str) -> bool:
    return path in _PUBLIC_PATHS or path.startswith(_PUBLIC_PREFIXES)


def _request_identity(request: web.Request) -> Optional[Identity]:
    """Who the request's credentials (Bearer token or session cookie) belong to."""
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        found = users.identify_token(auth[7:])
        if found:
            return found
    return users.identify_session_cookie(request.cookies.get(SESSION_COOKIE, ""))


def _safe_next(target: str) -> str:
    """Only allow same-origin relative redirects."""
    if target.startswith("/") and not target.startswith("//") and "\\" not in target:
        return target
    return "/"


def request_user(request: web.Request) -> User:
    """The user an authenticated request belongs to.

    Raises:
        web.HTTPUnauthorized: the request has no user (a public or inbound path).
    """
    user = request.get("user")
    if user is None:
        raise web.HTTPUnauthorized(text='{"error": "Unauthorized"}', content_type="application/json")
    return user


def request_device(request: web.Request) -> Optional[Device]:
    """The device an authenticated request was made from (its own token), or
    None (a token of no device, ``API_TOKEN``, local development)."""
    return request.get("device")


def requires(capability: str) -> Callable[[Handler], Handler]:
    """Wrap a handler so only users whose access level has ``capability``
    (core/access.py) reach it; anyone else gets 403
    ``{"error": "forbidden", "needs": capability}``.

    Raises:
        ValueError: (at decoration) no such capability.
    """
    if capability not in CAPABILITIES:
        raise ValueError(f"Unknown capability {capability!r} (known: {', '.join(CAPABILITIES)})")

    def decorator(handler: Handler) -> Handler:
        @functools.wraps(handler)
        async def wrapped(request: web.Request) -> web.StreamResponse:
            user = request_user(request)
            if not user.level.allows(capability):
                logger.info(f"Refused {request.method} {request.path} for user {user.id}: needs {capability}")
                return web.json_response({"error": "forbidden", "needs": capability}, status=403)
            return await handler(request)
        return wrapped
    return decorator


def not_connected_response(error: NotConnected) -> web.Response:
    """The answer to a request that needed an addon the user has not connected."""
    return web.json_response({"error": "not_connected", "addon": error.addon}, status=403)


@web.middleware
async def addon_middleware(request: web.Request, handler: Handler) -> web.StreamResponse:
    """Turn a :class:`NotConnected` raised by a handler (a direct call of a
    tool function with ``store.require``) into :func:`not_connected_response`.
    Handlers with a catch-all ``except`` re-raise it so it gets here."""
    try:
        return await handler(request)
    except NotConnected as e:
        logger.info(f"{request.method} {request.path}: {e}")
        return not_connected_response(e)


async def _as(identity: Identity, request: web.Request, handler: Handler) -> web.StreamResponse:
    user = identity.user
    device = devices.by_token_id(identity.token_id)
    if device is not None and device.user_id != user.id:
        device = None
    request["user"] = user
    request["device"] = device
    if device is not None:
        devices.touch(device)
    with users.acting_as(user):
        return await handler(request)


_SETUP_PAGE = """<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8"><meta name="viewport"
content="width=device-width, initial-scale=1.0"><title>Dana · Set up</title><style>body{margin:0;min-height:100vh;
display:grid;place-items:center;padding:16px;background:#2b2521;color:#f7f5f0;font:15px/1.5 Inter,system-ui,sans-serif}
p{max-width:420px;color:#c9c0ad}</style></head><body><main><h1>Dana is not set up yet</h1><p>Open the setup link
the server wrote to its log when it started (it ends in <code>/setup?code=…</code>).</p></main></body></html>"""


def setup_required_response(request: web.Request) -> web.Response:
    """503 while the server waits for its first admin: ``setup_required`` (a
    page saying where the setup link is, for a browser opening a page)."""
    if request.method == "GET" and not request.path.startswith(_API_PREFIXES):
        return web.Response(text=_SETUP_PAGE, content_type="text/html", status=503)
    return web.json_response({"error": "setup_required"}, status=503)


@web.middleware
async def auth_middleware(request: web.Request, handler: Handler) -> web.StreamResponse:
    if setup.required():
        if request.path in _SETUP_PATHS or request.path.startswith("/static/"):
            return await handler(request)
        return setup_required_response(request)
    if _is_public(request.path):
        return await handler(request)
    identity = _request_identity(request)
    if identity:
        return await _as(identity, request, handler)
    if users.insecure_dev():
        admin = users.first_admin()
        if admin:
            return await _as(Identity(admin, None), request, handler)
        return await handler(request)
    if request.path in INBOUND_PATHS:
        return await handler(request)

    if request.method == "GET" and not request.path.startswith(_API_PREFIXES):
        raise web.HTTPFound(URL("/login").with_query(next=request.path_qs))
    return web.json_response({"error": "Unauthorized"}, status=401)


async def login_page(request: web.Request) -> web.StreamResponse:
    """GET /login"""
    return web.FileResponse(_STATIC_DIR / "login.html")


def set_session_cookie(request: web.Request, response: web.StreamResponse, token: str) -> None:
    """Sign the browser in with ``token``: the ``dana_session`` cookie (an HMAC of it)."""
    response.set_cookie(
        SESSION_COOKIE,
        users.session_cookie_value(token),
        max_age=SESSION_MAX_AGE,
        httponly=True,
        samesite="Lax",
        secure=request.secure or request.headers.get("X-Forwarded-Proto") == "https",
        path="/",
    )


async def login_submit(request: web.Request) -> web.StreamResponse:
    """POST /login — a pairing code (this browser becomes a device of the
    code's user) or a valid token, exchanged for a session cookie."""
    form = await request.post()
    next_url = _safe_next(str(form.get("next") or request.query.get("next") or "/"))
    submitted = str(form.get("token") or "").strip()

    def refused(error: str) -> web.HTTPFound:
        return web.HTTPFound(URL("/login").with_query(error=error, next=next_url))

    token = submitted
    if devices.looks_like_code(submitted):
        key = ratelimit.client_key(request)
        if ratelimit.PAIRING_FAILURES.retry_after(key):
            raise refused("limited")
        try:
            token, _, _ = devices.redeem(submitted, kind="browser",
                                         name=devices.browser_name(request.headers.get("User-Agent", "")))
        except PairingError as e:
            if e.error == "wrong_kind":
                raise refused("kind")
            ratelimit.PAIRING_FAILURES.fail(key)
            logger.warning(f"Failed web login (pairing code) from {key}")
            raise refused("1")
    elif users.identify_token(submitted) is None:
        logger.warning(f"Failed web login from {request.remote}")
        raise refused("1")

    response = web.HTTPFound(next_url)
    set_session_cookie(request, response, token)
    raise response


def setup_auth(app: web.Application) -> None:
    """Install the auth middleware (then the addon middleware, inside
    it) and the login routes on ``app``."""
    app.middlewares.append(auth_middleware)
    app.middlewares.append(addon_middleware)
    app.router.add_get("/login", login_page)
    app.router.add_post("/login", login_submit)
    if users.insecure_dev():
        logger.warning("!!! DANA_INSECURE_DEV=1 without API_TOKEN: every request without credentials is the "
                       "first admin. Local development only (listening on 127.0.0.1). !!!")
