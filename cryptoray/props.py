import bpy
from bpy.props import BoolProperty, EnumProperty, IntProperty, PointerProperty, StringProperty


def _check_layer_name(self, context):
    n = self.layer_name.strip()
    if n and not (n[0].isalpha() or n[0] == "_") or any(not (c.isalnum() or c == "_") for c in n):
        self["layer_name"] = "".join(c for c in n if c.isalnum() or c == "_")


class CryptoRaySettings(bpy.types.PropertyGroup):
    mode: EnumProperty(
        name="ID Type",
        items=[("OBJECT", "Object", "One ID per object (named like Blender's CryptoObject)"),
               ("MATERIAL", "Material", "One ID per material (named like Blender's CryptoMaterial)")],
        default="OBJECT")
    packing: EnumProperty(
        name="Packing",
        items=[("SINGLE", "1 ID per Group", "White emission, one light group per ID. Works through tinted glass"),
               ("RGB", "3 IDs per Group", "R/G/B emission, three IDs per light group: a third of the passes, "
                                          "but only correct through neutral (untinted) glass")],
        default="SINGLE")
    scope: EnumProperty(
        name="Targets",
        items=[("ALL", "All Visible", "Every visible object except glass gets an ID"),
               ("COLLECTION", "Collection", "Only objects in the chosen collection get IDs; other visible "
                                            "objects still occlude correctly")],
        default="ALL")
    target_collection: PointerProperty(name="Target Collection", type=bpy.types.Collection)
    glass_collection: PointerProperty(
        name="Glass", type=bpy.types.Collection,
        description="Objects in here keep their real materials in the ID layer (refractive glass, liquids). "
                    "Objects can also be tagged with the custom property matte_role = \"glass\"")
    output_dir: StringProperty(
        name="Output Folder", subtype="DIR_PATH", default="",
        description="Where the Cryptomatte EXRs go. Empty = next to the beauty render output")
    prefix: StringProperty(name="File Prefix", default="crypto",
                           description="Files are named <prefix>_<frame>.exr")
    layer_name: StringProperty(
        name="Cryptomatte Layer", default="", update=_check_layer_name,
        description="Cryptomatte layer name shown in Nuke. Empty = CryptoRayObject / CryptoRayMaterial")
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
                           description="Also save the raw light-group passes (+ manifest) next to the Cryptomatte")
    strip_beauty: BoolProperty(name="Strip IDs from Beauty", default=True,
                               description="Remove the ID passes from the beauty EXR after conversion")
    auto_rebuild: BoolProperty(name="Rebuild Before Render", default=True,
                               description="CryptoRay render buttons rebuild the ID layer first, so new "
                                           "objects are always included")
    group_warning: IntProperty(name="Warn Above", default=64, min=1,
                               description="Warn when more light groups than this are needed (GPU memory)")


def register():
    bpy.utils.register_class(CryptoRaySettings)
    bpy.types.Scene.cryptoray = PointerProperty(type=CryptoRaySettings)


def unregister():
    del bpy.types.Scene.cryptoray
    bpy.utils.unregister_class(CryptoRaySettings)
