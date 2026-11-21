"""WhatsApp addon (docs/access.md, "Addons"; docs/whatsapp.md)."""

from addons.whatsapp import addon  # noqa: F401 — declares the addon
from addons.whatsapp import tools, latest  # noqa: F401 — registers the whatsapp tools (in this order)
