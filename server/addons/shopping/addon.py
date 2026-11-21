"""The ``shopping`` addon (Shopping)."""

from core.addons import Field, Addon, PromptPart, LlmJob, Page, Nav
from addons.shopping import lists
from addons.shopping.values import ACTIVE_SETTING

# Connecting it gives the user their own shopping list.
addon = Addon(
    "shopping", web=(Page("shopping", panel="shopping.js", style="shopping.css", nav=Nav("Shopping", icon="shopping-cart", order=10)),), order=30, default_on=True, llm_jobs={"shopping_category": LlmJob("fast", "Shopping aisles")},
    settings=(
        Field(ACTIVE_SETTING, "The list the user's shopping changes go to: their own (None) or the id of a "
                              "list shared with them to edit."),
    ),
    description="Shopping lists",
    on_connect=lists.ensure_list,
    validate=lists.validate_settings,
    prompt_parts=(
        PromptPart("undoable", "the shopping list", rank=30),
        PromptPart("done_examples", "'הוספתי חלב לרשימה.'", rank=20),
        PromptPart("routing_heading", "SHOPPING", rank=30),
        PromptPart("routing", "Use get_shopping_list / add_shopping_items / update_shopping_items / "
                   "edit_shopping_item for ALL grocery and supermarket products (milk, bread, toilet paper, etc.); "
                   "give each item its own quantity and note when the user says them ('2 חלב 3%' → name 'חלב', "
                   "quantity '2', note '3%'), and name items to tick or remove without reading the list first. ",
                   rank=30),
        PromptPart("routing", "NEVER use add_task for groceries; NEVER put grocery items in the Shopping task group. ",
                   rank=31, needs=("tasks",)),
    ),
)
