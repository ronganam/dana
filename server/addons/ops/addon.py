"""The ``ops`` addon (Ops)."""

from core.addons import Addon

# Admin only (core/access.py: AccessLevel.ops).
addon = Addon("ops", requires="ops", description="kubectl and sandboxed Python")
