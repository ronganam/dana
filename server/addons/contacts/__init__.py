"""Google Contacts addon (docs/contacts.md, docs/access.md "Addons").

``config`` (the addon's name, the field mask, the sync interval), ``client``
(the People API over the user's connection), ``store`` (each user's copy,
search, the caller-name lookup), ``sync`` (full / incremental sync, the loop,
connect and disconnect), ``enrich`` (people filled in from the copy),
``tools``.
"""

from addons.contacts import addon  # noqa: F401 — declares the addon
from addons.contacts import tools  # noqa: F401 — registers the contacts tools
