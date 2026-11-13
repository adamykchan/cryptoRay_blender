"""Check GitHub for a newer CryptoRay release and point the user to it.

Check-and-link only: nothing is downloaded or installed. Installs from extensions.blender.org are
updated by Blender itself; everyone else gets a "Download" button that opens the release page.
The check runs only when the user clicks the refresh button (never on its own), respects Blender's
"Allow Online Access" preference and never runs in background mode.
"""
import json
import os
import re
import threading
import urllib.request

import bpy
from bpy.app.translations import pgettext_iface as iface_, pgettext_tip as tip_

REPO = "adamykchan/cryptoRay_blender"
LATEST_API = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASES_URL = f"https://github.com/{REPO}/releases"

# status: idle | checking | current | available | error
_state = {"status": "idle", "latest": None, "url": RELEASES_URL, "error": None}


def _parse(text):
    """'v0.5.0' / '0.5' -> (0, 5, 0); None if there is no leading number."""
    m = re.match(r"\s*v?(\d+(?:\.\d+)*)", text or "")
    if not m:
        return None
    parts = [int(p) for p in m.group(1).split(".")]
    return tuple((parts + [0, 0, 0])[:3])


def _local_version():
    """Version from blender_manifest.toml (the extension's source of truth), else bl_info."""
    try:
        path = os.path.join(os.path.dirname(__file__), "blender_manifest.toml")
        with open(path, encoding="utf-8") as f:
            m = re.search(r'^version\s*=\s*"([^"]+)"', f.read(), re.MULTILINE)
        if m and _parse(m.group(1)):
            return _parse(m.group(1))
    except OSError:
        pass
    from . import bl_info
    return tuple((list(bl_info["version"]) + [0, 0, 0])[:3])


def _label(version):
    return ".".join(str(p) for p in version)


def online_allowed():
    return getattr(bpy.app, "online_access", True) and not bpy.app.background


def _check():
    """Runs on a worker thread: ask GitHub for the latest release and compare."""
    try:
        req = urllib.request.Request(LATEST_API, headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "CryptoRay-Blender/" + _label(_local_version()),
        })
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.load(resp)
        latest = _parse(data.get("tag_name"))
        if latest is None:
            raise RuntimeError("latest release has no version tag")
        _state.update(latest=latest, url=data.get("html_url") or RELEASES_URL, error=None,
                      status="available" if latest > _local_version() else "current")
    except Exception as e:  # offline, rate-limited, no releases yet...
        print("CryptoRay: update check failed:", e)
        _state.update(status="error", error=str(e))


def _redraw_when_done():
    if _state["status"] == "checking":
        return 0.5
    wm = getattr(bpy.context, "window_manager", None)
    for window in (wm.windows if wm else ()):
        for area in window.screen.areas:
            if area.type == "VIEW_3D":
                area.tag_redraw()
    return None


def start_check():
    if _state["status"] == "checking" or not online_allowed():
        return
    _state["status"] = "checking"
    threading.Thread(target=_check, name="cryptoray-update-check", daemon=True).start()
    bpy.app.timers.register(_redraw_when_done, first_interval=0.5)


def draw(layout):
    here = _label(_local_version())
    status = _state["status"]
    if status == "available":
        box = layout.box()
        box.label(text=iface_("Update available: %s") % _label(_state["latest"]), translate=False, icon="IMPORT")
        box.operator("wm.url_open", text="Download", icon="URL").url = _state["url"]
        return
    if not online_allowed():
        text = iface_("CryptoRay %s · online access is off") % here
    else:
        text = iface_({
            "checking": "CryptoRay %s · checking for updates…",
            "current": "CryptoRay %s · up to date",
            "error": "CryptoRay %s · couldn't check for updates",
        }.get(status, "CryptoRay %s")) % here
    row = layout.row(align=True)
    row.label(text=text, translate=False)
    row.operator("cryptoray.check_update", text="", icon="FILE_REFRESH")


class CRYPTORAY_OT_check_update(bpy.types.Operator):
    bl_idname = "cryptoray.check_update"
    bl_label = "Check for Updates"
    bl_description = "Check GitHub for a newer CryptoRay release"

    @classmethod
    def poll(cls, context):
        if online_allowed():
            return True
        if not bpy.app.background:  # shown as "Disabled: ..." in the button's tooltip
            cls.poll_message_set(tip_("Turn on Preferences > System > Network > Allow Online Access"))
        return False

    def execute(self, context):
        start_check()
        return {"FINISHED"}


def register():
    bpy.utils.register_class(CRYPTORAY_OT_check_update)


def unregister():
    if bpy.app.timers.is_registered(_redraw_when_done):
        bpy.app.timers.unregister(_redraw_when_done)
    bpy.utils.unregister_class(CRYPTORAY_OT_check_update)
