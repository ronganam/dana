"""People filled in from the user's Google contacts (docs/contacts.md, "People").

Delegates through the ``people`` addon's ``people_fill_from_contacts`` and
``people_forget_contacts`` providers so ``contacts`` never imports ``people``.
"""

from core.addons import registry
from core.users import User


def fill_people(user: User) -> int:
    """Fill ``user``'s people from their contacts copy (0 without ``people`` or ``contacts``)."""
    hook = registry.provider("people_fill_from_contacts", user)
    return int(hook.func(user)) if hook is not None else 0


def forget(user: User) -> None:
    """The user's contacts copy is gone: their people keep what was filled in, not the link."""
    hook = registry.provider("people_forget_contacts", user)
    if hook is not None:
        hook.func(user)
