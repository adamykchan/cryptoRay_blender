import bpy

from . import build, core, render

_cache = {"key": None, "stale": None}
_glass_cache = {"key": None, "list": []}


def _stale(context):
    """Cheap-to-key staleness check: re-analysed only when objects/collections/settings/build change."""
    s = context.scene.cryptoray
    vl = build.id_layer(context.scene)
    key = (context.scene.name, len(bpy.data.objects), len(bpy.data.collections),
           repr(build.settings_signature(s)), vl.get(build.MANIFEST_PROP) if vl else None,
           tuple(m.name for m in bpy.data.materials if getattr(m, "cryptoray_not_glass", False)))
    if _cache["key"] != key:
        _cache["key"] = key
        try:
            _cache["stale"] = build.stale_reason(context, context.scene, s)
        except Exception as e:  # never break the UI
            _cache["stale"] = str(e)
    return _cache["stale"]


def _scene_materials(scene):
    """Materials used by objects in the scene (slots only; cheap enough for the panel)."""
    key = (scene.as_pointer(), len(bpy.data.materials), len(bpy.data.objects))
    if _glass_cache["key"] != key:
        names = set()
        for o in scene.objects:
            for slot in o.material_slots:
                if slot.material is not None and not build.is_ours(slot.material):
                    names.add(slot.material.name)
        _glass_cache["key"] = key
        _glass_cache["list"] = sorted(names)   # names, not references: those go stale on undo/reload
    mats = bpy.data.materials
    return [mats[n] for n in _glass_cache["list"] if n in mats]


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
        built = build.id_layer(scene) is not None

        # ---- Proxy scene ----
        box = lay.box()
        box.label(text="Cryptomatte Proxy Scene and View Layer", icon="OUTLINER_COLLECTION")
        row = box.row(align=True)
        row.operator("cryptoray.build", text="Rebuild" if built else "Build", icon="FILE_REFRESH")
        sub = row.row(align=True)
        sub.enabled = built
        sub.operator("cryptoray.clear", text="Delete", icon="TRASH")
        if build.root_objects(scene):
            box.label(text="Objects sit directly in the Scene Collection", icon="ERROR")
            box.operator("cryptoray.move_root_objects", icon="OUTLINER_COLLECTION")
        if built:
            man = build.manifest(scene) or {}
            ids = sum(len(core.layer_names(man, k)) for k, _ in man.get("layers", []))
            groups = len(man.get("entries", []))
            box.label(text="Built: %d IDs, %d light groups" % (ids, groups), icon="CHECKMARK")
            box.label(text="Ctrl+F12 renders beauty + Cryptomatte")
            reason = _stale(context)
            if reason:
                box.label(text="Out of date: " + reason, icon="ERROR")
            if groups > s.group_warning:
                box.label(text="Many light groups: watch GPU memory", icon="ERROR")
            if not render.is_multilayer(scene):
                box.label(text="Ctrl+F12 needs Multilayer EXR output", icon="INFO")
            elif render.lossy_codec(scene):
                box.label(text="Lossy EXR codec: use ZIP/PIZ for clean mattes", icon="ERROR")
        else:
            box.label(text="Not built. Build to enable the settings below.")

        body = lay.column()
        body.active = built          # dimmed until built; settings stay editable for the first build

        # ---- IDs ----
        box = body.box()
        box.label(text="IDs", icon="OBJECT_DATA")
        for use, name in (("use_object", "object_layer"), ("use_material", "material_layer")):
            row = box.row(align=True)
            row.use_property_split = False
            row.prop(s, use)
            sub = row.row(align=True)
            sub.active = getattr(s, use)
            sub.prop(s, name, text="")
        box.prop(s, "scope")
        if s.scope == "COLLECTION":
            box.prop(s, "target_collection")

        # ---- Glass ----
        box = body.box()
        box.label(text="Glass", icon="MATERIAL")
        row = box.row()
        row.use_property_split = False
        row.prop(s, "detect_glass")
        if s.detect_glass:
            mats = [m for m in _scene_materials(scene) if build.detect_glass(m)]
            col = box.column(align=True)
            if not mats:
                col.label(text="No glass materials found")
            for m in mats:
                r = col.row(align=True)
                r.use_property_split = False
                r.separator(factor=2.0)
                r.label(text=m.name, icon="SHADING_RENDERED" if not m.cryptoray_not_glass else "SHADING_SOLID")
                r.prop(m, "cryptoray_not_glass")
        box.prop(s, "ignore_reflections")
        box.prop(s, "glass_collection")

        # ---- Output ----
        box = body.box()
        box.label(text="Output", icon="OUTPUT")
        box.prop(s, "embed")
        box.prop(s, "ranks")
        box.prop(s, "normalization")
        box.prop(s, "matte_samples")
        box.prop(s, "keep_raw")

        # ---- Render ----
        box = body.box()
        box.label(text="Render Cryptomatte Only", icon="RENDER_STILL")
        row = box.row(align=True)
        row.enabled = built
        op = row.operator("cryptoray.render", text="Render Still", icon="RENDER_STILL")
        op.sequence = False
        op = row.operator("cryptoray.render", text="Render Animation", icon="RENDER_ANIMATION")
        op.sequence = True
        try:
            naming = render.output_naming(scene)
            box.label(text="Writes " + bpy.path.basename(render.side_file(naming, scene.frame_current)))
        except RuntimeError as e:
            box.label(text=str(e), icon="ERROR")
        msg = render.STATUS.get(scene.name)
        if msg:
            for i in range(0, min(len(msg), 180), 60):
                lay.label(text=msg[i:i + 60])


def register():
    bpy.utils.register_class(CRYPTORAY_PT_main)


def unregister():
    bpy.utils.unregister_class(CRYPTORAY_PT_main)
