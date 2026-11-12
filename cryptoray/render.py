"""Render handlers: after each written frame, turn the ID_MATTES light-group passes into Cryptomatte
layers, embedded in the beauty EXR or written next to it as <render name>_crypto_<frame>.exr. The ID
passes are always removed from the beauty file.

Handlers only ever read scene data. Anything that changes the scene (restoring settings after an
"IDs only" render, deleting the ID layer afterwards) happens in a main-thread timer, never in the
render thread."""

import json
import os
import re
import time
import traceback

import bpy
from bpy.app.handlers import persistent

from . import build, core

_JOB = None              # current render job (dict) or None
STATUS = {}              # scene name -> one-line status for the panel
_PENDING_RESTORE = []    # restore callables to run on the main thread


def is_multilayer(scene):
    im = scene.render.image_settings
    if hasattr(im, "media_type"):
        return im.media_type == "MULTI_LAYER_IMAGE"
    return im.file_format == "OPEN_EXR_MULTILAYER"


def set_multilayer(im):
    if hasattr(im, "media_type"):
        im.media_type = "MULTI_LAYER_IMAGE"
    else:
        im.file_format = "OPEN_EXR_MULTILAYER"
    im.color_depth = "32"
    im.exr_codec = "ZIP"


def snapshot_image_settings(im):
    keys = [k for k in ("media_type", "file_format", "color_depth", "exr_codec") if hasattr(im, k)]
    return [(k, getattr(im, k)) for k in keys]


def restore_image_settings(im, snap):
    for k, v in snap:
        try:
            setattr(im, k, v)
        except (TypeError, AttributeError):
            pass


def lossy_codec(scene):
    return is_multilayer(scene) and scene.render.image_settings.exr_codec in core.LOSSY_EXR_CODECS


def output_naming(scene):
    """(folder, stem, frame padding) from the render output path: //render/shot_#### ->
    (<abs>/render, "shot", 4). Cryptomatte files are named <stem>_crypto_<frame>.exr."""
    path = bpy.path.abspath(scene.render.filepath)
    if path.startswith("//"):
        raise RuntimeError("Save the .blend file first (the render output path is relative).")
    folder, base = os.path.split(path)
    if base.lower().endswith(".exr"):
        base = base[:-4]
    runs = re.findall(r"#+", base)
    pad = len(runs[-1]) if runs else 4
    if runs:
        i = base.rfind(runs[-1])
        base = base[:i] + base[i + len(runs[-1]):]
    return folder, base.strip(" _.-"), pad


def side_file(naming, frame, kind="crypto"):
    folder, stem, pad = naming
    name = "%s_%0*d.exr" % (kind, pad, frame)
    return os.path.join(folder, "%s_%s" % (stem, name) if stem else name)


def manifest_file(naming):
    folder, stem, _ = naming
    return os.path.join(folder, "%s_crypto_manifest.json" % stem if stem else "crypto_manifest.json")


def written_file(scene, since):
    """The file Blender just wrote for the current frame (stills keep a literal #### in the name)."""
    r = scene.render
    cands = [r.frame_path(frame=scene.frame_current)]
    p = bpy.path.abspath(r.filepath)
    if r.use_file_extension and not p.lower().endswith(".exr"):
        p += ".exr"
    cands.append(p)
    found = [c for c in cands if os.path.isfile(c) and os.path.getmtime(c) >= since - 1]
    return max(found, key=os.path.getmtime) if found else None


def _set_status(scene_name, text):
    STATUS[scene_name] = text
    print("[CryptoRay] %s: %s" % (scene_name, text))


def new_job(scene, ids_only=False, naming=None, restore=None):
    s = scene.cryptoray
    man = build.manifest(scene)
    if man is None:
        return None
    return {
        "scene": scene.name, "ids_only": ids_only, "manifest": man, "t0": time.time(),
        "naming": naming or output_naming(scene), "ranks": s.ranks, "normalization": "SUM" if s.ignore_absorption else "RAW",
        "embed": s.embed and not ids_only, "keep_raw": s.keep_raw,
        "frames": [], "errors": [], "last": None, "restore": restore, "manifest_written": False,
    }


def begin(job):
    global _JOB
    _JOB = job


# --------------------------------------------------------------------------
# handlers
# --------------------------------------------------------------------------

@persistent
def on_render_init(scene, *_):
    global _JOB
    if _JOB is not None and _JOB["scene"] == scene.name:
        return  # started by our operator
    vl = build.id_layer(scene)
    if vl is None or not vl.use:
        return
    if not is_multilayer(scene):
        _set_status(scene.name, "ID passes are not saved: output format is not Multilayer EXR "
                                "(use 'IDs Only' or switch the format).")
        return
    try:
        _JOB = new_job(scene)
    except Exception as e:
        _set_status(scene.name, "CryptoRay disabled for this render: %s" % e)
        _JOB = None


@persistent
def on_render_write(scene, *_):
    job = _JOB
    if job is None or job["scene"] != scene.name:
        return
    frame = scene.frame_current
    try:
        src = written_file(scene, job["t0"])
        if src is None:
            raise RuntimeError("could not find the written frame file")
        naming = job["naming"]
        crypto = None if job["embed"] else side_file(naming, frame)
        raw = side_file(naming, frame, "crypto_raw") if job["keep_raw"] else None
        res = core.convert(src, job["manifest"], ranks=job["ranks"], normalization=job["normalization"],
                           crypto_path=crypto, embed=job["embed"], raw_path=raw, delete_source=job["ids_only"])
        if raw and not job["manifest_written"]:
            with open(manifest_file(naming), "w", encoding="utf-8") as f:
                json.dump(job["manifest"], f, indent=2)
            job["manifest_written"] = True
        job["frames"].append(frame)
        job["last"] = res["crypto"]
        _set_status(scene.name, "Frame %d -> %s%s (%d IDs, %.2fs)" % (
            frame, os.path.basename(res["crypto"]), " (embedded)" if res["embedded"] else "",
            res["ids"], res["seconds"]))
    except Exception as e:
        traceback.print_exc()
        job["errors"].append("frame %d: %s" % (frame, e))
        _set_status(scene.name, "Frame %d FAILED: %s" % (frame, e))


def _finish(scene, cancelled):
    global _JOB
    job = _JOB
    if job is None or job["scene"] != scene.name:
        return
    _JOB = None
    n = len(job["frames"])
    if job["errors"]:
        msg = "%d frame(s) written, %d failed: %s" % (n, len(job["errors"]), job["errors"][0])
    elif cancelled:
        msg = "Render cancelled; %d Cryptomatte frame(s) written." % n
    elif n == 0:
        msg = "Nothing written: a plain F12 isn't saved. Use the CryptoRay Still button."
    elif job["embed"]:
        msg = "Done: Cryptomatte embedded in %d beauty frame(s) in %s" % (n, job["naming"][0])
    else:
        msg = "Done: %d Cryptomatte frame(s) in %s" % (n, job["naming"][0])
    _set_status(scene.name, msg)
    if job["restore"] is not None:
        _PENDING_RESTORE.append(job["restore"])
        # background: the operator runs it once the (blocking) render call returns
        if not bpy.app.background and not bpy.app.timers.is_registered(_run_restore):
            bpy.app.timers.register(_run_restore, first_interval=0.1)
    if not bpy.app.background and not bpy.app.timers.is_registered(_redraw):
        bpy.app.timers.register(_redraw, first_interval=0.1)


def _run_restore():
    if not bpy.app.background and bpy.app.is_job_running("RENDER"):
        return 0.2
    while _PENDING_RESTORE:
        fn = _PENDING_RESTORE.pop(0)
        try:
            fn()
        except Exception:
            traceback.print_exc()
    return None


def _redraw():
    for w in bpy.context.window_manager.windows:
        for a in w.screen.areas:
            if a.type in ("VIEW_3D", "PROPERTIES", "OUTLINER"):
                a.tag_redraw()
    return None


def leave_id_layer(scene):
    """Point every window that shows the scene's ID layer back at a beauty layer."""
    wm = bpy.context.window_manager
    for w in (wm.windows if wm else []):
        if w.scene == scene and build.is_ours(w.view_layer):
            w.view_layer = build.beauty_layers(scene)[0]


def clear_scene(scene):
    leave_id_layer(scene)
    return build.clear(scene)


@persistent
def on_render_complete(scene, *_):
    _finish(scene, False)


@persistent
def on_render_cancel(scene, *_):
    _finish(scene, True)


@persistent
def on_load_pre(*_):
    global _JOB
    _JOB = None
    _run_restore()


HANDLERS = (("render_init", on_render_init), ("render_write", on_render_write),
            ("render_complete", on_render_complete), ("render_cancel", on_render_cancel),
            ("load_pre", on_load_pre))


def register():
    for name, fn in HANDLERS:
        lst = getattr(bpy.app.handlers, name)
        if fn not in lst:
            lst.append(fn)


def unregister():
    for name, fn in HANDLERS:
        lst = getattr(bpy.app.handlers, name)
        if fn in lst:
            lst.remove(fn)
