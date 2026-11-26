"""The ``homeassistant`` addon (Home Assistant)."""

from core.addons import ADDRESS, Field, Addon, PromptPart, Need, Touches
from addons.homeassistant import client, home_map, options, states
from addons.homeassistant.event_types import HOMEASSISTANT_EVENTS
from addons.homeassistant.config import ADDON, BATTERY_SENSOR, TV, TV_CHANNEL

# Entity settings have no default: whose entities they are is the user's
# choice, set on the Addons page.
addon = Addon(
    ADDON, order=80, title="Home Assistant", icon="house",
    credentials=(
        Field("host", "Home Assistant base URL, e.g. https://home.example.com", type=ADDRESS, label="Address",
              help="The address you open Home Assistant at in a browser, with http(s):// and the port if it has one.",
              placeholder="https://home.example.com"),
        Field("token", "Long-lived access token", label="Access token",
              help="In Home Assistant: click your name at the bottom left → Security → Long-lived access tokens → "
                   "Create token, then copy it here.",
              url="https://www.home-assistant.io/docs/authentication/#your-account-profile"),
    ),
    settings=(
        Field(BATTERY_SENSOR, "Phone battery level sensor (a dashboard's Batteries widget when it names none)",
              label="Phone's battery sensor", help="From the Home Assistant phone app; shown on dashboards.",
              placeholder="sensor.pixel_battery_level"),
        Field(TV, "Android TV media_player entity (the androidtv / ADB one) the TV tool controls by default",
              label="TV", help="Your Android TV's media player (the Android Debug Bridge one), if Dana should control it.",
              placeholder="media_player.living_room_tv"),
        Field(TV_CHANNEL, "ADB command that tunes the TV to a channel, with {channel} in it (e.g. your TV "
              "provider app's deep link); unset: no channel changes", label="TV channel command",
              help="Optional, for changing channels: a command with {channel} where the number goes."),
    ),
    needs=(Need("hardware", "a Home Assistant server Dana's server can reach", "Lights, devices and rooms come from it"),
           Need("account", "a Home Assistant long-lived access token", "Dana signs in to Home Assistant with it")),
    touches=Touches(hosts=("your Home Assistant server",), acts_outside=True, stores_credentials=True),
    description="Lights, devices and the TV, and which room each one is in",
    features=("Turn lights, the AC, gates and other devices on and off",
              "Knows which devices are in which room",
              "Controls the TV"),
    examples=("Turn off the living room lights", "Which lights are on?", "Is the gate open?"),
    prompt_section=home_map.prompt_section,
    events=HOMEASSISTANT_EVENTS,
    on_change=states.poke,
    modules=("widgets",),
    test=client.check,
    options=options.entity_options,
    prompt_parts=(
        PromptPart("tools", "HomeAssistant control", rank=20),
        PromptPart("read_examples", "listing Home Assistant entities", rank=10),
        PromptPart("id_examples", "Home Assistant entity IDs", rank=10),
        PromptPart("prompt_ids", "the Home Devices list"),
        PromptPart("runs_at_once", "; home-device commands run immediately too"),
        PromptPart("action_examples", "open/close a gate, turn on/off lights", rank=10),
        PromptPart("choice_examples", "which AC to control", rank=20),
    ),
)
