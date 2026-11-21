"""The ``homeassistant`` addon (Home Assistant)."""

from core.addons import ADDRESS, Field, Addon, PromptPart
from addons.homeassistant import client, home_map, options, watcher
from addons.homeassistant.config import (BATTERY_SENSOR, CAR_DEVICE, CAR_SENSOR, DEFAULT_WEATHER_ENTITY, ADDON,
                                               NOTIFY_SERVICE, PERSON, SCREEN_SENSOR, WEATHER)

# Entity settings have no default (whose entities they are is the user's
# choice, set on the Addons page), except
# Home Assistant's own name for the home forecast.
addon = Addon(
    ADDON, order=80,
    credentials=(
        Field("host", "Home Assistant base URL, e.g. https://home.example.com", type=ADDRESS),
        Field("token", "Long-lived access token"),
    ),
    settings=(
        Field(PERSON, "The user's person entity: zone enter / leave triggers"),
        Field(CAR_SENSOR, "The phone's Bluetooth connection sensor: car enter / leave triggers"),
        Field(CAR_DEVICE, "The car's Bluetooth name, as it shows in that sensor's connected_paired_devices"),
        Field(SCREEN_SENSOR, "Phone screen-on (interactive) binary sensor: confirms the wake signal"),
        Field(BATTERY_SENSOR, "Phone battery level sensor (the bedroom dashboard's phone tile)"),
        Field(NOTIFY_SERVICE, "The phone's mobile_app notify service suffix (reported by /api/client-config)"),
        Field(WEATHER, "Weather entity for the wake briefing and the Now screen", DEFAULT_WEATHER_ENTITY),
    ),
    description="Home Assistant",
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
