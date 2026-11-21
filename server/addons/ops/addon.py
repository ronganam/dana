"""The ``ops`` addon (Ops)."""

from core.addons import Addon

# Admin only (core/access.py: AccessLevel.ops).
addon = Addon("ops", order=160, requires="ops", description="kubectl and sandboxed Python")
