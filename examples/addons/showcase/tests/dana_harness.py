"""Runs this addon's tests against Dana's test harness (kept as written by ``bin/dana addons new``).

Import it first in every test script, then call :func:`start`::

    import dana_harness  # first: finds Dana, a clean environment and a temporary database
    dana_harness.start("myaddon_test")

Dana is found, in this order: ``DANA_HOME`` (a Dana checkout: the folder that
holds ``server/``, or ``server/`` itself), the Dana a runner already put on the
import path (``bin/dana addons test``, Dana's ``bin/test``), else a ``dana``
checkout next to this addon's folder. :func:`start` cleans the environment (no
real keys or ``.env`` reach a test), points the database at a temporary folder
removed at exit, and loads this addon under its own name from a development folder (``DANA_ADDON_DIRS``).
"""

import ast
import importlib.util
import os
import sys
from pathlib import Path
from typing import Optional

ADDON = Path(__file__).resolve().parents[1]  # this addon's folder


def addon_name() -> str:
    """The name ``addon.py`` declares (``addon = Addon("<name>", ...)``), read without running it."""
    tree = ast.parse((ADDON / "addon.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "Addon" and node.args \
                and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
            return node.args[0].value
    raise SystemExit(f"{ADDON / 'addon.py'} declares no addon = Addon(\"<name>\", ...)")


def _server(path: Path) -> Optional[Path]:
    for candidate in (path / "server", path):
        if (candidate / "core" / "addons").is_dir() and (candidate / "tests" / "testenv.py").is_file():
            return candidate.resolve()
    return None


def find_server() -> Path:
    """Dana's ``server/`` folder (see the module docstring).

    Raises:
        SystemExit: no Dana found, with how to point at one.
    """
    home = os.environ.get("DANA_HOME", "").strip()
    if home:
        found = _server(Path(home).expanduser())
        if found is None:
            raise SystemExit(f"DANA_HOME={home} is not a Dana checkout (no server/core/addons there).")
        return found
    spec = importlib.util.find_spec("testenv")
    if spec is not None and spec.origin:
        found = _server(Path(spec.origin).resolve().parents[1])
        if found is not None:
            return found
    found = _server(ADDON.parent / "dana")
    if found is not None:
        return found
    raise SystemExit("Dana not found: set DANA_HOME to a Dana checkout (git clone https://github.com/ronganam/dana), "
                     "e.g. DANA_HOME=~/dana python tests/test_x.py, or run bin/dana addons test <this folder> "
                     "from that checkout's server/.")


SERVER = find_server()
for _p in (SERVER / "tests", SERVER):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))
os.chdir(SERVER)


def start(test_name: str) -> str:
    """A clean environment and a temporary database for this test (a throwaway ``CREDENTIALS_KEY``),
    with only this addon loaded beside the official ones (``DANA_ADDON_DIRS``: a folder holding a link to it
    under its own name, so neither its folder's name nor its neighbours matter).
    Returns the database path."""
    import testenv  # Dana's harness: server/tests/testenv.py
    from cryptography.fernet import Fernet
    path = testenv.isolate(test_name)
    os.environ["CREDENTIALS_KEY"] = Fernet.generate_key().decode()  # a throwaway key: credentials are encrypted
    name = addon_name()
    holder = Path(testenv.scratch_dir()) / "addons"
    holder.mkdir(exist_ok=True)
    (holder / name).symlink_to(ADDON, target_is_directory=True)
    os.environ["DANA_ADDON_DIRS"] = str(holder)
    return path
