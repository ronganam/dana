"""The ``homeassistant`` addon (Home Assistant)."""

from core.addons import ADDRESS, Field, Addon, PromptPart, Need, Touches
from addons.homeassistant import client, home_map, options, watcher
from addons.homeassistant.config import (BATTERY_SENSOR, CAR_DEVICE, CAR_SENSOR, DEFAULT_WEATHER_ENTITY, ADDON,
                                               NOTIFY_SERVICE, PERSON, SCREEN_SENSOR, TV, TV_CHANNEL, WEATHER)

# Entity settings have no default (whose entities they are is the user's
# choice, set on the Addons page), except
# Home Assistant's own name for the home forecast.
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
        Field(PERSON, "The user's person entity: zone enter / leave triggers", label="You in Home Assistant",
              help="Your person in Home Assistant (Settings → People), for reminders when you arrive or leave.",
              placeholder="person.noa"),
        Field(CAR_SENSOR, "The phone's Bluetooth connection sensor: car enter / leave triggers",
              label="Phone's Bluetooth sensor",
              help="From the Home Assistant phone app, for reminders when you get in the car.",
              placeholder="sensor.pixel_bluetooth_connection"),
        Field(CAR_DEVICE, "The car's Bluetooth name, as it shows in that sensor's connected_paired_devices",
              label="Car's Bluetooth name", help="The name your car has in your phone's Bluetooth list.",
              placeholder="My Car"),
        Field(SCREEN_SENSOR, "Phone screen-on (interactive) binary sensor: confirms the wake signal",
              label="Phone's screen-on sensor", help="From the Home Assistant phone app; tells Dana you are awake.",
              placeholder="binary_sensor.pixel_interactive"),
        Field(BATTERY_SENSOR, "Phone battery level sensor (the bedroom dashboard's phone tile)",
              label="Phone's battery sensor", help="From the Home Assistant phone app; shown on the bedroom dashboard.",
              placeholder="sensor.pixel_battery_level"),
        Field(NOTIFY_SERVICE, "The phone's mobile_app notify service suffix (reported by /api/client-config)",
              label="Phone's notify service", help="Set by the Dana phone app; change it only if it is wrong.",
              placeholder="mobile_app_pixel"),
        Field(WEATHER, "Weather entity for the wake briefing and the Now screen", DEFAULT_WEATHER_ENTITY,
              label="Weather", help="The weather entity your forecast comes from (Settings → Devices & services).",
              placeholder=DEFAULT_WEATHER_ENTITY),
        Field(TV, "Android TV media_player entity (the androidtv / ADB one) the TV tool controls by default",
              label="TV", help="Your Android TV's media player (the Android Debug Bridge one), if Dana should control it.",
              placeholder="media_player.living_room_tv"),
        Field(TV_CHANNEL, "ADB command that tunes the TV to a channel, with {channel} in it (e.g. your TV "
              "provider app's deep link); unset: no channel changes", label="TV channel command",
              help="Optional, for changing channels: a command with {channel} where the number goes."),
    ),
    needs=(Need("hardware", "a Home Assistant server Dana's server can reach", "Lights, devices, zones and the weather come from it"),
           Need("account", "a Home Assistant long-lived access token", "Dana signs in to Home Assistant with it"),
           Need("app", "the Home Assistant phone app", "Arrive, leave, car and wake reminders use its sensors", optional=True)),
    touches=Touches(hosts=("your Home Assistant server",), acts_outside=True, stores_credentials=True),
    description="Lights, devices and the TV, reminders when you arrive or get in the car",
    features=("Turn lights, the AC, gates and other devices on and off",
              "Reminders when you arrive at a place, leave it or get in the car",
              "Knows which devices are in which room",
              "Controls the TV",
              "The weather for your morning briefing"),
    examples=("Turn off the living room lights", "Remind me to take out the trash when I get home",
              "Is the gate open?"),
    prompt_section=home_map.prompt_section,
    on_change=watcher.request_reconcile,
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
