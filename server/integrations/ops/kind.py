"""The ``ops`` integration kind (Ops)."""

from integrations.framework import Integration

# Admin only (core/access.py: AccessLevel.ops).
ops = Integration("ops", requires="ops", description="kubectl and sandboxed Python")
