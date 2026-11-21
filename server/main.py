import asyncio
import logging
import os
import sys
from dotenv import load_dotenv

# Load environment configuration before importing our packages: several modules
# (e.g. core.paths) resolve configuration at import time.
load_dotenv()

# Import our packages
from core.telegram_bot import TelegramBot
from core.server import start_webhook_listener
from core import auth, clock, db, llm, session_runner, users
from core.paths import DATA_DIR, ensure_data_dirs
from addons import bootstrap as addons_bootstrap
from core.addons import registry, store
from core.addons.crypto import CredentialsKeyError

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger("ganam-assistant")

async def main():
    logger.info("Initializing modular AI assistant...")
    # Fail closed: no API_TOKEN means no authentication, allowed only for local development.
    problem = auth.startup_problem()
    if problem:
        logger.error(problem)
        sys.exit(1)
    ensure_data_dirs()
    logger.info(f"Data directory: {DATA_DIR}")
    # The schema, then the data every part relies on, before anything reads
    # the database. A database older than 8.1.0 stops the start here.
    try:
        applied = db.migrate()
    except db.SchemaError as e:
        logger.error(str(e))
        sys.exit(1)
    if applied:
        logger.info(f"Database migrated: {', '.join(applied)}")
    users.bootstrap()
    # Addons: the credentials key (generated into the data directory when
    # CREDENTIALS_KEY is unset), then the first admin's default addons (once).
    # A wrong key stops the start here.
    try:
        addons_bootstrap.bootstrap()
    except CredentialsKeyError as e:
        logger.error(str(e))
        sys.exit(1)
    # What each addon prepares for its users at start (default lists, prompt
    # blocks…) runs as its at_start jobs once the web server listens.
    # Resolve the user's timezone up front (may query Home Assistant) so it is
    # never resolved lazily with a blocking call inside the event loop.
    clock.get_timezone()
    
    # 1. Each user's addons and the first admin's tools (every addon loaded on import: core/addons/loader.py)
    for user in users.list_users():
        logger.info(f"User {user.id} ({user.access}): {', '.join(store.connected_addons(user))}")
    admin = users.first_admin()
    tools = registry.tool_schemas(admin) if admin is not None else []
    logger.info(f"The first admin's tools: {len(tools)} (their descriptions at DEBUG)")
    for t in tools:
        logger.debug(f" - {t['function']['name']}: {t['function']['description']}")
        
    # 2. Telegram is optional: without a bot token Dana runs web / app only
    telegram_token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not telegram_token:
        logger.warning("TELEGRAM_BOT_TOKEN is not set: running without the Telegram bot (web and apps only).")

    llm.log_configuration()

    # 3. The conversation core: the agent every channel's runs use
    logger.info(f"Configuring ReAct loop with model: {session_runner.agent().model}")

    # 4. The Telegram channel adapter (also core.notify's sender), when configured
    bot = TelegramBot(token=telegram_token) if telegram_token else None
    
    # 5. The web server (with the background loops and the addons' jobs), then Telegram long polling
    webhook_port = int(os.getenv("PORT", "8080"))
    
    runner = None
    try:
        runner = await start_webhook_listener(port=webhook_port, host=auth.bind_host())
        if bot is not None:
            await bot.start()
        else:
            await asyncio.Event().wait()  # serve the web server until stopped
    except Exception as e:
        logger.error(f"Critical error running assistant services: {e}", exc_info=True)
        sys.exit(1)
    finally:
        if runner:
            logger.info("Stopping webhook listener...")
            await runner.cleanup()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Shutting down personal assistant. Goodbye!")
