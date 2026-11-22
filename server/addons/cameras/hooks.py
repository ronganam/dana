"""The cameras addon's dashboard widget (docs/dashboard.md): ``cameras``, one
camera of the user's Frigate live (WebRTC through ``/api/frigate/webrtc/{camera}``)
with its latest events at the large size. Drawn by ``web/widget.js``."""

from typing import Any, Dict

from core.addons import Field, WidgetContext, store
from core.users import User
from addons.cameras import client
from addons.cameras.addon import addon
from addons.cameras.config import ADDON

# How many recent events the large size shows beside the picture.
WIDGET_EVENTS = 3


@addon.widget("cameras", title="Camera", script="widget.js", icon="cctv", refresh=15,
              sizes=("large", "medium"),
              settings=(Field("camera", "The Frigate camera to show", default="", required=False, label="Camera",
                              help="Its name in Frigate (letters, digits, _ and -).", placeholder="front_door"),),
              description="A camera live, with its latest events at the large size")
async def camera_widget(user: User, ctx: WidgetContext) -> Dict[str, Any]:
    """``camera`` (its name, None until one is chosen), ``webrtc`` (the signaling
    path), ``events`` (newest first, the large size only; each as
    ``GET /api/frigate/events/{camera}`` gives it), ``server_time`` and ``error``
    (``frigate_unavailable`` / ``bad_camera``) when there is one."""
    conn = store.require(user, ADDON)
    camera = (ctx.settings.get("camera") or "").strip()
    if not camera:
        return {"camera": None, "webrtc": None, "events": [], "server_time": None}
    if not client.valid_camera(camera):
        return {"camera": None, "webrtc": None, "events": [], "server_time": None, "error": "bad_camera"}
    out: Dict[str, Any] = {"camera": camera, "webrtc": f"/api/frigate/webrtc/{camera}", "events": [],
                           "server_time": None}
    if ctx.size == "large":
        try:
            found = await client.events(conn, camera, WIDGET_EVENTS)
        except client.FrigateError:
            out["error"] = "frigate_unavailable"
        else:
            out["events"], out["server_time"] = found["events"], found["server_time"]
    return out
