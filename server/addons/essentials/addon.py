"""The ``essentials`` addon: what every user has."""

from core.addons import Addon, PromptPart, Touches

# Every user has it: no row, no credentials. Its parts of the agent's
# instructions (core/agent.py) are the ones every user gets.
addon = Addon(
    "essentials", order=10, everyone=True, title="Essentials", icon="sparkles",
    touches=Touches(hosts=("DuckDuckGo and other public search engines", "api.open-meteo.com")),
    description="The time, asking you to pick, web search, the weather at Home",
    prompt_parts=(
        PromptPart("tools", "web search", rank=40),
        PromptPart("action_examples", "set a timer", rank=20),
        PromptPart("choice_examples", "which of several matching events/items to act on", rank=30),
    ),
)
