"""Home Assistant configuration of one user, read from their connection: the
addon's name and the setting names and what each means. Nothing here reads
env: the values are set on the Addons page or with bin/dana."""

from typing import Optional

from core.addons import Connection

ADDON = "homeassistant"

# Home Assistant's own default address (used when a connection has no host).
DEFAULT_HOST = "http://homeassistant.local:8123"

# Setting names (docs/access.md, "Addons").
BATTERY_SENSOR = "phone_battery_sensor"  # phone battery sensor: the Batteries widget's default
TV = "tv_entity"                         # Android TV media_player: control_android_tv's default
TV_CHANNEL = "tv_channel_command"        # ADB command with {channel}: control_android_tv's watch_channel


def setting(conn: Connection, name: str) -> Optional[str]:
    """Setting ``name`` of ``conn`` as a stripped string (None when unset or empty)."""
    value = conn.settings.get(name)
    if value is None:
        return None
    text = str(value).strip()
    return text or None

