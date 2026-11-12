"""Builds / clears the ID_MATTES proxy layer.

Nothing that already exists in the scene is edited. Build only ADDS:
  * view layer ``ID_MATTES`` (all user collections excluded, own world override, own samples),
  * collection ``ID_MATTES_proxies`` (excluded from every other view layer),
  * one proxy object per ID: an empty mesh/curves/point-cloud object whose Geometry Nodes modifier
    reads the original through Object Info (so animation, deformation and modifiers follow live)
    and assigns an emission material; glass proxies keep the original materials,
  * the emission materials, the ID world and the node groups.
Every added datablock carries the custom property ``cryptoray`` so Clear removes exactly those.
"""

import json

import bpy

from . import core

TAG = "cryptoray"
PROXY_COLLECTION = "ID_MATTES_proxies"
MANIFEST_PROP = "cryptoray_manifest"
SOURCE_PROP = "cryptoray_source"
VERSION = 1

RENDERABLE = {"MESH", "CURVE", "SURFACE", "FONT", "META", "CURVES", "POINTCLOUD", "EMPTY"}
VIS_FLAGS = ("visible_camera", "visible_diffuse", "visible_glossy", "visible_transmission",
             "visible_volume_scatter", "visible_shadow")


class BuildError(Exception):
    pass


def _tag(idb):
    idb[TAG] = VERSION
    return idb


def is_ours(idb):
    return idb is not None and idb.get(TAG) is not None


def id_layer(scene):
    vl = scene.view_layers.get(core.ID_LAYER)
    return vl if vl is not None and is_ours(vl) else None


def manifest(scene):
    vl = id_layer(scene)
    if vl is None or MANIFEST_PROP not in vl:
        return None
    return json.loads(vl[MANIFEST_PROP])


def beauty_layers(scene):
    return [v for v in scene.view_layers if not is_ours(v)]


# --------------------------------------------------------------------------
# Shared datablocks
# --------------------------------------------------------------------------

def _emission_material(name, color):
    m = bpy.data.materials.get(name)
    if m is not None and is_ours(m):
        return m
    m = _tag(bpy.data.materials.new(name))
    m.use_nodes = True
    nt = m.node_tree
    nt.nodes.clear()
    e = nt.nodes.new("ShaderNodeEmission")
    e.inputs["Color"].default_value = (*color, 1.0)
    e.inputs["Strength"].default_value = 1.0
    o = nt.nodes.new("ShaderNodeOutputMaterial")
    o.location = (250, 0)
    nt.links.new(e.outputs[0], o.inputs["Surface"])
    return m


def _id_world():
    w = bpy.data.worlds.get("CR_id_world")
    if w is not None and is_ours(w):
        return w
    w = _tag(bpy.data.worlds.new("CR_id_world"))
    w.use_nodes = True
    bg = w.node_tree.nodes.get("Background") or w.node_tree.nodes.new("ShaderNodeBackground")
    bg.inputs["Color"].default_value = (1, 1, 1, 1)
    bg.inputs["Strength"].default_value = 1.0
    return w


def _node_group(kind):
    """kind: 'emit' (Source, Emit), 'keep' (Source), 'matsel' (Source, Only, Emit)."""
    name = "CR_proxy_" + kind
    g = bpy.data.node_groups.get(name)
    if g is not None and is_ours(g) and g.get("cryptoray_v") == VERSION:
        return g
    g = _tag(bpy.data.node_groups.new(name, "GeometryNodeTree"))
    g["cryptoray_v"] = VERSION
    g.interface.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    g.interface.new_socket("Source", in_out="INPUT", socket_type="NodeSocketObject")
    if kind == "matsel":
        g.interface.new_socket("Only", in_out="INPUT", socket_type="NodeSocketMaterial")
    if kind != "keep":
        g.interface.new_socket("Emit", in_out="INPUT", socket_type="NodeSocketMaterial")
    n, l = g.nodes, g.links
    gi, go = n.new("NodeGroupInput"), n.new("NodeGroupOutput")
    oi = n.new("GeometryNodeObjectInfo")
    oi.transform_space = "RELATIVE"
    l.new(gi.outputs["Source"], oi.inputs["Object"])
    geo = oi.outputs["Geometry"]
    if kind == "matsel":
        rl = n.new("GeometryNodeRealizeInstances")
        l.new(geo, rl.inputs["Geometry"])
        ms = n.new("GeometryNodeMaterialSelection")
        l.new(gi.outputs["Only"], ms.inputs["Material"])
        inv = n.new("FunctionNodeBooleanMath")
        inv.operation = "NOT"
        l.new(ms.outputs[0], inv.inputs[0])
        dl = n.new("GeometryNodeDeleteGeometry")
        dl.domain = "FACE"
        l.new(rl.outputs[0], dl.inputs["Geometry"])
        l.new(inv.outputs[0], dl.inputs["Selection"])
        geo = dl.outputs[0]
    if kind != "keep":
        sm = n.new("GeometryNodeSetMaterial")
        l.new(geo, sm.inputs["Geometry"])
        l.new(gi.outputs["Emit"], sm.inputs["Material"])
        geo = sm.outputs[0]
    l.new(geo, go.inputs["Geometry"])
    for i, node in enumerate(g.nodes):
        node.location = (i * 200, 0)
    return g


def _set_inputs(mod, **values):
    for item in mod.node_group.interface.items_tree:
        if item.item_type == "SOCKET" and item.in_out == "INPUT" and item.name in values:
            mod[item.identifier] = values[item.name]


# --------------------------------------------------------------------------
# Scene analysis
# --------------------------------------------------------------------------

def _glass_names(scene, settings):
    names = set()
    if settings.glass_collection is not None:
        names |= {o.name_full for o in settings.glass_collection.all_objects}
    return names


def _role(obj, glass_names):
    role = str(obj.get("matte_role", "")).lower()
    if role in ("glass", "ignore", "other"):
        return role
    if obj.name_full in glass_names:
        return "glass"
    return ""


def root_objects(scene):
    """Objects linked directly to the scene's master collection: they cannot be excluded from a
    view layer, so they must be moved into a collection first."""
    return [o for o in scene.collection.objects if o.type in RENDERABLE | {"LIGHT"}]


def _visible_objects(layer):
    """Objects that render in ``layer``: (object, camera_visible) honouring exclude, collection
    render visibility, holdout (skipped) and indirect-only (camera-invisible)."""
    found = {}

    def walk(lc, indirect):
        if lc.exclude or lc.collection.hide_render or lc.holdout:
            return
        indirect = indirect or lc.indirect_only
        for o in lc.collection.objects:
            if not o.hide_render and not o.is_holdout:
                found[o.name_full] = (o, found.get(o.name_full, (o, False))[1] or not indirect)
        for c in lc.children:
            walk(c, indirect)

    walk(layer.layer_collection, False)
    return list(found.values())


def _instanced_materials(depsgraph, obj):
    mats = set()
    for inst in depsgraph.object_instances:
        if inst.is_instance and inst.parent is not None and inst.parent.original == obj:
            for slot in inst.instance_object.material_slots:
                if slot.material is not None:
                    mats.add(slot.material.original)
    return mats


def _object_materials(depsgraph, obj):
    ev = obj.evaluated_get(depsgraph)
    mats = {s.material.original for s in ev.material_slots if s.material is not None}
    data = getattr(ev, "data", None)
    if data is not None and hasattr(data, "materials"):
        mats |= {m.original for m in data.materials if m is not None}
    return mats | _instanced_materials(depsgraph, obj)


def analyse(context, scene, settings):
    """Returns dict with targets, glass, others, warnings. Raises BuildError for blockers."""
    src_layer = context.view_layer if context.view_layer and not is_ours(context.view_layer) else beauty_layers(scene)[0]
    roots = root_objects(scene)
    if roots:
        raise BuildError("Objects directly in the Scene Collection can't be kept out of the ID layer: %s. "
                         "Use 'Move Root Objects' first." % ", ".join(o.name for o in roots[:6]))
    glass_names = _glass_names(scene, settings)
    scope = None
    if settings.scope == "COLLECTION":
        if settings.target_collection is None:
            raise BuildError("Choose the target collection (or set Targets to All).")
        scope = {o.name_full for o in settings.target_collection.all_objects}
    targets, glass, others, warnings = [], [], [], []
    for obj, cam_visible in _visible_objects(src_layer):
        if obj.type not in RENDERABLE:
            if obj.type == "VOLUME":
                warnings.append("Volume '%s' is not supported and is left out of the ID layer." % obj.name)
            continue
        if obj.type == "EMPTY" and not (obj.instance_type == "COLLECTION" and obj.instance_collection):
            continue
        if any(m.type == "PARTICLE_SYSTEM" and m.show_render for m in obj.modifiers):
            warnings.append("Particles on '%s' are not captured (convert them to Geometry Nodes instances)." % obj.name)
        if obj.instance_type in ("VERTS", "FACES") and obj.children:
            warnings.append("Vertex/face instancing on '%s' is not captured." % obj.name)
        role = _role(obj, glass_names)
        if role == "ignore":
            continue
        item = (obj, cam_visible)
        if role == "glass":
            glass.append(item)
        elif role == "other" or (scope is not None and obj.name_full not in scope):
            others.append(item)
        else:
            targets.append(item)
    if not targets:
        raise BuildError("No target objects found in view layer '%s'." % src_layer.name)
    return {"source_layer": src_layer, "targets": targets, "glass": glass, "others": others,
            "warnings": warnings}


# --------------------------------------------------------------------------
# Build / clear
# --------------------------------------------------------------------------

def _proxy(obj, name, collection):
    if obj.type == "CURVES":
        data = bpy.data.hair_curves.new(name)
    elif obj.type == "POINTCLOUD":
        data = bpy.data.pointclouds.new(name)
    else:
        data = bpy.data.meshes.new(name)
    _tag(data)
    p = _tag(bpy.data.objects.new(name, data))
    p[SOURCE_PROP] = obj.name_full
    collection.objects.link(p)
    for f in VIS_FLAGS:
        if hasattr(obj, f):
            setattr(p, f, getattr(obj, f))
    p.hide_select = True
    return p


def _attach(p, group, **inputs):
    m = p.modifiers.new("CryptoRay", "NODES")
    m.node_group = group
    _set_inputs(m, **inputs)
    return m


def clear(scene):
    removed = 0
    vl = id_layer(scene)
    if vl is not None:
        vl.world_override = None   # removing the layer doesn't release this user
        scene.view_layers.remove(vl)
        removed += 1
    coll = bpy.data.collections.get(PROXY_COLLECTION)
    if coll is not None and is_ours(coll):
        for o in list(coll.objects):
            if is_ours(o):
                data = o.data
                bpy.data.objects.remove(o)
                removed += 1
                if data is not None and is_ours(data) and data.users == 0:
                    if isinstance(data, bpy.types.Mesh):
                        bpy.data.meshes.remove(data)
                    elif isinstance(data, bpy.types.Curves):
                        bpy.data.hair_curves.remove(data)
                    elif isinstance(data, bpy.types.PointCloud):
                        bpy.data.pointclouds.remove(data)
        if coll.name in scene.collection.children:
            scene.collection.children.unlink(coll)
        if coll.users == 0:
            bpy.data.collections.remove(coll)
    # shared datablocks: only when nothing uses them any more (another built scene might)
    for store in (bpy.data.node_groups, bpy.data.materials, bpy.data.worlds):
        for idb in list(store):
            if is_ours(idb) and idb.users == 0:
                store.remove(idb)
                removed += 1
    return removed


def build(context, scene, settings):
    info = analyse(context, scene, settings)
    if not hasattr(bpy.types.ViewLayer, "bl_rna") or "world_override" not in bpy.types.ViewLayer.bl_rna.properties:
        raise BuildError("This Blender version has no per-view-layer world override (needs Blender 4.2+).")
    clear(scene)
    depsgraph = context.evaluated_depsgraph_get()

    coll = _tag(bpy.data.collections.new(PROXY_COLLECTION))
    scene.collection.children.link(coll)
    vl = _tag(scene.view_layers.new(core.ID_LAYER))
    for lc in vl.layer_collection.children:
        lc.exclude = lc.collection != coll
    for other in beauty_layers(scene):
        lc = other.layer_collection.children.get(coll.name)
        if lc is not None:
            lc.exclude = True
    vl.use = True
    vl.samples = settings.matte_samples
    vl.cycles.use_denoising = False
    vl.use_pass_combined = True
    for p in ("use_pass_z", "use_pass_mist", "use_pass_normal"):
        if hasattr(vl, p):
            setattr(vl, p, False)
    world = _id_world()
    vl.world_override = world
    vl.lightgroups.add(name=core.WORLD_GROUP)
    world.lightgroup = core.WORLD_GROUP

    white = _emission_material("CR_emit_white", (1, 1, 1))
    rgb = {c: _emission_material("CR_emit_" + c, col) for c, col in
           (("R", (1, 0, 0)), ("G", (0, 1, 0)), ("B", (0, 0, 1)))}
    g_emit, g_keep = _node_group("emit"), _node_group("keep")
    g_matsel = _node_group("matsel") if settings.mode == "MATERIAL" else None

    # units: (object, material or None, id name)
    units = []
    warnings = list(info["warnings"])
    for obj, cam in info["targets"]:
        if settings.mode == "MATERIAL":
            mats = sorted(_object_materials(depsgraph, obj), key=lambda m: m.name_full)
            if not mats:
                warnings.append("'%s' has no material; left out of the material IDs." % obj.name)
            for m in mats:
                units.append((obj, cam, m, m.name_full))
        else:
            units.append((obj, cam, None, obj.name_full))

    names = sorted({u[3] for u in units})
    core.check_collisions(names)
    group_of, channel_of = {}, {}
    for i, n in enumerate(names):
        if settings.packing == "RGB":
            group_of[n], channel_of[n] = "id_%04d" % (i // 3 + 1), "RGB"[i % 3]
        else:
            group_of[n], channel_of[n] = "id_%04d" % (i + 1), "L"
    for g in sorted(set(group_of.values())):
        vl.lightgroups.add(name=g)

    for obj, cam, mat, name in units:
        label = "CR_%s" % obj.name if mat is None else "CR_%s|%s" % (obj.name, mat.name)
        p = _proxy(obj, label, coll)
        emit = white if channel_of[name] == "L" else rgb[channel_of[name]]
        if mat is None:
            _attach(p, g_emit, Source=obj, Emit=emit)
        else:
            _attach(p, g_matsel, Source=obj, Only=mat, Emit=emit)
        p.visible_camera = p.visible_camera and cam
        p.lightgroup = group_of[name]

    extra = [core.WORLD_GROUP]
    if info["others"]:
        vl.lightgroups.add(name=core.OTHER_GROUP)
        extra.append(core.OTHER_GROUP)
    for obj, cam in info["others"]:
        p = _proxy(obj, "CR_other_%s" % obj.name, coll)
        _attach(p, g_emit, Source=obj, Emit=white)
        p.visible_camera = p.visible_camera and cam
        p.lightgroup = core.OTHER_GROUP
    for obj, cam in info["glass"]:
        p = _proxy(obj, "CR_glass_%s" % obj.name, coll)
        _attach(p, g_keep, Source=obj)
        p.visible_camera = p.visible_camera and cam

    entries = [{"name": n, "group": group_of[n], "channel": channel_of[n]} for n in names]
    man = core.make_manifest(settings.mode, settings.packing, entries, extra)
    man["settings"] = settings_signature(settings)
    man["sources"] = sorted({o.name_full for o, _ in info["targets"] + info["glass"] + info["others"]})
    vl[MANIFEST_PROP] = json.dumps(man)
    groups = len(set(group_of.values()))
    if groups > settings.group_warning:
        warnings.append("%d light groups: each is a full-resolution float pass; watch GPU memory "
                        "(consider RGB packing or fewer targets)." % groups)
    return {"ids": len(names), "groups": groups, "glass": len(info["glass"]),
            "others": len(info["others"]), "warnings": warnings}


def settings_signature(s):
    return [s.mode, s.packing, s.scope, s.target_collection.name_full if s.target_collection else "",
            s.glass_collection.name_full if s.glass_collection else ""]


def stale_reason(context, scene, settings):
    """Why the built layer no longer matches the scene/settings (None when up to date)."""
    man = manifest(scene)
    if man is None:
        return "not built"
    if man.get("settings") != settings_signature(settings):
        return "settings changed"
    try:
        info = analyse(context, scene, settings)
    except BuildError as e:
        return str(e)
    now = sorted({o.name_full for o, _ in info["targets"] + info["glass"] + info["others"]})
    if now != man.get("sources"):
        return "objects changed"
    vl = id_layer(scene)
    leaks = [lc.name for lc in vl.layer_collection.children if not lc.exclude and lc.name != PROXY_COLLECTION]
    if leaks:
        return "collections not excluded from ID layer: " + ", ".join(leaks)
    return None


def move_root_objects(scene):
    objs = root_objects(scene)
    if not objs:
        return 0
    c = bpy.data.collections.new("Scene Root Objects")
    scene.collection.children.link(c)
    for o in objs:
        c.objects.link(o)
        scene.collection.objects.unlink(o)
    return len(objs)
