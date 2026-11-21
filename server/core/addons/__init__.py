"""The addons framework: addons that own tools, a user's connections
(encrypted credentials, settings), and tools listed and executed for a user.

- :mod:`.addon` — :class:`Addon` (declare an addon, ``@addon.tool``), :class:`Field`, :class:`PromptPart`.
- :mod:`.store` — connect / update / disconnect / get a user's addon; :class:`Connection`.
- :mod:`.registry` — a user's tool schemas, execute a tool for a user, approval flags.
- :mod:`.crypto` — Fernet encryption with ``CREDENTIALS_KEY``.
- :mod:`.addresses` — validate / normalise a service address, build request URLs that cannot leave it.
- :mod:`.sharing` — items shared between users (task lists, shopping lists, projects).
"""

from core.addons.addon import (ADDRESS, NAME, TELEGRAM_CHAT, TEXT, Field, Addon, InvalidValue,
                                         PromptPart, Tool)
from core.addons.store import Connection, Forbidden, NotConnected

__all__ = ["ADDRESS", "Connection", "Field", "Forbidden", "Addon", "InvalidValue", "NAME", "NotConnected",
           "PromptPart", "TELEGRAM_CHAT", "TEXT", "Tool"]
