"""The ``ops`` addon (Ops)."""

from core.addons import Addon, Need, Touches

# Admin only (core/access.py: AccessLevel.ops), and never turned on by itself:
# an admin connects it on purpose (Addons page, bin/dana addons connect).
addon = Addon("ops", order=160, requires="ops", title="Ops", icon="terminal",
              needs=(Need("hardware", "Dana running in Kubernetes, with a service account allowed to read", "run_kubectl calls the cluster's API with it"),),
              touches=Touches(hosts=("the Kubernetes API server",), acts_outside=True),
              description="Read-only kubectl and Python run on the server (asks first; not a sandbox)",
              features=("Looks at the cluster with read-only kubectl", "Runs Python on the server (asks first)"))
