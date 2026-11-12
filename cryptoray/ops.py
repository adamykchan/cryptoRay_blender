import os
import tempfile

import bpy
from bpy.props import BoolProperty, EnumProperty

from . import build, render


def _safe_window_layer(context):
    """Make sure the window isn't showing the ID layer (it gets rebuilt/removed)."""
    win = context.window
    if win is not None and build.is_ours(win.view_layer):
        win.view_layer = build.beauty_layers(context.scene)[0]


def _do_build(op, context):
    _safe_window_layer(context)
    scene = context.scene
    try:
        res = build.build(context, scene, scene.cryptoray)
    except build.BuildError as e:
        op.report({"ERROR"}, str(e))
        return False
    for w in res["warnings"]:
        op.report({"WARNING"}, w)
    render.STATUS[scene.name] = "Built: %d IDs in %d light groups, %d glass, %d other" % (
        res["ids"], res["groups"], res["glass"], res["others"])
    return True


class CRYPTORAY_OT_build(bpy.types.Operator):
    bl_idname = "cryptoray.build"
    bl_label = "Build ID Layer"
    bl_description = "Create (or recreate) the ID_MATTES view layer with emission proxies. Nothing existing is edited"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        if bpy.app.is_job_running("RENDER"):
            return {"CANCELLED"}
        return {"FINISHED"} if _do_build(self, context) else {"CANCELLED"}


class CRYPTORAY_OT_clear(bpy.types.Operator):
    bl_idname = "cryptoray.clear"
    bl_label = "Clear ID Layer"
    bl_description = "Remove the ID_MATTES view layer, proxies and helper data. Rendered files are kept"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        _safe_window_layer(context)
        n = build.clear(context.scene)
        render.STATUS[context.scene.name] = "Cleared (%d items removed)." % n
        return {"FINISHED"}


class CRYPTORAY_OT_move_root(bpy.types.Operator):
    bl_idname = "cryptoray.move_root_objects"
    bl_label = "Move Root Objects"
    bl_description = ("Move objects that sit directly in the Scene Collection into a new collection "
                      "'Scene Root Objects' (render result unchanged), so the ID layer can exclude them")
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        n = build.move_root_objects(context.scene)
        self.report({"INFO"}, "Moved %d object(s) into 'Scene Root Objects'." % n)
        return {"FINISHED"}


class CRYPTORAY_OT_render(bpy.types.Operator):
    bl_idname = "cryptoray.render"
    bl_label = "Render"
    bl_description = "Render with Cryptomatte output"
    sequence: BoolProperty(name="Animation", default=False, options={"SKIP_SAVE"})
    mode: EnumProperty(items=[("TOGETHER", "Beauty + IDs", "Render beauty and ID layers in one job"),
                              ("IDS_ONLY", "IDs Only", "Render only the ID layer")],
                       default="TOGETHER", options={"SKIP_SAVE"})

    @classmethod
    def description(cls, context, props):
        what = "animation" if props.sequence else "current frame"
        if props.mode == "IDS_ONLY":
            return "Render only the ID layer (%s) and write Cryptomatte EXRs. Beauty is not rendered" % what
        return ("Render beauty + ID layers (%s) in one job; Cryptomatte EXRs are written next to it. "
                "Needs Multilayer EXR output" % what)

    @classmethod
    def poll(cls, context):
        return context.scene is not None and not bpy.app.is_job_running("RENDER") and render._JOB is None

    def execute(self, context):
        scene = context.scene
        s = scene.cryptoray
        if scene.render.engine != "CYCLES":
            self.report({"ERROR"}, "Light groups need Cycles: set the render engine to Cycles.")
            return {"CANCELLED"}
        if s.auto_rebuild or build.id_layer(scene) is None:
            if not _do_build(self, context):
                return {"CANCELLED"}
        else:
            reason = build.stale_reason(context, scene, s)
            if reason:
                self.report({"WARNING"}, "ID layer is out of date (%s). Rebuild to include changes." % reason)
        _safe_window_layer(context)
        vl = build.id_layer(scene)
        vl.use = True
        frame = scene.frame_current
        beauty_dir = os.path.dirname(bpy.path.abspath(scene.render.frame_path(frame=frame)))
        restore = None
        if self.mode == "TOGETHER":
            if not render.is_multilayer(scene):
                self.report({"ERROR"}, "Beauty output must be Multilayer EXR to carry the ID passes. "
                                       "Switch the output format, or use 'IDs Only'.")
                return {"CANCELLED"}
            if not any(v.use for v in build.beauty_layers(scene)):
                self.report({"ERROR"}, "No beauty view layer is enabled for rendering.")
                return {"CANCELLED"}
            if render.lossy_codec(scene):
                self.report({"WARNING"}, "Lossy EXR codec: matte edges may degrade. ZIP or PIZ is recommended.")
        else:
            uses = [(v.name, v.use) for v in build.beauty_layers(scene)]
            path = scene.render.filepath
            ext = scene.render.use_file_extension
            im = render.snapshot_image_settings(scene.render.image_settings)
            scene_name = scene.name

            def restore():
                sc = bpy.data.scenes.get(scene_name)
                if sc is None:
                    return
                for n, u in uses:
                    if n in sc.view_layers:
                        sc.view_layers[n].use = u
                sc.render.filepath = path
                sc.render.use_file_extension = ext
                render.restore_image_settings(sc.render.image_settings, im)

            for v in build.beauty_layers(scene):
                v.use = False
            tmp = os.path.join(tempfile.gettempdir(), "cryptoray_%d" % os.getpid())
            os.makedirs(tmp, exist_ok=True)
            scene.render.filepath = os.path.join(tmp, bpy.path.clean_name(scene.name) + "_ids_####")
            scene.render.use_file_extension = True
            render.set_multilayer(scene.render.image_settings)
        try:
            job = render.new_job(scene, ids_only=self.mode == "IDS_ONLY", out_dir=beauty_dir, restore=restore)
        except Exception as e:
            if restore:
                restore()
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}
        render.begin(job)
        how = "EXEC_DEFAULT" if bpy.app.background else "INVOKE_DEFAULT"
        if self.sequence:
            res = bpy.ops.render.render(how, animation=True)
        else:
            res = bpy.ops.render.render(how, write_still=True)
        if "CANCELLED" in res:
            render._JOB = None
            if restore:
                restore()
            self.report({"ERROR"}, "Render did not start.")
            return {"CANCELLED"}
        return {"FINISHED"}


classes = (CRYPTORAY_OT_build, CRYPTORAY_OT_clear, CRYPTORAY_OT_move_root, CRYPTORAY_OT_render)


def register():
    for c in classes:
        bpy.utils.register_class(c)


def unregister():
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
