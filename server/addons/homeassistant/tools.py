"""Home Assistant tools (the connection is in :mod:`.client`, the prompt's
home map in :mod:`.home_map`).

Tools exposed to the agent:
1. get_ha_exposed_entities  — returns all voice-exposed entities with names, aliases
                              and live state in one call.
2. call_ha_service          — controls a device by calling any HA service.
3. get_ha_zones             — lists configured zones (for location-based task triggers).

The agent's system prompt also carries a cached "home map" of the exposed
entities (ids, names, aliases, no state; see home_map.get_home_map), so most commands
go straight to call_ha_service.
"""

import asyncio
import logging
import re
from typing import Any, Dict, List, Optional

from core.addons import Approval, Connection
from core.addons.hooks import clean
from core.users import User
from addons.homeassistant import client as ha_api
from addons.homeassistant.addon import addon
from addons.homeassistant.config import TV, TV_CHANNEL, setting

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Internal: format a single entity for AI consumption
# ---------------------------------------------------------------------------

def _fmt_attrs(domain: str, state: str, attrs: Dict[str, Any]) -> str:
    """Return a compact, AI-readable attribute string for a given domain."""
    parts: List[str] = []

    if domain == "climate":
        cur = attrs.get("current_temperature")
        tgt = attrs.get("temperature")
        unit = attrs.get("temperature_unit", "°C")
        mode = attrs.get("hvac_mode") or state
        if cur is not None:
            parts.append(f"current:{cur}{unit}")
        if tgt is not None:
            parts.append(f"target:{tgt}{unit}")
        if mode and mode != state:
            parts.append(f"mode:{mode}")

    elif domain == "light" and state == "on":
        brightness = attrs.get("brightness")
        if brightness is not None:
            parts.append(f"brightness:{round(brightness / 255 * 100)}%")
        ct = attrs.get("color_temp_kelvin") or attrs.get("color_temp")
        if ct is not None:
            parts.append(f"color_temp:{ct}")

    elif domain == "media_player":
        vol = attrs.get("volume_level")
        if vol is not None:
            parts.append(f"vol:{round(vol * 100)}%")
        title = attrs.get("media_title")
        artist = attrs.get("media_artist")
        if title:
            parts.append(f"playing:\"{title}{(' – ' + artist) if artist else ''}\"")

    elif domain == "sensor":
        unit = attrs.get("unit_of_measurement", "")
        # state already IS the value; append unit only
        return f"{state}{unit}"

    elif domain == "cover":
        pos = attrs.get("current_position")
        if pos is not None and state not in ("open", "closed"):
            parts.append(f"position:{pos}%")

    elif domain in ("button", "scene", "calendar"):
        # These entities' state is a raw ISO timestamp — not useful for AI context
        return ""

    return " ".join(parts)


def _build_entity_line(eid: str, name: str, aliases: List[str],
                       state_obj: Optional[Dict[str, Any]]) -> str:
    """Build a single pipe-separated entity line for the AI context."""
    domain = eid.split(".")[0]
    state = "unknown"
    attrs: Dict[str, Any] = {}

    if state_obj:
        state = state_obj.get("state", "unknown")
        attrs = state_obj.get("attributes", {})
        # Prefer HA's friendly_name if our registry name is just the entity_id fallback
        if name == eid:
            name = attrs.get("friendly_name", eid)

    attr_str = _fmt_attrs(domain, state, attrs)
    alias_str = f"  aliases: {', '.join(aliases)}" if aliases else ""

    # Pipe-separated: entity_id | name | state[unit or attrs] [| aliases: ...]
    # For sensor, attr_str IS the "state+unit" combined string; for others it's extra info.
    if domain == "sensor":
        display_state = attr_str if attr_str else state  # e.g. "89%"
        parts = [eid, name, display_state]
    elif domain in ("button", "scene", "calendar"):
        parts = [eid, name]  # no useful state to show
    else:
        parts = [eid, name, state]
        if attr_str:
            parts.append(attr_str)

    line = " | ".join(parts)
    if alias_str:
        line += alias_str
    return line


# ---------------------------------------------------------------------------
# Tool 1 — get_ha_exposed_entities
# ---------------------------------------------------------------------------

@addon.tool()
async def get_ha_exposed_entities(conn: Connection) -> str:
    """Retrieve all Home Assistant entities exposed to the voice assistant, with live state.

    Call this when you need a device's current state or attributes (is it on, the
    temperature, what is playing), or when the device the user means is not in the
    Home Devices list of the system prompt nor known from this conversation.
    Entity ids in the Home Devices list are already verified: do not call this just
    to look them up. Never guess entity IDs (like 'media_player.tv_salon').
    Returns entity_id, friendly name, live state, key attributes and aliases,
    grouped by domain. Use aliases to map natural-language names (e.g. 'the gate', 'AC').
    """
    try:
        # Fetch registry (WS) and all states (REST) in parallel
        (exposed_ids, registry_map), states_map = await asyncio.gather(
            ha_api.fetch_registry(conn),
            ha_api.fetch_all_states(conn),
        )
    except Exception as e:
        logger.error(f"Failed to fetch HA context: {e}", exc_info=True)
        err_msg = str(e).strip() or type(e).__name__
        return f"Error: Could not retrieve Home Assistant entities: {err_msg}"

    if not exposed_ids:
        return (
            "No entities are currently exposed to the voice assistant in Home Assistant. "
            "Go to Settings → Voice Assistants → Expose to enable devices."
        )

    # Group by domain
    by_domain: Dict[str, List[str]] = {}
    for eid in sorted(exposed_ids):
        domain = eid.split(".")[0]
        reg = registry_map.get(eid, {"name": eid, "aliases": []})
        line = _build_entity_line(
            eid=eid,
            name=reg["name"],
            aliases=reg["aliases"],
            state_obj=states_map.get(eid),
        )
        by_domain.setdefault(domain, []).append(line)

    lines = [f"Home Assistant — {len(exposed_ids)} exposed entities\n"]
    for domain in sorted(by_domain):
        lines.append(f"[{domain}]")
        lines.extend(f"  {line}" for line in by_domain[domain])

    logger.info(f"[HA] Returned {len(exposed_ids)} exposed entities across {len(by_domain)} domains.")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Tool 2 — call_ha_service
# ---------------------------------------------------------------------------

def _describe_ha_service(user: Optional[User], a: Dict[str, Any]) -> Approval:
    what = f"{a.get('domain') or '?'}.{a.get('service') or '?'}"
    return Approval(f"Home: {what} on {a.get('entity_id') or 'a device'}", "Home", "🏠", "Go ahead?",
                    facts=[("Data", a.get("service_data"))])


@addon.tool(acts_outside=True, describe=_describe_ha_service)
async def call_ha_service(
    conn: Connection,
    domain: str,
    service: str,
    entity_id: str,
    service_data: Optional[Dict[str, Any]] = None,
) -> str:
    """Call a Home Assistant service to control a device or trigger an automation.

    Use an entity_id from the Home Devices list in the system prompt, from this
    conversation, or from get_ha_exposed_entities. Never guess or make up entity_id values.

    Common domain/service combinations:
      - light        / turn_on (brightness 0-255, color_temp_kelvin), turn_off, toggle
      - switch       / turn_on, turn_off, toggle
      - climate      / set_temperature (temperature: float), set_hvac_mode (hvac_mode: str)
      - lock         / lock, unlock
      - cover        / open_cover, close_cover, stop_cover, set_cover_position (position: 0-100)
      - media_player / media_play, media_pause, media_stop, volume_set (volume_level: 0.0-1.0)
      - vacuum       / start, pause, stop, return_to_base
      - script       / turn_on
      - automation   / trigger, turn_on, turn_off
      - scene        / turn_on

    Args:
        domain: The HA service domain (e.g. 'light', 'switch', 'climate', 'cover').
        service: The service name (e.g. 'turn_on', 'set_temperature', 'open_cover').
        entity_id: The target entity ID (from the Home Devices list or get_ha_exposed_entities).
        service_data: Optional extra parameters (e.g. {'temperature': 22.5} or {'brightness': 200}).
    """
    payload: Dict[str, Any] = {"entity_id": entity_id}
    if service_data:
        payload.update(service_data)

    logger.info(f"Calling HA service {domain}.{service} on {entity_id} — {service_data}")
    try:
        r = await ha_api.post(conn, ha_api.service_path(domain, service), json_body=payload, timeout=30.0)
        if r.status_code not in (200, 201):
            return (
                f"Error: HA returned HTTP {r.status_code} for {domain}.{service} "
                f"on '{entity_id}': {r.text}"
            )
        extra = f" with {service_data}" if service_data else ""
        return f"Done: called {domain}.{service} on '{entity_id}'{extra}."
    except Exception as e:
        logger.error(f"Error calling HA service {domain}.{service}: {e}", exc_info=True)
        err_msg = str(e).strip() or type(e).__name__
        return f"Error: Failed to call Home Assistant service: {err_msg}"


# ---------------------------------------------------------------------------
# Tool 3 — get_ha_zones
# ---------------------------------------------------------------------------

@addon.tool()
async def get_ha_zones(conn: Connection) -> str:
    """Retrieve all active zones configured in Home Assistant.

    Home Assistant ONLY supports zone-based location triggers. The location
    value MUST match the friendly name of an existing HA zone exactly.

    Use this to resolve natural-language location names (e.g. 'work', 'parents home')
    to the exact zone friendly_name required by location-based task triggers.

    IMPORTANT: You MUST call this tool BEFORE calling add_task or update_task
    with a location parameter. If the user asks for a location that doesn't match
    any zone, show them the available zones and ask which one they mean.
    """
    try:
        zones = await ha_api.get_ha_zones_list(conn)
    except Exception as e:
        logger.error(f"Error fetching HA zones: {e}", exc_info=True)
        err_msg = str(e).strip() or type(e).__name__
        return f"Error: Failed to connect to Home Assistant: {err_msg}"

    if not zones:
        return "No zones are configured in Home Assistant."

    lines = ["Available Home Assistant zones:"]
    for z in zones:
        lines.append(f"  {z['entity_id']} | {z['name']}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Tool 4 — control_android_tv
# ---------------------------------------------------------------------------

async def _is_app_active(conn: Connection, app_id: str, entity_id: str) -> bool:
    """Check if the given app_id is currently active on the TV."""
    base_name = entity_id.split(".")[-1]
    if base_name.endswith("_adb"):
        base_name = base_name[:-4]

    try:
        states = await ha_api.fetch_all_states(conn)
    except Exception as e:
        logger.error(f"Failed to fetch states for app check: {e}")
        return False

    candidates = [
        f"media_player.{base_name}_adb",
        f"media_player.{base_name}",
        f"remote.{base_name}",
        f"remote.{base_name}_adb",
    ]

    for c in candidates:
        s = states.get(c)
        if not s:
            continue
        attrs = s.get("attributes", {})
        # Check various attributes that might contain the app ID
        for key in ("app_id", "app_name", "source", "current_activity"):
            val = attrs.get(key)
            if val and str(val).strip().lower() == app_id.strip().lower():
                return True
    return False


async def _ensure_tv_on(conn: Connection, entity_id: str) -> bool:
    """Ensure the TV is powered on. Returns True if we had to turn it on."""
    base_name = entity_id.split(".")[-1]
    if base_name.endswith("_adb"):
        base_name = base_name[:-4]
        
    adb_entity = f"media_player.{base_name}_adb"
    
    try:
        states = await ha_api.fetch_all_states(conn)
    except Exception as e:
        logger.error(f"Failed to fetch states for TV power check: {e}")
        return False

    s = states.get(adb_entity)
    adb_state = s.get("state", "off") if s else "off"
    
    if adb_state in ("off", "standby", "unavailable", "unknown"):
        logger.info(f"TV ADB entity '{adb_entity}' is '{adb_state}'. Turning TV on...")
        main_entity = f"media_player.{base_name}"
        target_to_turn_on = main_entity if main_entity in states else entity_id
        await call_ha_service(conn, "media_player", "turn_on", target_to_turn_on)
        return True
    return False


async def _wait_for_adb_ready(conn: Connection, entity_id: str, timeout: float = 30.0) -> bool:
    """Wait for the ADB media_player entity to be ready (not 'off', 'unavailable', 'unknown', or 'standby')."""
    logger.info(f"Waiting for ADB entity '{entity_id}' to become ready...")
    start_time = asyncio.get_event_loop().time()
    
    while True:
        try:
            states = await ha_api.fetch_all_states(conn)
        except Exception as e:
            logger.warning(f"Failed to fetch states while waiting for ADB: {e}")
            await asyncio.sleep(1.0)
            continue
            
        s = states.get(entity_id)
        if s:
            state = s.get("state", "off")
            if state not in ("off", "standby", "unavailable", "unknown"):
                logger.info(f"ADB entity '{entity_id}' is ready with state '{state}' after {asyncio.get_event_loop().time() - start_time:.1f}s.")
                return True
                
        elapsed = asyncio.get_event_loop().time() - start_time
        if elapsed >= timeout:
            logger.warning(f"Timed out waiting for ADB entity '{entity_id}' to become ready (elapsed: {elapsed:.1f}s).")
            return False
            
        await asyncio.sleep(1.5)


def _describe_tv(user: Optional[User], a: Dict[str, Any]) -> Approval:
    detail = a.get("app_id") or a.get("channel") or a.get("query") or a.get("video_id") \
        or a.get("title_id") or a.get("command") or ""
    return Approval(f"TV: {str(a.get('action') or 'command').replace('_', ' ')}"
                    + (f" “{clean(detail, 80)}”" if detail else ""), "TV", "📺", "Go ahead?",
                    facts=[("Device", a.get("entity_id"))])


@addon.tool(acts_outside=True, describe=_describe_tv)
async def control_android_tv(
    conn: Connection,
    action: str,
    entity_id: Optional[str] = None,
    app_id: Optional[str] = None,
    channel: Optional[str] = None,
    query: Optional[str] = None,
    video_id: Optional[str] = None,
    title_id: Optional[str] = None,
    command: Optional[str] = None,
) -> str:
    """Control an Android TV / Google TV device using the androidtv integration.

    Use this tool to launch apps, change TV channels, search/play YouTube/Netflix, send remote keys,
    or run arbitrary ADB shell commands and get the output back.

    It automatically detects if an app is already open to avoid redundant launch commands and delays.
    This tool is for ADB-based TV control only. For simple media_player operations (volume, pause, play),
    use call_ha_service with the media_player domain instead.

    Actions:
      - "launch_app": Launch any app using its package/app ID (e.g. 'com.netflix.ninja', 'com.google.android.youtube.tv').
      - "watch_channel": Tune to a numeric channel with the TV provider command the user set up
        (their Home Assistant setting tv_channel_command); without one, use launch_app or send_key.
      - "play_youtube": Play a specific video by ID/URL, or search for a video query on YouTube.
      - "play_netflix": Play a specific Netflix title by ID, or search for a title query on Netflix.
      - "send_key": Send a remote key press (e.g. 'HOME', 'BACK', 'UP', 'DOWN', 'ENTER', 'VOLUME_UP').
      - "run_command": Run an arbitrary ADB shell command on the TV and return its output (e.g. 'top -n 1 -s 9 -m 5', 'dumpsys battery').

    Args:
        action: The action to perform: "launch_app", "watch_channel", "play_youtube", "play_netflix", "send_key", "run_command".
        entity_id: The Android TV media_player entity ID (defaults to the user's TV, the tv_entity setting).
        app_id: The package/app ID to launch (required for "launch_app").
        channel: The channel number to tune to (required for "watch_channel").
        query: Search query for YouTube or Netflix.
        video_id: The YouTube video ID or full YouTube video URL.
        title_id: The Netflix title ID (numeric).
        command: The remote key command (e.g., 'HOME', 'BACK') for "send_key", or the ADB shell command for "run_command".
    """
    action = action.lower()
    entity_id = entity_id or setting(conn, TV)
    if not entity_id:
        return ("Error: no TV given and none set up: pass entity_id (a media_player entity) or set "
                "the TV entity in the Home Assistant addon's settings.")
    channel_command = setting(conn, TV_CHANNEL)
    if action == "watch_channel":
        if not channel_command:
            return ("Error: no channel command is set up (the Home Assistant addon's TV channel command "
                    "setting); use launch_app or send_key instead.")
        if not channel or not re.fullmatch(r"\d{1,5}", str(channel).strip()):
            return "Error: 'channel' must be a channel number for action 'watch_channel'."

    # Ensure TV is powered on first
    try:
        await _ensure_tv_on(conn, entity_id)
    except Exception as e:
        logger.warning(f"Error ensuring TV is on: {e}")

    # Wait for ADB to be ready before executing any command
    base_name = entity_id.split(".")[-1]
    if base_name.endswith("_adb"):
        base_name = base_name[:-4]
    adb_entity = f"media_player.{base_name}_adb"
    await _wait_for_adb_ready(conn, adb_entity)

    if action == "launch_app":
        if not app_id:
            return "Error: 'app_id' is required for action 'launch_app'."
        
        is_active = await _is_app_active(conn, app_id, entity_id)
        if is_active:
            return f"App '{app_id}' is already active on '{entity_id}'."
        
        logger.info(f"Launching app '{app_id}' on '{entity_id}'...")
        res = await call_ha_service(conn, "media_player", "select_source", entity_id, {"source": app_id})
        if "Error" in res:
            return res
        
        # Sleep briefly to let the app start launching
        await asyncio.sleep(2.0)
        
        # Poll for the app to become active (up to 15 seconds)
        polled_active = False
        for _ in range(10):
            if await _is_app_active(conn, app_id, entity_id):
                polled_active = True
                break
            await asyncio.sleep(1.0)
            
        await asyncio.sleep(1.5)
        status_msg = " (detected active)" if polled_active else " (sent launch command, state unverified)"
        return f"Successfully launched app '{app_id}' on '{entity_id}'{status_msg}."

    elif action == "watch_channel" and channel and channel_command:
        adb_cmd = channel_command.replace("{channel}", str(channel).strip())
        logger.info(f"Tuning '{entity_id}' to channel {channel}: {adb_cmd}")
        res = await call_ha_service(conn, "androidtv", "adb_command", entity_id, {"command": adb_cmd})
        if "Error" in res:
            return res
        return f"Successfully tuned to channel {channel} on '{entity_id}'."

    elif action == "play_youtube":
        if not video_id and not query:
            return "Error: Either 'video_id' or 'query' is required for action 'play_youtube'."
        
        if video_id:
            # Extract video ID if it's a URL
            if "youtube.com" in video_id or "youtu.be" in video_id:
                match = re.search(r"(?:v=|\/)([a-zA-Z0-9_-]{11})", video_id)
                if match:
                    video_id = match.group(1)
            
            adb_cmd = (
                f'am start -a android.intent.action.VIEW -d "https://www.youtube.com/watch?v={video_id}" '
                f'-n com.google.android.youtube.tv/com.google.android.apps.youtube.tv.activity.ShellActivity'
            )
            detail = f"video ID '{video_id}'"
        else:
            adb_cmd = (
                f"am start -a android.intent.action.SEARCH -e query '{query}' "
                f"-n com.google.android.youtube.tv/com.google.android.apps.youtube.tv.activity.ShellActivity"
            )
            detail = f"query '{query}'"

        logger.info(f"Playing YouTube on '{entity_id}' with command: {adb_cmd}")
        res = await call_ha_service(conn, "androidtv", "adb_command", entity_id, {"command": adb_cmd})
        if "Error" in res:
            return res
        return f"Successfully started YouTube playback/search for {detail} on '{entity_id}'."

    elif action == "play_netflix":
        if not title_id and not query:
            return "Error: Either 'title_id' or 'query' is required for action 'play_netflix'."
        
        if title_id:
            if "/" in str(title_id):
                title_id = str(title_id).rstrip("/").split("/")[-1]
            
            adb_cmd = (
                f"am start -n com.netflix.ninja/.MainActivity -a android.intent.action.VIEW "
                f"-d netflix://title/{title_id} -f 0x10000020"
            )
            detail = f"title ID '{title_id}'"
        else:
            adb_cmd = (
                f"am start -a android.intent.action.VIEW -d 'netflix://search?q={query}' "
                f"-n com.netflix.ninja/.MainActivity"
            )
            detail = f"query '{query}'"

        logger.info(f"Playing Netflix on '{entity_id}' with command: {adb_cmd}")
        res = await call_ha_service(conn, "androidtv", "adb_command", entity_id, {"command": adb_cmd})
        if "Error" in res:
            return res
        return f"Successfully started Netflix playback/search for {detail} on '{entity_id}'."

    elif action == "send_key":
        if not command:
            return "Error: 'command' is required for action 'send_key'."
        
        res = await call_ha_service(conn, "androidtv", "adb_command", entity_id, {"command": command})
        if "Error" in res:
            return res
        return f"Successfully sent key '{command}' to '{entity_id}'."

    elif action == "run_command":
        if not command:
            return "Error: 'command' is required for action 'run_command'."

        logger.info(f"Running ADB shell command on '{entity_id}': {command}")
        res = await call_ha_service(conn, "androidtv", "adb_command", entity_id, {"command": command})
        if "Error" in res:
            return res

        # Fetch entity state to get adb_response attribute
        await asyncio.sleep(1.0)
        try:
            states = await ha_api.fetch_all_states(conn)
            s = states.get(adb_entity) or states.get(entity_id)
            if s:
                adb_output = (s.get("attributes") or {}).get("adb_response", "")
                if adb_output:
                    return f"Command executed. Output:\n{adb_output}"
        except Exception:
            pass
        return f"Command sent. Check the TV's adb_response attribute for output."

    else:
        return f"Error: Unknown action '{action}'."
