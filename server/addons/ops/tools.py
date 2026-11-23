"""The ops addon's tools: read-only kubectl over the in-cluster service account
(``run_kubectl``) and Python in a limited child process (``run_python``)."""

import asyncio
import json
import logging
import os
import resource
import subprocess
import sys
import tempfile
from typing import Any, Dict, List, Optional, Tuple

import httpx
from core.addons import Approval, Connection, addresses
from core.users import User
from addons.ops.addon import addon

logger = logging.getLogger(__name__)

SA_TOKEN_PATH = "/var/run/secrets/kubernetes.io/serviceaccount/token"
SA_CA_PATH = "/var/run/secrets/kubernetes.io/serviceaccount/ca.crt"
SA_NAMESPACE_PATH = "/var/run/secrets/kubernetes.io/serviceaccount/namespace"

API_SERVER = os.getenv("KUBERNETES_SERVICE_HOST", "")
API_PORT = os.getenv("KUBERNETES_SERVICE_PORT", "443")
API_BASE = f"https://{API_SERVER}:{API_PORT}" if API_SERVER else None

# Below core.agent.MAX_TOOL_RESULT_CHARS, so a long result is cut here with a note that says how to narrow it.
MAX_OUTPUT_CHARS = 10_000
MAX_LINES = 150
DEFAULT_TAIL = 200

# resource → (API path prefix, namespaced)
RESOURCES: Dict[str, Tuple[str, bool]] = {
    **{r: ("/api/v1", True) for r in ("pods", "services", "events", "configmaps", "secrets", "endpoints",
                                       "persistentvolumeclaims")},
    **{r: ("/api/v1", False) for r in ("nodes", "namespaces", "persistentvolumes")},
    **{r: ("/apis/apps/v1", True) for r in ("deployments", "statefulsets", "daemonsets", "replicasets")},
    **{r: ("/apis/batch/v1", True) for r in ("jobs", "cronjobs")},
    **{r: ("/apis/networking.k8s.io/v1", True) for r in ("ingresses", "networkpolicies")},
    "storageclasses": ("/apis/storage.k8s.io/v1", False),
}
ALIASES = {"po": "pods", "svc": "services", "ev": "events", "cm": "configmaps", "ep": "endpoints",
           "pvc": "persistentvolumeclaims", "pv": "persistentvolumes", "no": "nodes", "ns": "namespaces",
           "deploy": "deployments", "sts": "statefulsets", "ds": "daemonsets", "rs": "replicasets",
           "cj": "cronjobs", "ing": "ingresses", "netpol": "networkpolicies", "sc": "storageclasses"}
VALUE_FLAGS = {"-n": "namespace", "--namespace": "namespace", "-o": "output", "--output": "output",
               "-c": "container", "--container": "container", "--tail": "tail", "-l": "selector",
               "--selector": "selector"}


def _bounded(text: str, keep_end: bool = False) -> str:
    """``text`` cut to :data:`MAX_OUTPUT_CHARS` with a visible note (its end kept for logs and tracebacks)."""
    if len(text) <= MAX_OUTPUT_CHARS:
        return text
    note = f"[cut: {len(text):,} characters, showing the {'last' if keep_end else 'first'} {MAX_OUTPUT_CHARS:,}]"
    return f"{note}\n{text[-MAX_OUTPUT_CHARS:]}" if keep_end else f"{text[:MAX_OUTPUT_CHARS]}\n{note}"


def _resource(word: str) -> Optional[str]:
    w = ALIASES.get(word.lower(), word.lower())
    return next((r for r in (w, w + "s", w + "es") if r in RESOURCES), None)


def _parse(command: str) -> Tuple[List[str], Dict[str, str]]:
    """kubectl words → (positional words, flags by name; ``all`` for -A)."""
    words, flags = command.split(), {}
    positional: List[str] = []
    i = 0
    while i < len(words):
        w = words[i]
        key, _, value = w.partition("=")
        if w in ("-A", "--all-namespaces"):
            flags["all"] = "1"
        elif key in VALUE_FLAGS:
            if not value and i + 1 < len(words):
                i += 1
                value = words[i]
            flags[VALUE_FLAGS[key]] = value
        else:
            positional.append(w)
        i += 1
    return positional, flags


def _age(ts: Optional[str]) -> str:
    return (ts or "")[:16].replace("T", " ")


def _row(kind: str, item: Dict[str, Any]) -> str:
    """One compact line for a listed object, like kubectl's table."""
    meta, spec, status = item.get("metadata") or {}, item.get("spec") or {}, item.get("status") or {}
    name = meta.get("name", "?")
    if kind == "pods":
        cs = status.get("containerStatuses") or []
        waiting = next((c["state"]["waiting"].get("reason") for c in cs if (c.get("state") or {}).get("waiting")), None)
        return (f"{name}  {waiting or status.get('phase', '?')}  ready {sum(1 for c in cs if c.get('ready'))}/{len(cs)}"
                f"  restarts {sum(c.get('restartCount', 0) for c in cs)}  node {spec.get('nodeName', '?')}"
                f"  started {_age(status.get('startTime'))}")
    if kind in ("deployments", "statefulsets", "replicasets"):
        images = ",".join(c.get("image", "") for c in (spec.get("template") or {}).get("spec", {}).get("containers", []))
        return f"{name}  ready {status.get('readyReplicas') or 0}/{spec.get('replicas', '?')}  {images}"
    if kind == "daemonsets":
        return f"{name}  ready {status.get('numberReady', 0)}/{status.get('desiredNumberScheduled', '?')}"
    if kind == "services":
        ports = ",".join(f"{p.get('port')}/{p.get('protocol', 'TCP')}" for p in spec.get("ports") or [])
        return f"{name}  {spec.get('type', '')}  {spec.get('clusterIP', '')}  {ports}"
    if kind == "nodes":
        ready = any(c.get("type") == "Ready" and c.get("status") == "True" for c in status.get("conditions") or [])
        alloc = status.get("allocatable") or {}
        return f"{name}  {'Ready' if ready else 'NotReady'}  cpu {alloc.get('cpu', '?')}  memory {alloc.get('memory', '?')}"
    if kind == "events":
        obj = item.get("involvedObject") or {}
        return (f"{_age(item.get('lastTimestamp') or item.get('eventTime') or meta.get('creationTimestamp'))}  "
                f"{item.get('type', '')} {item.get('reason', '')}  {obj.get('kind', '')}/{obj.get('name', '')}  "
                f"×{item.get('count', 1)}  {(item.get('message') or '')[:160]}")
    if kind in ("persistentvolumes", "persistentvolumeclaims"):
        cap = (spec.get("capacity") or status.get("capacity") or {}).get("storage", "?")
        claim = (spec.get("claimRef") or {}).get("name") or spec.get("volumeName") or ""
        return f"{name}  {status.get('phase', '')}  {cap}  {claim}"
    return f"{name}  created {_age(meta.get('creationTimestamp'))}"


def _compact(data: Dict[str, Any]) -> str:
    """An object as JSON without the bulky bookkeeping (managedFields, last-applied annotation)."""
    for obj in data.get("items") or [data]:
        meta = obj.get("metadata") or {}
        meta.pop("managedFields", None)
        (meta.get("annotations") or {}).pop("kubectl.kubernetes.io/last-applied-configuration", None)
    return _bounded(json.dumps(data, indent=1, default=str))


def _describe_kubectl(user: Optional[User], a: Dict[str, Any]) -> Approval:
    return Approval("Run kubectl on the cluster", "Cluster", "⚙️", "Run this command?",
                    body_label="Command", body=a.get("command"), facts=[("Namespace", a.get("namespace"))])


@addon.tool(acts_outside=True, describe=_describe_kubectl)
async def run_kubectl(conn: Connection, command: str, namespace: Optional[str] = None) -> str:
    """Read the Kubernetes cluster with a kubectl-style command (read-only: get, describe, logs, top; no writes, no secret values).

    Examples: "get pods", "get pods -A", "get deploy -l app=dana", "get events", "describe pod NAME",
    "get pod NAME -o json", "logs POD -c CONTAINER --tail 500", "top pods", "top nodes".

    Args:
        command: The kubectl command without "kubectl". Flags: -n, -A, -o json|yaml, -l, -c, --tail (logs: last 200 lines by default).
        namespace: Namespace when the command has no -n; default the pod's own.
    """
    token = _read(SA_TOKEN_PATH)
    if API_BASE is None or not token:
        return "Error: Not running inside a Kubernetes pod or no service account token found."
    words, flags = _parse(command.strip().removeprefix("kubectl "))
    if not words:
        return "Error: Command cannot be empty."
    verb = words[0].lower()
    if len(words) > 1 and "/" in words[1]:  # pod/NAME
        words[1:2] = words[1].split("/", 1)
    if verb == "logs" and len(words) == 2:
        words = [verb, "pods", words[1]]
    if verb not in ("get", "describe", "logs", "top"):
        return f"Error: Unsupported verb '{verb}'. Supported: get, describe, logs, top."
    if len(words) < 2:
        return f"Error: '{verb}' needs a resource, e.g. '{verb} pods'."
    kind = _resource(words[1])
    if kind is None:
        return f"Error: unknown resource '{words[1]}'. Known: {', '.join(sorted(RESOURCES))}."
    name = words[2] if len(words) > 2 else None
    if verb == "logs" and kind != "pods":
        return f"Error: logs come from a pod: 'get pods -l app=NAME' finds it, then 'logs POD'."
    if verb in ("describe", "logs") and not name:
        return f"Error: '{verb}' needs a name: {verb} {kind} NAME."
    if kind == "secrets" and (name or flags.get("output")):
        return "Error: secret values are never shown; only 'get secrets' (their names) is supported."
    prefix, namespaced = RESOURCES[kind]
    ns = flags.get("namespace") or namespace or _read(SA_NAMESPACE_PATH) or "default"
    scope = f"/namespaces/{addresses.segment(ns)}" if namespaced and not (flags.get("all") and not name) else ""
    params: Dict[str, Any] = {}
    if verb == "top":
        if kind not in ("pods", "nodes"):
            return "Error: 'top' supports only pods or nodes."
        path = f"/apis/metrics.k8s.io/v1beta1{scope}/{kind}"
    else:
        path = f"{prefix}{scope}/{kind}" + (f"/{addresses.segment(name)}" if name else "")
    if verb == "logs":
        path += "/log"
        params["tailLines"] = flags.get("tail") or DEFAULT_TAIL
        if flags.get("container"):
            params["container"] = flags["container"]
    if flags.get("selector") and not name:
        params["labelSelector"] = flags["selector"]
    try:
        async with httpx.AsyncClient(verify=SA_CA_PATH if os.path.exists(SA_CA_PATH) else True, timeout=30.0) as client:
            r = await client.get(API_BASE + path, params=params, headers={"Authorization": f"Bearer {token}"})
    except httpx.HTTPError as e:
        logger.warning(f"run_kubectl: the API server failed: {e}", exc_info=True)
        return f"Error: Cannot reach the Kubernetes API server ({type(e).__name__})."
    where = "all namespaces" if not scope and namespaced else f"namespace '{ns}'" if scope else "the cluster"
    if r.status_code == 403:
        return (f"Error: Forbidden — the service account may not {verb} {kind}: grant it get/list on them "
                "(docs/access.md, ops).")
    if r.status_code == 404:
        if verb == "top":
            return "Error: Metrics API not available (metrics-server may not be installed)."
        return f"Error: {kind}{' ' + repr(name) if name else ''} not found in {where}."
    if r.status_code != 200:
        return f"Error: Kubernetes API returned HTTP {r.status_code}: {r.text[:300]}"
    if verb == "logs":
        return f"Logs of pod '{name}' ({where}, last {params['tailLines']} lines):\n" + _bounded(r.text, keep_end=True) \
            if r.text.strip() else f"Pod '{name}' has no logs."
    data = r.json()
    if verb == "describe" or flags.get("output") in ("json", "yaml"):
        return _compact(data)
    if name:
        return _row(kind, data)
    items = data.get("items") or []
    if not items:
        return f"No {kind} found in {where}."
    if kind == "events":
        items.sort(key=lambda e: e.get("lastTimestamp") or e.get("eventTime") or "", reverse=True)
    if verb == "top":
        lines = [f"{i['metadata']['name']}  cpu {i.get('usage', {}).get('cpu', '?')}  memory "
                 f"{i.get('usage', {}).get('memory', '?')}" for i in items[:MAX_LINES]]
    else:
        lines = [(f"{i['metadata'].get('namespace')}/" if not scope and namespaced else "") + _row(kind, i)
                 for i in items[:MAX_LINES]]
    more = f"\n…and {len(items) - MAX_LINES} more (narrow with -n or -l)." if len(items) > MAX_LINES else ""
    head = f"{len(items)} {kind} in {where}" + (" (newest first)" if kind == "events" else "") + ":\n"
    return _bounded(head + "\n".join(lines) + more)


def _read(path: str) -> Optional[str]:
    try:
        with open(path) as f:
            return f.read().strip() or None
    except OSError:
        return None


# ---------------------------------------------------------------------------
# Python: a limited child process (not a sandbox)
# ---------------------------------------------------------------------------

MAX_MEMORY_BYTES = 256 * 1024 * 1024
MAX_FILE_BYTES = 64 * 1024 * 1024
DEFAULT_TIMEOUT = 30
MAX_TIMEOUT = 60


def _set_limits(timeout: int) -> None:
    resource.setrlimit(resource.RLIMIT_CPU, (timeout, timeout + 10))
    resource.setrlimit(resource.RLIMIT_AS, (MAX_MEMORY_BYTES, MAX_MEMORY_BYTES))
    resource.setrlimit(resource.RLIMIT_FSIZE, (MAX_FILE_BYTES, MAX_FILE_BYTES))


def child_environment(workdir: str) -> dict:
    """The whole environment ``run_python``'s process gets: a fixed ``PATH``,
    the working directory as ``HOME`` / ``TMPDIR``, UTF-8 — nothing of the
    server's own environment (no API keys, tokens or ``CREDENTIALS_KEY``)."""
    return {"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": workdir, "TMPDIR": workdir, "LANG": "C.UTF-8",
            "PYTHONIOENCODING": "utf-8", "PYTHONDONTWRITEBYTECODE": "1"}


def _run(code: str, timeout: int) -> str:
    with tempfile.TemporaryDirectory(prefix="dana-python-") as tmpdir:
        script_path = os.path.join(tmpdir, "script.py")
        with open(script_path, "w") as f:
            f.write(code)
        try:
            proc = subprocess.run([sys.executable, "-I", "-u", script_path], capture_output=True,
                                  env=child_environment(tmpdir), stdin=subprocess.DEVNULL, text=True,
                                  timeout=timeout, preexec_fn=lambda: _set_limits(timeout), cwd=tmpdir)
        except subprocess.TimeoutExpired:
            return f"Error: timed out after {timeout} s; make the code faster or raise timeout (at most {MAX_TIMEOUT})."
    stdout, stderr = (proc.stdout or "").strip(), (proc.stderr or "").strip()
    if proc.returncode != 0:
        out = f"Output before the failure:\n{_bounded(stdout)}\n" if stdout else ""
        return f"Error: exit code {proc.returncode}.\n{out}Stderr:\n{_bounded(stderr or '(none)', keep_end=True)}"
    if not stdout:
        return "(no output: print() what should be returned)" + (f"\nStderr:\n{_bounded(stderr, keep_end=True)}"
                                                                   if stderr else "")
    return _bounded(stdout) + (f"\nStderr:\n{_bounded(stderr, keep_end=True)}" if stderr else "")


def _describe_python(user: Optional[User], a: Dict[str, Any]) -> Approval:
    return Approval("Run Python code", "Code", "🐍", "Run this code?", body_label="Code", body=a.get("code"))


@addon.tool(confirm=True, acts_outside=True, describe=_describe_python)
async def run_python(conn: Connection, code: str, timeout: Optional[int] = None) -> str:
    """Run Python code on the server (asks the user first) and return what it prints: calculations, data or text processing, format conversion. Prefer a dedicated tool when one fits.

    Args:
        code: The Python code; only print() output comes back (no input()).
        timeout: Seconds (default 30, max 60).
    """
    try:
        return await asyncio.to_thread(_run, code, max(1, min(int(timeout or DEFAULT_TIMEOUT), MAX_TIMEOUT)))
    except OSError as e:
        logger.warning(f"run_python failed to start: {e}", exc_info=True)
        return f"Error: could not run the code: {e}"
