"""Removes the settings of Home Assistant as a source of signals from every
stored ``homeassistant`` connection (``connections.settings``, plain JSON):
``person``, ``car_sensor``, ``car_device``, ``phone_interactive_sensor``,
``weather_entity`` and ``phone_notify_service``. Wake, screen, car and
location come from Dana's phone app, the weather from Open-Meteo. The other
settings and the credentials are kept as they are; a row whose settings are
not a JSON object is left alone. Also deleted: the entity watcher's marks
(``system_marks`` keys ``ha_watcher:<user>``, ``ha_entity:<user>:<role>``,
``ha_event:<user>:<role>``) and what Home Assistant last delivered as a
signal source (``signal_sources`` rows of source ``homeassistant``)."""

import json
import sqlite3

VERSION = 1
NAME = "drop_sensor_settings"

DROPPED = ("person", "car_sensor", "car_device", "phone_interactive_sensor", "weather_entity",
           "phone_notify_service")


def up(conn: sqlite3.Connection) -> None:
    rows = conn.execute("SELECT user_id, settings FROM connections WHERE addon = 'homeassistant'").fetchall()
    for user_id, raw in rows:
        try:
            settings = json.loads(raw or "{}")
        except ValueError:
            continue
        if not isinstance(settings, dict) or not any(k in settings for k in DROPPED):
            continue
        kept = {k: v for k, v in settings.items() if k not in DROPPED}
        conn.execute("UPDATE connections SET settings = ? WHERE user_id = ? AND addon = 'homeassistant'",
                     (json.dumps(kept, ensure_ascii=False), user_id))
    conn.execute("DELETE FROM system_marks WHERE key LIKE 'ha_watcher:%' OR key LIKE 'ha_entity:%' "
                 "OR key LIKE 'ha_event:%'")
    conn.execute("DELETE FROM signal_sources WHERE source = 'homeassistant'")
