"""The aiohttp app: creation, the auth middlewares, the route modules and the
background work started with it.

Every route lives in the module that owns it, each registering its routes
with ``setup(app)``: core's own (:data:`ROUTE_MODULES`, in this order), then
every loaded addon's ``api.py`` in the addon order (core/addons/loader.py).
Background work: core's own loops (usage, the briefing fallback, meeting
cards, the weekly review, the wake watch, app updates from GitHub Releases, the backup schedule, live activities) and the addons' jobs and services
(core/addons/scheduler.py). Nothing here imports an addon.

Every route an addon's ``setup(app)`` adds is remembered as that addon's:
while the addon is disabled on the server (core/addons/state.py) those routes
answer 404 ``{"error": "addon_disabled", "addon": <name>}``
(:func:`disabled_addon_middleware`).

A site / installed addon's ``setup`` runs on a scratch application first
(:func:`_isolated_setup`): when it raises (anything but ``KeyboardInterrupt`` /
``SystemExit``), adds a middleware or a static folder, or adds a route that
would shadow one already there (core's, an earlier addon's) or anything under
``/static/``, none of its routes are added and the addon is taken out
(``loader.unload``, recorded as failed). Otherwise its routes, signals and
app keys move to the real app. An official addon's ``setup`` runs on the app
directly; its failure stops the start.
"""
import asyncio
import logging
import os
import re
from typing import Any, Dict, Optional, Tuple

from aiohttp import web
from aiohttp.web_urldispatcher import AbstractResource, DynamicResource, PlainResource, StaticResource

from core import (actions_api, addons_api, app_updates, assistant_api, backups, backups_api, briefing, briefing_api,
                  dashboard_api, debug_api, demo_api, devices_api, events, household_api, live, live_api, llm_api, now_api,
                  pages_api, profile_api,
                  sessions_api, setup_api, sharing_api, signals_api, usage, usage_api, wake, weekly_review, wizard_api,
                  zones_api)
from core.addons import loader, scheduler, state
from core.auth import Handler, setup_auth

logger = logging.getLogger(__name__)

# Core's route modules, in registration order; each one's setup(app) adds its
# routes (and its startup / cleanup hooks). The addons' come after them, then
# the static directory.
ROUTE_MODULES = (
    pages_api, assistant_api, app_updates, debug_api, sessions_api, briefing_api, actions_api, now_api,
    usage_api, sharing_api, addons_api, profile_api, devices_api, setup_api, household_api, demo_api, zones_api, wizard_api, llm_api,
    dashboard_api,
    signals_api, backups_api, live_api,
)


# The addon that added each route resource (filled by setup_routes).
ROUTE_OWNERS = web.AppKey("route_owners", dict)
STATIC_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static")


def route_modules() -> tuple:
    """Every route module: core's, then each loaded addon's ``api.py``."""
    return ROUTE_MODULES + tuple(module for _, module in loader.apis())


@web.middleware
async def disabled_addon_middleware(request: web.Request, handler: Handler) -> web.StreamResponse:
    """404 ``addon_disabled`` for a route of an addon disabled on the server."""
    owner = request.app.get(ROUTE_OWNERS, {}).get(request.match_info.route.resource)
    if owner is not None and not state.is_enabled(owner):
        return web.json_response({"error": "addon_disabled", "addon": owner}, status=404)
    return await handler(request)


# Paths no site / installed addon may take (besides every route already added).
RESERVED_PREFIXES = ("/static/",)
_VAR = re.compile(r"\{[^{}]*\}")


def _sample(resource: AbstractResource) -> Optional[str]:
    """A path ``resource`` answers (its variables filled with ``x``), or None."""
    info = resource.get_info()
    if isinstance(resource, PlainResource):
        return str(info.get("path", ""))
    if isinstance(resource, DynamicResource):
        return _VAR.sub("x", str(info.get("formatter", "")))
    if isinstance(resource, StaticResource):
        return str(info.get("prefix", "")).rstrip("/") + "/x"
    return None


def _answers(resource: AbstractResource, path: str) -> bool:
    """Whether ``resource`` would match a request for ``path``."""
    info = resource.get_info()
    if isinstance(resource, PlainResource):
        return info.get("path") == path
    if isinstance(resource, DynamicResource):
        return bool(info["pattern"].fullmatch(path))
    if isinstance(resource, StaticResource):
        prefix = str(info.get("prefix", "")).rstrip("/")
        return path == prefix or path.startswith(prefix + "/")
    return False


def _shadowing(resource: AbstractResource, existing: Tuple[AbstractResource, ...]) -> Optional[str]:
    """Why ``resource`` (a site / installed addon's) may not be added next to ``existing``, or None."""
    sample = _sample(resource)
    if sample is None:
        return f"a route of an unsupported kind ({type(resource).__name__})"
    if any(sample.startswith(p) or sample == p.rstrip("/") for p in RESERVED_PREFIXES):
        return f"its route {resource.canonical} is under {', '.join(RESERVED_PREFIXES)}"
    for old in existing:
        other = _sample(old)
        if _answers(old, sample) or (other is not None and _answers(resource, other)):
            return f"its route {resource.canonical} would shadow {old.canonical}"
    return None


def _isolated_setup(app: web.Application, name: str, module: Any) -> Optional[str]:
    """Run a site / installed addon's ``setup`` on a scratch app and move what
    it added to ``app``; why it was refused instead (nothing moved), or None."""
    scratch = web.Application()
    try:
        module.setup(scratch)
    except (KeyboardInterrupt, SystemExit):
        raise
    except BaseException as e:
        return f"its api.py setup(app) failed: {type(e).__name__}: {e}"
    if len(scratch.middlewares):
        return "its setup(app) adds a middleware (only core's apply to every request)"
    existing = tuple(app.router.resources())
    added = tuple(scratch.router.resources())
    for resource in added:
        if isinstance(resource, StaticResource):
            return "its setup(app) adds a static folder (its web/ files are served by core)"
        problem = _shadowing(resource, existing)
        if problem:
            return problem
    for resource in added:
        app.router.register_resource(resource)
    for signal in ("on_startup", "on_shutdown", "on_cleanup", "cleanup_ctx"):
        getattr(app, signal).extend(getattr(scratch, signal))
    for key in scratch:
        app[key] = scratch[key]
    return None


def setup_routes(app: web.Application) -> None:
    """Core's routes, every loaded addon's (each remembered as its own), the
    middleware that closes a disabled addon's routes, and the static folder.
    Call after :func:`core.auth.setup_auth`."""
    for module in ROUTE_MODULES:
        module.setup(app)
    owners: Dict[Any, str] = {}
    for name, module in loader.apis():
        if name not in loader.loaded():
            continue  # taken out with an addon it needs, whose routes were refused
        before = set(app.router.resources())
        if loader.folder(name).source.official:
            module.setup(app)
        else:
            refused = _isolated_setup(app, name, module)
            if refused:
                logger.error(f"The routes of the addon {name} were refused: {refused}")
                loader.unload(name, refused)
                continue
        owners.update({r: name for r in app.router.resources() if r not in before})
    app[ROUTE_OWNERS] = owners
    app.middlewares.append(disabled_addon_middleware)
    os.makedirs(STATIC_DIR, exist_ok=True)
    app.router.add_static("/static/", STATIC_DIR, name="static")


async def start_webhook_listener(port: int = 8080, host: str = "0.0.0.0") -> web.AppRunner:
    """Initialize and start the webhook & Web UI server on ``host``:``port``
    (127.0.0.1 in insecure local development: core/auth.py ``bind_host``)."""
    # Setup in-memory log handler
    root_logger = logging.getLogger()
    log_handler = None
    for h in root_logger.handlers:
        if isinstance(h, debug_api.InMemoryLogHandler):
            log_handler = h
            break
    if not log_handler:
        log_handler = debug_api.InMemoryLogHandler()
        formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
        log_handler.setFormatter(formatter)
        root_logger.addHandler(log_handler)

    app = web.Application()
    setup_auth(app)
    app["log_handler"] = log_handler
    setup_routes(app)
    # Before runner.setup(), which freezes the app's signal lists.
    app.on_cleanup.append(scheduler.stop)
    app.on_cleanup.append(events.stop)

    runner = web.AppRunner(app)
    await runner.setup()
    
    site = web.TCPSite(runner, host, port)
    await site.start()
    logger.info(f"Webhook & Web UI server listening on http://{host}:{port}")
    
    # Usage and cost tracking: writes the queued records, folds old days (core/usage.py).
    app["usage_task"] = asyncio.create_task(usage.run_loop())
    _start_core_background(app)
    # Event handlers emitted from worker threads run on this loop (core/events.py).
    events.bind(asyncio.get_running_loop())
    # The addons' jobs (per user, as that user) and services.
    scheduler.start(app)
    return runner


def _start_core_background(app: web.Application) -> None:
    """Core's own loops, each for every user it applies to: meeting cards
    (calendar + phone), the 10:00 briefing fallback (users who get a
    briefing), the weekly review, and the wake fallback (the phone's screen
    history, in case no unlock signal arrives); and, server-wide, the Android
    app updates read from GitHub Releases (core/app_updates.py), the backup
    schedule (core/backups.py) and the live activities' expiry, refreshes and
    trailing pushes (core/live.py)."""
    app["meeting_cards_task"] = asyncio.create_task(briefing.meeting_cards_loop())
    app["briefing_fallback_task"] = asyncio.create_task(briefing.fallback_loop())
    app["weekly_review_task"] = asyncio.create_task(weekly_review.schedule_loop())
    app["wake_watch_task"] = asyncio.create_task(wake.wake_watch_loop())
    app["app_releases_task"] = asyncio.create_task(app_updates.releases_loop())
    app["backups_task"] = asyncio.create_task(backups.schedule_loop())
    app["live_task"] = asyncio.create_task(live.run_loop())
