"""Integrations: each kind is a package here that declares its credentials and
settings and owns its tools (docs/access.md, "Integration", "Kinds").

Importing this package registers every kind and its tools; :data:`KIND_ORDER`
fixes their order (tool lists follow it, then the order tools are defined in).
``integrations.<kind>`` is the kind's package; the kind itself is
``integrations.<kind>.<kind>`` (e.g. ``from integrations.homeassistant import homeassistant``). Adding a
kind: a package with ``kind.py`` (the :class:`~integrations.framework.Integration`),
``tools.py`` (``@<kind>.tool``), one import line here and its name in
:data:`KIND_ORDER`.

Plugins: a kind can also live outside this repository. ``DANA_PLUGIN_DIRS``
(directories separated by ``os.pathsep``) names folders whose sub-packages with
a ``kind.py`` are loaded as extra kinds after the built-in ones, under the same
``integrations.<kind>`` name, so a plugin is written exactly like a built-in
kind (docs/plugins.md). The container image loads ``/app/plugins``.
"""

import os
from importlib import import_module
from pathlib import Path

from integrations.framework import kind as _kind
from integrations import (core, tasks, shopping, knowledge, about, people, phone,  # noqa: F401 — registers each kind
                          homeassistant, whatsapp, calendar, contacts, gmail, telegram_account, money, cameras, ops)

KIND_ORDER = ("core", "tasks", "shopping", "knowledge", "about", "people", "phone", "homeassistant", "whatsapp",
              "calendar", "contacts", "gmail", "telegram_account", "money", "cameras", "ops")


def plugin_dirs() -> tuple[Path, ...]:
    """The plugin folders ``DANA_PLUGIN_DIRS`` names that exist."""
    raw = os.environ.get("DANA_PLUGIN_DIRS", "")
    return tuple(p for p in (Path(d).expanduser().resolve() for d in raw.split(os.pathsep) if d.strip()) if p.is_dir())


def _load_plugins() -> tuple[str, ...]:
    """Import every plugin kind; returns their names in load order.

    Raises:
        ValueError: a plugin has a built-in kind's name, or two plugins share one.
    """
    built_in = Path(__file__).resolve().parent
    names: list[str] = []
    for folder in plugin_dirs():
        found = sorted(p.name for p in folder.iterdir() if (p / "kind.py").is_file())
        for name in found:
            if (built_in / name).exists() or name in names:
                raise ValueError(f"Plugin kind {name!r} in {folder} clashes with another kind of that name")
        names += found
        __path__.append(str(folder))  # integrations.<name> now resolves inside the plugin folder
    for name in names:
        import_module(f"integrations.{name}")
    return tuple(names)


PLUGIN_KINDS = _load_plugins()
KIND_ORDER = KIND_ORDER + PLUGIN_KINDS

_kind.set_order(KIND_ORDER)
_kind.validate()
