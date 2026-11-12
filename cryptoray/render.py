"""Render handlers: after each written frame, turn the ID_MATTES light-group passes into a
Cryptomatte EXR (and optionally strip them from the beauty file / keep them as a raw file).

Handlers only ever read scene data. Anything that changes the scene (restoring settings after an
"IDs only" render) happens in a main-thread timer, never in the render thread."""

import json
import os
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


def layer_name(settings):
    if settings.layer_name.strip():
        return settings.layer_name.strip()
    return "CryptoRayMaterial" if settings.mode == "MATERIAL" else "CryptoRayObject"


def resolve_dir(path):
    p = bpy.path.abspath(path)
    if p.startswith("//"):
        raise RuntimeError("Save the .blend file or use an absolute CryptoRay output folder.")
    return p


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


def new_job(scene, ids_only=False, out_dir=None, restore=None):
    s = scene.cryptoray
    man = build.manifest(scene)
    if man is None:
        return None
    if out_dir is None:
        out_dir = os.path.dirname(bpy.path.abspath(scene.render.frame_path(frame=scene.frame_current)))
    return {
        "scene": scene.name, "ids_only": ids_only, "manifest": man, "t0": time.time(),
        "out_dir": resolve_dir(s.output_dir) if s.output_dir.strip() else out_dir,
        "prefix": (s.prefix.strip() or "crypto"), "ranks": s.ranks, "layer": layer_name(s),
        "normalization": s.normalization, "keep_raw": s.keep_raw, "strip": s.strip_beauty,
        "frames": [], "errors": [], "restore": restore, "manifest_written": False,
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
        name = "%s_%04d.exr" % (job["prefix"], frame)
        crypto = os.path.join(job["out_dir"], name)
        raw = os.path.join(job["out_dir"], "%s_raw_%04d.exr" % (job["prefix"], frame)) if job["keep_raw"] else None
        res = core.convert(src, job["manifest"], crypto, ranks=job["ranks"], layer_name=job["layer"],
                           normalization=job["normalization"], raw_path=raw,
                           strip_source=job["strip"] and not job["ids_only"],
                           delete_source=job["ids_only"])
        if raw and not job["manifest_written"]:
            with open(os.path.join(job["out_dir"], "%s_manifest.json" % job["prefix"]), "w", encoding="utf-8") as f:
                json.dump(job["manifest"], f, indent=2)
            job["manifest_written"] = True
        job["frames"].append(frame)
        _set_status(scene.name, "Frame %d -> %s (%d IDs, %.2fs)" % (frame, name, res["ids"], res["seconds"]))
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
        msg = "Nothing written: a plain F12 still isn't saved. Use CryptoRay 'Render Still'."
    else:
        msg = "Done: %d Cryptomatte frame(s) in %s" % (n, job["out_dir"])
    _set_status(scene.name, msg)
    if job["restore"] is not None:
        _PENDING_RESTORE.append(job["restore"])
        if bpy.app.background:
            _run_restore()
        elif not bpy.app.timers.is_registered(_run_restore):
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
            if a.type in ("VIEW_3D", "PROPERTIES"):
                a.tag_redraw()
    return None


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
