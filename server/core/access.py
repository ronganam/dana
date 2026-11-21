"""Access levels (docs/access.md, "Access level").

A level covers only what addons cannot express. Every level is one row
of :data:`LEVELS`; adding a level is adding a row, adding a capability is a
field of :class:`AccessLevel` (named in :data:`CAPABILITIES`) set on every row.
Code asks a level with :meth:`AccessLevel.allows` (web endpoints:
``core.auth.requires``; addons: ``Addon(requires=...)``) or, for
chat model profiles, :meth:`AccessLevel.may_use_profile`.
"""

from dataclasses import dataclass
from typing import Dict, Optional, Tuple


@dataclass(frozen=True)
class AccessLevel:
    name: str
    # May connect the ``ops`` addon (kubectl, Python).
    ops: bool
    # May manage users and other users' addons.
    manage_users: bool
    # Sees everyone's usage and the system self-checks (otherwise only their own).
    see_everyone: bool
    # Runs the server itself: its log buffer, the shared product catalog.
    manage_server: bool
    # Chat model profiles (core/models.py) the level may pick; None = all.
    chat_profiles: Optional[Tuple[str, ...]]

    def allows(self, capability: str) -> bool:
        """Whether this level has ``capability`` (one of :data:`CAPABILITIES`).

        Raises:
            ValueError: no such capability.
        """
        if capability not in CAPABILITIES:
            raise ValueError(f"Unknown capability {capability!r} (known: {', '.join(CAPABILITIES)})")
        return bool(getattr(self, capability))

    def may_use_profile(self, profile_id: str) -> bool:
        return self.chat_profiles is None or profile_id in self.chat_profiles


# The yes/no capabilities of a level (the bool fields of AccessLevel).
CAPABILITIES: Tuple[str, ...] = ("ops", "manage_users", "see_everyone", "manage_server")

ADMIN = "admin"
MEMBER = "member"

LEVELS: Dict[str, AccessLevel] = {
    ADMIN: AccessLevel(ADMIN, ops=True, manage_users=True, see_everyone=True, manage_server=True,
                       chat_profiles=None),
    MEMBER: AccessLevel(MEMBER, ops=False, manage_users=False, see_everyone=False, manage_server=False,
                        chat_profiles=("fast", "balanced")),
}


def level(name: str) -> AccessLevel:
    """The level called ``name``.

    Raises:
        ValueError: no such level.
    """
    try:
        return LEVELS[name]
    except KeyError:
        raise ValueError(f"Unknown access level {name!r} (known: {', '.join(LEVELS)})") from None
