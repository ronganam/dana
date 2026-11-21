"""Knowledge integration: projects and the entries saved in them, per user
with sharing (docs/access.md, "Kinds", "Sharing").

``projects`` (owner, sharing item type ``project``), ``entries`` (entries
follow their project; FTS search), ``media`` (link download / transcript /
summary, run as the entry's owner; the ``/media/`` files), ``values``
(vocabulary, normalisation, payload shapes), ``tools``.
"""

from integrations.knowledge.kind import knowledge
from integrations.knowledge import projects  # noqa: F401 — registers the `project` sharing item type
from integrations.knowledge import tools  # noqa: F401 — registers the knowledge tools

__all__ = ["knowledge"]
