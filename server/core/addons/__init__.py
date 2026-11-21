"""The addons framework: addons that own tools, a user's connections
(encrypted credentials, settings), and tools listed and executed for a user.

- :mod:`.addon` — :class:`Addon` (declare an addon, ``@addon.tool``, ``@addon.job``…), :class:`Field`, :class:`PromptPart`.
- :mod:`.hooks` — what the manifest and the hooks use: :class:`Web`, :class:`Nav`, :class:`Worker`,
  :class:`LlmJob`, :func:`minutes`, :class:`Card`, :class:`DayItem`, :class:`Section`, :class:`Hit`,
  :class:`Check`, :class:`Approval`…
- :mod:`.loader` — finding and importing every addon (official, site, image, installed).
- :mod:`.scheduler` — running jobs and services.
- :mod:`.store` — connect / update / disconnect / get a user's addon; :class:`Connection`.
- :mod:`.registry` — a user's tool schemas, execute a tool for a user, approval flags.
- :mod:`.crypto` — Fernet encryption with ``CREDENTIALS_KEY``.
- :mod:`.addresses` — validate / normalise a service address, build request URLs that cannot leave it.
- :mod:`.sharing` — items shared between users (task lists, shopping lists, projects).
"""

from core.addons.addon import (ADDRESS, NAME, TELEGRAM_CHAT, TEXT, Field, Addon, InvalidValue,
                               PromptPart, Tool)
from core.addons.hooks import (Approval, BriefingContext, Card, Check, ContextRequest, DayItem, Hit, LlmJob, Nav,
                               NowContext, Section, Web, Worker, hours, minutes)
from core.addons.store import Connection, Forbidden, NotConnected

__all__ = ["ADDRESS", "Approval", "BriefingContext", "Card", "Check", "Connection", "ContextRequest", "DayItem",
           "Field", "Forbidden", "Addon", "Hit", "InvalidValue", "LlmJob", "NAME", "Nav", "NotConnected", "NowContext",
           "PromptPart", "Section", "TELEGRAM_CHAT", "TEXT", "Tool", "Web", "Worker", "hours", "minutes"]
