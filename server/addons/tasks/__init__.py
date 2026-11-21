"""Tasks addon (docs/access.md, "Addons"; docs/tasks-and-triggers.md).

- :mod:`.groups` — task lists per user (defaults, lookups, changes, sharing
  as item type ``task_list``);
- :mod:`.service` — the tasks a user can see and change, shared by the tools,
  the HTTP handlers and other services;
- :mod:`.links` — task links (calls, notifications, people);
- :mod:`.titles` — the emoji and list picked for a new task;
- :mod:`.triggers` — the trigger engine (time checker, location / car / wake);
- :mod:`.tools` — the tools.
"""

from addons.tasks import addon  # noqa: F401 — declares the addon
from addons.tasks import tools  # noqa: F401 — registers the tasks tools
