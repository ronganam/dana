"""The Home Assistant addon's background work, self-checks and composer hooks
(../docs/addons.md, "The manifest").

- the entity watchers (a service): one per user with a person entity or a
  car sensor set, over their own connection; each transition is emitted as
  the event ``homeassistant.location.changed`` or ``homeassistant.car.changed``;
- at start: every user's home map fetched in the background for their prompt;
- self-checks: ``location``, ``car``, ``ha_entities``, ``ha_watcher``;
- morning briefing: ``weather``;
- providers: ``weather``, ``screen_sessions``.
"""

import datetime
import logging
import os
import time
from typing import Any, Dict, List, Optional, Tuple

from core import events
from core.addons import BriefingContext, Check, Section, helpers, store
from core.users import User
from addons.homeassistant import client as ha_client, config, home_map, status as ha_status, watcher as ha_watcher
from addons.homeassistant.addon import addon
from addons.homeassistant.config import ADDON
from addons.homeassistant.watcher import Watchers

logger = logging.getLogger(__name__)

LOCATION_DAYS = float(os.getenv("CHECK_LOCATION_DAYS", "3"))
CAR_DAYS = float(os.getenv("CHECK_CAR_DAYS", "7"))
HA_WATCHER_MINUTES = float(os.getenv("CHECK_HA_WATCHER_MINUTES", "10"))
_STARTED = time.monotonic()

async def emit_transition(user: User, trigger_type: str, *, action: str = "", target: str = "") -> None:
    """A watcher saw ``user`` arrive / leave a zone or enter / leave the car."""
    events.emit(f"homeassistant.{trigger_type}.changed", user, action=action, target=target)


@addon.service
async def watch_entities(app: Any) -> None:
    """Keep every user's entity watcher running (reconciled with their connections)."""
    await Watchers(emit_transition).run_forever()


@addon.job(at_start=True)
async def warm_home_map(user: User) -> None:
    conn = store.get(user, ADDON)
    task = home_map.warm_home_map(conn) if conn is not None else None
    if task is not None:
        await task


# --- Providers & briefing -------------------------------------------------------------------

@addon.provide("weather")
async def fetch_weather(user: User) -> Optional[Dict[str, Any]]:
    """``user``'s weather (their Home Assistant ``weather_entity``):
    condition and temperature, today's high / low / rain chance when the
    forecast answers. None without Home Assistant or when it fails."""
    conn = store.get(user, ADDON)
    entity = config.setting(conn, config.WEATHER) if conn is not None else None
    if conn is None or not entity:
        return None
    try:
        s = await ha_client.entity_state(conn, entity)
        if s is None:
            return None
        out = {"condition": str(s.get("state") or "").replace("-", " "),
               "temperature": (s.get("attributes") or {}).get("temperature"),
               "unit": (s.get("attributes") or {}).get("temperature_unit") or "°C"}
        d = await ha_client.forecast(conn, entity)
        if d:
            out.update({"high": d.get("temperature"), "low": d.get("templow"),
                        "rain_chance": d.get("precipitation_probability")})
        return out
    except Exception as e:
        logger.debug(f"Briefing weather failed: {e}")
        return None


@addon.briefing("weather", default=None, rank=10)
async def briefing_weather(user: User, ctx: BriefingContext) -> Section:
    w = await fetch_weather(user)
    points = []
    if w and w.get("temperature") is not None:
        hi = f", high {w['high']}°" if w.get("high") is not None else ""
        points.append(f"{w['condition'].capitalize()}, {w['temperature']}°{hi}")
    return Section(w, points=points)


@addon.provide("screen_sessions")
async def screen_sessions(user: User, since_utc: datetime.datetime,
                          until_utc: datetime.datetime) -> Optional[List[float]]:
    """Seconds of each screen-on stretch of ``user``'s phone screen sensor in
    [since, until], from their Home Assistant's history."""
    conn = store.get(user, ADDON)
    entity = config.setting(conn, config.SCREEN_SENSOR) if conn is not None else None
    if conn is None or not entity:
        return None
    rows = await ha_client.history(conn, entity, since_utc, until_utc)
    if rows is None:
        return None
    points = []
    for x in rows:
        try:
            t = datetime.datetime.fromisoformat(str(x.get("last_changed")).replace("Z", "+00:00"))
        except ValueError:
            continue
        points.append((t, x.get("state")))
    out: List[float] = []
    for i, (t, state) in enumerate(points):
        if state != "on":
            continue
        t_end = points[i + 1][0] if i + 1 < len(points) else until_utc
        a, b = max(t, since_utc), min(t_end, until_utc)
        if b > a:
            out.append((b - a).total_seconds())
    return out


# --- Self-checks ----------------------------------------------------------------------------

def _check_feed(user: User, now: datetime.datetime, role: str, setting: str, days: float, what: str,
                why: str) -> Check:
    last = helpers.parse_time(ha_status.read(user.id).last_event.get(role))
    if not last:
        return Check(False, "never received", None, title=f"No {what} updates received yet", why=why)
    quiet = (now - last).total_seconds()
    if quiet > days * 86400:
        return Check(False, f"quiet for {helpers.span(quiet)}", last,
                     title=f"No {what} updates for {helpers.span(quiet)}", why=why,
                     blocks=[{"type": "text", "label": "Where it comes from",
                              "text": f"Dana watches the entity in the {setting} setting of your Home Assistant "
                                      "addon. After a phone change, set the new phone's entity there."}])
    return Check(True, f"last {helpers.ago(last, now)}", last)


def _ha_watch(user: User) -> Tuple[Any, Optional[ha_watcher.WatchSpec]]:
    conn = store.get(user, ADDON)
    return conn, ha_watcher.spec_for(conn)


@addon.selfcheck("location", "Location updates")
def check_location(user: User, now: datetime.datetime) -> Check:
    return _check_feed(user, now, "person", config.PERSON, LOCATION_DAYS, "location",
                       "Reminders for arriving at or leaving places won't fire.")


@addon.selfcheck("car", "Car updates")
def check_car(user: User, now: datetime.datetime) -> Check:
    return _check_feed(user, now, "car", config.CAR_SENSOR, CAR_DAYS, "car",
                       "In-the-car reminders won't fire.")


@addon.selfcheck("ha_entities", "Home Assistant entities")
def check_ha_entities(user: User, now: datetime.datetime) -> Check:
    _, spec = _ha_watch(user)
    if spec is None:
        return Check(True, "nothing selected")
    st = ha_status.read(user.id)
    seen = {role: st.entities.get(role) for role in spec.entities}
    missing = [(role, eid) for role, eid in spec.entities.items()
               if seen[role] is not None and seen[role].entity_id == eid and not seen[role].exists]
    if missing:
        names = ", ".join(eid for _, eid in missing)
        return Check(False, f"{names} not found", None,
                     title=f"Home Assistant has no {names}",
                     why="Location and car reminders need the entities selected in your Home Assistant "
                         "addon settings. Pick the right ones there.",
                     blocks=[{"type": "facts", "label": "Not found",
                              "items": [f"{role}: {eid}" for role, eid in missing]}])
    checked = all(seen[role] is not None and seen[role].entity_id == eid for role, eid in spec.entities.items())
    return Check(True, "all found" if checked else "not checked yet")


@addon.selfcheck("ha_watcher", "Home Assistant connection")
def check_ha_watcher(user: User, now: datetime.datetime) -> Check:
    _, spec = _ha_watch(user)
    if spec is None:
        return Check(True, "nothing to watch")
    st = ha_status.read(user.id)
    since = helpers.parse_time(st.since)
    why = "Location and car reminders won't fire while Dana can't watch your Home Assistant."
    blocks = [{"type": "text", "label": "Error", "text": st.detail}] if st.detail else []
    if st.connected:
        return Check(True, "connected", since)
    if st.connected is None:
        if time.monotonic() - _STARTED < HA_WATCHER_MINUTES * 60:
            return Check(True, "starting")
        return Check(False, "never connected", None, title="Dana can't reach your Home Assistant",
                     why=why, blocks=blocks)
    down = (now - since).total_seconds() if since else float("inf")
    if down < HA_WATCHER_MINUTES * 60:
        return Check(True, "reconnecting", since)
    return Check(False, f"disconnected for {helpers.span(down)}", since,
                 title=f"Dana lost your Home Assistant {helpers.ago(since, now) if since else 'never'}",
                 why=why, blocks=blocks)
