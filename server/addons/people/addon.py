"""The ``people`` addon (People)."""

from core.addons import Addon, PromptPart, LlmJob, Web, Nav
from addons.people.values import ADDON

addon = Addon(ADDON, web=Web(panel="people.js", style="people.css", nav=Nav("People", icon="users", order=40)), order=60, default_on=True, llm_jobs={"people_brief": LlmJob("fast", "People briefs")}, title="People", icon="users", description="The people you talk to and a brief on each",
                     features=("A page per person: who they are, your calls and open things",
                               "A short brief before you call or meet them"),
                     prompt_parts=(PromptPart("undoable", "people", rank=50),))
