"""Addon needs and touches: Need / Touches validation, the addons an addon depends on, reading them from addon.py without running it, every official addon's declarations, and the README's "What each feature needs" table matching `bin/dana addons table`.

Run: bin/test test_addon_needs
"""

import re
import tempfile
from pathlib import Path
from typing import Any, Callable, List, Tuple

import testenv  # first: clean env, temp DB removed at exit, no .env (see testenv.py)
testenv.isolate("addon_needs_test")

import addons  # noqa: E402,F401 — loads the official addons
from cli.addons import official_table  # noqa: E402
from core import addon_dirs  # noqa: E402
from core.addons import Addon, Field, Need, Touches, install, loader  # noqa: E402
from core.addons.addon import all_addons, forget, get_addon  # noqa: E402
from core.addons.hooks import NEED_KINDS  # noqa: E402

README = Path(__file__).resolve().parents[2] / "README.md"
START, END = "<!-- addons-table:start -->", "<!-- addons-table:end -->"

results: List[Tuple[str, bool]] = []


def check(name: str, cond: Any, detail: Any = "") -> None:
    results.append((name, bool(cond)))
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f"\n       {str(detail)[:1500]}" if detail and not cond else ""))


def refused(make: Callable[[], Any], words: str) -> bool:
    try:
        make()
    except (TypeError, ValueError) as e:
        return words in str(e)
    return False


# --- Need ------------------------------------------------------------------------------------------
check("need: the kinds", NEED_KINDS == ("env", "account", "hardware", "app", "region", "addon"))
check("need: required by default; to_json gives every part",
      Need("account", "a Google account", "Your calendars").to_json()
      == {"kind": "account", "what": "a Google account", "why": "Your calendars", "optional": False})
check("need: an unknown kind is refused", refused(lambda: Need("gadget", "x", "y"), "kind must be one of"))
check("need: an env need names the variable", refused(lambda: Need("env", "google client id", "y"), "names the variable")
      and Need("env", "GOOGLE_CLIENT_ID", "y").what == "GOOGLE_CLIENT_ID")
check("need: an addon need names the addon", refused(lambda: Need("addon", "Home Assistant", "y"), "names the addon"))
check("need: a why is required, one short line",
      refused(lambda: Need("app", "the app", ""), "why must be one line")
      and refused(lambda: Need("app", "the app", "a\nb"), "why must be one line")
      and refused(lambda: Need("app", "the app", "x" * 161), "keep it to 160"))

# --- Touches ---------------------------------------------------------------------------------------
t = Touches(hosts=("api.example.com",), acts_outside=True, reads=("tasks",))
check("touches: to_json and from_json agree",
      Touches.from_json(t.to_json()) == t and Touches.from_json({}) == Touches() and Touches().empty and not t.empty)
check("touches: hosts / reads must be tuples, not one string",
      refused(lambda: Touches(hosts="api.example.com"), "must be a tuple")  # type: ignore[arg-type]
      and refused(lambda: Touches.from_json({"hosts": "x"}), "must be lists"))
check("touches: reads names addons; flags are booleans; unknown keys refused",
      refused(lambda: Touches(reads=("Google Contacts",)), "reads names addons")
      and refused(lambda: Touches(acts_outside="yes"), "True or False")  # type: ignore[arg-type]
      and refused(lambda: Touches.from_json({"payments": True}), "has no 'payments'"))
grown = Touches(hosts=("api.example.com", "cdn.example.com"), acts_outside=True, stores_credentials=True, reads=("tasks",))
check("touches: added_since gives only what is new",
      grown.added_since(t) == Touches(hosts=("cdn.example.com",), stores_credentials=True)
      and t.added_since(grown).empty and t.added_since(t).empty)

# --- Addon(needs=, touches=) -----------------------------------------------------------------------
probe = Addon("needs_probe", description="A probe",
              needs=(Need("addon", "tasks", "Its tasks"), Need("addon", "phone", "Push", optional=True),
                     Need("env", "PROBE_KEY", "Its service")),
              touches=Touches(hosts=("api.example.com",)))
check("addon: depends = its required addon needs only", probe.depends == ("tasks",))
forget("needs_probe")
check("addon: needs= takes Need values, not names",
      refused(lambda: Addon("needs_probe2", needs=("tasks",)), "needs= takes a tuple of Need"))  # type: ignore[arg-type]
check("addon: a need named twice is refused",
      refused(lambda: Addon("needs_probe3", needs=(Need("env", "K", "a"), Need("env", "K", "b"))), "names one thing twice"))
check("addon: credentials need touches that say they are stored",
      refused(lambda: Addon("needs_probe4", credentials=(Field("token", "A token"),)), "stores_credentials=True"))
check("addon: touches= takes a Touches", refused(lambda: Addon("needs_probe5", touches={"hosts": []}), "Touches(...)"))  # type: ignore[arg-type]

# --- Read without running addon.py ----------------------------------------------------------------
folder = Path(tempfile.mkdtemp(dir=testenv.scratch_dir()))
(folder / "addon.py").write_text(
    'import sys; sys.exit("never run")\n'
    'from core.addons import Addon, Need, Touches\n'
    'addon = Addon("reader", needs=(Need("addon", "money", "Its sources"), Need("addon", "phone", "Codes", optional=True),\n'
    '                               Need(kind="addon", what="tasks", why="Links"), Need("env", "KEY", "A key")),\n'
    '              touches=Touches(("api.example.com",), True, reads=["tasks"]))\n', encoding="utf-8")
manifest = addon_dirs.read_manifest(folder / "addon.py")
check("read: the required addon needs (positional or by keyword), never the optional ones",
      manifest is not None and manifest.name == "reader" and manifest.depends == ("money", "tasks"), manifest)
check("read: touches as plain values; as Touches",
      manifest is not None and manifest.touches == {"hosts": ("api.example.com",), "acts_outside": True, "reads": ["tasks"]}
      and install.manifest_touches(folder / "addon.py") == Touches(hosts=("api.example.com",), acts_outside=True,
                                                                     reads=("tasks",)), manifest)
(folder / "addon.py").write_text('from core.addons import Addon\naddon = Addon("reader")\n', encoding="utf-8")
check("read: no touches declared = touches nothing", install.manifest_touches(folder / "addon.py") == Touches())
(folder / "addon.py").write_text('HOSTS = ("a.example",)\naddon = Addon("reader", touches=Touches(hosts=HOSTS))\n',
                                 encoding="utf-8")
check("read: touches written with names cannot be read before installing",
      refused(lambda: install.manifest_touches(folder / "addon.py"), "plain values")
      and install.installed_touches(folder) == Touches())
# Manifests whose name is a literal are read (a name from a constant, ADDON, is not: those order by order=).
readable = [f for f in addon_dirs.addon_folders() if addon_dirs.read_manifest(f.path / "addon.py") is not None]
mismatch = [(f.name, f.depends, get_addon(f.name).depends) for f in readable if f.depends != get_addon(f.name).depends]
check("read: every official folder's depends, read without running it, is what its manifest declares",
      readable and not mismatch, mismatch)

# --- The official addons' declarations ---------------------------------------------------------------
official = [a for a in all_addons() if loader.folder(a.name).source.official]
names = {a.name for a in all_addons()}
problems = []
for a in official:
    for n in a.needs:
        if n.kind == "addon" and not n.optional and n.what not in names:
            problems.append(f"{a.name}: needs {n.what}, not loaded")
    for r in a.touches.reads:
        if r not in names:
            problems.append(f"{a.name}: reads {r}, not loaded")
    if a.credentials and not a.touches.stores_credentials:
        problems.append(f"{a.name}: credentials not declared")
check("official: every required addon need and every read names a loaded addon", not problems, problems)


def official_addon(name: str) -> Addon:
    return next(a for a in official if a.name == name)


ga = [official_addon(n) for n in ("calendar", "contacts", "gmail")]
check("official: the Google addons need the Google OAuth client and a Google account",
      all({("env", "GOOGLE_CLIENT_ID"), ("env", "GOOGLE_CLIENT_SECRET"), ("account", "a Google account")}
          <= {(n.kind, n.what) for n in a.needs if not n.optional} for a in ga))
ha = official_addon("homeassistant")
check("official: Home Assistant needs its server and a long-lived token, keeps them, acts outside",
      [n.kind for n in ha.needs if not n.optional] == ["hardware", "account"] and "long-lived" in ha.needs[1].what
      and ha.touches.stores_credentials and ha.touches.acts_outside)
ph = official_addon("phone")
check("official: Phone needs the Android app; a recorder and a transcription model are optional",
      [(n.kind, n.optional) for n in ph.needs][:3] == [("app", False), ("hardware", True), ("account", True)]
      and "call recordings to a folder" in ph.needs[1].what and ph.needs[2].what.startswith("a transcription model") and ph.needs[2].optional)
tg = official_addon("telegram_account")
check("official: the Telegram account needs the server's Telegram app and never acts outside",
      {n.what for n in tg.needs if n.kind == "env"} == {"TELEGRAM_API_ID", "TELEGRAM_API_HASH"}
      and not tg.touches.acts_outside and tg.touches.stores_credentials)
wa = official_addon("whatsapp")
check("official: WhatsApp needs a WAHA server", wa.needs[0].kind == "hardware" and "WAHA" in wa.needs[0].what)

# --- The README's table ------------------------------------------------------------------------------
if README.is_file():  # the repository's README; an image ships only server/
    text = README.read_text(encoding="utf-8")
    found = re.search(re.escape(START) + r"\n(.*?)" + re.escape(END), text, re.S)
    table = official_table()
    check("readme: the addons table is between its markers under \"What each feature needs\"",
          found is not None and text.index("## What each feature needs") < text.index(START))
    check("readme: the table is what `bin/dana addons table` prints (after changing an addon's needs, run "
          "`cd server && bin/dana addons table` and paste it between the addons-table markers in README.md)",
          found is not None and found.group(1) == table,
          "\n".join(line for line in __import__("difflib").unified_diff(
              (found.group(1) if found else "").splitlines(), table.splitlines(), "README.md", "bin/dana addons table",
              lineterm="")))
    check("table: one row per official addon, the header first",
          table.splitlines()[0] == "| Feature | What it does | Required | Optional | Region |"
          and len(table.splitlines()) == 2 + len(official))
else:
    print("[SKIP] readme: no README.md beside server/")

passed = sum(1 for _, ok in results if ok)
print(f"\n{passed}/{len(results)} passed")
raise SystemExit(0 if passed == len(results) else 1)
