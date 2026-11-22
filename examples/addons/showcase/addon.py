"""The ``showcase`` addon's manifest: a house-plant watering helper that uses every
capability an addon has once (server/docs/addon-reference.md quotes it).

Everything the addon adds is declared on this object, as a field or as an
``@addon.<hook>`` in tools.py and hooks.py.
"""

from typing import Any, Dict, List, Mapping, Optional

from core.addons import Addon, Connection, Field, InvalidValue, LlmJob, Nav, Need, Page, PromptPart, Touches, Worker
from core.users import User


# --- reference: test ---
async def check_connection(conn: Connection) -> Optional[str]:
    """"Test connection": None when the plant service answers with the stored key, else a reason a person can act on."""
    from addons.showcase import service
    try:
        await service.tip(conn, "fern")
    except service.ServiceError as e:
        return str(e)
    return None
# --- end ---


# --- reference: options ---
async def setting_options(conn: Connection) -> Dict[str, List[Dict[str, Any]]]:
    """Values the Addons page offers for the ``room`` setting."""
    return {"room": [{"value": r, "label": r.title()} for r in ("kitchen", "bedroom", "balcony")]}
# --- end ---


# --- reference: validate ---
def check_values(user: User, given: Mapping[str, Any], credentials: Mapping[str, Any],
                 settings: Mapping[str, Any], by: Optional[User]) -> None:
    """Refuse a reminder hour outside the day."""
    hour = settings.get("remind_hour")
    if not isinstance(hour, int) or not 6 <= hour <= 22:
        raise InvalidValue("remind_hour", "Pick an hour between 6 and 22.")
# --- end ---


# --- reference: on_connect ---
def welcome(user: User) -> None:
    """Right after a user turns it on: their first plant, so the Now screen has something to show."""
    from addons.showcase import store
    if not store.plants(user):
        store.add_plant(user, "Basil", every_days=2)
# --- end ---


# --- reference: prompt_section ---
def plants_section(user: User) -> str:
    """The user's plants in the system prompt, every turn (cheap: one small query)."""
    from addons.showcase import store
    names = [p["name"] for p in store.plants(user)]
    return f"The user's house plants: {', '.join(names)}." if names else ""
# --- end ---


# --- reference: blocked ---
def paused(conn: Connection) -> Optional[str]:
    """Why its tools cannot run for this connection now: while the user paused it."""
    return "Plant care is paused in your settings." if conn.settings.get("paused") else None
# --- end ---


# --- reference: manifest ---
addon = Addon(
    "showcase",
    title="Plant care",
    description="Example addon: watering reminders for your house plants, using every capability once",
    version="1.0.0",
    icon="sprout",
    features=("Reminds you which plants need water", "Care tips for a plant"),
    setup=("Get a key at the plant service (any value works with the example's fake).",),
    examples=("Which plants need water today?", "Add a basil that needs water every 2 days"),
    disconnect_warning="Turning it off keeps your plants; turning it on again brings them back.",
    needs=(Need("env", "SHOWCASE_API_URL", "Another address for the plant service (tests point it at a fake)",
                optional=True),
           Need("env", "SHOWCASE_WORKER_URL", "Where Dana reaches the worker (default http://127.0.0.1:8092)",
                optional=True),
           Need("account", "a plant service account", "Its key unlocks the care tips", optional=True),
           Need("addon", "tasks", "Watering can also become a task", optional=True)),
    touches=Touches(hosts=("api.plants.example",), stores_credentials=True),
    credentials=(Field("api_key", "The plant service key", required=False, label="API key",
                       help="On the plant service: Account → API keys.", url="https://api.plants.example/keys",
                       placeholder="pk-123"),),
    settings=(Field("room", "Where most plants are", default="kitchen", label="Room",
                    help="Used in reminders.", placeholder="kitchen"),
              Field("remind_hour", "The hour reminders appear", default=9, label="Reminder hour",
                    help="Between 6 and 22."),
              Field("paused", "Pause plant care", default=False, label="Paused", help="True pauses its tools.")),
    prompt_parts=(PromptPart("tools", "your house plants"),
                  PromptPart("guides", "For house plants, use the showcase_ tools; add a plant before watering it.",
                             rank=90)),
    prompt_section=plants_section,
    llm_jobs={"showcase_poem": LlmJob("fast", "Plant poems")},
    web=(Page("plants", panel="plants.js", style="plants.css", nav=Nav("Plants", icon="sprout", order=85)),),
    worker=Worker(port=8092, url_env="SHOWCASE_WORKER_URL"),
    modules=("now",),                    # more modules the loader imports (tools.py and hooks.py go without saying)
    on_connect=welcome,
    test=check_connection,
    options=setting_options,
    validate=check_values,
    blocked=paused,
)
# --- end ---
