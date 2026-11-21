"""The ``core`` integration kind (Core)."""

from integrations.framework import Integration, PromptPart

# Every user has it: no row, no credentials. Its parts of the agent's
# instructions (core/agent.py) are the ones every user gets.
core = Integration(
    "core", everyone=True, description="The time, asking the user to pick, web search",
    prompt_parts=(
        PromptPart("tools", "web search", rank=40),
        PromptPart("action_examples", "set a timer", rank=20),
        PromptPart("choice_examples", "which of several matching events/items to act on", rank=30),
    ),
)
