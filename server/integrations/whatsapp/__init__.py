"""WhatsApp integration (docs/access.md, "Kinds"; docs/whatsapp.md)."""

from integrations.whatsapp.kind import whatsapp
from integrations.whatsapp import tools, latest  # noqa: F401 — registers the whatsapp tools (in this order)

__all__ = ["whatsapp"]
