"""About you addon: short facts Dana keeps about each user, always in
that user's system prompt (docs/about.md, docs/access.md "Addons").

``values`` (sections, limits, the text rules every writer shares), ``facts``
(each user's facts: store, size cap, lookups; rebuilds their prompt block),
``prompt`` (the block and its per-user cache, the addon's prompt section),
``legacy`` (the old ``memories`` table and the old ``/api/memories`` shape),
``learner`` (per-user background learning from quiet chats and calls),
``api`` (``/api/about`` routes, starts the learner), ``tools``.
"""

from addons.about import addon  # noqa: F401 — declares the addon
from addons.about import tools  # noqa: F401 — registers the about tools
