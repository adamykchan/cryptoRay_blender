import bpy
from bpy.app.translations import pgettext_iface as iface_

from . import build, core, render

HELP_URL = "https://github.com/adamykchan/cryptoRay_blender"

_cache = {"key": None, "stale": None}
_glass_cache = {"key": None, "list": []}


def _stale(context):
    """Cheap-to-key staleness check: re-analysed only when objects/collections/settings/build change."""
    s = context.scene.cryptoray
    vl = build.id_layer(context.scene)
    key = (context.scene.name, len(bpy.data.objects), len(bpy.data.collections),
           repr(build.settings_signature(s)), vl.get(build.MANIFEST_PROP) if vl else None,
           tuple(m.name for m in bpy.data.materials if getattr(m, "cryptoray_not_glass", False)),
           tuple(m.name for m in bpy.data.materials if getattr(m, "cryptoray_not_chrome", False)))
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


def _material_box(layout, mats, title, empty, prop, icon="MATERIAL"):
    """Detected materials in their own box, one checkbox each (on = glass / chrome, off = own ID)."""
    row = layout.row()
    row.separator(factor=2.0)
    inner = row.box().column(align=True)
    inner.use_property_split = False
    if not mats:
        inner.label(text=empty)
        return
    inner.label(text=title)
    for m in mats:
        r = inner.row(align=True)
        r.prop(m, prop, text="")
        r.label(text=m.name, translate=False, icon=icon)


def _toggle_section(layout, s, toggle, icon):
    """A box whose header checkbox switches the whole section on/off. Returns the indented body
    column, greyed out and locked while the toggle is off (the settings keep their values)."""
    box = layout.box()
    head = box.row()
    head.use_property_split = False
    head.prop(s, toggle, text="")
    head.label(text=s.bl_rna.properties[toggle].name, icon=icon)
    row = box.row()
    row.separator(factor=1.5)              # indent the body under the header checkbox
    col = row.column()
    col.use_property_split = False         # checkboxes left-aligned so long labels aren't cut off
    col.enabled = getattr(s, toggle)
    return col


def _collection_row(layout, s, toggle, prop):
    """[x] Collection [field]: the checkbox ignores the collection without clearing it."""
    row = layout.row(align=False)
    row.prop(s, toggle, text="")
    split = row.split(factor=0.38)          # room for the whole word: the default split cuts it off
    split.active = getattr(s, toggle)
    split.label(text="Collection")
    split.prop(s, prop, text="")


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
        row = box.row()
        row.use_property_split = False
        row.prop(s, "auto_update")
        if build.root_objects(scene):
            box.label(text="Objects sit directly in the Scene Collection", icon="ERROR")
            box.operator("cryptoray.move_root_objects", icon="OUTLINER_COLLECTION")
        if built:
            man = build.manifest(scene) or {}
            ids = sum(len(core.layer_names(man, k)) for k, _ in man.get("layers", []))
            groups = len(man.get("entries", []))
            if render.rebuilding(scene):
                box.label(text="Rebuilding...", icon="STRIP_COLOR_03")     # yellow light: busy, not a problem
            else:
                box.label(text=iface_("Built: %d IDs, %d light groups") % (ids, groups), translate=False,
                          icon="CHECKMARK")
            box.label(text="Ctrl+F12 renders beauty + Cryptomatte")
            # an Auto Update about to run: no warning, so the panel doesn't jump for a moment
            # (a settings problem is shown in the IDs box instead)
            reason = None if render.rebuilding(scene) or build.settings_problem(s) else _stale(context)
            if reason:
                warn = box.row()
                warn.alert = True           # drawn red
                warn.label(text=iface_("Out of date: %s") % iface_(reason), translate=False, icon="ERROR")
            if groups > s.group_warning:
                box.label(text="Many light groups: watch GPU memory", icon="ERROR")
            if not render.is_multilayer(scene):
                box.label(text="Ctrl+F12 needs Multilayer EXR output", icon="INFO")
            elif render.lossy_codec(scene):
                box.label(text="Lossy EXR codec: use ZIP/PIZ for clean mattes", icon="ERROR")
        else:
            if build.settings_problem(s):
                box.label(text="Fix the setting marked below, then Build.")
            else:
                box.label(text="Not built. Build to enable the settings below.")

        body = lay.column()
        # locked until built (Build works with any settings); editable when something must be fixed first
        body.enabled = built or bool(build.settings_problem(s))

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
        problem = build.settings_problem(s)
        if problem:
            warn = box.row()
            warn.alert = True
            warn.label(text=problem, icon="ERROR")
        elif not build.requested_layers(s):
            box.label(text="No IDs ticked: renders write no Cryptomatte", icon="INFO")

        # ---- Glass ----
        col = _toggle_section(body, s, "use_glass", "MATERIAL")
        col.prop(s, "detect_glass")
        if s.detect_glass:
            _material_box(col, [m for m in _scene_materials(scene) if build.detect_glass(m)],
                          "Detected glass materials", "No glass materials found", "cryptoray_use_glass")
        _collection_row(col, s, "use_glass_collection", "glass_collection")
        col.separator(type="LINE")
        col.prop(s, "ignore_reflections")
        col.prop(s, "ignore_absorption")

        # ---- Chrome ----
        col = _toggle_section(body, s, "use_chrome", "SHADING_RENDERED")
        col.prop(s, "detect_chrome")
        if s.detect_chrome:
            _material_box(col, [m for m in _scene_materials(scene)
                                if build.detect_chrome(m) and not build.is_glass(m, s)],
                          "Detected chrome materials", "No chrome materials found", "cryptoray_use_chrome",
                          icon="SHADING_RENDERED")
        _collection_row(col, s, "use_chrome_collection", "chrome_collection")
        col.separator(type="LINE")
        col.prop(s, "pure_chrome")

        # ---- Output ----
        box = body.box()
        box.label(text="Output", icon="OUTPUT")
        col = box.column()
        col.use_property_split = False
        col.prop(s, "embed")
        box.prop(s, "ranks")
        box.prop(s, "matte_samples")
        col = box.column()
        col.use_property_split = False
        col.prop(s, "keep_raw")

        # ---- Render ----
        box = body.box()
        box.label(text="Render Cryptomatte Only", icon="RENDER_STILL")
        try:
            naming = render.output_naming(scene)
            path = render.side_file(naming, scene.frame_current)
            shown = bpy.path.relpath(path) if bpy.data.filepath else path
            box.label(text=shown.replace("\\", "/"), translate=False, icon="FILE")
        except RuntimeError as e:
            box.label(text=iface_(str(e)), translate=False, icon="ERROR")
        row = box.row(align=True)
        row.enabled = built
        op = row.operator("cryptoray.render", text="Render Still", icon="RENDER_STILL")
        op.sequence = False
        op = row.operator("cryptoray.render", text="Render Animation", icon="RENDER_ANIMATION")
        op.sequence = True
        msg = render.STATUS.get(scene.name)
        if msg:
            for i in range(0, min(len(msg), 180), 60):
                lay.label(text=msg[i:i + 60], translate=False)

        lay.separator()
        lay.operator("wm.url_open", text="Help", icon="URL").url = HELP_URL


def register():
    bpy.utils.register_class(CRYPTORAY_PT_main)


def unregister():
    bpy.utils.unregister_class(CRYPTORAY_PT_main)
