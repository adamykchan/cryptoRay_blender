"""CryptoRay test scene (SPEC acceptance scene + edge cases).

Run in Blender's Text Editor (Run Script) or:
    blender --python tests/blender/make_test_scene.py

Creates scene "CryptoRay_Test":
  * Cube_L / Cube_C / Cube_R behind a glass sphere (IOR 1.45) - Cube_C moves up and down,
    Cube_R has two materials (red + blue) for the Material IDs
  * GlassBall in collection "Glass" (its material is detected as glass automatically)
  * Bottle: one object with a green-tinted glass body and a paper "Label" material
  * Instancers: a collection instance, a Geometry Nodes scatter, a hair curves object, and a legacy
    hair particle system instancing cones
  * Camera steps around the ball over 7 frames; in each, the ball fully covers one element
  * Blender's own Cryptomatte passes are on in the Beauty layer, for comparison
  * Multilayer EXR output (32-bit ZIP) to //render/beauty_####.exr
Set ROUGHNESS below to 0.3 for the rough-glass acceptance test, or N_EXTRA to add many cubes.
"""
import math

import bmesh
import bpy

ROUGHNESS = 0.0
N_EXTRA = 0          # e.g. 47 -> 50 target objects for the stress test
# Camera orbit angles (degrees), one frame each: every angle puts the glass ball fully in front of
# one element (Cube_C stays behind it throughout). Measured on this scene.
ORBIT = [(22.5, "Cube_L"), (60.0, "ConeInstance"), (138.75, "Hair"), (206.25, "Bottle"),
         (228.75, "ParticleEmitter"), (303.75, "ConeScatter"), (337.5, "Cube_R")]
FRAMES = len(ORBIT)
SAMPLES = 256
SCENE = "CryptoRay_Test"


def _fcurves(obj):
    """F-curves of an object's action (layered actions in Blender 4.4+/5.x, plain before)."""
    act = obj.animation_data.action
    try:
        from bpy_extras import anim_utils
        bag = anim_utils.action_get_channelbag_for_slot(act, obj.animation_data.action_slot)
        return list(bag.fcurves) if bag else []
    except (ImportError, AttributeError):
        return list(act.fcurves)


def material(name, color=None, glass=False, rough=0.0, tint=(1, 1, 1)):
    m = bpy.data.materials.get(name) or bpy.data.materials.new(name)
    m.use_nodes = True
    b = m.node_tree.nodes.get("Principled BSDF")
    if glass:
        b.inputs["Base Color"].default_value = (*tint, 1)
        b.inputs["Transmission Weight"].default_value = 1.0
        b.inputs["IOR"].default_value = 1.45
        b.inputs["Roughness"].default_value = rough
    else:
        b.inputs["Base Color"].default_value = (*color, 1)
        b.inputs["Roughness"].default_value = 0.5
    return m


def mesh_object(name, make, coll, loc, mats=()):
    me = bpy.data.meshes.new(name)
    bm = bmesh.new()
    make(bm)
    bm.to_mesh(me)
    bm.free()
    o = bpy.data.objects.new(name, me)
    o.location = loc
    if coll is not None:
        coll.objects.link(o)
    for m in mats:
        me.materials.append(m)
    return o


def collection(scene, name):
    c = bpy.data.collections.new(name)
    scene.collection.children.link(c)
    return c


def main():
    old = bpy.data.scenes.get(SCENE)
    sc = bpy.data.scenes.new(SCENE + "_new")
    for win in bpy.context.window_manager.windows:
        win.scene = sc
    if old:
        bpy.data.scenes.remove(old)
        bpy.data.orphans_purge(do_local_ids=True, do_linked_ids=False, do_recursive=True)  # its objects etc.
    sc.name = SCENE
    sc.render.engine = "CYCLES"
    sc.cycles.device = "GPU"
    sc.cycles.samples = SAMPLES
    sc.cycles.use_adaptive_sampling = False
    sc.cycles.use_denoising = True       # beauty only; the ID layer turns it off
    sc.render.resolution_x, sc.render.resolution_y = 640, 360
    sc.frame_start = 1
    sc.frame_end = FRAMES
    sc.render.filepath = "//render/beauty_####"
    im = sc.render.image_settings
    if hasattr(im, "media_type"):
        im.media_type = "MULTI_LAYER_IMAGE"
    else:
        im.file_format = "OPEN_EXR_MULTILAYER"
    im.color_depth = "32"
    im.exr_codec = "ZIP"
    sc.view_layers[0].name = "Beauty"
    sc.view_layers[0].use_pass_cryptomatte_object = True      # Blender's own, for comparison
    sc.view_layers[0].use_pass_cryptomatte_material = True

    w = bpy.data.worlds.new("TestWorld")
    w.use_nodes = True
    bg = w.node_tree.nodes["Background"]
    bg.inputs["Color"].default_value = (0.35, 0.42, 0.5, 1)
    sc.world = w

    props, glass, lights, cam, inst = (collection(sc, n) for n in ("Props", "Glass", "Lights", "Camera", "Instancers"))
    red = material("Red", (0.8, 0.08, 0.08))
    green = material("Green", (0.1, 0.7, 0.1))
    blue = material("Blue", (0.1, 0.15, 0.85))
    yellow = material("Yellow", (0.85, 0.75, 0.1))
    cube = lambda bm: bmesh.ops.create_cube(bm, size=1.0)

    mesh_object("Cube_L", cube, props, (-1.3, 3, 0), [red])
    cc = mesh_object("Cube_C", cube, props, (0, 3.5, -0.8), [green])
    for f, z in ((1, -0.8), (FRAMES, 0.8)):              # rises over the shot
        cc.location.z = z
        cc.keyframe_insert("location", index=2, frame=f)
    cr = mesh_object("Cube_R", cube, props, (1.3, 3, 0), [red, blue])
    for i, p in enumerate(cr.data.polygons):
        p.material_index = i % 2
    for i in range(N_EXTRA):
        x, z = (i % 10 - 4.5) * 0.45, (i // 10 - 2) * 0.45
        mesh_object("Extra_%03d" % i, lambda bm: bmesh.ops.create_cube(bm, size=0.3), props, (x, 5.5, z), [yellow])

    ball = mesh_object("GlassBall", lambda bm: bmesh.ops.create_uvsphere(bm, u_segments=64, v_segments=32, radius=1.3),
                       glass, (0, 0, 0), [material("Glass", glass=True, rough=ROUGHNESS)])
    for p in ball.data.polygons:
        p.use_smooth = True

    # one object, two materials: glass body (detected automatically) + paper label (gets an ID)
    bottle = mesh_object("Bottle", lambda bm: bmesh.ops.create_uvsphere(bm, u_segments=32, v_segments=16, radius=0.4),
                         props, (1.1, -2.4, -1.0), [material("BottleGlass", glass=True, tint=(0.35, 0.75, 0.4)), material("Label", (0.9, 0.85, 0.7))])
    bottle.scale.z = 1.5
    for p in bottle.data.polygons:
        p.use_smooth = True
        p.material_index = 1 if abs(p.center.z) < 0.13 else 0

    ld = bpy.data.lights.new("Key", "AREA")
    ld.energy, ld.size = 400, 3
    lo = bpy.data.objects.new("Key", ld)
    lo.location, lo.rotation_euler = (3, -3, 5), (math.radians(40), 0, math.radians(45))
    lights.objects.link(lo)
    fl = bpy.data.objects.new("Floor", bpy.data.meshes.new("Floor"))
    bm = bmesh.new()
    bmesh.ops.create_grid(bm, x_segments=1, y_segments=1, size=8)
    bm.to_mesh(fl.data)
    bm.free()
    fl.location.z = -1.6
    fl.data.materials.append(material("FloorGrey", (0.5, 0.5, 0.5)))
    props.objects.link(fl)

    cd = bpy.data.cameras.new("Cam")
    cd.lens = 32
    co = bpy.data.objects.new("Cam", cd)
    co.location, co.rotation_euler = (0, -6.5, 0.4), (math.radians(88), 0, 0)
    rig = bpy.data.objects.new("CamOrbit", None)         # camera orbits the ball once
    co.parent = rig
    for f, (angle, _) in enumerate(ORBIT, start=1):
        rig.rotation_euler.z = math.radians(angle)
        rig.keyframe_insert("rotation_euler", index=2, frame=f)
    for fc in _fcurves(rig):
        for kp in fc.keyframe_points:
            kp.interpolation = "LINEAR"
    cam.objects.link(rig)
    cam.objects.link(co)
    sc.camera = co

    # instancing cases
    src = bpy.data.collections.new("ConeSource")              # not linked to the scene
    cone = mesh_object("Cone", lambda bm: bmesh.ops.create_cone(bm, cap_ends=True, segments=16, radius1=0.25,
                                                                 radius2=0, depth=0.5), None, (0, 0, 0), [blue])
    src.objects.link(cone)
    e = bpy.data.objects.new("ConeInstance", None)
    e.instance_type, e.instance_collection, e.location = "COLLECTION", src, (-2.6, 1.5, -1.0)
    inst.objects.link(e)

    sc_obj = mesh_object("ConeScatter", lambda bm: bmesh.ops.create_grid(bm, x_segments=2, y_segments=2, size=0.5),
                         inst, (2.6, 1.5, -1.2), [green])
    ng = bpy.data.node_groups.new("Scatter", "GeometryNodeTree")
    ng.interface.new_socket("Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    ng.interface.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    gi, go = ng.nodes.new("NodeGroupInput"), ng.nodes.new("NodeGroupOutput")
    iop, oi = ng.nodes.new("GeometryNodeInstanceOnPoints"), ng.nodes.new("GeometryNodeObjectInfo")
    oi.inputs["Object"].default_value = cone
    oi.inputs["As Instance"].default_value = True
    ng.links.new(gi.outputs[0], iop.inputs["Points"])
    ng.links.new(oi.outputs["Geometry"], iop.inputs["Instance"])
    ng.links.new(iop.outputs["Instances"], go.inputs[0])
    sc_obj.modifiers.new("Scatter", "NODES").node_group = ng

    hc = bpy.data.hair_curves.new("Hair")
    hc.add_curves([6] * 7)
    for i in range(7):
        for j in range(6):
            hc.position_data[i * 6 + j].vector = (-0.45 + 0.15 * i, 0, j * 0.1)
    rad = hc.attributes.get("radius") or hc.attributes.new("radius", "FLOAT", "POINT")
    for v in rad.data:
        v.value = 0.025
    hc.materials.append(yellow)
    ho = bpy.data.objects.new("Hair", hc)
    ho.location = (-1.6, -2.0, 0.45)
    inst.objects.link(ho)

    pe = mesh_object("ParticleEmitter", lambda bm: bmesh.ops.create_grid(bm, x_segments=1, y_segments=1, size=0.4),
                     inst, (1.7, -1.5, -1.4), [green])
    ps = pe.modifiers.new("Particles", "PARTICLE_SYSTEM").particle_system
    ps.settings.type, ps.settings.count = "HAIR", 8
    ps.settings.render_type, ps.settings.instance_object = "OBJECT", cone
    ps.settings.particle_size = 0.5
    # glass is found by material (GlassBall, Bottle body); no Glass collection needed
    sc.frame_set(1)
    return sc


if __name__ == "__main__":
    main()
