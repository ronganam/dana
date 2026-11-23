"""Home Assistant tools (the connection is in :mod:`.client`, the prompt's
home map in :mod:`.home_map`).

Tools exposed to the agent:
1. get_ha_exposed_entities  — the voice-exposed entities with names, aliases and live
                              state in one call (optionally only those matching a word).
2. call_ha_service          — controls a device by calling any HA service.
3. control_android_tv       — the Android TV over ADB (apps, channels, YouTube, Netflix, keys).

The agent's system prompt also carries a cached "home map" of the exposed
entities (ids, names, aliases, no state; see home_map.get_home_map), so most commands
go straight to call_ha_service.
"""

import asyncio
import logging
import re
import shlex
import urllib.parse
from typing import Any, Dict, List, Literal, Optional, Tuple, get_args

from core.addons import Approval, Connection
from core.addons.hooks import clean
from core.text import untrusted
from core.users import User
from addons.homeassistant import client as ha_api, home_map
from addons.homeassistant.addon import addon
from addons.homeassistant.config import TV, TV_CHANNEL, setting

logger = logging.getLogger(__name__)

MAX_LIST_CHARS = 10_000


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
            # Whoever published the media chose its title: framed, so a turn that read it asks
            # before acting outside Dana (core/agent.py); a listing with nothing playing does not.
            parts.append("playing: " + untrusted("a media player's title", f"{title}{(' – ' + artist) if artist else ''}"))

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

@addon.tool(shortcut_prep=True)
async def get_ha_exposed_entities(conn: Connection, search: Optional[str] = None) -> str:
    """Live state of the Home Assistant entities exposed to the voice assistant (state, key attributes, aliases), grouped by domain. Use it for a device's current state (is it on, the temperature, what is playing) or a device missing from the Home Devices list; never just to look up ids that list already has, and never guess ids.

    Args:
        search: Only entities whose id, name, alias or domain contains this, e.g. "light", "kitchen", "AC"; default all.
    """
    try:
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

    needle = (search or "").strip().lower()
    by_domain: Dict[str, List[str]] = {}
    for eid in sorted(exposed_ids):
        reg = registry_map.get(eid, {"name": eid, "aliases": []})
        state_obj = states_map.get(eid)
        if needle:
            friendly = str(((state_obj or {}).get("attributes") or {}).get("friendly_name") or "")
            if needle not in " ".join([eid, reg["name"], friendly, *reg["aliases"]]).lower():
                continue
        line = _build_entity_line(eid=eid, name=reg["name"], aliases=reg["aliases"], state_obj=state_obj)
        by_domain.setdefault(eid.split(".")[0], []).append(line)
    if not by_domain:
        return f"No exposed entity matches '{search}' ({len(exposed_ids)} exposed): search by another word or none."

    count = sum(len(v) for v in by_domain.values())
    lines = [f"Home Assistant — {count} of {len(exposed_ids)} exposed entities" + (f" matching '{search}'" if needle else "")]
    for domain in sorted(by_domain):
        lines.append(f"[{domain}]")
        lines.extend(f"  {line}" for line in by_domain[domain])
    text = "\n".join(lines)
    if len(text) > MAX_LIST_CHARS:
        text = text[:MAX_LIST_CHARS] + "\n[cut: more entities; pass search (a domain, room or name) to narrow]"
    return text


# ---------------------------------------------------------------------------
# Tool 2 — call_ha_service
# ---------------------------------------------------------------------------

def _entity_name(user: Optional[User], entity_id: str) -> str:
    for line in home_map.cached_text(user).splitlines():
        eid, _, rest = line.partition("|")
        if eid.strip() == entity_id and rest.strip():
            return rest.strip().split("  ")[0].strip()
    return entity_id


def _shortcut_ha_service(user: Optional[User], args: Dict[str, Any]) -> str:
    service = str(args.get("service") or "").replace("_", " ")
    what = _entity_name(user, str(args.get("entity_id") or ""))
    extra = args.get("service_data")
    text = f"{service.capitalize()} {what}".strip()
    if extra:
        text += " (" + ", ".join(f"{k} {v}" for k, v in dict(extra).items()) + ")"
    return text


def _describe_ha_service(user: Optional[User], a: Dict[str, Any]) -> Approval:
    what = f"{a.get('domain') or '?'}.{a.get('service') or '?'}"
    return Approval(f"Home: {what} on {a.get('entity_id') or 'a device'}", "Home", "🏠", "Go ahead?",
                    facts=[("Data", a.get("service_data"))])


@addon.tool(acts_outside=True, describe=_describe_ha_service, shortcut=True, shortcut_describe=_shortcut_ha_service)
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
    if not ha_api.valid_entity_id(entity_id):
        return (f"Error: '{entity_id}' is not an entity id (domain.object_id): take one from the Home Devices "
                "list or get_ha_exposed_entities.")
    payload: Dict[str, Any] = {**(service_data or {}), "entity_id": entity_id}
    logger.info(f"Calling HA service {domain}.{service} on {entity_id} — {service_data}")
    try:
        r = await ha_api.post(conn, ha_api.service_path(domain, service), json_body=payload, timeout=30.0)
    except Exception as e:
        logger.error(f"Error calling HA service {domain}.{service}: {e}", exc_info=True)
        return f"Error: Failed to call Home Assistant service: {str(e).strip() or type(e).__name__}"
    if r.status_code not in (200, 201):
        return f"Error: HA returned HTTP {r.status_code} for {domain}.{service} on '{entity_id}': {r.text[:300]}"
    extra = f" with {service_data}" if service_data else ""
    try:  # Home Assistant answers with the states the call changed
        now = next((c.get("state") for c in r.json() if c.get("entity_id") == entity_id), None)
    except (ValueError, TypeError, AttributeError):
        now = None
    return f"Done: called {domain}.{service} on '{entity_id}'{extra}." + (f" It is now {now}." if now else "")


# ---------------------------------------------------------------------------
# Tool 3 — control_android_tv
# ---------------------------------------------------------------------------

NOT_READY = ("off", "standby", "unavailable", "unknown")
TvAction = Literal["launch_app", "watch_channel", "play_youtube", "play_netflix", "send_key", "run_command"]
TV_ACTIONS = get_args(TvAction)
YOUTUBE_ACTIVITY = "com.google.android.youtube.tv/com.google.android.apps.youtube.tv.activity.ShellActivity"


def _tv_entities(entity_id: str) -> Tuple[str, str]:
    """(the ADB media_player, the base name) of a TV entity (``media_player.x`` or ``media_player.x_adb``)."""
    base = entity_id.split(".")[-1].removesuffix("_adb")
    return f"media_player.{base}_adb", base


async def _is_app_active(conn: Connection, app_id: str, entity_id: str) -> bool:
    """Whether ``app_id`` is the app in front on the TV (any of its media_player / remote entities says so)."""
    _, base = _tv_entities(entity_id)
    names = (f"media_player.{base}_adb", f"media_player.{base}", f"remote.{base}", f"remote.{base}_adb")
    want = app_id.strip().lower()
    for s in await asyncio.gather(*(ha_api.fetch_state(conn, n) for n in names)):
        attrs = (s or {}).get("attributes") or {}
        if any(str(attrs.get(k) or "").strip().lower() == want
               for k in ("app_id", "app_name", "source", "current_activity")):
            return True
    return False


async def _wake_tv(conn: Connection, entity_id: str, timeout: float = 30.0) -> None:
    """Turn the TV on when its ADB entity is off, then wait (at most ``timeout``) until ADB answers."""
    adb, base = _tv_entities(entity_id)
    state = await ha_api.fetch_state(conn, adb) or await ha_api.fetch_state(conn, entity_id)
    if state and state.get("state") not in NOT_READY:
        return
    main = f"media_player.{base}"
    await call_ha_service(conn, "media_player", "turn_on", main if await ha_api.fetch_state(conn, main) else entity_id)
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        await asyncio.sleep(1.5)
        state = await ha_api.fetch_state(conn, adb) or await ha_api.fetch_state(conn, entity_id)
        if state and state.get("state") not in NOT_READY:
            return
    logger.warning(f"[HA] the TV's ADB entity {adb} was not ready after {timeout:.0f}s")


def _tv_command(action: str, channel_command: Optional[str], channel: Optional[str], app_id: Optional[str],
                query: Optional[str], video_id: Optional[str], title_id: Optional[str],
                command: Optional[str]) -> Tuple[Optional[str], str]:
    """(the ADB command, what it does) for an action, or (None, an error) when an argument is missing or wrong."""
    if action == "watch_channel":
        if not channel_command:
            return None, ("Error: no channel command is set up (the Home Assistant addon's TV channel command "
                          "setting); use launch_app or send_key instead.")
        if not channel or not re.fullmatch(r"\d{1,5}", str(channel).strip()):
            return None, "Error: 'channel' must be a channel number for action 'watch_channel'."
        return channel_command.replace("{channel}", str(channel).strip()), f"tuned to channel {str(channel).strip()}"
    if action == "play_youtube":
        if video_id:
            match = re.search(r"(?:v=|/|^)([A-Za-z0-9_-]{11})(?:[?&#]|$)", video_id.strip())
            if not match:
                return None, "Error: 'video_id' must be an 11-character YouTube video id or a YouTube URL."
            return (f'am start -a android.intent.action.VIEW -d "https://www.youtube.com/watch?v={match.group(1)}" '
                    f"-n {YOUTUBE_ACTIVITY}"), f"started YouTube video {match.group(1)}"
        if query:
            return (f"am start -a android.intent.action.SEARCH -e query {shlex.quote(query)} -n {YOUTUBE_ACTIVITY}",
                    f"searched YouTube for '{query}'")
        return None, "Error: Either 'video_id' or 'query' is required for action 'play_youtube'."
    if action == "play_netflix":
        if title_id:
            tid = str(title_id).rstrip("/").split("/")[-1]
            if not tid.isdigit():
                return None, "Error: 'title_id' must be a numeric Netflix title id (or its URL)."
            return (f"am start -n com.netflix.ninja/.MainActivity -a android.intent.action.VIEW "
                    f"-d netflix://title/{tid} -f 0x10000020"), f"started Netflix title {tid}"
        if query:
            target = shlex.quote(f"netflix://search?q={urllib.parse.quote(query)}")
            return (f"am start -a android.intent.action.VIEW -d {target} -n com.netflix.ninja/.MainActivity",
                    f"searched Netflix for '{query}'")
        return None, "Error: Either 'title_id' or 'query' is required for action 'play_netflix'."
    if action in ("send_key", "run_command"):
        if not command:
            return None, f"Error: 'command' is required for action '{action}'."
        return command, f"sent key {command}" if action == "send_key" else "ran the command"
    if not app_id:
        return None, "Error: 'app_id' is required for action 'launch_app'."
    return "", ""


def _shortcut_tv_allowed(args: Dict[str, Any]) -> bool:
    return str(args.get("action") or "").lower() != "run_command"


def _shortcut_tv_describe(user: Optional[User], args: Dict[str, Any]) -> str:
    action = str(args.get("action") or "").replace("_", " ")
    detail = next((str(args[k]) for k in ("app_id", "channel", "query", "video_id", "title_id", "command")
                   if args.get(k)), "")
    return f"TV: {action} {detail}".strip()


def _describe_tv(user: Optional[User], a: Dict[str, Any]) -> Approval:
    detail = a.get("app_id") or a.get("channel") or a.get("query") or a.get("video_id") \
        or a.get("title_id") or a.get("command") or ""
    return Approval(f"TV: {str(a.get('action') or 'command').replace('_', ' ')}"
                    + (f" “{clean(detail, 80)}”" if detail else ""), "TV", "📺", "Go ahead?",
                    facts=[("Device", a.get("entity_id"))])


@addon.tool(acts_outside=True, describe=_describe_tv, shortcut=_shortcut_tv_allowed,
            shortcut_describe=_shortcut_tv_describe)
async def control_android_tv(
    conn: Connection,
    action: TvAction,
    entity_id: Optional[str] = None,
    app_id: Optional[str] = None,
    channel: Optional[str] = None,
    query: Optional[str] = None,
    video_id: Optional[str] = None,
    title_id: Optional[str] = None,
    command: Optional[str] = None,
) -> str:
    """Control the Android / Google TV over ADB: launch an app, tune a channel, play or search YouTube / Netflix, press a remote key, or run an ADB shell command and get its output. Turns the TV on first. For volume, play and pause use call_ha_service (media_player).

    Args:
        action: launch_app (app_id), watch_channel (channel; needs the user's tv_channel_command setting), play_youtube (video_id or query), play_netflix (title_id or query), send_key (command), run_command (command).
        entity_id: The TV's media_player entity; default the user's TV (tv_entity setting).
        app_id: Package to launch, e.g. 'com.netflix.ninja', 'com.google.android.youtube.tv'.
        channel: Channel number.
        query: What to search for on YouTube or Netflix.
        video_id: YouTube video id or URL.
        title_id: Netflix title id (numeric) or its URL.
        command: A key for send_key ('HOME', 'BACK', 'UP', 'ENTER', 'VOLUME_UP'), or an ADB shell command for run_command ('dumpsys battery').
    """
    action = (action or "").strip().lower()
    if action not in TV_ACTIONS:
        return f"Error: Unknown action '{action}'. Use one of: {', '.join(TV_ACTIONS)}."
    entity_id = entity_id or setting(conn, TV)
    if not entity_id:
        return ("Error: no TV given and none set up: pass entity_id (a media_player entity) or set "
                "the TV entity in the Home Assistant addon's settings.")
    adb_cmd, did = _tv_command(action, setting(conn, TV_CHANNEL), channel, app_id, query, video_id, title_id, command)
    if adb_cmd is None:
        return did
    try:
        await _wake_tv(conn, entity_id)
    except Exception as e:
        logger.warning(f"Error waking the TV: {e}", exc_info=True)

    if action == "launch_app":
        if await _is_app_active(conn, app_id, entity_id):
            return f"App '{app_id}' is already active on '{entity_id}'."
        res = await call_ha_service(conn, "media_player", "select_source", entity_id, {"source": app_id})
        if res.startswith("Error"):
            return res
        for _ in range(8):  # up to ~12 s for the app to come to the front
            await asyncio.sleep(1.5)
            if await _is_app_active(conn, app_id, entity_id):
                return f"Launched '{app_id}' on '{entity_id}' (it is in front now)."
        return f"Sent the launch of '{app_id}' to '{entity_id}' (not seen in front yet)."

    res = await call_ha_service(conn, "androidtv", "adb_command", entity_id, {"command": adb_cmd})
    if res.startswith("Error"):
        return res
    if action == "run_command":
        await asyncio.sleep(1.0)
        adb, _ = _tv_entities(entity_id)
        s = await ha_api.fetch_state(conn, adb) or await ha_api.fetch_state(conn, entity_id) or {}
        output = str((s.get("attributes") or {}).get("adb_response") or "")
        if not output:
            return "Command sent; it returned no output (yet)."
        return "Command output:\n" + (output if len(output) <= MAX_LIST_CHARS
                                       else output[:MAX_LIST_CHARS] + "\n[cut: narrow the command, e.g. with | head]")
    return f"Done on '{entity_id}': {did}."
