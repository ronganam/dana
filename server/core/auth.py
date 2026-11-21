"""Authentication for the web server: every request resolves to a user.

Trust model
-----------
* **Apps / scripts** send ``Authorization: Bearer <token>``: ``API_TOKEN`` (env)
  is the first admin user's; any other token is looked up (hashed) in the DB
  (core/users.py).
* **Browsers** sign in once at ``/login`` with any valid token; a long-lived
  HttpOnly cookie holding an HMAC of the token (never the token itself) is
  set. The cookie format is unchanged, so logins made with ``API_TOKEN``
  before users existed stay valid.
* **In-cluster callers** reach the pod through its ClusterIP Service, so their
  ``Host`` header is the Service DNS name. Hosts listed in ``INTERNAL_HOSTS``
  may call the inbound webhook paths (:data:`INBOUND_PATHS`) without
  credentials; those handlers find their user from the payload, so auth sets
  no user. Every other path needs credentials, from any host. Clients outside
  the cluster can only get in through the gateway, which only routes the
  public hostname, so they cannot present an internal ``Host``.

An authenticated request carries its :class:`core.users.User` in
``request["user"]`` (handlers read it with :func:`request_user`), and the
handler runs with that user current (:func:`core.users.acting_as`), so undo
entries, usage records and tools inside the request know whose they are. If
``API_TOKEN`` is unset, authentication is disabled (local development): every
request is the first admin user, and a warning is logged at startup.

Access (docs/access.md, "Enforcement"): endpoints about the whole server are
wrapped in :func:`requires` (403 ``{"error": "forbidden", "needs": <capability>}``
for a level without it), and :func:`addon_middleware` answers a handler
that needed an addon the user has not connected
(``core.addons.store.NotConnected``) with 403
``{"error": "not_connected", "addon": <name>}``.
"""

import functools
import logging
import os
from pathlib import Path
from typing import Awaitable, Callable, Optional

from aiohttp import web
from yarl import URL

from core import users
from core.access import CAPABILITIES
from core.users import User
from core.addons.store import NotConnected

Handler = Callable[[web.Request], Awaitable[web.StreamResponse]]

logger = logging.getLogger(__name__)

SESSION_COOKIE = "ganam_session"
# Browsers cap cookie lifetime at 400 days.
SESSION_MAX_AGE = 400 * 24 * 3600

_STATIC_DIR = Path(__file__).resolve().parent.parent / "static"

# Reachable without credentials. The app-update endpoints stay public so an
# installed app can always fetch an update (the APK holds no secrets);
# /api/app/upload is guarded by its own APP_PUBLISH_TOKEN.
_PUBLIC_PATHS = frozenset({
    "/login",
    "/api/health",
    "/api/app/version",
    "/api/app/upload",
    "/sw.js",
    "/manifest.json",
})
_PUBLIC_PREFIXES = ("/static/", "/api/app/download/")

# Inbound webhooks that in-cluster callers (INTERNAL_HOSTS) may post to
# without credentials (WAHA; Home Assistant posts nothing: Dana watches it).
INBOUND_PATHS = frozenset({"/webhook/waha"})

# Paths answered with 401 rather than a redirect to the login page.
_API_PREFIXES = ("/api/", "/webhook", "/media/")


def _internal_hosts() -> frozenset[str]:
    raw = os.getenv("INTERNAL_HOSTS", "")
    return frozenset(h.strip().lower() for h in raw.split(",") if h.strip())


def _is_public(path: str) -> bool:
    return path in _PUBLIC_PATHS or path.startswith(_PUBLIC_PREFIXES)


def _is_internal(request: web.Request) -> bool:
    host = (request.host or "").rsplit(":", 1)[0].lower()
    return host in _internal_hosts()


def _request_user(request: web.Request) -> Optional[User]:
    """The user the request's credentials (Bearer token or session cookie) belong to."""
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        user = users.by_token(auth[7:])
        if user:
            return user
    return users.by_session_cookie(request.cookies.get(SESSION_COOKIE, ""))


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


async def _as(user: User, request: web.Request, handler):
    request["user"] = user
    with users.acting_as(user):
        return await handler(request)


@web.middleware
async def auth_middleware(request: web.Request, handler):
    if _is_public(request.path):
        return await handler(request)
    if not users.env_token():
        user = users.first_admin()
        if user:
            return await _as(user, request, handler)
        return await handler(request)
    user = _request_user(request)
    if user:
        return await _as(user, request, handler)
    if request.path in INBOUND_PATHS and _is_internal(request):
        return await handler(request)

    if request.method == "GET" and not request.path.startswith(_API_PREFIXES):
        raise web.HTTPFound(URL("/login").with_query(next=request.path_qs))
    return web.json_response({"error": "Unauthorized"}, status=401)


async def login_page(request: web.Request) -> web.StreamResponse:
    """GET /login"""
    return web.FileResponse(_STATIC_DIR / "login.html")


async def login_submit(request: web.Request) -> web.StreamResponse:
    """POST /login — exchange a valid API token for a session cookie."""
    form = await request.post()
    next_url = _safe_next(str(form.get("next") or request.query.get("next") or "/"))
    submitted = str(form.get("token") or "").strip()

    if not users.env_token() or users.by_token(submitted) is None:
        logger.warning(f"Failed web login from {request.remote}")
        raise web.HTTPFound(URL("/login").with_query(error=1, next=next_url))

    response = web.HTTPFound(next_url)
    response.set_cookie(
        SESSION_COOKIE,
        users.session_cookie_value(submitted),
        max_age=SESSION_MAX_AGE,
        httponly=True,
        samesite="Lax",
        secure=request.secure or request.headers.get("X-Forwarded-Proto") == "https",
        path="/",
    )
    raise response


def setup_auth(app: web.Application) -> None:
    """Install the auth middleware (then the addon middleware, inside
    it) and the login routes on ``app``."""
    app.middlewares.append(auth_middleware)
    app.middlewares.append(addon_middleware)
    app.router.add_get("/login", login_page)
    app.router.add_post("/login", login_submit)
    if users.env_token():
        hosts = ", ".join(sorted(_internal_hosts())) or "none"
        logger.info(f"API authentication enabled (internal hosts allowed on {', '.join(sorted(INBOUND_PATHS))}: {hosts})")
    else:
        logger.warning("API_TOKEN is not set — the web server accepts unauthenticated requests")
