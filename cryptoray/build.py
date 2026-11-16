"""Builds / clears the ID_MATTES proxy layer.

Nothing that already exists in the scene is edited. Build only ADDS:
  * view layer ``ID_MATTES`` (all user collections excluded, own world override, own samples),
  * collection ``ID_MATTES_proxies`` (excluded from every other view layer),
  * proxy objects: an empty mesh/curves/point-cloud object whose Geometry Nodes modifier reads the
    original through Object Info (so animation, deformation and modifiers follow live) and assigns
    a white emission material. Glass and chrome keep their shaders, through ID-layer copies of
    their materials: glass without its reflection (Ignore Glass Reflections), chrome with the
    non-metal parts emitting the object's own ID (or nothing, with Treat as Pure Chrome). Objects mixing
    glass/chrome and other materials are split per material, so a bottle's label gets an ID while
    the glass refracts,
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
MIRROR_NODES = {"ShaderNodeBsdfGlossy", "ShaderNodeBsdfAnisotropic", "ShaderNodeBsdfMetallic"}
# shader nodes that don't reflect: in a chrome copy they emit the object's own ID instead
MATTE_NODES = {"ShaderNodeBsdfDiffuse", "ShaderNodeSubsurfaceScattering", "ShaderNodeBsdfToon",
               "ShaderNodeBsdfSheen", "ShaderNodeBsdfTranslucent"}
COPY_PROP = "cryptoray_copy_of"     # on ID-layer material copies: name of the original
KIND_PROP = "cryptoray_copy_kind"   # "glass" (no reflection), "glass_refl" (Ignore Glass Reflections off),
                                    # "chrome" (own ID) or "mirror" (Treat as Pure Chrome)


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

def _find(tree, starts, seen, test):
    """True when ``test(node)`` holds for a node feeding ``starts`` (node groups followed)."""
    stack, visited = list(starts), set()
    while stack:
        n = stack.pop()
        if n.as_pointer() in visited or n.mute:
            continue
        visited.add(n.as_pointer())
        if test(n):
            return True
        if n.bl_idname == "ShaderNodeGroup" and n.node_tree is not None and n.node_tree not in seen:
            seen.add(n.node_tree)
            outs = [x for x in n.node_tree.nodes if x.bl_idname == "NodeGroupOutput"]
            if _find(n.node_tree, outs, seen, test):
                return True
        for s in n.inputs:
            for link in s.links:
                if not link.is_muted:
                    stack.append(link.from_node)
    return False


def _surface_starts(mat):
    tree = getattr(mat, "node_tree", None)
    if tree is None:
        return None, []
    outs = [n for n in tree.nodes if n.bl_idname == "ShaderNodeOutputMaterial"]
    active = [n for n in outs if n.is_active_output and n.target in ("ALL", "CYCLES")]
    starts = []
    for n in active or outs[:1]:
        s = n.inputs.get("Surface")
        if s is not None:
            starts += [l.from_node for l in s.links if not l.is_muted]
    return tree, starts


def _input_on(node, *keys):
    """The first existing input among ``keys`` is driven by a link or above 0."""
    for key in keys:
        s = node.inputs.get(key)
        if s is not None:
            return s.is_linked or s.default_value > 0
    return False


def _refracts(n):
    return n.bl_idname in GLASS_NODES or (
        n.bl_idname == "ShaderNodeBsdfPrincipled" and _input_on(n, "Transmission Weight", "Transmission"))


def _metal(n):
    return n.bl_idname in MIRROR_NODES or (
        n.bl_idname == "ShaderNodeBsdfPrincipled" and _input_on(n, "Metallic"))


def detect_glass(mat):
    """True when the material's surface shader refracts (Glass / Refraction BSDF, or Principled
    with Transmission above 0 or driven by a texture). Node groups are followed."""
    tree, starts = _surface_starts(mat)
    return tree is not None and _find(tree, starts, set(), _refracts)


def is_glass(mat, settings):
    """Detected as glass and not marked Not Glass (the Glass Collection / matte_role work per object)."""
    return (settings.use_glass and settings.detect_glass and not getattr(mat, "cryptoray_not_glass", False)
            and detect_glass(mat))


def detect_chrome(mat):
    """True when the surface shader has metal in it: a Glossy / Anisotropic / Metallic BSDF, or a
    Principled BSDF with Metallic above 0 or driven by a texture, anywhere in the shader (mixes and
    node groups included). Any roughness: the ID layer uses the same shader, so reflections blur alike."""
    tree, starts = _surface_starts(mat)
    return tree is not None and _find(tree, starts, set(), _metal)


def is_chrome(mat, settings):
    """Detected as chrome and not marked Not Chrome. Glass wins over chrome."""
    return (settings.use_chrome and settings.detect_chrome and not getattr(mat, "cryptoray_not_chrome", False)
            and not is_glass(mat, settings) and detect_chrome(mat))


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


# --------------------------------------------------------------------------
# ID-layer copies of glass and chrome materials
# --------------------------------------------------------------------------

def _set_input(tree, node, key, value):
    """Unlink the input and set its value (missing inputs are skipped)."""
    s = node.inputs.get(key)
    if s is None:
        return
    for l in list(s.links):
        tree.links.remove(l)
    s.default_value = value


def _replace(tree, node, idname, carry=()):
    """Put a new ``idname`` node where ``node`` was: same output links, the ``carry`` inputs copied."""
    new = tree.nodes.new(idname)
    new.location = node.location
    for key in carry:
        a, b = node.inputs.get(key), new.inputs.get(key)
        if a is None or b is None:
            continue
        try:
            b.default_value = a.default_value
        except (AttributeError, TypeError, ValueError):
            pass
        for l in a.links:
            tree.links.new(l.from_socket, b)
    for l in list(node.outputs[0].links):
        tree.links.new(new.outputs[0], l.to_socket)
    tree.nodes.remove(node)
    return new


def _emission(tree, node, strength):
    """Replace ``node`` with a white emission of ``strength`` (0 = black, no light)."""
    e = _replace(tree, node, "ShaderNodeEmission")
    e.inputs["Color"].default_value = (1, 1, 1, 1)
    e.inputs["Strength"].default_value = strength
    return e


def _source(tree, node, key):
    """(linked socket or None, value) of an input."""
    s = node.inputs.get(key)
    if s is None:
        return None, None
    return (s.links[0].from_socket if s.is_linked else None), s.default_value


def _feed(tree, sock, src):
    link, value = src
    if link is not None:
        tree.links.new(link, sock)
    elif value is not None:
        sock.default_value = value


def _glossy_path(tree, cache):
    """1 on rays that have bounced off something glossy (chrome seeing the glass), else 0."""
    if tree not in cache:
        lp = tree.nodes.new("ShaderNodeLightPath")
        gt = tree.nodes.new("ShaderNodeMath")
        gt.operation = "GREATER_THAN"
        gt.inputs[1].default_value = 0.5
        tree.links.new(lp.outputs["Glossy Depth"], gt.inputs[0])
        lp.location, gt.location = (-900, 600), (-700, 600)
        cache[tree] = gt.outputs[0]
    return cache[tree]


def _whiten(tree, src, glossy):
    """``src`` (link, value) colour, white on glossy rays: glass tint never reaches chrome's reflection."""
    mix = tree.nodes.new("ShaderNodeMix")
    mix.data_type = "RGBA"
    tree.links.new(glossy, mix.inputs[0])
    a = next(i for i in mix.inputs if i.identifier == "A_Color")
    b = next(i for i in mix.inputs if i.identifier == "B_Color")
    _feed(tree, a, src)
    b.default_value = (1, 1, 1, 1)
    return next(o for o in mix.outputs if o.identifier == "Result_Color"), None


_NODE_BASE = set(bpy.types.ShaderNode.bl_rna.properties.keys())


def _split(tree, n, glossy):
    """Keep ``n`` for camera/refraction rays and return a duplicate used on glossy rays only (the
    caller strips the duplicate): Mix(n, duplicate) by the glossy-ray factor."""
    dup = tree.nodes.new(n.bl_idname)
    dup.location = (n.location.x, n.location.y - 900)
    for p in n.bl_rna.properties:
        if p.identifier not in _NODE_BASE and not p.is_readonly:
            try:
                setattr(dup, p.identifier, getattr(n, p.identifier))
            except (AttributeError, TypeError, ValueError):
                pass
    for a, b in zip(n.inputs, dup.inputs):
        try:
            b.default_value = a.default_value
        except (AttributeError, TypeError, ValueError):
            pass
        for l in a.links:
            tree.links.new(l.from_socket, b)
    mix = tree.nodes.new("ShaderNodeMixShader")
    mix.location = (n.location.x + 250, n.location.y - 450)
    for l in list(n.outputs[0].links):
        tree.links.new(mix.outputs[0], l.to_socket)
    tree.links.new(glossy, mix.inputs[0])
    tree.links.new(n.outputs[0], mix.inputs[1])
    tree.links.new(dup.outputs[0], mix.inputs[2])
    return dup


def _with_tir(tree, refr):
    """Refraction BSDF -> Mix(refr, white Glossy) where real glass reflects everything: inside the glass,
    past the critical angle (total internal reflection). A refraction-only shader kills those rays, so
    thick glass (a bottle neck seen from above) would go black in the ID layer. This isn't a reflection
    of the outside world, so the matte stays solid."""
    outs = [l.to_socket for l in refr.outputs[0].links]
    x, y = refr.location
    fres = tree.nodes.new("ShaderNodeFresnel")        # for backfacing it uses 1/IOR: 1 past the critical angle
    _feed(tree, fres.inputs["IOR"], _source(tree, refr, "IOR"))
    _feed(tree, fres.inputs["Normal"], _source(tree, refr, "Normal"))
    full = tree.nodes.new("ShaderNodeMath")
    full.operation = "GREATER_THAN"
    full.inputs[1].default_value = 0.999
    tree.links.new(fres.outputs[0], full.inputs[0])
    geo = tree.nodes.new("ShaderNodeNewGeometry")
    inside = tree.nodes.new("ShaderNodeMath")
    inside.operation = "MULTIPLY"
    tree.links.new(full.outputs[0], inside.inputs[0])
    tree.links.new(geo.outputs["Backfacing"], inside.inputs[1])
    gl = tree.nodes.new("ShaderNodeBsdfGlossy")
    gl.inputs["Color"].default_value = (1, 1, 1, 1)
    _feed(tree, gl.inputs["Roughness"], _source(tree, refr, "Roughness"))
    _feed(tree, gl.inputs["Normal"], _source(tree, refr, "Normal"))
    mix = tree.nodes.new("ShaderNodeMixShader")
    tree.links.new(inside.outputs[0], mix.inputs[0])
    tree.links.new(refr.outputs[0], mix.inputs[1])
    tree.links.new(gl.outputs[0], mix.inputs[2])
    for i, node in enumerate((fres, full, geo, inside, gl)):
        node.location = (x - 200, y - 200 - 150 * i)
    mix.location = (x + 180, y)
    for sock in outs:
        tree.links.new(mix.outputs[0], sock)
    return mix


def _strip_principled(tree, n, glossy):
    """Principled glass -> Mix(Principled without transmission or reflection, Refraction BSDF) by
    Transmission Weight (then Alpha). A black Specular Tint alone isn't enough: it removes the outside
    reflection, but light still reflects inside the glass (and Specular IOR Level 0 does nothing on
    transmissive Principled in Blender 5.0)."""
    trans_key = "Transmission Weight" if "Transmission Weight" in n.inputs else "Transmission"
    trans = _source(tree, n, trans_key)
    alpha = _source(tree, n, "Alpha")
    outs = [l.to_socket for l in n.outputs[0].links]
    refr = tree.nodes.new("ShaderNodeBsdfRefraction")
    refr.location = (n.location.x, n.location.y - 600)
    for a, b in (("Base Color", "Color"), ("Roughness", "Roughness"), ("IOR", "IOR"), ("Normal", "Normal")):
        if b in refr.inputs:
            src = _source(tree, n, a)
            _feed(tree, refr.inputs[b], _whiten(tree, src, glossy) if b == "Color" else src)
    _set_input(tree, n, trans_key, 0.0)
    _set_input(tree, n, "Specular Tint", (0, 0, 0, 1))
    _set_input(tree, n, "Coat Weight", 0.0)
    _set_input(tree, n, "Sheen Weight", 0.0)
    _set_input(tree, n, "Alpha", 1.0)
    mix = tree.nodes.new("ShaderNodeMixShader")
    mix.location = (n.location.x + 300, n.location.y)
    _feed(tree, mix.inputs[0], trans)
    tree.links.new(n.outputs[0], mix.inputs[1])
    tree.links.new(refr.outputs[0], mix.inputs[2])
    _with_tir(tree, refr)
    last = mix
    if alpha[0] is not None or (alpha[1] is not None and alpha[1] < 1.0):
        cut = tree.nodes.new("ShaderNodeMixShader")
        cut.location = (n.location.x + 500, n.location.y)
        _feed(tree, cut.inputs[0], alpha)
        tree.links.new(tree.nodes.new("ShaderNodeBsdfTransparent").outputs[0], cut.inputs[1])
        tree.links.new(mix.outputs[0], cut.inputs[2])
        last = cut
    for s in outs:
        tree.links.new(last.outputs[0], s)


def _strip_glass(tree, keep, cache):
    """No reflection, same refraction: transmissive Principled is split into its non-glass part and a
    Refraction BSDF, Glass BSDF becomes a Refraction BSDF, reflective BSDFs go black. The refraction is
    untinted on glossy rays. ``keep`` (Ignore Glass Reflections off): the real shader stays for camera and
    refraction rays and the stripped one is used only on glossy rays, so chrome reflecting the glass sees
    it the same way whatever the glass settings."""
    glossy = _glossy_path(tree, cache)
    for n in list(tree.nodes):
        if n.bl_idname == "ShaderNodeBsdfPrincipled":
            t = _split(tree, n, glossy) if keep else n
            if _refracts(t):
                _strip_principled(tree, t, glossy)
            else:
                _set_input(tree, t, "Specular Tint", (0, 0, 0, 1))
                _set_input(tree, t, "Coat Weight", 0.0)
                _set_input(tree, t, "Sheen Weight", 0.0)
        elif n.bl_idname == "ShaderNodeBsdfGlass":
            t = _split(tree, n, glossy) if keep else n
            dist = t.distribution
            col = _source(tree, t, "Color")
            r = _replace(tree, t, "ShaderNodeBsdfRefraction", ("Roughness", "IOR", "Normal"))
            _feed(tree, r.inputs["Color"], _whiten(tree, col, glossy))
            try:
                r.distribution = dist
            except TypeError:
                r.distribution = "GGX"
            _with_tir(tree, r)
        elif n.bl_idname in MIRROR_NODES:
            _emission(tree, _split(tree, n, glossy) if keep else n, 0.0)


def _chrome_tree(tree, own):
    """Metal keeps reflecting. What doesn't reflect (Principled's non-metal part, Diffuse and other
    matte BSDFs in a mix) emits the object's own ID when ``own``, else nothing. The material's own
    emission is switched off. Metal colours go white: a gold or tinted metal reflects at full strength,
    so its colour doesn't weaken the matte (with Ignore Glass Absorption off, coverage is raw light).
    Returns True when something emits."""
    white = (1, 1, 1, 1)
    emits = False
    for n in list(tree.nodes):
        if n.bl_idname in MIRROR_NODES:
            for key in ("Color", "Base Color", "Edge Tint"):
                _set_input(tree, n, key, white)
        if n.bl_idname == "ShaderNodeBsdfPrincipled":
            _set_input(tree, n, "Base Color", white)
            _set_input(tree, n, "Specular Tint", white)      # metal edge tint
            _set_input(tree, n, "Emission Color", white)
            strength = n.inputs.get("Emission Strength")
            metal = n.inputs.get("Metallic")
            _set_input(tree, n, "Emission Strength", 0.0)
            if own and strength is not None and metal is not None:
                if metal.is_linked:
                    inv = tree.nodes.new("ShaderNodeMath")
                    inv.operation = "SUBTRACT"
                    inv.use_clamp = True
                    inv.inputs[0].default_value = 1.0
                    inv.location = (n.location.x - 200, n.location.y - 400)
                    tree.links.new(metal.links[0].from_socket, inv.inputs[1])
                    tree.links.new(inv.outputs[0], strength)
                    emits = True
                elif metal.default_value < 1.0:
                    strength.default_value = 1.0 - max(0.0, metal.default_value)
                    emits = True
        elif n.bl_idname in MATTE_NODES:
            _emission(tree, n, 1.0 if own else 0.0)
            emits = emits or own
        elif n.bl_idname in ("ShaderNodeEmission", "ShaderNodeBackground"):
            _set_input(tree, n, "Strength", 0.0)
    return emits


def _id_copy(mat, kind):
    """A tagged copy of ``mat`` for the ID layer (node groups it uses are copied too).
    kind: 'glass', 'chrome' (own ID where not metal) or 'mirror'. Returns (copy, emits)."""
    m = _tag(mat.copy())
    m.name = "CR_%s_%s" % (kind, mat.name)
    m[COPY_PROP] = mat.name_full
    m[KIND_PROP] = kind
    emits = False
    if m.node_tree is not None:
        groups, cache = {}, {}
        trees = [m.node_tree]
        while trees:
            t = trees.pop()
            for n in t.nodes:
                if n.bl_idname == "ShaderNodeGroup" and n.node_tree is not None:
                    g = groups.get(n.node_tree)
                    if g is None:
                        g = groups[n.node_tree] = _tag(n.node_tree.copy())
                        trees.append(g)
                    n.node_tree = g
            if kind in ("glass", "glass_refl"):
                _strip_glass(t, kind == "glass_refl", cache)
            else:
                emits = _chrome_tree(t, kind == "chrome") or emits
    if kind in ("chrome", "mirror") and m.node_tree is not None:
        # marks the pixels where the camera sees chrome (AOVs are written on camera rays only)
        out = m.node_tree.nodes.new("ShaderNodeOutputAOV")
        out.aov_name = core.CHROME_AOV
        out.inputs["Value"].default_value = 1.0
        out.location = (300, -400)
    m["cryptoray_emits"] = emits
    return m, emits


def copy_sources():
    """Names of the original materials that have ID-layer copies."""
    return {m[COPY_PROP] for m in bpy.data.materials if is_ours(m) and COPY_PROP in m and m.users}


def _copy_groups(mat):
    """Our shader node-group copies used (directly or nested) by a material copy."""
    found, trees = set(), [mat.node_tree] if mat.node_tree is not None else []
    while trees:
        for n in trees.pop().nodes:
            g = getattr(n, "node_tree", None) if n.bl_idname == "ShaderNodeGroup" else None
            if g is not None and is_ours(g) and g not in found:
                found.add(g)
                trees.append(g)
    return found


def refresh_copies(sources=None):
    """Re-copy ID-layer materials from their originals (all, or those whose original's name is in
    ``sources``), so material edits reach the ID layer. Runs shortly after a glass/chrome material
    is edited, and when a CryptoRay render starts."""
    for old in [m for m in bpy.data.materials if is_ours(m) and COPY_PROP in m]:
        if sources is not None and old[COPY_PROP] not in sources:
            continue
        src = bpy.data.materials.get(old[COPY_PROP])
        if src is None or old.users == 0:
            continue
        name = old.name
        groups = _copy_groups(old)
        new, _ = _id_copy(src, old[KIND_PROP])
        old.user_remap(new)
        bpy.data.materials.remove(old)
        new.name = name
        for g in groups:
            if g.users == 0:
                bpy.data.node_groups.remove(g)


def _swap_group(swap):
    """Geometry Nodes group that replaces every original glass/chrome material with its ID copy."""
    g = _tag(bpy.data.node_groups.new("CR_material_swap", "GeometryNodeTree"))
    g.interface.new_socket("Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    g.interface.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    n, l = g.nodes, g.links
    gi, go = n.new("NodeGroupInput"), n.new("NodeGroupOutput")
    # instances (Geometry Nodes, collection instances) are realized so their materials get swapped too
    rl = n.new("GeometryNodeRealizeInstances")
    l.new(gi.outputs[0], rl.inputs["Geometry"])
    geo = rl.outputs[0]
    for i, (orig, copy) in enumerate(sorted(swap.items(), key=lambda x: x[0].name_full)):
        r = n.new("GeometryNodeReplaceMaterial")
        r.inputs["Old"].default_value = orig
        r.inputs["New"].default_value = copy
        r.location = (200 * (i + 2), 0)
        l.new(geo, r.inputs["Geometry"])
        geo = r.outputs[0]
    l.new(geo, go.inputs[0])
    go.location = (200 * (len(swap) + 2), 0)
    return g


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

class _Roles(dict):
    """{object name: 'glass' | 'chrome'} plus ``off``: the roles switched off (Glass / Chrome unticked)."""
    off = frozenset()


def _glass_names(settings):
    """{object name: 'glass' | 'chrome'} from the Glass and Chrome Collections (glass wins).
    Glass / Chrome switched off, or their collection toggle off, leaves that collection out."""
    roles = _Roles()
    roles.off = frozenset(r for r, on in (("glass", settings.use_glass), ("chrome", settings.use_chrome))
                          if not on)
    for coll, role, on in ((settings.chrome_collection, "chrome", settings.use_chrome_collection),
                           (settings.glass_collection, "glass", settings.use_glass_collection)):
        if coll is not None and on and role not in roles.off:
            roles.update((o.name_full, role) for o in coll.all_objects)
    return roles


def _role(obj, glass_names):
    """matte_role first, then the Glass / Chrome Collections. A matte_role of a switched-off
    role (Glass / Chrome unticked) counts as no role: the object gets its own ID."""
    role = str(obj.get("matte_role", "")).lower()
    if role in getattr(glass_names, "off", ()):
        return ""
    if role in ("glass", "chrome", "ignore", "other"):
        return role
    return glass_names.get(obj.name_full, "")


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


def settings_problem(settings):
    """Why the ID settings can't be built as they are (None when fine). Shown in the panel's IDs box."""
    names = [n for _, n in requested_layers(settings)]
    for n in names:
        if not core.valid_layer_name(n):
            return "Cryptomatte layer name %r: use letters, digits and _ only." % n
    if len(set(names)) != len(names):
        return "The Object and Material layers need different names."
    if settings.scope == "COLLECTION" and settings.target_collection is None:
        return "Choose the target collection (or set Targets to All)."
    return None


def analyse(context, scene, settings):
    """Returns dict with targets, glass, others, warnings. Raises BuildError for blockers."""
    src_layer = context.view_layer if context.view_layer and not is_ours(context.view_layer) else beauty_layers(scene)[0]
    roots = root_objects(scene)
    if roots:
        raise BuildError("Objects directly in the Scene Collection can't be kept out of the ID layer: %s. "
                         "Use 'Move Root Objects' first." % ", ".join(o.name for o in roots[:6]))
    problem = settings_problem(settings)
    if problem:
        raise BuildError(problem)
    glass_names = _glass_names(settings)
    scope = None
    if settings.scope == "COLLECTION":
        scope = {o.name_full for o in settings.target_collection.all_objects}
    targets, glass, chrome, others, warnings = [], [], [], [], []
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
        elif role == "chrome":
            chrome.append(item)
        elif role == "other" or (scope is not None and obj.name_full not in scope):
            others.append(item)
        else:
            targets.append(item)
    if not targets:
        raise BuildError("No target objects found in view layer '%s'." % src_layer.name)
    return {"source_layer": src_layer, "targets": targets, "glass": glass, "chrome": chrome,
            "others": others, "warnings": warnings}


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
    # shared datablocks: only when nothing uses them any more (another built scene might). Twice:
    # material copies use copied shader groups, and the swap group uses the material copies.
    for _ in range(2):
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
        self.chrome_cache = {}
        self.swap = {}             # original glass/chrome material -> its ID-layer copy
        self.keep_proxies = []     # proxies that keep their materials (swapped by CR_material_swap)
        self.other = False
        self.n = 0

    # ---- helpers ----
    def glass(self, mat):
        g = self.glass_cache.get(mat)
        if g is None:
            g = self.glass_cache[mat] = is_glass(mat, self.s)
            if g:
                self.glass_copy(mat)
        return g

    def chrome(self, mat):
        c = self.chrome_cache.get(mat)
        if c is None:
            c = self.chrome_cache[mat] = not self.glass(mat) and is_chrome(mat, self.s)
            if c:
                self.chrome_copy(mat)
        return c

    def glass_copy(self, mat):
        """The ID layer uses a copy of the glass without its reflection (with Ignore Glass Reflections off,
        only where chrome reflects the glass)."""
        if mat is not None and mat not in self.swap:
            self.swap[mat], _ = _id_copy(mat, "glass" if self.s.ignore_reflections else "glass_refl")

    def chrome_copy(self, mat):
        if mat is not None and mat not in self.swap:
            self.swap[mat], _ = _id_copy(
                mat, "mirror" if self.s.pure_chrome else "chrome")

    def how(self, mat):
        """'keep' (glass), 'chrome' (real shader, reflects IDs) or 'emit' (gets an ID)."""
        return "keep" if self.glass(mat) else "chrome" if self.chrome(mat) else "emit"

    def material(self, how):
        """Override material for a proxy part (None: keep the material, swapped for its ID copy)."""
        return self.white if how == "emit" else None

    def lightgroup(self, obj_name, mat, how):
        """Emitting parts get the ID's group. Chrome does too unless Treat as Pure Chrome: its non-metal parts
        emit the object's own ID (a group even when nothing emits yet: material edits are picked up at
        render time, see refresh_copies)."""
        if how == "emit" or (how == "chrome" and not self.s.pure_chrome):
            return self.group(obj_name, mat)
        return ""

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
        """How to build ``obj``: [(material or None, 'emit'|'keep'|'chrome')], None = the whole object.
        Chrome parts always name their material: its own-ID light group depends on it."""
        if not mats:
            return [(None, "emit")]
        hows = {m: self.how(m) for m in mats}
        solid = sorted((m for m in mats if hows[m] == "emit"), key=lambda m: m.name_full)
        special = sorted((m for m in mats if hows[m] != "emit"), key=lambda m: m.name_full)
        if not solid:
            if all(hows[m] == "keep" for m in special):
                return [(None, "keep")]
            return [(m, hows[m]) for m in special]
        if not special and (len(solid) == 1 or not self.s.use_material):
            return [(solid[0] if len(solid) == 1 else None, "emit")]
        return [(m, "emit") for m in solid] + [(m, hows[m]) for m in special]

    def proxy(self, obj, part, collection, label, other=False):
        mat, how = part
        p = _proxy(obj, label, collection)
        emit = self.material(how)
        if mat is None:
            grp = _node_group("emit" if emit else "keep")
            inputs = {"Source": obj, "Emit": emit} if emit else {"Source": obj}
        else:
            grp = _node_group("matsel" if emit else "keepsel")
            inputs = {"Source": obj, "Only": mat}
            if emit:
                inputs["Emit"] = emit
        _attach(p, grp, **inputs)
        if emit is None:
            self.keep_proxies.append(p)
        if other:      # no ID: light that would carry one counts towards the total only
            glows = how == "emit" or (how == "chrome" and not self.s.pure_chrome)
            p.lightgroup = core.OTHER_GROUP if glows else ""
        else:
            p.lightgroup = self.lightgroup(obj.name_full, mat, how)
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
                slot.material = self.material(how) or self.clear_mat
        elif how in ("keep", "chrome"):
            self.swap_slots(c)
        c.lightgroup = self.lightgroup(obj.name_full, mat, how)
        self.n += 1
        return c

    def swap_slots(self, c):
        """Object-level slot overrides: original glass/chrome materials -> their ID copies."""
        for slot in c.material_slots:
            m = slot.material
            if m is not None and m in self.swap:
                slot.link = "OBJECT"
                slot.material = self.swap[m]

    def finish(self):
        """Swap materials on the proxies that keep theirs."""
        if not self.swap or not self.keep_proxies:
            return
        g = _swap_group(self.swap)
        for p in self.keep_proxies:
            m = p.modifiers.new("CryptoRay swap", "NODES")
            m.node_group = g

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
        """No ID. matte_role = "other": a plain blocker. Outside the Target Collection: glass still
        refracts and chrome still reflects (their parts are built as usual); the rest blocks."""
        self.other = True
        if _role(obj, self.glass_names) != "other":
            for part in self.parts(obj, self._mats(obj)):
                if part[1] == "emit":
                    continue
                label = "CR_other_%s|%s" % (obj.name, part[0].name if part[0] is not None else "")
                p = self.proxy(obj, part, self.coll, label, other=True)
                p.visible_camera = p.visible_camera and cam
            emits = [part for part in self.parts(obj, self._mats(obj)) if part[1] == "emit"]
            if not emits:
                return
            if emits != [(None, "emit")]:
                for mat, _ in emits:
                    p = _proxy(obj, "CR_other_%s|%s" % (obj.name, mat.name), self.coll)
                    _attach(p, _node_group("matsel"), Source=obj, Only=mat, Emit=self.white)
                    p.visible_camera = p.visible_camera and cam
                    p.lightgroup = core.OTHER_GROUP
                return
        p = _proxy(obj, "CR_other_%s" % obj.name, self.coll)
        _attach(p, _node_group("emit"), Source=obj, Emit=self.white)
        p.visible_camera = p.visible_camera and cam
        p.lightgroup = core.OTHER_GROUP

    def _mats(self, obj):
        return sorted(_object_materials(self.depsgraph, obj), key=lambda m: m.name_full)

    def glass_object(self, obj, cam):
        """matte_role = "glass" / Glass Collection: every material refracts as it is."""
        for m in self._mats(obj):
            self.glass_copy(m)
        p = _proxy(obj, "CR_glass_%s" % obj.name, self.coll)
        _attach(p, _node_group("keep"), Source=obj)
        self.keep_proxies.append(p)
        p.visible_camera = p.visible_camera and cam

    def chrome_parts(self, obj):
        """matte_role = "chrome": every material reflects, detected as chrome or not."""
        mats = self._mats(obj)
        for m in mats:
            self.chrome_copy(m)
        return [(m, "chrome") for m in mats]

    def chrome_object(self, obj, cam):
        parts = self.chrome_parts(obj)
        if not parts:
            self.warnings.append("'%s' has no material to reflect with: it gets its own ID." % obj.name)
            return self.target(obj, cam)
        for part in parts:
            p = self.proxy(obj, part, self.coll, "CR_chrome_%s|%s" % (obj.name, part[0].name))
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
            how = "chrome" if mat is not None and self.chrome(mat) else "emit"
            c, st = self._emitter_copy(obj, cam, index, base)
            for slot in c.material_slots:      # object-level override on the copy only
                slot.link = "OBJECT"
                slot.material = self.swap[mat] if how == "chrome" else self.white
            c.lightgroup = self.lightgroup(obj.name_full, mat, how)
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
                for m in self._mats(o):
                    self.glass_copy(m)
                plans[o] = [(None, "keep")]
            elif role == "chrome":
                plans[o] = self.chrome_parts(o) or [(None, "emit")]
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
    vl.use = bool(requested_layers(settings))    # no IDs ticked: built, but nothing to render
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
    aov = vl.aovs.add()
    aov.name, aov.type = core.CHROME_AOV, "VALUE"

    b = _Builder(scene, settings, depsgraph, vl, coll, sources)
    for obj, cam in info["targets"]:
        b.target(obj, cam)
    for obj, cam in info["others"]:
        b.other_object(obj, cam)
    for obj, cam in info["glass"]:
        b.glass_object(obj, cam)
    for obj, cam in info["chrome"]:
        b.chrome_object(obj, cam)

    b.finish()
    # Reflections (on chrome, and on glass unless its copy has none) see the proxies and the world.
    # Nothing is visible to diffuse rays: light bouncing off the non-metal part of a chrome material
    # would otherwise carry the IDs of its neighbours.
    world.cycles_visibility.glossy = True
    world.cycles_visibility.diffuse = False
    for o in list(coll.objects) + list(sources.all_objects):
        if is_ours(o):
            o.visible_diffuse = False
    lost = []

    entries = [{"group": g, "object": k[0], "material": k[1]} for k, g in sorted(b.entries.items(), key=lambda x: x[1])]
    if not entries and requested_layers(settings):   # no IDs ticked: an empty build is fine
        clear(scene)
        raise BuildError("Nothing gets an ID (everything is glass or chrome, or has no material in Material-only mode).")
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
    man["chrome_materials"] = sorted(m.name_full for m, c in b.chrome_cache.items() if c)
    man["glass_check"] = _slot_glass(scene, settings)
    vl[MANIFEST_PROP] = json.dumps(man)
    warnings = list(info["warnings"]) + b.warnings
    groups = len(entries)
    if groups > settings.group_warning:
        warnings.append("%d light groups: each is a full-resolution float pass; watch GPU memory "
                        "(use the Collection target to limit IDs)." % groups)
    counts = {kind: len(core.layer_names(man, kind)) for kind, _ in layers}
    return {"ids": counts, "groups": groups, "proxies": b.n, "glass": len(info["glass"]),
            "glass_materials": man["glass_materials"], "chrome": len(info["chrome"]),
            "chrome_materials": man["chrome_materials"], "others": len(info["others"]), "warnings": warnings}


def _sources(info):
    return sorted({o.name_full for o, _ in info["targets"] + info["glass"] + info["chrome"] + info["others"]})


def _slot_glass(scene, settings):
    """Glass and chrome materials in the scene's object slots (cheap staleness check for Not Glass /
    Not Chrome edits)."""
    mats = {sl.material for o in scene.objects for sl in o.material_slots
            if sl.material is not None and not is_ours(sl.material)}
    return (sorted(m.name_full for m in mats if is_glass(m, settings))
            + sorted("chrome:" + m.name_full for m in mats if is_chrome(m, settings)))


def settings_signature(s):
    return [bool(s.use_object), bool(s.use_material), [n for _, n in requested_layers(s)], s.scope,
            s.target_collection.name_full if s.target_collection else "",
            s.glass_collection.name_full if s.glass_collection else "", bool(s.detect_glass),
            bool(s.ignore_reflections), bool(s.detect_chrome), bool(s.pure_chrome),
            s.chrome_collection.name_full if s.chrome_collection else "",
            bool(s.use_glass), bool(s.use_chrome), bool(s.use_glass_collection), bool(s.use_chrome_collection)]


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
        return "glass or chrome materials changed"
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
