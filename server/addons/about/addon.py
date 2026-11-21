"""The ``about`` addon (About you)."""

from addons.about import facts, prompt
from core.addons import Addon, PromptPart, LlmJob, Web, Nav

# ``{user}``: what the agent's instructions call the user (core/agent.py).
MEMORY_GUIDE = (
    "What you know about {user} is listed in the About section of this prompt when there is one: use it without "
    "looking it up and without mentioning the list. When you learn something durable about {user} (who a person "
    "is to them, what a nickname or device name refers to, a standing preference, a contact chat ID), save it with "
    "remember_info in one short sentence without being asked; never save secrets or one-time codes. "
)
CONTACTS_GUIDE = (
    "When asked to invite or message someone by name (like 'dan'), first search your persistent memories/contacts "
    "using recall_info to find their details (like email or chat ID) before asking the user for them. "
)

# Connecting it builds the user's prompt block; the block is the addon's
# section of their system prompt (core/agent.py asks for it every turn).
addon = Addon(facts.ADDON, web=Web(panel="memory.js", style="memory.css", nav=Nav("Memory", icon="star", order=60)), order=50, default_on=True, llm_jobs={"about_learner": LlmJob("fast", "About you learner")}, description="About-you facts and their learner",
                    on_connect=facts.refresh_prompt, prompt_section=prompt.section,
                    prompt_parts=(
                        PromptPart("tools", "persistent memory", rank=70),
                        PromptPart("read_examples", "recalling memories", rank=30),
                        PromptPart("id_examples", "memory keys", rank=50),
                        PromptPart("guides", MEMORY_GUIDE, rank=10),
                        PromptPart("guides", CONTACTS_GUIDE, rank=30),
                    ))
