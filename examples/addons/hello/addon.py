"""The ``hello`` addon's manifest: everything it adds to Dana is declared on this object."""

from core.addons import Addon, Field, Nav, Page, PromptPart, Touches

addon = Addon(
    "hello",
    title="Hello",
    description="Example addon: greets people in your words, and reminds you who to greet",
    version="1.0.0",
    icon="hand",
    features=("Greets people in your words", "Reminds you who to greet"),
    examples=("Say hello to Noa",),
    # What running it takes (needs=(Need("env", "HELLO_API_KEY", "why"),) …) and what it may touch, shown
    # before it is turned on: hello needs nothing and touches nothing beyond Dana's own data.
    needs=(),
    touches=Touches(),
    # Per-user choices (plain JSON); credentials=(Field(...),) would be stored encrypted.
    settings=(Field("greeting", "How to greet people", default="Hello", label="Greeting",
                    help="The word Dana starts a greeting with.", placeholder="Hello"),),
    # A line of the agent's instructions, only for users who turned the addon on.
    prompt_parts=(PromptPart("guides", "When the user asks you to greet someone, use hello_greet.", rank=90),),
    # A page in the web app: web/hello.js and web/hello.css, a button in the navigation.
    web=(Page("hello", panel="hello.js", style="hello.css", nav=Nav("Hello", icon="hand", order=80)),),
)
