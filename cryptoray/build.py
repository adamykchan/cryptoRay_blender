"""Builds / clears the ID_MATTES proxy layer.

Nothing that already exists in the scene is edited. Build only ADDS:
  * view layer ``ID_MATTES`` (all user collections excluded, own world override, own samples),
  * collection ``ID_MATTES_proxies`` (excluded from every other view layer),
  * proxy objects: an empty mesh/curves/point-cloud object whose Geometry Nodes modifier reads the
    original through Object Info (so animation, deformation and modifiers follow live) and assigns
    a white emission material. Glass keeps its real materials. Objects mixing glass and other
    materials are split per material, so a bottle's label gets an ID while the glass refracts,
  * for legacy particle systems: a copy of the emitter whose particles instance proxies of the
    instanced objects (same seed and settings, so the same particles),
  * the emission material, the ID world and the node groups.
Every added datablock carries the custom property ``cryptoray`` so Clear removes exactly those.
"""

import json

import bpy

from . import core

TAG = "cryptoray"
PROXY_COLLECTION = "ID_MATTES_proxies"
SOURCES_COLLECTION = "ID_MATTES_instance_sources"
MANIFEST_PROP = "cryptoray_manifest"
SOURCE_PROP = "cryptoray_source"
VERSION = 2

RENDERABLE = {"MESH", "CURVE", "SURFACE", "FONT", "META", "CURVES", "POINTCLOUD", "EMPTY"}
VIS_FLAGS = ("visible_camera", "visible_diffuse", "visible_glossy", "visible_transmission",
             "visible_volume_scatter", "visible_shadow")
GLASS_NODES = {"ShaderNodeBsdfGlass", "ShaderNodeBsdfRefraction"}


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


def requested_layers(settings):
    """[(kind, Cryptomatte layer name)] from the settings."""
    out = []
    if settings.use_object:
        out.append(("object", settings.object_layer.strip() or core.DEFAULT_LAYER_NAMES["object"]))
    if settings.use_material:
        out.append(("material", settings.material_layer.strip() or core.DEFAULT_LAYER_NAMES["material"]))
    return out


# --------------------------------------------------------------------------
# Glass materials
# --------------------------------------------------------------------------

def _transmissive(tree, starts, seen):
    stack, visited = list(starts), set()
    while stack:
        n = stack.pop()
        if n.as_pointer() in visited or n.mute:
            continue
        visited.add(n.as_pointer())
        if n.bl_idname in GLASS_NODES:
            return True
        if n.bl_idname == "ShaderNodeBsdfPrincipled":
            for key in ("Transmission Weight", "Transmission"):
                s = n.inputs.get(key)
                if s is not None and (s.is_linked or s.default_value > 0):
                    return True
        if n.bl_idname == "ShaderNodeGroup" and n.node_tree is not None and n.node_tree not in seen:
            seen.add(n.node_tree)
            outs = [x for x in n.node_tree.nodes if x.bl_idname == "NodeGroupOutput"]
            if _transmissive(n.node_tree, outs, seen):
                return True
        for s in n.inputs:
            for link in s.links:
                if not link.is_muted:
                    stack.append(link.from_node)
    return False


def detect_glass(mat):
    """True when the material's surface shader refracts (Glass / Refraction BSDF, or Principled
    with Transmission above 0 or driven by a texture). Node groups are followed."""
    tree = getattr(mat, "node_tree", None)
    if tree is None:
        return False
    outs = [n for n in tree.nodes if n.bl_idname == "ShaderNodeOutputMaterial"]
    active = [n for n in outs if n.is_active_output and n.target in ("ALL", "CYCLES")]
    starts = []
    for n in active or outs[:1]:
        s = n.inputs.get("Surface")
        if s is not None:
            starts += [l.from_node for l in s.links if not l.is_muted]
    return _transmissive(tree, starts, set())


def is_glass(mat, settings):
    """Detected as glass and not marked Not Glass (the Glass Collection / matte_role work per object)."""
    return settings.detect_glass and not getattr(mat, "cryptoray_not_glass", False) and detect_glass(mat)


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


def _transparent_material():
    m = bpy.data.materials.get("CR_transparent")
    if m is not None and is_ours(m):
        return m
    m = _tag(bpy.data.materials.new("CR_transparent"))
    m.use_nodes = True
    nt = m.node_tree
    nt.nodes.clear()
    t = nt.nodes.new("ShaderNodeBsdfTransparent")
    t.inputs["Color"].default_value = (1, 1, 1, 1)
    o = nt.nodes.new("ShaderNodeOutputMaterial")
    o.location = (250, 0)
    nt.links.new(t.outputs[0], o.inputs["Surface"])
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
    """kind: 'emit' (Source, Emit), 'keep' (Source), 'matsel' (Source, Only, Emit: only the faces
    using one material, emitting), 'keepsel' (Source, Only: those faces with their real material)."""
    name = "CR_proxy_" + kind
    g = bpy.data.node_groups.get(name)
    if g is not None and is_ours(g) and g.get("cryptoray_v") == VERSION:
        return g
    g = _tag(bpy.data.node_groups.new(name, "GeometryNodeTree"))
    g["cryptoray_v"] = VERSION
    g.interface.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    g.interface.new_socket("Source", in_out="INPUT", socket_type="NodeSocketObject")
    select = kind in ("matsel", "keepsel")
    emit = kind in ("emit", "matsel")
    if select:
        g.interface.new_socket("Only", in_out="INPUT", socket_type="NodeSocketMaterial")
    if emit:
        g.interface.new_socket("Emit", in_out="INPUT", socket_type="NodeSocketMaterial")
    n, l = g.nodes, g.links
    gi, go = n.new("NodeGroupInput"), n.new("NodeGroupOutput")
    oi = n.new("GeometryNodeObjectInfo")
    oi.transform_space = "RELATIVE"
    l.new(gi.outputs["Source"], oi.inputs["Object"])
    geo = oi.outputs["Geometry"]
    if select:
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
    if emit:
        sm = n.new("GeometryNodeSetMaterial")
        l.new(geo, sm.inputs["Geometry"])
        l.new(gi.outputs["Emit"], sm.inputs["Material"])
        geo = sm.outputs[0]
    l.new(geo, go.inputs["Geometry"])
    for i, node in enumerate(g.nodes):
        node.location = (i * 200, 0)
    return g


def _keep_faces(mesh, mat):
    """Delete the faces of ``mesh`` that don't use ``mat`` (all faces when ``mat`` is None).
    Shape keys, UVs and vertex groups survive (BMesh keeps them)."""
    import bmesh
    keep = {i for i, m in enumerate(mesh.materials) if mat is not None and m == mat}
    bm = bmesh.new()
    bm.from_mesh(mesh)
    gone = [f for f in bm.faces if f.material_index not in keep]
    bmesh.ops.delete(bm, geom=gone, context="FACES")
    loose = [v for v in bm.verts if not v.link_faces]
    bmesh.ops.delete(bm, geom=loose, context="VERTS")
    bm.to_mesh(mesh)
    bm.free()


def _set_inputs(mod, **values):
    for item in mod.node_group.interface.items_tree:
        if item.item_type == "SOCKET" and item.in_out == "INPUT" and item.name in values:
            mod[item.identifier] = values[item.name]


# --------------------------------------------------------------------------
# Scene analysis
# --------------------------------------------------------------------------

def _glass_names(settings):
    if settings.glass_collection is None:
        return set()
    return {o.name_full for o in settings.glass_collection.all_objects}


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
    """Materials of Geometry Nodes / collection instances (not particles, handled separately)."""
    mats = set()
    for inst in depsgraph.object_instances:
        if (inst.is_instance and inst.parent is not None and inst.parent.original == obj
                and inst.particle_system is None):
            for slot in inst.instance_object.material_slots:
                if slot.material is not None:
                    mats.add(slot.material.original)
    return mats


def _object_materials(depsgraph, obj):
    try:
        ev = obj.evaluated_get(depsgraph)
    except Exception:          # not part of this depsgraph (e.g. only instanced)
        ev = obj
    mats = {s.material.original for s in ev.material_slots if s.material is not None}
    data = getattr(ev, "data", None)
    if data is not None and hasattr(data, "materials"):
        mats |= {m.original for m in data.materials if m is not None}
    return mats | _instanced_materials(depsgraph, obj)


def _particle_systems(obj):
    """[(index, particle system)] that render instances or strands."""
    out = []
    mods = {m.particle_system.name: m for m in obj.modifiers if m.type == "PARTICLE_SYSTEM"}
    for i, ps in enumerate(obj.particle_systems):
        m = mods.get(ps.name)
        if m is None or not m.show_render:
            continue
        st = ps.settings
        if st.render_type == "OBJECT" and st.instance_object is not None:
            out.append((i, ps))
        elif st.render_type == "COLLECTION" and st.instance_collection is not None:
            out.append((i, ps))
        elif st.render_type == "PATH":
            out.append((i, ps))
    return out


def _shows_emitter(obj):
    """An emitter mesh renders only with Object > Instancing > Show Instancer: Render on."""
    if not any(m.type == "PARTICLE_SYSTEM" and m.show_render for m in obj.modifiers):
        return True
    return obj.show_instancer_for_render


def analyse(context, scene, settings):
    """Returns dict with targets, glass, others, warnings. Raises BuildError for blockers."""
    src_layer = context.view_layer if context.view_layer and not is_ours(context.view_layer) else beauty_layers(scene)[0]
    roots = root_objects(scene)
    if roots:
        raise BuildError("Objects directly in the Scene Collection can't be kept out of the ID layer: %s. "
                         "Use 'Move Root Objects' first." % ", ".join(o.name for o in roots[:6]))
    if not settings.use_object and not settings.use_material:
        raise BuildError("Tick Object and/or Material IDs.")
    names = [n for _, n in requested_layers(settings)]
    for n in names:
        if not core.valid_layer_name(n):
            raise BuildError("Cryptomatte layer name %r: use letters, digits and _ only." % n)
    if len(set(names)) != len(names):
        raise BuildError("The Object and Material layers need different names.")
    glass_names = _glass_names(settings)
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

def _new_data(kind, name):
    if kind == "CURVES":
        data = bpy.data.hair_curves.new(name)
    elif kind == "POINTCLOUD":
        data = bpy.data.pointclouds.new(name)
    else:
        data = bpy.data.meshes.new(name)
    return _tag(data)


def _proxy(obj, name, collection):
    p = _tag(bpy.data.objects.new(name, _new_data(obj.type, name)))
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


def _remove_data(data):
    if data is None or not is_ours(data) or data.users:
        return
    if isinstance(data, bpy.types.Mesh):
        bpy.data.meshes.remove(data)
    elif isinstance(data, bpy.types.Curves):
        bpy.data.hair_curves.remove(data)
    elif isinstance(data, bpy.types.PointCloud):
        bpy.data.pointclouds.remove(data)


def clear(scene):
    removed = 0
    vl = id_layer(scene)
    if vl is not None:
        vl.world_override = None   # removing the layer doesn't release this user
        scene.view_layers.remove(vl)
        removed += 1
    # every tagged object: proxies, emitter copies and instance sources
    for o in [o for o in bpy.data.objects if is_ours(o)]:
        data = o.data
        bpy.data.objects.remove(o)
        removed += 1
        _remove_data(data)
    for c in [c for c in bpy.data.collections if is_ours(c)]:
        bpy.data.collections.remove(c)
        removed += 1
    # shared datablocks: only when nothing uses them any more (another built scene might)
    for store in (bpy.data.particles, bpy.data.node_groups, bpy.data.materials, bpy.data.worlds):
        for idb in list(store):
            if is_ours(idb) and idb.users == 0:
                store.remove(idb)
                removed += 1
    return removed


class _Builder:
    def __init__(self, scene, settings, depsgraph, vl, coll, sources):
        self.s = settings
        self.depsgraph = depsgraph
        self.vl = vl
        self.coll = coll
        self.sources = sources
        self.white = _emission_material("CR_emit_white", (1, 1, 1))
        self.clear_mat = _transparent_material()
        self.glass_names = _glass_names(settings)
        self.warnings = []
        self.entries = {}          # (object name, material name) -> group
        self.glass_cache = {}
        self.other = False
        self.n = 0

    # ---- helpers ----
    def glass(self, mat):
        g = self.glass_cache.get(mat)
        if g is None:
            g = self.glass_cache[mat] = is_glass(mat, self.s)
        return g

    def group(self, obj_name, mat):
        """Light group for an (object, material) pair under the current ID settings."""
        key = (obj_name if self.s.use_object else None,
               mat.name_full if (mat is not None and self.s.use_material) else None)
        if key == (None, None):
            self.other = True
            return core.OTHER_GROUP
        g = self.entries.get(key)
        if g is None:
            g = self.entries[key] = "id_%04d" % (len(self.entries) + 1)
            self.vl.lightgroups.add(name=g)
        return g

    def parts(self, obj, mats):
        """How to build ``obj``: [(material or None, 'emit'|'keep')], None = the whole object."""
        glass = {m for m in mats if self.glass(m)}
        solid = sorted(set(mats) - glass, key=lambda m: m.name_full)
        if not solid:
            return [(None, "keep")] if mats else [(None, "emit")]
        if not glass and (len(solid) == 1 or not self.s.use_material):
            return [(solid[0] if len(solid) == 1 else None, "emit")]
        return [(m, "emit") for m in solid] + [(m, "keep") for m in sorted(glass, key=lambda m: m.name_full)]

    def proxy(self, obj, part, collection, label):
        mat, how = part
        p = _proxy(obj, label, collection)
        if mat is None:
            grp = _node_group("emit" if how == "emit" else "keep")
            inputs = {"Source": obj, "Emit": self.white} if how == "emit" else {"Source": obj}
        else:
            grp = _node_group("matsel" if how == "emit" else "keepsel")
            inputs = {"Source": obj, "Only": mat}
            if how == "emit":
                inputs["Emit"] = self.white
        _attach(p, grp, **inputs)
        p.lightgroup = self.group(obj.name_full, mat) if how == "emit" else ""
        self.n += 1
        return p

    def source_copy(self, obj, part, label):
        """Particle instance source: a duplicate of ``obj`` (same modifiers, transform and
        animation) with an object-level emission override. Geometry Nodes proxies don't work here:
        Blender doesn't evaluate them for objects that are only used by particles."""
        c = _tag(obj.copy())
        c.name = label
        c[SOURCE_PROP] = obj.name_full
        self.sources.objects.link(c)
        c.hide_select = True
        mat, how = part if part is not None else (None, "hide")
        if (mat is not None or how == "hide") and c.type == "MESH":
            # keep only this variant's faces; other variants draw the rest. Done on a mesh copy at
            # build time: Blender doesn't evaluate modifiers we add to particle-only objects in
            # final renders, so a Geometry Nodes split would silently do nothing.
            c.data = _tag(c.data.copy())
            _keep_faces(c.data, mat)
        if how == "emit" or (how == "hide" and c.type != "MESH"):
            if not c.material_slots and hasattr(c.data, "materials"):
                if not is_ours(c.data):
                    c.data = _tag(c.data.copy())   # needs a slot to override; the original stays untouched
                c.data.materials.append(None)
            for slot in c.material_slots:
                slot.link = "OBJECT"
                slot.material = self.white if how == "emit" else self.clear_mat
        c.lightgroup = self.group(obj.name_full, mat) if how == "emit" else ""
        self.n += 1
        return c

    # ---- objects ----
    def target(self, obj, cam):
        mats = sorted(_object_materials(self.depsgraph, obj), key=lambda m: m.name_full)
        if _shows_emitter(obj):
            for part in self.parts(obj, mats):
                label = "CR_%s" % obj.name if part[0] is None else "CR_%s|%s" % (obj.name, part[0].name)
                p = self.proxy(obj, part, self.coll, label)
                p.visible_camera = p.visible_camera and cam
        for index, ps in _particle_systems(obj):
            self.particles(obj, cam, index, ps)

    def other_object(self, obj, cam):
        p = _proxy(obj, "CR_other_%s" % obj.name, self.coll)
        _attach(p, _node_group("emit"), Source=obj, Emit=self.white)
        p.visible_camera = p.visible_camera and cam
        p.lightgroup = core.OTHER_GROUP
        self.other = True

    def glass_object(self, obj, cam):
        p = _proxy(obj, "CR_glass_%s" % obj.name, self.coll)
        _attach(p, _node_group("keep"), Source=obj)
        p.visible_camera = p.visible_camera and cam

    # ---- legacy particles ----
    def _emitter_copy(self, obj, cam, index, label):
        c = _tag(obj.copy())
        c.name = label
        c[SOURCE_PROP] = obj.name_full
        self.coll.objects.link(c)
        c.hide_select = True
        c.lightgroup = ""
        c.visible_camera = c.visible_camera and cam
        mods = [m for m in c.modifiers if m.type == "PARTICLE_SYSTEM"]
        for i, m in enumerate(mods):
            m.show_render = m.show_viewport = (i == index)
        ps = c.particle_systems[index]
        st = _tag(ps.settings.copy())
        ps.settings = st
        c.show_instancer_for_render = False   # the emitter mesh has its own proxy
        return c, st

    def particles(self, obj, cam, index, ps):
        st0 = ps.settings
        base = "CR_ps_%s_%d" % (obj.name, index)
        if st0.render_type == "PATH":
            # strands render with the emitter's material slot st0.material (1-based)
            idx = st0.material - 1
            mat = obj.material_slots[idx].material if 0 <= idx < len(obj.material_slots) else None
            if mat is not None and self.glass(mat):
                return
            if not obj.material_slots:
                self.warnings.append("Hair strands on '%s' need a material slot to get an ID." % obj.name)
                return
            c, st = self._emitter_copy(obj, cam, index, base)
            for slot in c.material_slots:      # object-level override on the copy only
                slot.link = "OBJECT"
                slot.material = self.white
            c.lightgroup = self.group(obj.name_full, mat)
            self.n += 1
            return
        if st0.render_type == "OBJECT":
            members = [st0.instance_object]
        else:
            members = list(st0.instance_collection.all_objects)
        plans = {}
        for o in members:
            role = _role(o, self.glass_names)
            if role == "ignore":
                plans[o] = []
            elif role == "glass":
                plans[o] = [(None, "keep")]
            else:
                plans[o] = self.parts(o, sorted(_object_materials(self.depsgraph, o), key=lambda m: m.name_full))
        # one variant when nothing needs splitting, else one per material (+ whole objects)
        if all(len(p) == 1 and (p[0][0] is None or p[0][1] == "emit") for p in plans.values()):
            variants = [None]
        else:
            variants = sorted({part for p in plans.values() for part in p if part[0] is not None},
                              key=lambda x: (x[0].name_full, x[1]))
            if any(part[0] is None for p in plans.values() for part in p):
                variants.append(None)
        for vi, variant in enumerate(variants):
            label = "%s_v%d" % (base, vi)
            c, st = self._emitter_copy(obj, cam, index, label)
            proxies = []
            for o in members:
                plan = plans[o]
                if variant is None:
                    part = plan[0] if len(plan) == 1 and (variants == [None] or plan[0][0] is None) else None
                else:
                    part = variant if variant in plan else None
                # part None -> fully transparent copy: keeps member count/order for random picking
                proxies.append(self.source_copy(o, part, "CR_src_%s_%s" % (o.name, label)))
            if st0.render_type == "OBJECT":
                st.instance_object = proxies[0]
            else:
                ic = _tag(bpy.data.collections.new("CR_src_%s" % label))
                ic.instance_offset = st0.instance_collection.instance_offset
                for p in proxies:
                    ic.objects.link(p)
                self.sources.children.link(ic)
                st.instance_collection = ic


def build(context, scene, settings):
    info = analyse(context, scene, settings)
    if "world_override" not in bpy.types.ViewLayer.bl_rna.properties:
        raise BuildError("This Blender version has no per-view-layer world override (needs Blender 4.2+).")
    clear(scene)
    depsgraph = context.evaluated_depsgraph_get()

    coll = _tag(bpy.data.collections.new(PROXY_COLLECTION))
    scene.collection.children.link(coll)
    # particle instance sources live in a collection that is NOT linked to the scene (like a
    # typical instance library): only then does Blender evaluate their modifiers for the particles
    sources = _tag(bpy.data.collections.new(SOURCES_COLLECTION))
    sources.use_fake_user = True
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
    vl.lightgroups.add(name=core.OTHER_GROUP)

    b = _Builder(scene, settings, depsgraph, vl, coll, sources)
    for obj, cam in info["targets"]:
        b.target(obj, cam)
    for obj, cam in info["others"]:
        b.other_object(obj, cam)
    for obj, cam in info["glass"]:
        b.glass_object(obj, cam)

    # Ignore Glass Reflections: nothing in the ID layer is visible to glossy (reflection) rays, so a
    # reflection on glass adds no light; refracted (transmission) rays still see everything
    world.cycles_visibility.glossy = not settings.ignore_reflections
    lost = []
    if settings.ignore_reflections:
        for o in list(coll.objects) + list(sources.all_objects):
            if is_ours(o):
                o.visible_glossy = False
        _reflection_catcher(coll, b.white)
        vl.lightgroups.add(name=core.LOST_GROUP)
        lost = [core.LOST_GROUP]

    entries = [{"group": g, "object": k[0], "material": k[1]} for k, g in sorted(b.entries.items(), key=lambda x: x[1])]
    if not entries:
        clear(scene)
        raise BuildError("Nothing gets an ID (everything is glass, or has no material in Material-only mode).")
    layers = requested_layers(settings)
    for kind, _ in layers:
        try:
            core.check_collisions(list(core.layer_names({"entries": entries}, kind)))
        except ValueError as e:
            clear(scene)
            raise BuildError(str(e))
    # the background (world) is an ID too, in every layer
    entries.append({"group": core.WORLD_GROUP, "object": core.BACKGROUND_ID, "material": core.BACKGROUND_ID})
    man = core.make_manifest(entries, [core.OTHER_GROUP], layers, lost)
    man["settings"] = settings_signature(settings)
    man["sources"] = _sources(info)
    man["glass_materials"] = sorted(m.name_full for m, g in b.glass_cache.items() if g)
    man["glass_check"] = _slot_glass(scene, settings)
    vl[MANIFEST_PROP] = json.dumps(man)
    warnings = list(info["warnings"]) + b.warnings
    groups = len(entries)
    if groups > settings.group_warning:
        warnings.append("%d light groups: each is a full-resolution float pass; watch GPU memory "
                        "(use the Collection target to limit IDs)." % groups)
    counts = {kind: len(core.layer_names(man, kind)) for kind, _ in layers}
    return {"ids": counts, "groups": groups, "proxies": b.n, "glass": len(info["glass"]),
            "glass_materials": man["glass_materials"], "others": len(info["others"]), "warnings": warnings}


def _reflection_catcher(coll, white):
    """A huge sphere seen only by reflection rays: with everything else invisible to them, it measures
    how much of each pixel's light comes from reflections on glass (light group LOST_GROUP)."""
    import bmesh
    me = _tag(bpy.data.meshes.new("CR_reflection_catcher"))
    bm = bmesh.new()
    bmesh.ops.create_icosphere(bm, subdivisions=3, radius=10000.0)
    bm.to_mesh(me)
    bm.free()
    me.materials.append(white)
    o = _tag(bpy.data.objects.new("CR_reflection_catcher", me))
    coll.objects.link(o)
    for f in VIS_FLAGS:
        if hasattr(o, f):
            setattr(o, f, f == "visible_glossy")
    o.lightgroup = core.LOST_GROUP
    o.hide_select = True
    return o


def _sources(info):
    return sorted({o.name_full for o, _ in info["targets"] + info["glass"] + info["others"]})


def _slot_glass(scene, settings):
    """Glass materials in the scene's object slots (cheap staleness check for Not Glass edits)."""
    mats = {sl.material for o in scene.objects for sl in o.material_slots
            if sl.material is not None and not is_ours(sl.material)}
    return sorted(m.name_full for m in mats if is_glass(m, settings))


def settings_signature(s):
    return [bool(s.use_object), bool(s.use_material), [n for _, n in requested_layers(s)], s.scope,
            s.target_collection.name_full if s.target_collection else "",
            s.glass_collection.name_full if s.glass_collection else "", bool(s.detect_glass),
            bool(s.ignore_reflections)]


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
    if _sources(info) != man.get("sources"):
        return "objects changed"
    if _slot_glass(scene, settings) != man.get("glass_check"):
        return "glass materials changed"
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
