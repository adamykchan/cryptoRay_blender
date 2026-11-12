import bpy
from bpy.props import BoolProperty, EnumProperty, IntProperty, PointerProperty, StringProperty


def _clean(key):
    def update(self, context):
        n = self[key] if key in self else ""
        clean = "".join(c for c in n.strip() if c.isalnum() or c == "_")
        if clean and not (clean[0].isalpha() or clean[0] == "_"):
            clean = "_" + clean
        if clean != n:
            self[key] = clean
    return update


class CryptoRaySettings(bpy.types.PropertyGroup):
    use_object: BoolProperty(name="Object", default=True,
                             description="One ID per object (named like Blender's CryptoObject)")
    object_layer: StringProperty(name="Object Layer", default="CryptoRayObject", update=_clean("object_layer"),
                                 description="Cryptomatte layer name for object IDs, as shown in Nuke")
    use_material: BoolProperty(name="Material", default=False,
                               description="One ID per material (named like Blender's CryptoMaterial). "
                                           "Both can be on: they come from the same render")
    material_layer: StringProperty(name="Material Layer", default="CryptoRayMaterial",
                                   update=_clean("material_layer"),
                                   description="Cryptomatte layer name for material IDs, as shown in Nuke")
    scope: EnumProperty(
        name="Targets",
        items=[("ALL", "All Visible", "Every visible object except glass gets an ID"),
               ("COLLECTION", "Collection", "Only objects in the chosen collection get IDs; other visible "
                                            "objects still occlude correctly")],
        default="ALL")
    target_collection: PointerProperty(name="Target Collection", type=bpy.types.Collection)
    detect_glass: BoolProperty(
        name="Detect Glass Materials", default=True,
        description="Materials that refract (Glass or Refraction BSDF, Principled with Transmission) keep "
                    "their real shader in the ID layer. Untick Not Glass per material below")
    glass_collection: PointerProperty(
        name="Glass Collection", type=bpy.types.Collection,
        description="Optional: whole objects in here keep their real materials in the ID layer. Objects can "
                    "also be tagged with the custom property matte_role = \"glass\"")
    ignore_reflections: BoolProperty(
        name="Ignore Glass Reflections", default=True,
        description="Reflections on glass carry no IDs, so objects seen through glass (clear or tinted) get "
                    "a solid matte. Off: reflected objects also show up in the glass, as partial coverage")
    embed: BoolProperty(
        name="Embed in Beauty EXR", default=True,
        description="Write the Cryptomatte layers into the beauty EXR. Off: a separate "
                    "<render name>_crypto_<frame>.exr next to it. IDs Only renders always write a separate file")
    ranks: IntProperty(name="Levels", default=6, min=1, max=16,
                       description="IDs stored per pixel (Cryptomatte depth). 6 is the usual default")
    normalization: EnumProperty(
        name="Coverage",
        items=[("SUM", "Normalized", "Coverage of all IDs + background sums to 1 (undoes glass absorption)"),
               ("RAW", "Raw Energy", "Coverage = light actually reaching the camera (darker through glass)")],
        default="SUM")
    matte_samples: IntProperty(name="Matte Samples", default=0, min=0,
                               description="Samples for the ID layer. 0 = same as the scene")
    keep_raw: BoolProperty(name="Keep Raw ID Passes", default=False,
                           description="Also save the raw light-group passes (+ manifest) next to the render")
    group_warning: IntProperty(name="Warn Above", default=64, min=1,
                               description="Warn when more light groups than this are needed (GPU memory)")


def register():
    bpy.utils.register_class(CryptoRaySettings)
    bpy.types.Scene.cryptoray = PointerProperty(type=CryptoRaySettings)
    bpy.types.Material.cryptoray_not_glass = BoolProperty(
        name="Not Glass", default=False,
        description="Give this material an ID even though its shader refracts")


def unregister():
    del bpy.types.Material.cryptoray_not_glass
    del bpy.types.Scene.cryptoray
    bpy.utils.unregister_class(CryptoRaySettings)
