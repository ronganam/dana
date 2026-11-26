"""The ``watches`` addon (Watches): any "when X happens, do Y" / "make sure" /
multi-trigger reminder, compiled by a model into a watch spec and fired by a
cheap engine (docs/watches.md)."""

from core.addons import Addon, LlmJob, Nav, Need, Page, PromptPart, Touches

ADDON = "watches"

addon = Addon(
    ADDON, order=170, default_on=True, title="Watches", icon="radar",
    description="Dana watches for what you ask and acts when it happens",
    features=("Reminders with several triggers: a time, before it, when you get in the car",
              "Make sure something happens by a deadline (a payment, a message)",
              "When something happens, Dana notifies you, adds a task or does the work"),
    examples=("Make sure a transfer of about 2,000 from Maya arrives by tomorrow",
              "Remind me at 20:30 to pick up Noa, 30 minutes before too, or when I get in the car",
              "When the dishwasher finishes, ask Gal on WhatsApp to empty it"),
    needs=(Need("addon", "tasks", "A reminder is also a task you can tick off", optional=True),
           Need("addon", "phone", "Notifications, emails and calls to watch for", optional=True),
           Need("addon", "money", "Bank and card transactions to watch for", optional=True)),
    touches=Touches(acts_outside=True, reads=("phone", "money", "tasks")),
    llm_jobs={"watch_compile": LlmJob("balanced", "Watch builder"),
              "watch_match": LlmJob("fast", "Watch checks", background=True),
              "watch_agent": LlmJob("balanced", "Watch actions", background=True)},
    web=(Page("watches", panel="watches.js", style="watches.css", nav=Nav("Watches", icon="radar", order=45)),),
    prompt_parts=(
        PromptPart("tools", "watches (when something happens, do something)", rank=35),
        PromptPart("guides", "WATCHES: use create_watch with the user's own words for anything conditional or "
                   "with more than one trigger: 'make sure X happens by …', 'tell me when …', 'if … then …', "
                   "'when X happens do Y', 'every time …', a reminder at a time plus before it or when they get "
                   "in the car / arrive somewhere, 'unless …'. A plain one-time reminder at one time can stay "
                   "add_task. Change one with update_watch (plain words), see them with list_watches.", rank=40),
    ),
)
