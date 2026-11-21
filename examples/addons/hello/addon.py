"""The ``hello`` manifest: what a user connects, with one setting and one line of instructions."""

from core.addons import Addon, Field, PromptPart

addon = Addon(
    "hello",
    title="Hello",
    description="Example addon: greets people in your words, and reminds you who to greet",
    # Per-user choices (plain JSON); credentials=(Field(...),) would be stored encrypted.
    settings=(Field("greeting", "How to greet people", default="Hello"),),
    # A piece of the agent's instructions, only for users who connected the addon.
    prompt_parts=(PromptPart("guides", "When the user asks you to greet someone, use hello_greet.", rank=90),),
)
