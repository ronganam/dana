"""The ops tools against a fake Kubernetes API. Run: bin/test test_ops_tools

run_kubectl: verbs, resources and flags become the right API path and query
(namespaces, -A, -l, logs' tail and container), secret values are refused
before any request, lists are compact rows, long output is cut with a note.
run_python: runs off the event loop, its output is bounded, a failure keeps
the printed output and the traceback's end. No network beyond localhost."""

import asyncio
import os
import pathlib
import tempfile
import time

import testenv  # first: clean env, temp DB removed at exit, no .env (see testenv.py)
testenv.isolate("ops_tools_test")

from aiohttp import web
from aiohttp.test_utils import TestServer

from addons.ops import tools

failures = 0
requests = []  # (path, query, bearer)


def check(name: str, cond, detail="") -> None:
    global failures
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail and not cond else ""))
    failures += 0 if cond else 1


def pod(name: str, ns: str = "apps", waiting: str = "") -> dict:
    state = {"waiting": {"reason": waiting}} if waiting else {"running": {}}
    return {"metadata": {"name": name, "namespace": ns, "managedFields": [{"x": 1}]},
            "spec": {"nodeName": "node-1", "containers": [{"name": "app"}]},
            "status": {"phase": "Running", "startTime": "2026-11-20T10:00:00Z",
                       "containerStatuses": [{"ready": not waiting, "restartCount": 3 if waiting else 0,
                                              "state": state}]}}


async def api(request: web.Request) -> web.Response:
    requests.append((request.path, dict(request.query), request.headers.get("Authorization", "")))
    path = request.path
    if path.endswith("/log"):
        return web.Response(text="".join(f"line {i}\n" for i in range(5000)))
    if path == "/api/v1/namespaces/apps/pods/web-1":
        return web.json_response(pod("web-1"))
    if path in ("/api/v1/namespaces/apps/pods", "/api/v1/pods"):
        return web.json_response({"items": [pod("web-1"), pod("worker-1", "jobs", "CrashLoopBackOff")]})
    if path == "/apis/apps/v1/namespaces/apps/deployments":
        return web.json_response({"items": [{"metadata": {"name": f"d{i}"}, "spec": {"replicas": 1}, "status": {}}
                                            for i in range(400)]})
    return web.json_response({"message": "not found"}, status=404)


async def main() -> None:
    app = web.Application()
    app.router.add_get("/{tail:.*}", api)
    server = TestServer(app)
    await server.start_server()
    sa = pathlib.Path(tempfile.mkdtemp())
    (sa / "token").write_text("sa-test-token")
    (sa / "namespace").write_text("apps")
    tools.API_BASE = str(server.make_url("")).rstrip("/")
    tools.SA_TOKEN_PATH, tools.SA_NAMESPACE_PATH = str(sa / "token"), str(sa / "namespace")

    out = await tools.run_kubectl(None, "get pods")
    check("get pods: the pod's own namespace, the service account's token, compact rows",
          requests[-1][:1] == ("/api/v1/namespaces/apps/pods",) and requests[-1][2] == "Bearer sa-test-token"
          and out.splitlines()[0] == "2 pods in namespace 'apps':"
          and "web-1  Running  ready 1/1  restarts 0  node node-1" in out
          and "worker-1  CrashLoopBackOff  ready 0/1  restarts 3" in out, out)
    out = await tools.run_kubectl(None, "kubectl get po -A -l app=web")
    check("get po -A -l: every namespace, the label selector, rows with their namespace",
          requests[-1][:2] == ("/api/v1/pods", {"labelSelector": "app=web"}) and "jobs/worker-1" in out, (requests[-1], out))
    out = await tools.run_kubectl(None, "get pod/web-1")
    check("get pod/NAME: one row", out.startswith("web-1  Running"), out)
    out = await tools.run_kubectl(None, "get pod web-1 -o json")
    check("-o json: the object without managedFields", '"web-1"' in out and "managedFields" not in out, out[:200])
    out = await tools.run_kubectl(None, "logs web-1 -c app")
    check("logs: the last 200 lines by default, the container as a query",
          requests[-1][:2] == ("/api/v1/namespaces/apps/pods/web-1/log", {"tailLines": "200", "container": "app"})
          and out.startswith("Logs of pod 'web-1'"), requests[-1])
    out = await tools.run_kubectl(None, "logs web-1 --tail 5000")
    check("logs: long output keeps its end, with a note", out.rstrip().endswith("line 4999")
          and "[cut:" in out and len(out) < 12_000, out[:200])
    out = await tools.run_kubectl(None, "get deploy")
    check("get deploy: the apps API; a long list is cut with a count of the rest",
          requests[-1][0] == "/apis/apps/v1/namespaces/apps/deployments" and f"…and {400 - tools.MAX_LINES} more" in out
          and len(out) < 12_000, out[-200:])
    n = len(requests)
    out = await tools.run_kubectl(None, "logs deploy/web")
    check("logs: only from a pod, saying how to find it, without a request",
          out.startswith("Error: logs come from a pod") and len(requests) == n, out)
    n = len(requests)
    for command in ("get secret db -o yaml", "describe secret db", "get secrets -o json"):
        out = await tools.run_kubectl(None, command)
        check(f"'{command}': refused before any request", out.startswith("Error: secret values") and len(requests) == n, out)
    for command, error in (("delete pod web-1", "Error: Unsupported verb"), ("get spaceships", "Error: unknown resource"),
                           ("describe pod", "Error: 'describe' needs a name")):
        out = await tools.run_kubectl(None, command)
        check(f"'{command}': {error}", out.startswith(error) and len(requests) == n, out)
    out = await tools.run_kubectl(None, "get pod nope")
    check("a missing object: an error naming it and the namespace",
          out == "Error: pods 'nope' not found in namespace 'apps'.", out)
    await server.close()

    # run_python: off the event loop, bounded.
    ticks = 0

    async def ticker() -> None:
        nonlocal ticks
        while True:
            await asyncio.sleep(0.05)
            ticks += 1

    t = asyncio.create_task(ticker())
    started = time.monotonic()
    out = await tools.run_python(None, "import time; time.sleep(0.6); print('x' * 50000)")
    t.cancel()
    check("run_python: the event loop keeps running meanwhile", ticks >= 5 and time.monotonic() - started >= 0.6, ticks)
    check("run_python: long output is cut with a note", out.startswith("x" * 100) and "[cut: 50,000 characters" in out
          and len(out) < 12_000, out[-100:])
    out = await tools.run_python(None, "print('half way')\nraise ValueError('boom')")
    check("run_python: a failure keeps the output and the traceback",
          out.startswith("Error: exit code 1.") and "half way" in out and "ValueError: boom" in out, out)
    out = await tools.run_python(None, "import os; print(sorted(os.environ))")
    check("run_python: the child sees only its minimal environment", "CREDENTIALS_KEY" not in out
          and "API_TOKEN" not in out and "PATH" in out, out)


if __name__ == "__main__":
    asyncio.run(main())
    raise SystemExit(1 if failures else 0)
