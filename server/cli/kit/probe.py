"""Load one addon in a clean process and print what it declares, as JSON (run by
:mod:`cli.kit.check`, never imported by the server).

    python -m cli.kit.probe <addon folder>      (from server/)

The environment is cleaned like a test's (server/tests/testenv.py: no ``.env``, no real
keys, a temporary database removed at exit); the addon is loaded as a site addon under
the name its ``addon.py`` declares, next to the official ones, then the migrations run
on the temporary database. Prints :data:`MARK` and ``{"name", "loaded", "error",
"migration_error", "manifest", "tools", "hooks", "migrations"}``.
"""

import inspect
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List

SERVER = Path(__file__).resolve().parents[2]
# The line of output that holds the JSON (an addon may print while it is imported).
MARK = "DANA-PROBE "


def probe(folder: Path) -> Dict[str, Any]:
    """What the addon in ``folder`` declares once loaded (see the module docstring)."""
    sys.path[:0] = [str(SERVER), str(SERVER / "tests")]
    import testenv  # first: a clean environment and a temporary database (not in the server's image)
    testenv.isolate("addon_probe")
    from core import addon_dirs
    manifest = addon_dirs.read_manifest(folder / "addon.py")
    if manifest is None:
        return {"name": None, "loaded": False, "error": 'addon.py declares no addon = Addon("<name>", ...)'}
    holder = Path(testenv.scratch_dir()) / "addons"
    holder.mkdir()
    (holder / manifest.name).symlink_to(folder.resolve(), target_is_directory=True)
    os.environ["DANA_ADDON_DIRS"] = str(holder)
    from cryptography.fernet import Fernet
    os.environ["CREDENTIALS_KEY"] = Fernet.generate_key().decode()

    from core.addons import loader
    from core.addons.addon import get_addon
    from core.addons.schema import tool_parameters
    from cli.kit.schema import args_section, manifest_json
    out: Dict[str, Any] = {"name": manifest.name, "loaded": False, "error": None, "migration_error": None}
    try:
        loader.load()
    except Exception as e:  # an official addon failing: not this addon's fault, but say so
        out["error"] = f"Dana could not load its addons: {type(e).__name__}: {e}"
        return out
    failed = loader.failed().get(manifest.name)
    if failed is not None:
        out["error"] = failed.error
        return out
    try:
        result: List[str] = list(loader.migrate())
    except Exception as e:
        result = []
        out["migration_error"] = f"{type(e).__name__}: {e}"
    failed = loader.failed().get(manifest.name)
    if failed is not None:  # its migrations failed: the server would unload it
        out["migration_error"] = failed.error
    a = get_addon(manifest.name)
    tools: List[Dict[str, Any]] = []
    for t in a.tools:
        doc = inspect.getdoc(t.func) or ""
        tools.append({"name": t.name, "file": inspect.getsourcefile(t.func), "line": inspect.getsourcelines(t.func)[1],
                      "summary": doc.split("\n\n")[0].strip(), "params": [p.name for p in tool_parameters(t.func)],
                      "documented": sorted(args_section(doc)), "is_write": t.is_write, "confirm": bool(t.confirm),
                      "acts_outside": t.acts_outside, "schema": t.schema})
    out.update(loaded=True, manifest=manifest_json(a), tools=tools,
               hooks=[{"kind": h.kind, "key": h.key} for h in a.hooks],
               jobs=[j.key for j in a.jobs], services=[s.key for s in a.services],
               handlers=[h.event for h in a.handlers], channels=[c.name for c in a.channels],
               migrations=[label for label in result if label.startswith(f"{manifest.name}:")])
    return out


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: python -m cli.kit.probe <addon folder>")
    print(MARK + json.dumps(probe(Path(sys.argv[1]).expanduser().resolve())))
