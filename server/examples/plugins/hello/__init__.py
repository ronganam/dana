"""``hello``: a complete, minimal plugin kind to copy from (docs/plugins.md).

Copy this folder into your site's ``plugins/`` (``site/plugins/hello``), rename
it and the kind, and restart Dana: the kind appears on the Integrations page,
and its tools and route work for the users who connect it. ``test_plugins.py``
loads this exact folder, so it keeps working as Dana changes.
"""

from integrations.hello.kind import hello
from integrations.hello import tools  # noqa: F401 — registers the tools

__all__ = ["hello"]
