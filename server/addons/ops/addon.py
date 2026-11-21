"""The ``ops`` addon (Ops)."""

from core.addons import Addon

# Admin only (core/access.py: AccessLevel.ops), and never turned on by itself:
# an admin connects it on purpose (Addons page, bin/users.py addons connect).
addon = Addon("ops", order=160, requires="ops",
              description="Read-only kubectl and Python run on the server (asks first; not a sandbox)")
