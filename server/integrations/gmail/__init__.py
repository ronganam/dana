"""Gmail integration (docs/gmail.md)."""

from integrations.gmail.kind import gmail
from integrations.gmail import tools  # noqa: F401 — registers the Gmail tools

__all__ = ["gmail"]
