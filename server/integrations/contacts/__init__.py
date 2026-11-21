"""Google Contacts integration (docs/contacts.md, docs/access.md "Kinds").

``config`` (the kind's name, the field mask, the sync interval), ``client``
(the People API over the user's connection), ``store`` (each user's copy,
search, the caller-name lookup), ``sync`` (full / incremental sync, the loop,
connect and disconnect), ``enrich`` (people filled in from the copy),
``tools``.
"""

from integrations.contacts.kind import contacts
from integrations.contacts import tools  # noqa: F401 — registers the contacts tools

__all__ = ["contacts"]
