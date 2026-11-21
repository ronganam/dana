import json
import logging
import os
import resource
import subprocess
import sys
import tempfile
from typing import Any, Dict, Optional

import httpx
from core.addons import Approval, Connection
from core.users import User
from addons.ops.addon import addon

logger = logging.getLogger(__name__)

SA_TOKEN_PATH = "/var/run/secrets/kubernetes.io/serviceaccount/token"
SA_CA_PATH = "/var/run/secrets/kubernetes.io/serviceaccount/ca.crt"
SA_NAMESPACE_PATH = "/var/run/secrets/kubernetes.io/serviceaccount/namespace"

API_SERVER = os.getenv("KUBERNETES_SERVICE_HOST", "")
API_PORT = os.getenv("KUBERNETES_SERVICE_PORT", "443")
API_BASE = f"https://{API_SERVER}:{API_PORT}" if API_SERVER else None


def _load_sa_token() -> Optional[str]:
    try:
        with open(SA_TOKEN_PATH) as f:
            return f.read().strip()
    except (FileNotFoundError, OSError):
        return None


def _load_sa_ca() -> Optional[str]:
    try:
        return SA_CA_PATH
    except (FileNotFoundError, OSError):
        return None


def _load_namespace() -> Optional[str]:
    try:
        with open(SA_NAMESPACE_PATH) as f:
            return f.read().strip()
    except (FileNotFoundError, OSError):
        return None


def _build_headers() -> dict:
    token = _load_sa_token()
    if not token:
        return {}
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def _verify() -> bool:
    return API_BASE is not None and _load_sa_token() is not None


def _describe_kubectl(user: Optional[User], a: Dict[str, Any]) -> Approval:
    return Approval("Run kubectl on the cluster", "Cluster", "⚙️", "Run this command?",
                    body_label="Command", body=a.get("command"), facts=[("Namespace", a.get("namespace"))])


@addon.tool(acts_outside=True, describe=_describe_kubectl)
async def run_kubectl(
    conn: Connection,
    command: str,
    namespace: Optional[str] = None,
) -> str:
    """Run a kubectl command against the Kubernetes cluster using the in-cluster service account.

    Translates kubectl-style commands into direct Kubernetes API calls.
    Supported read-only verbs: get, describe, logs, top.
    Write operations (create, delete, patch, apply) are NOT supported.

    Examples:
      - "get pods" — list pods in namespace
      - "get pods -o wide" — list pods with more details
      - "get services" — list services
      - "get deployments" — list deployments
      - "describe pod my-pod" — describe a specific resource
      - "logs my-pod" — get logs of a pod
      - "logs my-pod -c my-container" — get logs for a specific container
      - "top pods" — show pod resource usage
      - "get nodes" — list nodes
      - "get events" — list events

    Args:
        command: The kubectl-style command to execute (e.g. 'get pods', 'describe deployment xxx').
        namespace: The Kubernetes namespace to operate in. Defaults to the pod's own namespace.
    """
    if not _verify():
        return "Error: Not running inside a Kubernetes pod or no service account token found."

    parts = command.strip().split()
    if not parts:
        return "Error: Command cannot be empty."

    # Scan for -n/--namespace and -o/--output in all parts
    ns_override = None
    output_format = None
    filtered = []
    skip_next = False
    for i, flag in enumerate(parts):
        if skip_next:
            skip_next = False
            continue
        if flag in ("-n", "--namespace"):
            if i + 1 < len(parts):
                ns_override = parts[i + 1]
                skip_next = True
        elif flag.startswith("--namespace="):
            ns_override = flag.split("=", 1)[1]
        elif flag in ("-o", "--output"):
            if i + 1 < len(parts):
                output_format = parts[i + 1]
                skip_next = True
        elif flag.startswith("-o=") or flag.startswith("--output="):
            output_format = flag.split("=", 1)[1]
        else:
            filtered.append(flag)

    # Re-parse verb/resource/name from filtered tokens
    if not filtered:
        return "Error: Command cannot be empty."
    verb = filtered[0].lower()
    resource = filtered[1].lower() if len(filtered) > 1 else ""
    name = filtered[2] if len(filtered) > 2 else None
    extra_flags = filtered[3:] if len(filtered) > 3 else []

    # 'logs' takes a pod name directly, not a resource type.
    # Fix: "logs my-pod" → verb=logs, name=my-pod (not resource=my-pod)
    if verb == "logs" and resource and not name:
        name = resource
        resource = "pods"
    if verb == "describe" and resource in ("secret", "secrets"):
        return "Error: describing a secret would show its values; only listing secret names is supported."

    effective_ns = ns_override or namespace or _load_namespace() or "default"

    headers = _build_headers()
    ca_path = _load_sa_ca()
    verify = ca_path if ca_path else True

    api_url = f"{API_BASE}/api/v1"
    if resource in ("deployments", "deployment", "statefulsets", "statefulset",
                    "daemonsets", "daemonset", "ingresses", "ingress",
                    "networkpolicies", "networkpolicy"):
        api_url = f"{API_BASE}/apis/apps/v1"
    elif resource in ("services", "service", "endpoints", "endpoint"):
        api_url = f"{API_BASE}/api/v1"
    elif resource in ("nodes", "node"):
        api_url = f"{API_BASE}/api/v1"
        effective_ns = None
    elif resource in ("events", "event"):
        api_url = f"{API_BASE}/api/v1"
    elif resource in ("all",):
        api_url = f"{API_BASE}/api/v1"

    async with httpx.AsyncClient(verify=verify, timeout=30.0) as client:
        if verb == "get":
            if resource == "all":
                return "Error: 'get all' is not supported via API. Use specific resource types."
            resource_plural = resource + "s" if not resource.endswith("s") else resource
            if resource in ("deployment", "service", "node", "pod", "event"):
                resource_plural = resource + "s" if resource != "services" else "services"

            # Map to proper k8s API plurals
            plural_map = {
                "pod": "pods", "pods": "pods",
                "service": "services", "services": "services",
                "deployment": "deployments", "deployments": "deployments",
                "node": "nodes", "nodes": "nodes",
                "event": "events", "events": "events",
                "namespace": "namespaces", "namespaces": "namespaces",
                "secret": "secrets", "secrets": "secrets",
                "configmap": "configmaps", "configmaps": "configmaps",
                "ingress": "ingresses", "ingresses": "ingresses",
                "statefulset": "statefulsets", "statefulsets": "statefulsets",
                "daemonset": "daemonsets", "daemonsets": "daemonsets",
                "endpoint": "endpoints", "endpoints": "endpoints",
                "pv": "persistentvolumes", "persistentvolume": "persistentvolumes",
                "pvc": "persistentvolumeclaims", "persistentvolumeclaim": "persistentvolumeclaims",
                "sc": "storageclasses", "storageclass": "storageclasses",
                "ns": "namespaces",
            }
            resource_plural = plural_map.get(resource, resource + "s")

            if name:
                url = f"{api_url}/namespaces/{effective_ns}/{resource_plural}/{name}"
            elif effective_ns and resource_plural != "nodes" and resource_plural != "namespaces":
                url = f"{api_url}/namespaces/{effective_ns}/{resource_plural}"
            elif resource_plural == "nodes" or resource_plural == "namespaces":
                url = f"{api_url}/{resource_plural}"
            else:
                url = f"{api_url}/namespaces/{effective_ns}/{resource_plural}"

            try:
                r = await client.get(url, headers=headers)
            except httpx.ConnectError as e:
                return f"Error: Cannot connect to Kubernetes API server: {e}"

            if r.status_code == 403:
                return (
                    f"Error: Forbidden — the service account does not have permission to {verb} {resource}.\n"
                    "To fix, the cluster admin needs to grant RBAC permissions. Apply:\n"
                    "---\n"
                    "apiVersion: rbac.authorization.k8s.io/v1\n"
                    "kind: ClusterRole\n"
                    "metadata:\n"
                    "  name: dana-reader\n"
                    "rules:\n"
                    "- apiGroups: [\"\"]\n"
                    "  resources: [\"pods\", \"services\", \"events\", \"nodes\", \"namespaces\",\n"
                    "              \"persistentvolumes\", \"persistentvolumeclaims\",\n"
                    "              \"secrets\", \"configmaps\", \"endpoints\"]\n"
                    "  verbs: [\"get\", \"list\", \"watch\"]\n"
                    "- apiGroups: [\"apps\"]\n"
                    "  resources: [\"deployments\", \"statefulsets\", \"daemonsets\"]\n"
                    "  verbs: [\"get\", \"list\", \"watch\"]\n"
                    "- apiGroups: [\"metrics.k8s.io\"]\n"
                    "  resources: [\"pods\", \"nodes\"]\n"
                    "  verbs: [\"get\", \"list\"]\n"
                    "- apiGroups: [\"networking.k8s.io\"]\n"
                    "  resources: [\"ingresses\", \"networkpolicies\"]\n"
                    "  verbs: [\"get\", \"list\", \"watch\"]\n"
                    "- nonResourceURLs: [\"/api\", \"/apis\"]\n"
                    "  verbs: [\"get\"]\n"
                    "---\n"
                    "apiVersion: rbac.authorization.k8s.io/v1\n"
                    "kind: ClusterRoleBinding\n"
                    "metadata:\n"
                    "  name: dana-reader-binding\n"
                    "roleRef:\n"
                    "  apiGroup: rbac.authorization.k8s.io\n"
                    "  kind: ClusterRole\n"
                    "  name: dana-reader\n"
                    "subjects:\n"
                    "- kind: ServiceAccount\n"
                    "  name: default\n"
                    "  namespace: <your namespace>\n"
                )
            if r.status_code == 404:
                return f"Error: Resource not found. Check the resource type and name."
            if r.status_code != 200:
                return f"Error: Kubernetes API returned HTTP {r.status_code}: {r.text[:500]}"

            data = r.json()

            if output_format == "yaml" or output_format == "json":
                return json.dumps(data, indent=2, default=str)

            items = data.get("items", [data] if not data.get("items") else [])
            if not items:
                return f"No {resource_plural} found in namespace '{effective_ns}'."

            lines = []
            for item in items:
                meta = item.get("metadata", {})
                item_name = meta.get("name", "?")
                status = item.get("status", {})
                phase = status.get("phase", status.get("state", ""))
                kind = item.get("kind", resource.capitalize())
                api_ver = item.get("apiVersion", "")
                creation_ts = meta.get("creationTimestamp", "")[:10] if meta.get("creationTimestamp") else ""

                if resource_plural == "pods":
                    host_ip = status.get("hostIP", "")
                    pod_ip = status.get("podIP", "")
                    node_name = status.get("hostIP", status.get("nodeName", ""))
                    containers = (item.get("spec") or {}).get("containers", [])
                    container_str = ",".join(c.get("name", "") for c in containers[:3])
                    lines.append(f"{item_name:40s} {phase:15s} IP={pod_ip:15s} Node={node_name:15s} Containers=[{container_str}]")
                elif resource_plural == "services":
                    spec = item.get("spec", {})
                    cluster_ip = spec.get("clusterIP", "")
                    ports = spec.get("ports", [])
                    port_str = ",".join(f"{p.get('port','?')}/{p.get('protocol','TCP')}" for p in ports[:5])
                    lines.append(f"{item_name:40s} CLUSTER_IP={cluster_ip:15s} PORTS={port_str}")
                elif resource_plural == "deployments":
                    spec = item.get("spec", {})
                    replicas = spec.get("replicas", "?")
                    ready = (status.get("readyReplicas") or status.get("availableReplicas") or 0)
                    image = ""
                    containers = (spec.get("template", {}).get("spec", {})).get("containers", [])
                    if containers:
                        image = containers[0].get("image", "")[:30]
                    lines.append(f"{item_name:40s} replicas={replicas} ready={ready} image={image}")
                elif resource_plural == "nodes":
                    alloc = status.get("allocatable", {})
                    cpu = alloc.get("cpu", "?")
                    mem = alloc.get("memory", "?")
                    conds = status.get("conditions", [])
                    node_status = "Ready" if any(c.get("type") == "Ready" and c.get("status") == "True" for c in conds) else "NotReady"
                    lines.append(f"{item_name:40s} STATUS={node_status:10s} CPU={cpu:8s} MEM={mem}")
                elif resource_plural == "events":
                    count = item.get("count", "?")
                    reason = item.get("reason", "")
                    msg = item.get("message", "")[:80]
                    lines.append(f"{creation_ts} | {reason:20s} | count={str(count):>5s} | {msg}")
                elif resource_plural == "secrets" or resource_plural == "configmaps":
                    lines.append(f"{item_name:40s} created={creation_ts}")
                elif resource_plural == "persistentvolumes":
                    spec = item.get("spec", {})
                    capacity = (spec.get("capacity") or {}).get("storage", "?")
                    claim = (spec.get("claimRef") or {}).get("name", "")
                    lines.append(f"{item_name:40s} CAP={capacity:10s} CLAIM={claim}")
                elif resource_plural == "persistentvolumeclaims":
                    spec = item.get("spec", {})
                    capacity = (status.get("capacity") or {}).get("storage", "?")
                    phase_s = status.get("phase", "")
                    lines.append(f"{item_name:40s} STATUS={phase_s:10s} CAP={capacity}")
                else:
                    lines.append(f"{item_name:40s} kind={kind:15s} created={creation_ts}")

            if not lines:
                return json.dumps(data, indent=2, default=str)

            summary = f"Found {len(items)} {resource_plural} in namespace '{effective_ns}':\n"
            summary += "\n".join(lines)
            return summary

        elif verb == "describe":
            if not name:
                return f"Error: 'describe' requires a resource name. Usage: describe {resource} <name>"
            resource_plural = resource + "s" if not resource.endswith("s") else resource
            plural_map = {
                "pod": "pods", "pods": "pods",
                "service": "services", "services": "services",
                "deployment": "deployments", "deployments": "deployments",
                "node": "nodes", "nodes": "nodes",
                "event": "events", "events": "events",
                "secret": "secrets", "secrets": "secrets",
                "configmap": "configmaps", "configmaps": "configmaps",
            }
            resource_plural = plural_map.get(resource, resource + "s")

            if resource_plural == "nodes":
                url = f"{api_url}/{resource_plural}/{name}"
            else:
                url = f"{api_url}/namespaces/{effective_ns}/{resource_plural}/{name}"

            try:
                r = await client.get(url, headers=headers)
            except httpx.ConnectError as e:
                return f"Error: Cannot connect to Kubernetes API server: {e}"

            if r.status_code == 403:
                return f"Error: Forbidden — the service account does not have permission to describe {resource}."
            if r.status_code == 404:
                return f"Error: {resource} '{name}' not found in namespace '{effective_ns}'."
            if r.status_code != 200:
                return f"Error: HTTP {r.status_code}: {r.text[:500]}"

            return json.dumps(r.json(), indent=2, default=str)

        elif verb == "logs":
            if not name:
                return "Error: 'logs' requires a pod name."
            resource_plural = "pods"
            url = f"{api_url}/namespaces/{effective_ns}/{resource_plural}/{name}/log"

            container_param = ""
            if extra_flags:
                for i, flag in enumerate(extra_flags):
                    if flag == "-c" and i + 1 < len(extra_flags):
                        container_param = f"?container={extra_flags[i+1]}"

            try:
                r = await client.get(url + container_param, headers=headers)
            except httpx.ConnectError as e:
                return f"Error: Cannot connect to Kubernetes API server: {e}"

            if r.status_code == 403:
                return f"Error: Forbidden — the service account does not have permission to read logs of pod '{name}'."
            if r.status_code == 404:
                return f"Error: Pod '{name}' not found in namespace '{effective_ns}'."
            if r.status_code != 200:
                return f"Error: HTTP {r.status_code}: {r.text[:500]}"

            log_text = r.text
            if len(log_text) > 100_000:
                log_text = log_text[:100_000] + "\n... (truncated to 100K chars)"
            if not log_text.strip():
                return f"Pod '{name}' has no logs."
            return f"Logs for pod '{name}' (namespace: {effective_ns}):\n{log_text}"

        elif verb == "top":
            if resource not in ("pods", "pod", "nodes", "node"):
                return "Error: 'top' supports only 'pods' or 'nodes'."
            resource_plural = "pods" if resource in ("pods", "pod") else "nodes"

            # Use the metrics API
            metrics_url = f"{API_BASE}/apis/metrics.k8s.io/v1beta1"
            if resource_plural == "nodes":
                url = f"{metrics_url}/{resource_plural}"
            else:
                url = f"{metrics_url}/namespaces/{effective_ns}/{resource_plural}"

            try:
                r = await client.get(url, headers=headers)
            except httpx.ConnectError as e:
                return f"Error: Cannot connect to Kubernetes metrics API: {e}"

            if r.status_code == 404:
                return "Error: Metrics API not available (metrics-server may not be installed)."
            if r.status_code == 403:
                return (
                    "Error: Forbidden — service account cannot access metrics API.\n"
                    "The ClusterRole needs metrics.k8s.io access (see the 'dana-reader' ClusterRole)."
                )
            if r.status_code != 200:
                return f"Error: HTTP {r.status_code}: {r.text[:500]}"

            data = r.json()
            items = data.get("items", [])
            if not items:
                return f"No metrics found for {resource_plural}."

            lines = []
            for item in items:
                meta = item.get("metadata", {})
                item_name = meta.get("name", "?")
                usage = item.get("usage", {})
                cpu = usage.get("cpu", "?")
                mem = usage.get("memory", "?")
                lines.append(f"{item_name:40s} CPU={cpu:10s} MEM={mem}")

            return f"Resource usage for {resource_plural}:\n" + "\n".join(lines)

        else:
            return f"Error: Unsupported verb '{verb}'. Supported: get, describe, logs, top."


# ---------------------------------------------------------------------------
# Python: a limited child process (not a sandbox)
# ---------------------------------------------------------------------------

MAX_MEMORY_BYTES = 256 * 1024 * 1024
MAX_OUTPUT_CHARS = 100_000
DEFAULT_TIMEOUT = 30
MAX_TIMEOUT = 60


def _set_limits(timeout: int):
    resource.setrlimit(resource.RLIMIT_CPU, (timeout, timeout + 10))
    resource.setrlimit(resource.RLIMIT_AS, (MAX_MEMORY_BYTES, MAX_MEMORY_BYTES))


def child_environment(workdir: str) -> dict:
    """The whole environment ``run_python``'s process gets: a fixed ``PATH``,
    the working directory as ``HOME`` / ``TMPDIR``, UTF-8 — nothing of the
    server's own environment (no API keys, tokens or ``CREDENTIALS_KEY``)."""
    return {"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": workdir, "TMPDIR": workdir, "LANG": "C.UTF-8",
            "PYTHONIOENCODING": "utf-8", "PYTHONDONTWRITEBYTECODE": "1"}


def _describe_python(user: Optional[User], a: Dict[str, Any]) -> Approval:
    return Approval("Run Python code", "Code", "🐍", "Run this code?", body_label="Code", body=a.get("code"))


@addon.tool(confirm=True, acts_outside=True, describe=_describe_python)
def run_python(conn: Connection, code: str, timeout: Optional[int] = None) -> str:
    """Run Python code on the server (asks the user first) and return its output.

    Use this for calculations, data processing, JSON manipulation, text parsing,
    format conversion, or file generation. Only use when no other specialized tool
    fits the task — prefer using dedicated tools for Home Assistant (call_ha_service),
    WhatsApp (send_whatsapp_message), Calendar (search_calendar), etc. instead of
    reimplementing their logic in Python.

    IMPORTANT: The provided code MUST use print() to produce any output that
    should be visible — only printed text is captured and returned. Do not use
    input() or interactive functions.

    Args:
        code: The Python code to execute. Use print() to return results.
        timeout: Maximum execution time in seconds (default 30, max 60).
    """
    effective_timeout = min(timeout or DEFAULT_TIMEOUT, MAX_TIMEOUT)

    with tempfile.TemporaryDirectory(prefix="dana-python-") as tmpdir:
        script_path = os.path.join(tmpdir, "script.py")
        try:
            with open(script_path, "w") as f:
                f.write(code)
        except OSError as e:
            return f"Error: Failed to write script to temp directory: {e}"

        try:
            proc = subprocess.run(
                [sys.executable, "-I", "-u", script_path],
                capture_output=True,
                env=child_environment(tmpdir),
                stdin=subprocess.DEVNULL,
                text=True,
                timeout=effective_timeout,
                preexec_fn=lambda: _set_limits(effective_timeout),
                cwd=tmpdir,
            )
        except subprocess.TimeoutExpired:
            return (
                f"Execution timed out after {effective_timeout} seconds.\n"
                f"The code took too long to finish. Consider optimizing it or "
                f"increasing the timeout."
            )
        except OSError as e:
            return f"Error: Failed to run subprocess: {e}"

        if proc.returncode != 0:
            stderr = proc.stderr.strip() or "(no stderr output)"
            return f"Execution failed with exit code {proc.returncode}:\n{stderr}"

        stdout = (proc.stdout or "").strip()
        stderr = (proc.stderr or "").strip()

        if not stdout and stderr:
            return f"Warning — stderr was produced (non-fatal):\n{stderr}"

        if not stdout:
            return (
                "(no output — your code did not call print())\n"
                f"Stderr was:\n{stderr}"
            )

        if len(stdout) > MAX_OUTPUT_CHARS:
            stdout = stdout[:MAX_OUTPUT_CHARS]
            stdout += (
                f"\n\n--- Output truncated at {MAX_OUTPUT_CHARS} characters ---"
            )

        return stdout
