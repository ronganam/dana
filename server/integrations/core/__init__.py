"""Core integration (docs/access.md, "Kinds")."""

from integrations.core.kind import core
from integrations.core import tools  # noqa: F401 — registers the core tools

__all__ = ["core"]
