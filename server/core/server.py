"""The aiohttp app: creation, the auth middlewares, the route modules and the
background work started with it.

Every route lives in the module that owns it, each registering its routes
with ``setup(app)``: core's own (:data:`ROUTE_MODULES`, in this order), then
every loaded addon's ``api.py`` in the addon order (core/addons/loader.py).
Background work: core's own loops (usage, the briefing fallback, meeting
cards, the weekly review, the wake watch) and the addons' jobs and services
(core/addons/scheduler.py). Nothing here imports an addon.

Every route an addon's ``setup(app)`` adds is remembered as that addon's:
while the addon is disabled on the server (core/addons/state.py) those routes
answer 404 ``{"error": "addon_disabled", "addon": <name>}``
(:func:`disabled_addon_middleware`).
"""
import asyncio
import logging
import os

from aiohttp import web

from core import (actions_api, addons_api, app_updates, assistant_api, briefing, briefing_api, debug_api, now_api,
                  pages_api, sessions_api, sharing_api, usage, usage_api, wake, weekly_review)
from core.addons import loader, scheduler, state
from core.auth import Handler, setup_auth

logger = logging.getLogger(__name__)

# Core's route modules, in registration order; each one's setup(app) adds its
# routes (and its startup / cleanup hooks). The addons' come after them, then
# the static directory.
ROUTE_MODULES = (
    pages_api, assistant_api, app_updates, debug_api, sessions_api, briefing_api, actions_api, now_api,
    usage_api, sharing_api, addons_api,
)


# The addon that added each route resource (filled by setup_routes).
ROUTE_OWNERS = web.AppKey("route_owners", dict)
STATIC_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static")


def route_modules() -> tuple:
    """Every route module: core's, then each loaded addon's ``api.py``."""
    return ROUTE_MODULES + loader.route_modules()


@web.middleware
async def disabled_addon_middleware(request: web.Request, handler: Handler) -> web.StreamResponse:
    """404 ``addon_disabled`` for a route of an addon disabled on the server."""
    owner = request.app.get(ROUTE_OWNERS, {}).get(request.match_info.route.resource)
    if owner is not None and not state.is_enabled(owner):
        return web.json_response({"error": "addon_disabled", "addon": owner}, status=404)
    return await handler(request)


def setup_routes(app: web.Application) -> None:
    """Core's routes, every loaded addon's (each remembered as its own), the
    middleware that closes a disabled addon's routes, and the static folder.
    Call after :func:`core.auth.setup_auth`."""
    for module in ROUTE_MODULES:
        module.setup(app)
    owners = {}
    for name, module in loader.apis():
        before = set(app.router.resources())
        module.setup(app)
        owners.update({r: name for r in app.router.resources() if r not in before})
    app[ROUTE_OWNERS] = owners
    app.middlewares.append(disabled_addon_middleware)
    os.makedirs(STATIC_DIR, exist_ok=True)
    app.router.add_static("/static/", STATIC_DIR, name="static")


async def start_webhook_listener(port: int = 8080) -> web.AppRunner:
    """Initialize and start the webhook & Web UI server."""
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

    runner = web.AppRunner(app)
    await runner.setup()
    
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logger.info(f"Webhook & Web UI server listening on http://0.0.0.0:{port}")
    
    # Usage and cost tracking: writes the queued records, folds old days (core/usage.py).
    app["usage_task"] = asyncio.create_task(usage.run_loop())
    _start_core_background(app)
    # The addons' jobs (per user, as that user) and services.
    scheduler.start(app)
    return runner


def _start_core_background(app: web.Application) -> None:
    """Core's own loops, each for every user it applies to: meeting cards
    (calendar + phone), the 10:00 briefing fallback (users who get a
    briefing), the weekly review, and the wake fallback (the phone's screen
    history, in case no unlock signal arrives)."""
    app["meeting_cards_task"] = asyncio.create_task(briefing.meeting_cards_loop())
    app["briefing_fallback_task"] = asyncio.create_task(briefing.fallback_loop())
    app["weekly_review_task"] = asyncio.create_task(weekly_review.schedule_loop())
    app["wake_watch_task"] = asyncio.create_task(wake.wake_watch_loop())
