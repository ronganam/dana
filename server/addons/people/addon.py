"""The ``people`` addon (People)."""

from core.addons import Addon, PromptPart
from addons.people.values import ADDON

addon = Addon(ADDON, description="People and their briefs",
                     prompt_parts=(PromptPart("undoable", "people", rank=50),))
