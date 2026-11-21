"""The ``people`` addon (People)."""

from core.addons import Addon, PromptPart, LlmJob, Page, Nav
from addons.people.values import ADDON

addon = Addon(ADDON, web=(Page("people", panel="people.js", style="people.css", nav=Nav("People", icon="users", order=40)),), order=60, default_on=True, llm_jobs={"people_brief": LlmJob("fast", "People briefs")}, description="People and their briefs",
                     prompt_parts=(PromptPart("undoable", "people", rank=50),))
