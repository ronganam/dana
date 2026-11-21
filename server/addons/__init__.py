"""Addons: each one is a package here that declares its credentials and
settings and owns its tools (docs/access.md, "Addon", "Addons"; ../docs/addons.md).

Importing this package registers every addon and its tools; :data:`ADDON_ORDER`
fixes their order (tool lists follow it, then the order tools are defined in).
``addons.<name>`` is the addon's package; its declared object is ``addon`` in
``addons/<name>/addon.py``, and other modules refer to it as
``from addons.<name>.addon import addon as <name>_addon``. Adding an addon: a
package with ``addon.py`` (``addon = Addon("<name>", ...)``, a
:class:`~core.addons.Addon`), ``tools.py`` (``@addon.tool``), one import line
here and its name in :data:`ADDON_ORDER`.

Site addons: an addon can also live outside this repository, in your
deployment's site folder (``site/addons/`` at the repository root, or
``$DANA_SITE/addons``; docs/site.md). ``DANA_ADDON_DIRS`` (directories
separated by ``os.pathsep``) replaces that default when set ("" = official
addons only: the tests). Each sub-package with an ``addon.py`` is loaded after
the official ones, under the same ``addons.<name>`` name, so a site addon is
written exactly like an official one (docs/writing-an-addon.md). The container
image loads ``/app/addons-site``.
"""

import os
from importlib import import_module
from pathlib import Path

from core.addons import addon as _addon
from addons import (essentials, tasks, shopping, knowledge, about, people, phone,  # noqa: F401 — registers each addon
                    homeassistant, whatsapp, calendar, contacts, gmail, telegram_account, money, cameras, ops)

ADDON_ORDER = ("essentials", "tasks", "shopping", "knowledge", "about", "people", "phone", "homeassistant", "whatsapp",
               "calendar", "contacts", "gmail", "telegram_account", "money", "cameras", "ops")


def site_dir() -> Path:
    """The deployment's site folder: ``DANA_SITE``, else ``site/`` at the repository root."""
    raw = os.environ.get("DANA_SITE", "").strip()
    return Path(raw).expanduser().resolve() if raw else Path(__file__).resolve().parents[2] / "site"


def addon_dirs() -> tuple[Path, ...]:
    """The site addon folders that exist: ``DANA_ADDON_DIRS`` when set, else the site's ``addons/``."""
    raw = os.environ.get("DANA_ADDON_DIRS")
    dirs = raw.split(os.pathsep) if raw is not None else [str(site_dir() / "addons")]
    return tuple(p for p in (Path(d).expanduser().resolve() for d in dirs if d.strip()) if p.is_dir())


def _load_site_addons() -> tuple[str, ...]:
    """Import every site addon; returns their names in load order.

    Raises:
        ValueError: a site addon has an official addon's name, or two site addons share one.
    """
    official = Path(__file__).resolve().parent
    names: list[str] = []
    for folder in addon_dirs():
        found = sorted(p.name for p in folder.iterdir() if (p / "addon.py").is_file())
        for name in found:
            if (official / name).exists() or name in names:
                raise ValueError(f"Site addon {name!r} in {folder} clashes with another addon of that name")
        names += found
        __path__.append(str(folder))  # addons.<name> now resolves inside the site folder
    for name in names:
        import_module(f"addons.{name}")
    return tuple(names)


SITE_ADDONS = _load_site_addons()
ADDON_ORDER = ADDON_ORDER + SITE_ADDONS

_addon.set_order(ADDON_ORDER)
_addon.validate()
