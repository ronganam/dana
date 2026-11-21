"""The ``people`` integration kind (People)."""

from integrations.framework import Integration, PromptPart
from integrations.people.values import KIND

people = Integration(KIND, description="People and their briefs",
                     prompt_parts=(PromptPart("undoable", "people", rank=50),))
