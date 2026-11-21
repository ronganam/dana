"""The integrations framework: kinds that own tools, a user's connections
(encrypted credentials, settings), and tools listed and executed for a user.

- :mod:`.kind` — :class:`Integration` (declare a kind, ``@kind.tool``), :class:`Field`, :class:`PromptPart`.
- :mod:`.store` — connect / update / disconnect / get a user's integration; :class:`Connection`.
- :mod:`.registry` — a user's tool schemas, execute a tool for a user, approval flags.
- :mod:`.crypto` — Fernet encryption with ``CREDENTIALS_KEY``.
- :mod:`.addresses` — validate / normalise a service address, build request URLs that cannot leave it.
- :mod:`.sharing` — items shared between users (task lists, shopping lists, projects).
"""

from integrations.framework.kind import (ADDRESS, NAME, TELEGRAM_CHAT, TEXT, Field, Integration, InvalidValue,
                                         PromptPart, Tool)
from integrations.framework.store import Connection, Forbidden, NotConnected

__all__ = ["ADDRESS", "Connection", "Field", "Forbidden", "Integration", "InvalidValue", "NAME", "NotConnected",
           "PromptPart", "TELEGRAM_CHAT", "TEXT", "Tool"]
