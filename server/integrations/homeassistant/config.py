"""Home Assistant configuration of one user, read from their connection: the
kind's name, the setting names and what each means, and the
``/api/client-config`` payload. Nothing here reads env: the first admin's
values are imported once by ``integrations/bootstrap.py``."""

from typing import Any, Dict, Optional

from integrations.framework import Connection

KIND = "homeassistant"

# Home Assistant's own default address (used when a connection has no host).
DEFAULT_HOST = "http://homeassistant.local:8123"

# Setting names (docs/access.md, "Kinds").
PERSON = "person"                          # person entity: zone enter / leave triggers
CAR_SENSOR = "car_sensor"                  # phone Bluetooth connection sensor: car enter / leave
CAR_DEVICE = "car_device"                  # the car's Bluetooth name in that sensor's connected_paired_devices
SCREEN_SENSOR = "phone_interactive_sensor"  # phone screen-on binary sensor: wake confirmation
BATTERY_SENSOR = "phone_battery_sensor"     # phone battery sensor: the bedroom dashboard's tile
NOTIFY_SERVICE = "phone_notify_service"     # mobile_app notify service suffix (only reported by /api/client-config)
WEATHER = "weather_entity"                  # weather entity: the briefing and the Now lede

# Generic default of the weather entity (Home Assistant's own home forecast).
DEFAULT_WEATHER_ENTITY = "weather.forecast_home"


def setting(conn: Connection, name: str) -> Optional[str]:
    """Setting ``name`` of ``conn`` as a stripped string (None when unset or empty)."""
    value = conn.settings.get(name)
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def client_config(conn: Connection) -> Dict[str, Any]:
    """``GET /api/client-config``: the user's phone and person entities (key
    order and names are the clients' contract)."""
    return {
        "notify_service": conn.settings.get(NOTIFY_SERVICE),
        "interactive_sensor": conn.settings.get(SCREEN_SENSOR),
        "battery_sensor": conn.settings.get(BATTERY_SENSOR),
        "person": conn.settings.get(PERSON),
    }
