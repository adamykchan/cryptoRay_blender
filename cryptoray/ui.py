import bpy

from . import build, render

_cache = {"key": None, "stale": None}
_glass_cache = {"key": None, "list": []}


def _stale(context):
    """Cheap-to-key staleness check: re-analysed only when objects/collections/settings/build change."""
    s = context.scene.cryptoray
    vl = build.id_layer(context.scene)
    key = (context.scene.name, len(bpy.data.objects), len(bpy.data.collections),
           repr(build.settings_signature(s)), vl.get(build.MANIFEST_PROP) if vl else None)
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

        # ---- IDs ----
        box = lay.box()
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
        if build.root_objects(scene):
            box.label(text="Objects sit directly in the Scene Collection", icon="ERROR")
            box.operator("cryptoray.move_root_objects", icon="OUTLINER_COLLECTION")

        # ---- Glass ----
        box = lay.box()
        box.label(text="Glass", icon="MATERIAL")
        box.prop(s, "detect_glass")
        box.prop(s, "glass_collection")
        mats = _scene_materials(scene)
        glass = [m for m in mats if build.is_glass(m, s)]
        row = box.row()
        row.use_property_split = False
        row.prop(s, "show_glass", icon="TRIA_DOWN" if s.show_glass else "TRIA_RIGHT", emboss=False,
                 text="Materials: %d glass of %d" % (len(glass), len(mats)))
        if s.show_glass:
            col = box.column(align=True)
            for m in mats:
                r = col.row(align=True)
                r.use_property_split = False
                r.label(text=m.name, icon="SHADING_RENDERED" if m in glass else "SHADING_SOLID")
                r.prop(m, "cryptoray_glass", text="")

        # ---- Output ----
        box = lay.box()
        box.label(text="Output", icon="OUTPUT")
        box.prop(s, "embed")
        try:
            naming = render.output_naming(scene)
            if s.embed:
                box.label(text="Into the beauty EXR (IDs Only: separate file)")
            else:
                box.label(text=bpy.path.basename(render.side_file(naming, scene.frame_current)))
        except RuntimeError as e:
            box.label(text=str(e), icon="ERROR")
        box.prop(s, "ranks")
        box.prop(s, "normalization")
        box.prop(s, "matte_samples")
        box.prop(s, "keep_raw")

        # ---- Render ----
        box = lay.box()
        box.label(text="Render", icon="RENDER_STILL")
        box.prop(s, "auto_rebuild")
        box.prop(s, "delete_after")
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

        built = build.id_layer(scene) is not None
        row = box.row(align=True)
        row.operator("cryptoray.build", text="Rebuild ID Layer" if built else "Build ID Layer", icon="FILE_REFRESH")
        if built:
            row.operator("cryptoray.clear", text="", icon="TRASH")
            man = build.manifest(scene) or {}
            groups = len(man.get("entries", []))
            box.label(text="ID layer in scene: %d light groups" % groups, icon="CHECKMARK")
            if not s.auto_rebuild:
                reason = _stale(context)
                if reason:
                    box.label(text="Out of date: " + reason, icon="ERROR")
            if groups > s.group_warning:
                box.label(text="Many light groups: watch GPU memory", icon="ERROR")
        msg = render.STATUS.get(scene.name)
        if msg:
            for i in range(0, min(len(msg), 180), 60):
                box.label(text=msg[i:i + 60])


def register():
    bpy.utils.register_class(CRYPTORAY_PT_main)


def unregister():
    bpy.utils.unregister_class(CRYPTORAY_PT_main)
