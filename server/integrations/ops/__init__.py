"""Ops integration (docs/access.md, "Kinds")."""

from integrations.ops.kind import ops
from integrations.ops import tools  # noqa: F401 — registers the ops tools

__all__ = ["ops"]
