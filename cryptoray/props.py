import bpy
from bpy.props import BoolProperty, EnumProperty, IntProperty, PointerProperty, StringProperty


def _rebuild(self, context):
    """Settings that shape the proxy scene: rebuild it (Auto Update)."""
    from . import render
    render.request_rebuild(context.scene)


def _clean(key):
    def update(self, context):
        n = self[key] if key in self else ""
        clean = "".join(c for c in n.strip() if c.isalnum() or c == "_")
        if clean and not (clean[0].isalpha() or clean[0] == "_"):
            clean = "_" + clean
        if clean != n:
            self[key] = clean
        _rebuild(self, context)
    return update


def _samples(self, context):
    """Matte Samples only sets the ID layer's samples: no rebuild needed."""
    from . import build
    vl = build.id_layer(context.scene)
    if vl is not None:
        vl.samples = self.matte_samples


class CryptoRaySettings(bpy.types.PropertyGroup):
    auto_update: BoolProperty(
        name="Auto Update", default=True, update=_rebuild,
        description="Rebuild the proxy scene by itself when a setting below or a material checkbox changes. "
                    "Turn off for heavy scenes and press Rebuild when ready")
    use_object: BoolProperty(update=_rebuild, name="Object", default=True,
                             description="One ID per object (named like Blender's CryptoObject)")
    object_layer: StringProperty(name="Object Layer", default="CryptoRayObject", update=_clean("object_layer"),
                                 description="Cryptomatte layer name for object IDs, as shown in Nuke")
    use_material: BoolProperty(update=_rebuild, name="Material", default=False,
                               description="One ID per material (named like Blender's CryptoMaterial). "
                                           "Both can be on: they come from the same render")
    material_layer: StringProperty(name="Material Layer", default="CryptoRayMaterial",
                                   update=_clean("material_layer"),
                                   description="Cryptomatte layer name for material IDs, as shown in Nuke")
    scope: EnumProperty(update=_rebuild,
        name="Targets",
        items=[("ALL", "All Visible", "Every visible object except glass gets an ID"),
               ("COLLECTION", "Collection", "Only objects in the chosen collection get IDs; other visible "
                                            "objects still occlude correctly")],
        default="ALL")
    target_collection: PointerProperty(update=_rebuild, name="Target Collection", type=bpy.types.Collection)
    use_glass: BoolProperty(update=_rebuild,
        name="Glass", default=True,
        description="See IDs through glass. Off: glass is treated like any other surface and gets its own "
                    "ID (the glass settings below keep their values but have no effect)")
    use_chrome: BoolProperty(update=_rebuild,
        name="Chrome", default=False,
        description="See IDs reflected in chrome. Off: chrome is treated like any other surface and gets its "
                    "own ID (the chrome settings below keep their values but have no effect)")
    use_glass_collection: BoolProperty(update=_rebuild,
        name="Use Glass Collection", default=True,
        description="Use the Glass Collection. Off: it is ignored but stays set")
    use_chrome_collection: BoolProperty(update=_rebuild,
        name="Use Chrome Collection", default=True,
        description="Use the Chrome Collection. Off: it is ignored but stays set")
    detect_glass: BoolProperty(update=_rebuild,
        name="Detect Glass Materials", default=True,
        description="Materials that refract (Glass or Refraction BSDF, Principled with Transmission) keep "
                    "their real shader in the ID layer. Untick a material below to give it its own ID")
    glass_collection: PointerProperty(update=_rebuild,
        name="Glass Collection", type=bpy.types.Collection,
        description="Optional: whole objects in here keep their real materials in the ID layer. Objects can "
                    "also be tagged with the custom property matte_role = \"glass\"")
    detect_chrome: BoolProperty(update=_rebuild,
        name="Detect Chrome Materials", default=True,
        description="Metals (Glossy, Anisotropic or Metallic BSDF, Principled with Metallic, also in mixes "
                    "and with textures) keep their shader in the ID layer: what they reflect keeps its own ID, "
                    "blurred like the reflection. Untick a material below to give it its own ID")
    chrome_collection: PointerProperty(update=_rebuild,
        name="Chrome Collection", type=bpy.types.Collection,
        description="Optional: every material of the objects in here reflects IDs like chrome, detected or not. "
                    "Objects can also be tagged with the custom property matte_role = \"chrome\"")
    pure_chrome: BoolProperty(update=_rebuild,
        name="Treat as Pure Chrome", default=True,
        description="Chrome shows only what it reflects, never its own ID. Off: where chrome isn't fully "
                    "metal (Metallic below 1, rust in a texture, diffuse in a mix) the object's own ID shows, "
                    "in proportion")
    ignore_reflections: BoolProperty(update=_rebuild,
        name="Ignore Glass Reflections", default=True,
        description="Reflections on glass carry no IDs (the ID layer uses a copy of each glass material "
                    "without its reflection), so objects seen through glass (clear or tinted) get a solid "
                    "matte. Off: reflected objects also show up in the glass, as partial coverage. Chrome isn't "
                    "affected: glass reflected in chrome always looks the same")
    ignore_absorption: BoolProperty(
        name="Ignore Glass Absorption", default=True,
        description="Coverage is each ID's share of the light in the pixel, so glass absorption and tint "
                    "(light lost inside the glass) don't weaken the matte. Off: coverage is the light that "
                    "actually reaches the camera, darker through tinted glass, and needn't add up to 1. Chrome "
                    "isn't affected")
    embed: BoolProperty(
        name="Embed in Beauty EXR", default=True,
        description="Write the Cryptomatte layers into the beauty EXR. Off: a separate "
                    "<render name>_crypto_<frame>.exr next to it. IDs Only renders always write a separate file")
    ranks: IntProperty(name="Levels", default=6, min=1, max=16,
                       description="IDs stored per pixel (Cryptomatte depth). 6 is the usual default")
    matte_samples: IntProperty(name="Matte Samples", default=0, min=0, update=_samples,
                               description="Samples for the ID layer. 0 = same as the scene")
    keep_raw: BoolProperty(name="Keep Raw ID Passes", default=False,
                           description="Also save the raw light-group passes (+ manifest) next to the render")
    group_warning: IntProperty(name="Warn Above", default=64, min=1,
                               description="Warn when more light groups than this are needed (GPU memory)")


def _inverted(key):
    """get/set for a checkbox that shows the opposite of the stored ``key`` (kept for older files)."""
    # through the RNA property, not self[key]: Blender 5.0 keeps add-on properties apart from custom ones
    return {"get": lambda self: not getattr(self, key),
            "set": lambda self, value: setattr(self, key, not value)}


def register():
    bpy.utils.register_class(CryptoRaySettings)
    bpy.types.Scene.cryptoray = PointerProperty(type=CryptoRaySettings)
    bpy.types.Material.cryptoray_not_glass = BoolProperty(
        name="Not Glass", default=False, update=_rebuild,
        description="Give this material an ID even though its shader refracts")
    bpy.types.Material.cryptoray_not_chrome = BoolProperty(
        name="Not Chrome", default=False, update=_rebuild,
        description="Give this material an ID instead of showing the IDs it reflects")
    # the panel's per-material checkboxes: on = treated as glass / chrome (the inverse of Not Glass / Not Chrome)
    bpy.types.Material.cryptoray_use_glass = BoolProperty(
        name="Use as Glass", update=_rebuild, description="Treat this material as glass. Off: it gets its own ID",
        **_inverted("cryptoray_not_glass"))
    bpy.types.Material.cryptoray_use_chrome = BoolProperty(
        name="Use as Chrome", update=_rebuild, description="Treat this material as chrome: it shows the IDs it reflects. "
                                          "Off: it gets its own ID",
        **_inverted("cryptoray_not_chrome"))


def unregister():
    del bpy.types.Material.cryptoray_use_chrome
    del bpy.types.Material.cryptoray_use_glass
    del bpy.types.Material.cryptoray_not_chrome
    del bpy.types.Material.cryptoray_not_glass
    del bpy.types.Scene.cryptoray
    bpy.utils.unregister_class(CryptoRaySettings)
