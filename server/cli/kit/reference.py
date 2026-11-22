"""The addon reference, generated from Dana's code: ``bin/dana addons reference`` writes
docs/addon-reference.md, and ``bin/dana addons new`` copies it into each new addon as
REFERENCE.md. ``tests/test_addon_kit.py`` fails when the file differs from :func:`render`.

What it is made of: ``Addon``'s signature and docstring (the manifest fields), the manifest
dataclasses (``Need``, ``Touches``, ``Field``…), each ``@addon.<hook>`` method's signature
and docstring, the types hooks get and return, and the examples: code of
``examples/addons/showcase`` between ``# --- reference: <key> ---`` and ``# --- end ---``,
so every example is code that runs in Dana's tests. Nothing here is written by hand
but the short introductions.
"""

import collections.abc
import dataclasses
import datetime
import inspect
import json
import re
import typing
from pathlib import Path
from typing import Any, Dict, List, Sequence, Tuple

from core.addons import hooks as hook_types
from core.addons.addon import Addon
from cli.kit import schema

SERVER = Path(__file__).resolve().parents[2]
REPO = SERVER.parent
SHOWCASE = REPO / "examples" / "addons" / "showcase"
OUTPUT = SERVER / "docs" / "addon-reference.md"
GITHUB = "https://github.com/ronganam/dana/blob/master"
MARK = re.compile(r"^\s*(?:#|//) --- reference: ([a-z_]+) ---\s*$")
END = re.compile(r"^\s*(?:#|//) --- end ---\s*$")

# The hooks, in the order the reference lists them, with the showcase examples each shows
# ("" = the example is under the hook named instead).
HOOKS: Tuple[Tuple[str, Sequence[str], str], ...] = (
    ("tool", ("tool", "approval", "llm"), ""),
    ("job", ("job",), ""),
    ("service", ("service",), ""),
    ("on", ("event",), ""),
    ("channel", ("channel",), ""),
    ("now_cards", ("now_cards",), ""),
    ("now_action", ("now_action",), ""),
    ("day_items", ("day_items",), ""),
    ("briefing", ("briefing",), ""),
    ("review", (), "review_action"),
    ("review_action", ("review",), ""),
    ("search", ("search",), ""),
    ("selfcheck", ("selfcheck",), ""),
    ("context", ("context",), ""),
    ("context_card", (), "context"),
    ("context_block", (), "context"),
    ("provide", ("provide",), ""),
)
# Manifest fields that take a function, and the example of each.
CALLBACKS: Tuple[Tuple[str, str], ...] = (
    ("test", "test"), ("options", "options"), ("validate", "validate"), ("on_connect", "on_connect"),
    ("prompt_section", "prompt_section"), ("blocked", "blocked"), ("on_change", ""), ("on_credentials_change", ""),
)
# The types hooks get and return (core/addons/hooks.py).
RESULT_TYPES = (hook_types.Card, hook_types.DayItem, hook_types.NowContext, hook_types.BriefingContext,
                hook_types.Section, hook_types.Hit, hook_types.Check, hook_types.ContextRequest, hook_types.Approval)
# More examples: (heading, showcase keys, words before them).
MORE = (
    ("Settings and credentials", ("manifest",), "Declared on the manifest (`settings=`, `credentials=`); a tool reads "
     "them from `conn.settings` / `conn.credentials` (decrypted only for the connection's own user). Secrets go in "
     "`credentials` only: never in code, settings, env or logs."),
    ("Calling a service over HTTP", ("http",), "Every host it calls is in `touches=Touches(hosts=...)`; an env value "
     "it reads is a `Need(\"env\", ...)` (`bin/dana addons check` compares both with the code)."),
    ("Its own tables", ("migration", "data"), "`migrations/m0001_<name>.py`, `m0002_…` (numbered per addon, never "
     "edited once shipped), and queries that always name the user (`UserTable` does)."),
    ("A route", ("route",), "`api.py` with `setup(app)`, behind the same sign-in as every route."),
    ("A web page", ("page",), "`web=(Page(id, panel, style, nav),)` on the manifest and a script in `web/` that "
     "registers with `Dana.panel` (the page API: server/docs/writing-an-addon.md, \"Pages\")."),
    ("A worker (sidecar)", ("worker",), "`worker=Worker(port, url_env)` on the manifest and a `worker/` folder with "
     "its own `Dockerfile`; Dana reaches it at the URL in `url_env`."),
)


def md(text: str) -> str:
    """Docstring markup as Markdown: ``x`` → `x`, :class:`~a.b.C` → `C`."""
    text = re.sub(r":(?:class|func|data|mod|attr|meth):`~?([\w.]+)`", lambda m: f"`{m.group(1).rsplit('.', 1)[-1]}`", text)
    return re.sub(r"``(.+?)``", r"`\1`", text)


def one_line(doc: str) -> str:
    return " ".join(doc.split())


def type_words(tp: Any) -> str:
    """A type hint in a few words ("list of Need", "str or None")."""
    origin, args = typing.get_origin(tp), typing.get_args(tp)
    if tp is type(None):
        return "None"
    if tp is Any:
        return "any"
    if schema.is_code(tp):
        return "function"
    if origin is typing.Union:
        return " or ".join(type_words(a) for a in args)
    if origin in (tuple, list, collections.abc.Sequence, collections.abc.Collection):
        return f"list of {type_words(args[0])}" if args else "list"
    if origin in (dict, collections.abc.Mapping):
        return f"dict of {type_words(args[0])} → {type_words(args[1])}" if args else "dict"
    if tp is datetime.datetime:
        return "datetime"
    return getattr(tp, "__name__", str(tp))


def default_words(value: Any) -> str:
    if value is inspect.Parameter.empty or value is dataclasses.MISSING:
        return "required"
    if callable(value) and not isinstance(value, type):
        return "—"
    return f"`{value!r}`" if not dataclasses.is_dataclass(value) else f"`{type(value).__name__}()`"


def cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ").strip()


def snippets(folder: Path = SHOWCASE) -> Dict[str, Tuple[str, str]]:
    """The showcase's marked examples: key → (file path from the repository, code).

    Raises:
        ValueError: a key marked twice, or a mark without its end.
    """
    out: Dict[str, Tuple[str, str]] = {}
    for path in sorted(p for p in folder.rglob("*") if p.suffix in (".py", ".js") and "tests" not in p.parts):
        lines = path.read_text(encoding="utf-8").splitlines()
        key, body = "", []
        for ln in lines:
            m = MARK.match(ln)
            if m:
                key, body = m.group(1), []
            elif END.match(ln) and key:
                if key in out:
                    raise ValueError(f"reference example {key!r} is marked twice")
                out[key] = (str(path.relative_to(REPO)), "\n".join(body).strip("\n"))
                key = ""
            elif key:
                body.append(ln)
        if key:
            raise ValueError(f"{path}: reference example {key!r} has no '--- end ---'")
    return out


def example(key: str, found: Dict[str, Tuple[str, str]]) -> str:
    path, code = found[key]
    lang = "js" if path.endswith(".js") else "python"
    return f"From [`{path}`]({GITHUB}/{path}):\n\n```{lang}\n{code}\n```\n"


def decorator_signature(name: str) -> str:
    """``@addon.<name>(...)`` with its parameters' names and defaults."""
    params = list(inspect.signature(getattr(Addon, name)).parameters.values())[1:]
    if [p.name for p in params] == ["func"]:
        return f"@addon.{name}"
    parts: List[str] = []
    star = False
    for p in params:
        if p.name == "func":
            continue
        if p.kind is inspect.Parameter.KEYWORD_ONLY and not star:
            parts.append("*")
            star = True
        default = "" if p.default is inspect.Parameter.empty else \
            f"={'...' if p.default.__class__ is object else repr(p.default)}"
        parts.append(f"{p.name}{default}")
    return f"@addon.{name}({', '.join(parts)})"


def fields_table(cls: type) -> str:
    hints = typing.get_type_hints(cls)
    rows = ["| Field | Type | Default |", "|---|---|---|"]
    for f in dataclasses.fields(cls):
        default = f.default if f.default is not dataclasses.MISSING else \
            (f.default_factory() if f.default_factory is not dataclasses.MISSING else dataclasses.MISSING)
        rows.append(f"| `{f.name}` | {cell(type_words(hints[f.name]))} | {default_words(default)} |")
    return "\n".join(rows) + "\n"


def render() -> str:
    """The whole reference (Markdown)."""
    found = snippets()
    out: List[str] = [
        "# Addon reference\n",
        "Generated from Dana's code by `bin/dana addons reference` (`server/cli/kit/reference.py`): do not edit it "
        "by hand; Dana's tests fail when it differs from the code. Every example is code of the "
        f"[showcase addon]({GITHUB}/examples/addons/showcase), which uses each capability once and is tested with "
        "Dana. The guide to writing one: [`server/docs/writing-an-addon.md`]"
        f"({GITHUB}/server/docs/writing-an-addon.md); the design: [`docs/addons.md`]({GITHUB}/docs/addons.md).\n",
        "## Contents\n",
        "- [The folder](#the-folder)\n- [The manifest](#the-manifest)\n- [Manifest values](#manifest-values)\n"
        "- [Functions on the manifest](#functions-on-the-manifest)\n- [Hooks](#hooks)\n"
        "- [What hooks get and return](#what-hooks-get-and-return)\n- [More examples](#more-examples)\n"
        "- [The manifest as JSON](#the-manifest-as-json)\n",
        "## The folder\n",
        "```\n<name>/\n  addon.py          addon = Addon(\"<name>\", ...): the manifest (required)\n"
        "  tools.py          @addon.tool() functions              imported when present\n"
        "  hooks.py          jobs, handlers, Now cards…           imported when present\n"
        "  <module>.py       more hooks: list it in modules=(...)\n"
        "  api.py            setup(app): its HTTP routes          registered automatically\n"
        "  migrations/       m0001_baseline.py, m0002_…           its own tables\n"
        "  web/              its pages: scripts, styles           served at /addons/<name>/web/<file>\n"
        "  worker/           a sidecar's Dockerfile and code\n"
        "  requirements.txt  extra Python packages\n"
        "  tests/            test_*.py, test_*.mjs                bin/dana addons test\n"
        "  README.md         what it does, settings, events       shown in the catalog\n```\n",
        "Another module of the addon imports the manifest as `from addons.<name>.addon import addon`.\n",
        "## The manifest\n",
        "`addon = Addon(\"<name>\", ...)` in `addon.py`. Every field but `name` is optional.\n",
        "| Field | Type | Default | What |", "|---|---|---|---|",
    ]
    for name, hint, default, doc in schema.addon_params():
        out.append(f"| `{name}` | {cell(type_words(hint))} | {default_words(default)} | {cell(md(doc))} |")
    out.append("")
    out.append(example("manifest", found))
    out.append("## Manifest values\n")
    for cls in schema.MANIFEST_TYPES:
        out.append(f"### {cls.__name__}\n")
        out.append(md(inspect.cleandoc(cls.__doc__ or "")) + "\n")
        out.append(fields_table(cls))
    out.append("## Functions on the manifest\n")
    out.append("Given as manifest fields (`test=check_connection`); the signature is the example's.\n")
    docs = {name: doc for name, _hint, _default, doc in schema.addon_params()}
    for name, key in CALLBACKS:
        out.append(f"### `{name}=`\n")
        out.append(md(docs[name]) + "\n")
        if key:
            out.append(example(key, found))
    out.append("## Hooks\n")
    out.append("Declared with a decorator on the `addon` object, in any module the loader imports. Composer "
               "hooks (`now_cards`, `day_items`, `review`, `search`, `selfcheck`, `context*`) are plain functions; "
               "jobs, services, handlers, channels and the action hooks are `async`; `briefing` and `provide` may "
               "be either. Keys (a card prefix, a section, a check, a provider) are unique across addons.\n")
    for name, keys, see in HOOKS:
        out.append(f"### `{decorator_signature(name)}`\n")
        out.append(md(inspect.cleandoc(getattr(Addon, name).__doc__ or "")) + "\n")
        for key in keys:
            out.append(example(key, found))
        if see:
            out.append(f"Example: under `@addon.{see}`.\n")
    out.append("## What hooks get and return\n")
    out.append("From `core.addons` (`core/addons/hooks.py`); `to_json()` gives the payload clients read.\n")
    for cls in RESULT_TYPES:
        out.append(f"### {cls.__name__}\n")
        out.append(md(inspect.cleandoc(cls.__doc__ or "")) + "\n")
        out.append(fields_table(cls))
    out.append("## More examples\n")
    for heading, keys, words in MORE:
        out.append(f"### {heading}\n")
        out.append(words + "\n")
        for key in keys:
            if key != "manifest":
                out.append(example(key, found))
        if keys == ("manifest",):
            out.append("See the manifest's `credentials=` and `settings=` above, and `Field` under Manifest values.\n")
    out.append("## The manifest as JSON\n")
    out.append("The manifest's plain values (functions left out) follow this JSON schema, generated from the "
               "dataclasses above; `bin/dana addons check` validates a loaded manifest against it.\n")
    out.append("```json\n" + json.dumps(schema.manifest_schema(), indent=1, ensure_ascii=False) + "\n```\n")
    return "\n".join(out).rstrip("\n") + "\n"


def write(path: Path = OUTPUT) -> Path:
    """Write :func:`render` to ``path``; returns it."""
    path.write_text(render(), encoding="utf-8")
    return path
