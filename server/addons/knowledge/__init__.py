"""Knowledge addon: projects and the entries saved in them, per user
with sharing (docs/access.md, "Addons", "Sharing").

``projects`` (owner, sharing item type ``project``), ``entries`` (entries
follow their project; FTS search), ``media`` (link download / transcript /
summary, run as the entry's owner; the ``/media/`` files), ``values``
(vocabulary, normalisation, payload shapes), ``tools``.
"""

from addons.knowledge import addon  # noqa: F401 — declares the addon
from addons.knowledge import projects  # noqa: F401 — registers the `project` sharing item type
from addons.knowledge import tools  # noqa: F401 — registers the knowledge tools
