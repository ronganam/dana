"""Home Assistant integration (docs/access.md, "Kinds")."""

from integrations.homeassistant.kind import homeassistant
from integrations.homeassistant import tools  # noqa: F401 — registers the homeassistant tools

__all__ = ["homeassistant"]
