"""``hello``: a complete, minimal site addon to copy from (server/docs/writing-an-addon.md).

Copy this folder into your site's ``addons/`` (``site/addons/hello``), rename
it and the addon, and restart Dana: the addon appears on the Addons page,
and its tools and route work for the users who connect it.
``server/tests/test_addon_loading.py`` loads this exact folder, so it keeps
working as Dana changes.
"""

from addons.hello import addon, tools  # noqa: F401 — declares the addon, registers its tools
