"""The aiohttp app: creation, the auth middlewares, the route modules and the
background work started with it.

Every route lives in the module that owns it, each registering its routes
with ``setup(app)``: core's own (:data:`ROUTE_MODULES`, in this order), then
every loaded addon's ``api.py`` in the addon order (core/addons/loader.py).
Background work: core's own loops (usage, the briefing fallback, meeting
cards, the weekly review, the wake watch) and the addons' jobs and services
(core/addons/scheduler.py). Nothing here imports an addon.
"""
import asyncio
import logging
import os

from aiohttp import web

from core import (actions_api, addons_api, app_updates, assistant_api, briefing, briefing_api, debug_api, now_api,
                  pages_api, profile_api, sessions_api, sharing_api, usage, usage_api, wake, weekly_review)
from core.addons import loader, scheduler
from core.auth import setup_auth

logger = logging.getLogger(__name__)

# Core's route modules, in registration order; each one's setup(app) adds its
# routes (and its startup / cleanup hooks). The addons' come after them, then
# the static directory.
ROUTE_MODULES = (
    pages_api, assistant_api, app_updates, debug_api, sessions_api, briefing_api, actions_api, now_api,
    usage_api, sharing_api, addons_api, profile_api,
)


def route_modules() -> tuple:
    """Every route module: core's, then each loaded addon's ``api.py``."""
    return ROUTE_MODULES + loader.route_modules()


async def start_webhook_listener(port: int = 8080, host: str = "0.0.0.0") -> web.AppRunner:
    """Initialize and start the webhook & Web UI server on ``host``:``port``
    (127.0.0.1 in insecure development: core/auth.py ``bind_host``)."""
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
    
    # Static files & Root
    static_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static")
    if not os.path.exists(static_dir):
        os.makedirs(static_dir, exist_ok=True)
        
    for module in route_modules():
        module.setup(app)

    # Serve static directory after endpoints
    app.router.add_static("/static/", static_dir, name="static")

    runner = web.AppRunner(app)
    await runner.setup()
    
    site = web.TCPSite(runner, host, port)
    await site.start()
    logger.info(f"Webhook & Web UI server listening on http://{host}:{port}")
    
    # Usage and cost tracking: writes the queued records, folds old days (core/usage.py).
    app["usage_task"] = asyncio.create_task(usage.run_loop())
    _start_core_background(app)
    # The addons' jobs (per user, as that user) and services.
    scheduler.start(app)
    app.on_cleanup.append(scheduler.stop)
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
