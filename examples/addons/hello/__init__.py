"""``hello``: a complete, small site addon to copy from (server/docs/writing-an-addon.md).

Copy this folder into your site's ``addons/`` (``site/addons/hello``), rename
it and the addon, and restart Dana: the addon appears on the Addons page, its
table is created, and its tool, job, event handler, Now card and route work
for the users who connect it. The loader imports ``addon.py``, then
``tools.py``, ``hooks.py`` and ``api.py``; this file needs nothing.
``tests/test_hello.py`` is its test; ``server/tests/test_addon_loading.py``
loads this exact folder and runs it, so it keeps working as Dana changes.
"""
