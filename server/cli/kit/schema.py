"""The manifest as JSON: its schema, generated from ``Addon``'s signature and the manifest
dataclasses (core/addons/addon.py, core/addons/hooks.py), what a loaded addon's manifest
looks like in that shape, and a small validator for it (no dependency).

Functions given to the manifest (``test=``, ``on_connect=``…) are code, not JSON: they
are left out of the schema and of :func:`manifest_json`.
"""

import collections.abc
import dataclasses
import inspect
import json
import re
import typing
from typing import Any, Dict, List, Optional, Tuple

from core.addons.addon import FIELD_TYPES, ICON, Addon, Field, PromptPart
from core.addons.hooks import ADDON_NAME, NEED_KINDS, EventType, LlmJob, Nav, Need, Page, Touches, Worker

# The manifest's dataclasses, in the order the reference lists them.
MANIFEST_TYPES: Tuple[type, ...] = (Need, Touches, Field, PromptPart, LlmJob, Page, Nav, Worker, EventType)
# Values a field can only take (beyond its type).
ENUMS: Dict[Tuple[str, str], Tuple[str, ...]] = {("Need", "kind"): NEED_KINDS, ("Field", "type"): FIELD_TYPES,
                                                 ("LlmJob", "profile"): ("fast", "balanced", "deep")}
PATTERNS: Dict[Tuple[str, str], str] = {("Addon", "name"): ADDON_NAME.pattern, ("Addon", "icon"): f"^$|{ICON.pattern}",
                                        ("Page", "id"): r"^[a-z][a-z0-9_-]*$"}


def is_code(tp: Any) -> bool:
    """``tp`` is (or may be) a function: not part of the JSON."""
    if tp is collections.abc.Callable or typing.get_origin(tp) is collections.abc.Callable:
        return True
    return any(is_code(a) for a in typing.get_args(tp) if a is not type(None))


def type_schema(tp: Any, defs: Dict[str, Any]) -> Dict[str, Any]:
    """The JSON schema of a Python type hint; dataclasses go into ``defs`` and are referenced."""
    origin, args = typing.get_origin(tp), typing.get_args(tp)
    if tp is Any:
        return {}
    if tp is type(None):
        return {"type": "null"}
    for py, js in ((bool, "boolean"), (str, "string"), (int, "integer"), (float, "number")):
        if tp is py:
            return {"type": js}
    if origin is typing.Union:
        return {"anyOf": [type_schema(a, defs) for a in args]}
    if origin in (tuple, list, collections.abc.Sequence, collections.abc.Collection):
        item = args[0] if args else Any
        return {"type": "array", "items": type_schema(item, defs)}
    if origin in (dict, collections.abc.Mapping):
        return {"type": "object", "additionalProperties": type_schema(args[1] if args else Any, defs)}
    if dataclasses.is_dataclass(tp):
        name = tp.__name__
        if name not in defs:
            defs[name] = {}  # placeholder against recursion
            defs[name] = dataclass_schema(tp, defs)
        return {"$ref": f"#/$defs/{name}"}
    raise TypeError(f"No JSON schema for {tp!r}")


def first_paragraph(doc: Optional[str]) -> str:
    """The first paragraph of a docstring, on one line."""
    return " ".join((inspect.cleandoc(doc or "").split("\n\n")[0]).split())


def dataclass_schema(cls: type, defs: Dict[str, Any]) -> Dict[str, Any]:
    """The JSON schema of a manifest dataclass: its fields, those without a default required."""
    hints = typing.get_type_hints(cls)
    props: Dict[str, Any] = {}
    required: List[str] = []
    for f in dataclasses.fields(cls):
        if is_code(hints[f.name]):
            continue
        prop = type_schema(hints[f.name], defs)
        if (cls.__name__, f.name) in ENUMS:
            prop["enum"] = list(ENUMS[(cls.__name__, f.name)])
        if (cls.__name__, f.name) in PATTERNS:
            prop["pattern"] = PATTERNS[(cls.__name__, f.name)]
        props[f.name] = prop
        if f.default is dataclasses.MISSING and f.default_factory is dataclasses.MISSING:
            required.append(f.name)
    return {"type": "object", "description": first_paragraph(cls.__doc__), "properties": props,
            "required": required, "additionalProperties": False}


def args_section(doc: Optional[str]) -> Dict[str, str]:
    """The ``Args:`` of a Google-style docstring: name → its description on one line (an entry is a
    line at the section's indent, ``name: words``; deeper lines continue it)."""
    lines = inspect.cleandoc(doc or "").splitlines()
    out: Dict[str, str] = {}
    try:
        start = next(i for i, ln in enumerate(lines) if ln.strip() in ("Args:", "Arguments:"))
    except StopIteration:
        return out
    indent: Optional[int] = None
    current = ""
    for ln in lines[start + 1:]:
        if not ln.strip():
            continue
        depth = len(ln) - len(ln.lstrip())
        if depth == 0:
            break
        if indent is None:
            indent = depth
        m = re.match(r"(\w+)\s*(?:\([^)]*\))?:\s*(.*)$", ln.strip())
        if depth == indent and m:
            current = m.group(1)
            out[current] = m.group(2)
        elif current:
            out[current] += " " + ln.strip()
    return out


def addon_params() -> List[Tuple[str, Any, Any, str]]:
    """``Addon``'s manifest fields: (name, type hint, default or ``inspect.Parameter.empty``, description)."""
    hints = typing.get_type_hints(Addon.__init__)
    docs = args_section(Addon.__doc__)
    return [(p.name, hints[p.name], p.default, docs.get(p.name, ""))
            for p in inspect.signature(Addon.__init__).parameters.values() if p.name != "self"]


def manifest_schema() -> Dict[str, Any]:
    """The JSON schema of a manifest (what :func:`manifest_json` gives), draft 2020-12."""
    defs: Dict[str, Any] = {}
    props: Dict[str, Any] = {}
    for name, hint, default, doc in addon_params():
        if is_code(hint):
            continue
        prop = type_schema(hint, defs)
        if ("Addon", name) in PATTERNS:
            prop["pattern"] = PATTERNS[("Addon", name)]
        if doc:
            prop["description"] = doc
        props[name] = prop
    for cls in MANIFEST_TYPES:
        type_schema(cls, defs)
    return {"$schema": "https://json-schema.org/draft/2020-12/schema", "title": "Dana addon manifest",
            "description": "addon = Addon(name, ...) in an addon's addon.py, as JSON (functions left out).",
            "type": "object", "properties": props, "required": ["name"], "additionalProperties": False,
            "$defs": dict(sorted(defs.items()))}


def _plain(value: Any) -> Any:
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        hints = typing.get_type_hints(type(value))
        return {f.name: _plain(getattr(value, f.name)) for f in dataclasses.fields(value) if not is_code(hints[f.name])}
    if isinstance(value, (tuple, list)):
        return [_plain(v) for v in value]
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    return value


def manifest_json(addon: Addon) -> Dict[str, Any]:
    """A loaded addon's manifest in the schema's shape (``everyone`` and the rest as declared)."""
    out: Dict[str, Any] = {}
    for name, hint, _default, _doc in addon_params():
        if is_code(hint):
            continue
        out[name] = _plain(getattr(addon, name))
    return json.loads(json.dumps(out))


# --- Validation ------------------------------------------------------------------------------------

_TYPES = {"string": str, "boolean": bool, "integer": int, "number": (int, float), "array": list, "object": dict,
          "null": type(None)}


def _is(value: Any, kind: str) -> bool:
    if kind in ("integer", "number") and isinstance(value, bool):
        return False
    return isinstance(value, _TYPES[kind])


def validate(value: Any, schema: Dict[str, Any], root: Optional[Dict[str, Any]] = None, path: str = "") -> List[str]:
    """Where ``value`` breaks ``schema`` (the keywords :func:`manifest_schema` uses), one line each; [] when valid."""
    root = root or schema
    where = path or "the manifest"
    if "$ref" in schema:
        return validate(value, root["$defs"][schema["$ref"].rsplit("/", 1)[-1]], root, path)
    if "anyOf" in schema:
        results = [validate(value, s, root, path) for s in schema["anyOf"]]
        return [] if any(not r for r in results) else min(results, key=len)
    kind = schema.get("type")
    if kind and not _is(value, kind):
        return [f"{where}: must be {kind}, not {type(value).__name__}"]
    errors: List[str] = []
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{where}: must be one of {', '.join(map(str, schema['enum']))}, not {value!r}")
    if "pattern" in schema and isinstance(value, str) and not re.search(schema["pattern"], value):
        errors.append(f"{where}: {value!r} does not match {schema['pattern']}")
    if kind == "array":
        for i, item in enumerate(value):
            errors += validate(item, schema.get("items", {}), root, f"{path}[{i}]")
    if kind == "object":
        props = schema.get("properties", {})
        for name in schema.get("required", []):
            if name not in value:
                errors.append(f"{where}: {name} is missing")
        for name, item in value.items():
            if name in props:
                errors += validate(item, props[name], root, f"{path}.{name}" if path else name)
            elif schema.get("additionalProperties") is False:
                errors.append(f"{where}: unknown field {name}")
            elif isinstance(schema.get("additionalProperties"), dict):
                errors += validate(item, schema["additionalProperties"], root, f"{path}.{name}" if path else name)
    return errors
