"""The official addons, one package each (../docs/addons.md; docs/writing-an-addon.md).

Importing this package loads every addon (official, site, image, installed):
core/addons/loader.py imports each one's ``addon.py``, ``tools.py``,
``hooks.py``, the modules its manifest names and ``api.py``, in the addon
order. Adding an addon is adding its folder; there is no list to edit here.
A site / installed addon resolves as ``addons.<name>`` too (the loader adds
its source folder to this package's path).
"""

from core.addons import loader

loader.load()
