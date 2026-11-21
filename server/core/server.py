"""The aiohttp app: creation, the auth middlewares, the route modules and the
background loops started with it.

Every route lives in the module that owns it (``core/<area>_api.py`` or a
kind's ``integrations/<kind>/api.py``), each registering its routes with
``setup(app)``; :data:`ROUTE_MODULES` is the order they are registered in.
"""
import asyncio
import importlib
import importlib.util
import logging
import os

from aiohttp import web

from core import (actions_api, app_updates, assistant_api, briefing, briefing_api, checks, debug_api,
                  integrations_api, now_api, pages_api, sessions_api, sharing_api, usage, usage_api, wake,
                  weekly_review)
from core.auth import setup_auth
from integrations.about import api as about_api
from integrations.calendar import api as calendar_api
from integrations.google_account import api as google_api
from integrations.cameras import api as cameras_api
from integrations.contacts import sync as contacts_sync
from integrations.homeassistant import api as homeassistant_api, home_map
from integrations.homeassistant.watcher import Watchers as HAWatchers
from integrations.knowledge import api as knowledge_api
from integrations.money import api as money_api, schedule as money_schedule
from integrations.people import api as people_api, briefs as people_briefs
from integrations.phone import api as phone_api, config as phone_config
from integrations.phone.calls import api as calls_api, processing as calls_processing, retention as calls_retention
from integrations.phone.notifications import api as notifications_api
from integrations.phone.notifications import digest as notification_digest, escalation as notification_escalation
from integrations.phone.notifications import processing as notification_processing
from integrations.phone.notifications import retention as notification_retention
from integrations.shopping import api as shopping_api
from integrations.tasks import api as tasks_api, triggers as task_triggers
from integrations.telegram_account import api as telegram_account_api
from integrations.whatsapp import accounts as whatsapp_accounts, api as whatsapp_api

logger = logging.getLogger(__name__)

# Route modules, in registration order; each one's setup(app) adds its routes
# (and its startup / cleanup hooks). Imported explicitly above: a module that
# fails to import fails startup. The static directory is served after them.
ROUTE_MODULES = (
    pages_api, tasks_api, about_api, knowledge_api, assistant_api, app_updates, debug_api, calls_api, phone_api,
    notifications_api, sessions_api, whatsapp_api, calendar_api, google_api, homeassistant_api, cameras_api,
    people_api, briefing_api, actions_api, now_api, usage_api, sharing_api, integrations_api, shopping_api,
    money_api, telegram_account_api,
)


def plugin_route_modules() -> tuple:
    """The ``api`` module (with ``setup(app)``) of each plugin kind that has one (docs/plugins.md)."""
    import integrations
    return tuple(importlib.import_module(f"integrations.{kind}.api") for kind in integrations.PLUGIN_KINDS
                 if importlib.util.find_spec(f"integrations.{kind}.api") is not None)


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
    
    # Static files & Root
    static_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static")
    if not os.path.exists(static_dir):
        os.makedirs(static_dir, exist_ok=True)
        
    for module in ROUTE_MODULES + plugin_route_modules():
        module.setup(app)

    # Serve static directory after endpoints
    app.router.add_static("/static/", static_dir, name="static")

    runner = web.AppRunner(app)
    await runner.setup()
    
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logger.info(f"Webhook & Web UI server listening on http://0.0.0.0:{port}")
    
    # Usage and cost tracking: writes the queued records, folds old days (core/usage.py).
    app["usage_task"] = asyncio.create_task(usage.run_loop())

    # The phone's call and notification loops run per user (every user with
    # ``phone``, each as that user).
    _start_phone_background(app)

    # Every other loop runs per user (each user with the kinds it needs, as
    # that user, a failing one not stopping the next).
    _start_background(app)
    return runner


def _start_phone_background(app: web.Application) -> None:
    """Calls and notifications, for every user with ``phone`` (each as that
    user, a failing one not stopping the next): finish call processing and
    notification triage interrupted by a restart, drop call audio older than
    CALL_AUDIO_RETENTION_DAYS (transcripts are kept), keep notifications 30
    days, re-nudge unseen alerts, and send the digest at
    NOTIFICATION_DIGEST_TIMES."""
    for user in phone_config.phone_users():
        try:
            calls_processing.resume_unfinished(user)  # each call is processed as its owner
        except Exception as e:
            logger.error(f"Resuming call processing for user {user.id} failed: {e}", exc_info=True)
        try:
            notification_processing.resume_unfinished(user)
        except Exception as e:
            logger.error(f"Resuming notification triage for user {user.id} failed: {e}", exc_info=True)
    app["call_retention_task"] = asyncio.create_task(calls_retention.retention_loop())
    app["notif_retention_task"] = asyncio.create_task(notification_retention.retention_loop())
    app["notif_digest_task"] = asyncio.create_task(notification_digest.digest_loop())
    app["notif_renudge_task"] = asyncio.create_task(notification_escalation.renudge_loop())


def _start_background(app: web.Application) -> None:
    """Start the background loops; each runs for every user it applies to."""
    # Time-based trigger background checker
    interval = int(os.getenv("TIME_CHECK_INTERVAL", "30"))
    app["time_trigger_task"] = asyncio.create_task(task_triggers.time_checker_loop(
        interval, heartbeat=lambda: checks.mark("time_checker")))  # heartbeat for the self-check

    # People briefs (every user with people), meeting cards (calendar + phone),
    # the 10:00 briefing fallback (users who get a briefing) and the weekly
    # review (tasks).
    app["people_brief_task"] = asyncio.create_task(people_briefs.brief_loop())
    app["meeting_cards_task"] = asyncio.create_task(briefing.meeting_cards_loop())
    app["briefing_fallback_task"] = asyncio.create_task(briefing.fallback_loop())
    app["weekly_review_task"] = asyncio.create_task(weekly_review.schedule_loop())
    # Wake fallback (phone + Home Assistant): the phone's screen history, in
    # case no unlock signal arrives.
    app["wake_watch_task"] = asyncio.create_task(wake.wake_watch_loop())
    # Home Assistant, for every user who has it, each over their own
    # connection: the entity watchers that fire location / car triggers, and
    # the home maps for their prompts (fetched in the background; HA being
    # down only logs a warning).
    # WhatsApp: learn the account of connections that have none (WAHA being
    # down only delays it), so inbound events find their user.
    app["whatsapp_account_task"] = asyncio.create_task(whatsapp_accounts.account_loop())
    # Google Contacts: every user's copy brought up to date (every
    # CONTACTS_SYNC_MINUTES), their people filled in from it.
    app["contacts_sync_task"] = asyncio.create_task(contacts_sync.sync_loop())
    # Money: every user's bank / card logins synced at their slots (and when
    # a signal or "Sync now" pulls them earlier).
    app["money_sync_task"] = asyncio.create_task(money_schedule.loop())
    app["ha_watchers"] = HAWatchers(task_triggers.fire)
    app["ha_watch_task"] = asyncio.create_task(app["ha_watchers"].run_forever())
    try:
        app["home_map_tasks"] = home_map.warm_all()
    except Exception as e:
        logger.warning(f"Home map warm-up skipped: {e}")

