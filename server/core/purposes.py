"""LLM purposes: what each non-chat model call is for (docs/usage.md, docs/configuration.md).

Every ``core.llm`` call names its ``purpose``; usage is filed under it and the
Usage screen shows its label. Core's own purposes are :data:`CORE`; an addon
adds its own with ``Addon(llm_jobs={"task_emoji": "fast"})`` (registered by
the loader, :func:`register`). A purpose has a default model profile
(``fast`` / ``balanced`` / ``deep``, core/models.py); ``MODEL_<PURPOSE>`` in env
overrides the model for that purpose alone (core/llm.py). An unknown purpose
is refused (:class:`UnknownPurpose`), so a typo never files usage under a
name nobody reads.

A purpose is either **background** (work Dana does on its own: notification
triage, briefs and briefings, the About learner, money categories,
summaries…) or **user-started** (chat, voice, anything a person asked for).
The monthly budget (core/budget.py) pauses only background purposes;
an addon marks one with ``LlmJob("fast", "Label", background=True)``.
"""

from dataclasses import dataclass
from typing import Dict, Optional

PROFILES = ("fast", "balanced", "deep")


class UnknownPurpose(ValueError):
    """A purpose neither core nor any loaded addon declares."""


@dataclass(frozen=True)
class Purpose:
    name: str
    profile: str
    label: str
    addon: str = "core"
    background: bool = False


# Core's own purposes: name → (default profile, label, background).
CORE: Dict[str, tuple] = {
    "chat": ("balanced", "Chat with Dana", False),
    "voice_reply": ("balanced", "Voice chat replies", False),
    "realtime_voice": ("balanced", "Live voice conversation", False),
    "voice_transcript": ("fast", "Voice note transcripts", False),
    "voice_summary": ("fast", "Voice note summaries", False),
    "wake_briefing": ("fast", "Wake briefing", True),
    "weekly_review": ("fast", "Weekly review", True),
    "setup_check": ("fast", "Testing a new LLM key", False),
    "other": ("fast", "Other (untagged)", False),
}

_purposes: Dict[str, Purpose] = {name: Purpose(name, profile, label, background=background)
                                 for name, (profile, label, background) in CORE.items()}


def register(name: str, profile: str, label: str = "", *, addon: str, background: bool = False) -> Purpose:
    """Declare ``addon``'s purpose ``name`` with its default ``profile``
    (``background``: the monthly budget may pause it).

    Raises:
        ValueError: an unknown profile, or ``name`` already belongs to core or another addon.
    """
    if profile not in PROFILES:
        raise ValueError(f"{addon}: LLM purpose {name!r} has unknown profile {profile!r} (one of {PROFILES})")
    existing = _purposes.get(name)
    if existing is not None and existing.addon != addon:
        raise ValueError(f"{addon}: LLM purpose {name!r} already belongs to {existing.addon}")
    p = _purposes[name] = Purpose(name, profile, label or name.replace("_", " ").capitalize(), addon, background)
    return p


def forget_addon(addon: str) -> None:
    """Remove every purpose of ``addon`` (the loader, for an addon that did not load after all)."""
    for name in [n for n, p in _purposes.items() if p.addon == addon]:
        del _purposes[name]


def get(name: str) -> Purpose:
    """The purpose called ``name``.

    Raises:
        UnknownPurpose: no such purpose.
    """
    try:
        return _purposes[name]
    except KeyError:
        raise UnknownPurpose(f"Unknown LLM purpose {name!r}: declare it in the addon's llm_jobs") from None


def label(name: str) -> Optional[str]:
    """How the Usage screen names ``name`` (None for an unknown one)."""
    p = _purposes.get(name)
    return p.label if p else None


def known(name: str) -> bool:
    return name in _purposes


def is_background(name: str) -> bool:
    """Whether ``name`` is background work (False for a user-started or unknown purpose)."""
    p = _purposes.get(name)
    return bool(p and p.background)
