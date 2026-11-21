"""Shopping addon: the grocery list shared live in a household (docs/shopping.md).

- :mod:`.values` — constants, errors, timestamps;
- :mod:`.names` — the name key and typed text split into items;
- :mod:`.lists` — lists per user, the active list, sharing, invitations;
- :mod:`.items` — the items of a list: add / change / tick / remove, the 12-hour clear;
- :mod:`.aisles` — the fixed aisles and how an item gets one (the background model call);
- :mod:`.history` — what was bought, suggestions and usuals;
- :mod:`.journal` — the undo of one change;
- :mod:`.events` — list versions, the live stream and the phone push;
- :mod:`.invites` — answering an invitation;
- :mod:`.screen` — the screen payload; :mod:`.api` — the routes; :mod:`.tools` — Dana's tools.
"""

from addons.shopping import addon  # noqa: F401 — declares the addon
from addons.shopping import tools  # noqa: F401 — registers the shopping tools
