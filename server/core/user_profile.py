"""A user's profile for prompts: how a prompt names them and what it knows about them.

Every prompt written for or about a user (the agent's instructions, the
shared phone screen, call / notification analysis, people briefs, the
briefing, the weekly review, context chats) takes these words from
:func:`for_user`, which reads only the user's own row (``core/users.py``:
``name``, ``context``, ``pronoun``, ``other_names``, ``prompt_name``; migration
``m0016_user_profile``). The first admin's row carries what the prompts said
about them before it was stored, so their prompts are unchanged; anyone else
gets their own name and context ("they" until a pronoun is set with
``bin/dana users update``).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, Optional, Tuple

from core.users import User

# pronoun -> (subject, object, possessive, reflexive)
_FORMS: Dict[str, Tuple[str, str, str, str]] = {
    "he": ("he", "him", "his", "himself"),
    "she": ("she", "her", "her", "herself"),
    "they": ("they", "them", "their", "themselves"),
}


def pronoun_forms(pronoun: str) -> Tuple[str, str, str, str]:
    """(subject, object, possessive, reflexive) for ``pronoun`` ("they" forms for anything unknown)."""
    return _FORMS.get(pronoun, _FORMS["they"])


@dataclass(frozen=True)
class Profile:
    name: str        # full name, e.g. "Robin Levi"
    first_name: str  # how prompts refer to them, e.g. "Robin"
    context: str     # who they are and who they talk about ("" when nothing is known)
    called: str      # their first name as the analysis prompts give it, e.g. "Robin (רובין)"
    pronoun: str     # "he" / "she" / "they"
    referent: str    # what Dana's own instructions call them: "the owner" (the first admin), else their first name

    @property
    def obj(self) -> str:
        """The object form of :attr:`pronoun`: "him" / "her" / "them"."""
        return _FORMS[self.pronoun][1]

    @property
    def possessive(self) -> str:
        """"his" / "her" / "their"."""
        return _FORMS[self.pronoun][2]

    @property
    def reflexive(self) -> str:
        """"himself" / "herself" / "themselves"."""
        return _FORMS[self.pronoun][3]

    def agree(self, singular: str, plural: str) -> str:
        """The verb form that goes with :attr:`pronoun` ("finds" after "he", "find" after "they")."""
        return plural if self.pronoun == "they" else singular

    def about(self) -> str:
        """The prompts' "About <first name>:" block followed by a blank line ("" without a context)."""
        return f"About {self.first_name}:\n{self.context}\n\n" if self.context else ""


def for_user(user: Optional[User]) -> Profile:
    """The profile prompts use for ``user`` (empty, "they", without a user)."""
    if user is None:
        return Profile(name="", first_name="", context="", called="", pronoun="they", referent="")
    first = (user.name.split() or [""])[0]
    pronoun = user.pronoun if user.pronoun in _FORMS else "they"
    called = f"{first} ({user.other_names})" if user.other_names else first
    return Profile(name=user.name, first_name=first, context=user.context, called=called, pronoun=pronoun,
                   referent=user.prompt_name or first)
