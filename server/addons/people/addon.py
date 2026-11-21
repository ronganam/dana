"""The ``people`` addon (People)."""

from core.addons import Addon, PromptPart, LlmJob
from addons.people.values import ADDON

addon = Addon(ADDON, order=60, default_on=True, llm_jobs={"people_brief": LlmJob("fast", "People briefs")}, description="People and their briefs",
                     prompt_parts=(PromptPart("undoable", "people", rank=50),))
