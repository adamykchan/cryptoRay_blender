import bpy

from . import build, render

_cache = {"key": None, "stale": None}


def _stale(context):
    """Cheap-to-key staleness check: re-analysed only when objects/collections/settings/build change
    (visibility toggles are caught by 'Rebuild Before Render')."""
    s = context.scene.cryptoray
    vl = build.id_layer(context.scene)
    key = (context.scene.name, len(bpy.data.objects), len(bpy.data.collections),
           tuple(build.settings_signature(s)), vl.get(build.MANIFEST_PROP) if vl else None)
    if _cache["key"] != key:
        _cache["key"] = key
        try:
            _cache["stale"] = build.stale_reason(context, context.scene, context.scene.cryptoray)
        except Exception as e:  # never break the UI
            _cache["stale"] = str(e)
    return _cache["stale"]


class CRYPTORAY_PT_main(bpy.types.Panel):
    bl_label = "CryptoRay"
    bl_idname = "CRYPTORAY_PT_main"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "CryptoRay"

    def draw(self, context):
        scene = context.scene
        s = scene.cryptoray
        lay = self.layout
        lay.use_property_split = True
        lay.use_property_decorate = False

        if scene.render.engine != "CYCLES":
            lay.label(text="Needs Cycles (light groups)", icon="ERROR")

        box = lay.box()
        box.label(text="IDs", icon="NODE_MATERIAL" if s.mode == "MATERIAL" else "OBJECT_DATA")
        box.prop(s, "mode")
        box.prop(s, "packing")
        box.prop(s, "scope")
        if s.scope == "COLLECTION":
            box.prop(s, "target_collection")
        box.prop(s, "glass_collection")
        row = box.row(align=True)
        built = build.id_layer(scene) is not None
        row.operator("cryptoray.build", text="Rebuild" if built else "Build", icon="FILE_REFRESH")
        row.operator("cryptoray.clear", text="Clear", icon="TRASH")
        if build.root_objects(scene):
            box.label(text="Objects sit directly in the Scene Collection", icon="ERROR")
            box.operator("cryptoray.move_root_objects", icon="OUTLINER_COLLECTION")
        if built:
            man = build.manifest(scene) or {}
            groups = len({e["group"] for e in man.get("entries", [])})
            box.label(text="%d IDs, %d light groups" % (len(man.get("entries", [])), groups), icon="CHECKMARK")
            reason = _stale(context)
            if reason:
                box.label(text="Out of date: " + reason, icon="ERROR")
            if groups > s.group_warning:
                box.label(text="Many light groups: watch GPU memory", icon="ERROR")

        box = lay.box()
        box.label(text="Output", icon="OUTPUT")
        box.prop(s, "output_dir")
        box.prop(s, "prefix")
        box.prop(s, "layer_name")
        if not s.layer_name.strip():
            box.label(text="Layer: " + render.layer_name(s))
        box.prop(s, "ranks")
        box.prop(s, "normalization")
        box.prop(s, "matte_samples")
        box.prop(s, "keep_raw")
        box.prop(s, "strip_beauty")
        box.prop(s, "auto_rebuild")

        box = lay.box()
        box.label(text="Render", icon="RENDER_STILL")
        if not render.is_multilayer(scene):
            box.label(text="Beauty output isn't Multilayer EXR:", icon="INFO")
            box.label(text="use 'IDs Only', or switch the format")
        elif render.lossy_codec(scene):
            box.label(text="Lossy EXR codec: use ZIP/PIZ for clean mattes", icon="ERROR")
        col = box.column(align=True)
        row = col.row(align=True)
        op = row.operator("cryptoray.render", text="Still", icon="RENDER_STILL")
        op.mode, op.sequence = "TOGETHER", False
        op = row.operator("cryptoray.render", text="Animation", icon="RENDER_ANIMATION")
        op.mode, op.sequence = "TOGETHER", True
        row = col.row(align=True)
        op = row.operator("cryptoray.render", text="IDs Only Still", icon="RENDER_STILL")
        op.mode, op.sequence = "IDS_ONLY", False
        op = row.operator("cryptoray.render", text="IDs Only Anim", icon="RENDER_ANIMATION")
        op.mode, op.sequence = "IDS_ONLY", True
        msg = render.STATUS.get(scene.name)
        if msg:
            for i in range(0, min(len(msg), 180), 60):
                box.label(text=msg[i:i + 60])


def register():
    bpy.utils.register_class(CRYPTORAY_PT_main)


def unregister():
    bpy.utils.unregister_class(CRYPTORAY_PT_main)
