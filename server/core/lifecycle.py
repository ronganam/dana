"""Asking the server to restart (``POST /api/server/restart``, after installing
or removing an addon; docs/operations.md, "Restarting").

The server does not restart itself: :func:`request_restart` makes ``main.py``
stop the web server and the Telegram bot and exit with status 0, and the
supervisor starts it again (Docker's ``restart: unless-stopped`` /
``always``, Kubernetes' default ``restartPolicy: Always``). Run by hand
(``python main.py``), it just exits.
"""

import asyncio
import logging
from typing import Optional

logger = logging.getLogger(__name__)

_event: Optional[asyncio.Event] = None
_requested = False


def _get() -> asyncio.Event:
    global _event
    if _event is None:
        _event = asyncio.Event()
        if _requested:
            _event.set()
    return _event


def request_restart(reason: str = "") -> None:
    """Ask ``main.py`` to exit cleanly so the supervisor starts the server again."""
    global _requested
    _requested = True
    logger.warning(f"Restart requested{': ' + reason if reason else ''}; exiting for the supervisor to start Dana again")
    if _event is not None:
        _event.set()


def restart_requested() -> bool:
    """A restart was asked for in this process."""
    return _requested


async def wait_for_restart() -> None:
    """Return once :func:`request_restart` was called (``main.py`` waits on it next to the bot)."""
    await _get().wait()
