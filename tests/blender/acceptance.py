"""CryptoRay acceptance checks, run inside Blender on the scene made by make_test_scene.py:

    blender cryptoray_test.blend --background --python tests/blender/acceptance.py

Renders frame 1 several ways into //acceptance/ and prints a JSON report. The scene is left as it
was (ID layer cleared, settings restored). Needs the CryptoRay add-on enabled.
"""
import json
import os
import time
import traceback

import bpy
import numpy as np
import OpenImageIO as oiio

from cryptoray import build, core, render

R = {"checks": {}}


def check(name, ok, detail=None):
    R["checks"][name] = {"ok": bool(ok), "detail": detail}


def read(path):
    inp = oiio.ImageInput.open(path)
    d, meta, si = {}, {}, 0
    while inp.seek_subimage(si, 0):
        sp = inp.spec()
        px = np.asarray(inp.read_image(si, 0, 0, sp.nchannels, "float")).reshape(sp.height, sp.width, sp.nchannels)
        for i, n in enumerate(sp.channelnames):
            d[n] = px[..., i]
        if si == 0:
            for a in sp.extra_attribs:
                meta[a.name] = a.value
        si += 1
    inp.close()
    return d, meta


def render_still(scene, path):
    scene.render.filepath = path
    win = bpy.context.window_manager.windows[0] if bpy.context.window_manager.windows else None
    if win:
        with bpy.context.temp_override(window=win, scene=scene):
            bpy.ops.render.render(write_still=True)
    else:
        bpy.ops.render.render(write_still=True)
    return bpy.path.abspath(path) + ".exr"


def do_build(sc, s):
    win = bpy.context.window_manager.windows[0] if bpy.context.window_manager.windows else None
    if win:
        with bpy.context.temp_override(window=win, scene=sc, view_layer=build.beauty_layers(sc)[0]):
            return build.build(bpy.context, sc, s)
    return build.build(bpy.context, sc, s)


def inventory():
    return {k: len(getattr(bpy.data, k)) for k in ("objects", "meshes", "materials", "node_groups", "worlds",
                                                    "collections", "hair_curves", "particles")}


def coverage(d, layer, name, ranks, lower=False):
    """Coverage of ``name`` in a Cryptomatte layer (ours: .R/.G/.B/.A, Blender's: .r/.g/.b/.a)."""
    idf = np.float32(core.id_to_float(core.name_to_id(name)))
    ch = "rgba" if lower else "RGBA"
    cov = 0
    for k in range(ranks):
        pre = "%s%02d." % (layer, k // 2)
        i, w = (ch[0], ch[1]) if k % 2 == 0 else (ch[2], ch[3])
        if pre + i in d:
            cov = cov + np.where(d[pre + i] == idf, d[pre + w], 0)
    return cov


def main():
    sc = bpy.context.scene
    s = sc.cryptoray
    out = bpy.path.abspath("//acceptance/")
    os.makedirs(out, exist_ok=True)
    beauty = build.beauty_layers(sc)[0]
    orig_path = sc.render.filepath
    saved = {k: getattr(s, k) for k in ("use_object", "use_material", "embed", "keep_raw")}
    native = (beauty.use_pass_cryptomatte_object, beauty.use_pass_cryptomatte_material)
    beauty.use_pass_cryptomatte_object = True       # Blender's own Cryptomatte, for comparison
    beauty.use_pass_cryptomatte_material = True
    sc.frame_set(1)
    try:
        build.clear(sc)
        inv0 = inventory()
        before = render_still(sc, "//acceptance/before")

        # ---- Object + Material, embedded in the beauty, keep raw ----
        s.use_object, s.use_material, s.embed, s.keep_raw = True, True, True, True
        t = time.time()
        res = do_build(sc, s)
        R["build"] = dict(res, seconds=round(time.time() - t, 3))
        sc.render.filepath = "//acceptance/together"
        render.begin(render.new_job(sc))
        t = time.time()
        together = render_still(sc, "//acceptance/together")
        R["render_together_seconds"] = round(time.time() - t, 2)
        R["status"] = render.STATUS.get(sc.name)

        b0, _ = read(before)
        b1, meta = read(together)
        ids_left = [k for k in b1 if k.startswith(core.ID_LAYER + ".")]
        check("beauty file stripped of ID passes", not ids_left, ids_left[:3])
        diff = max(float(np.abs(b0[k] - b1[k]).max()) for k in b0 if k.startswith("Beauty.Combined"))
        check("beauty unchanged by ID layer", diff < 1e-4, diff)

        lo, lm = "CryptoRayObject", "CryptoRayMaterial"
        ko, km = ("cryptomatte/%s/" % core.layer_key(n)[:7] for n in (lo, lm))
        check("embedded: both layers + metadata in the beauty EXR",
              lo + "00.R" in b1 and lm + "00.R" in b1 and meta.get(ko + "name") == lo and meta.get(km + "name") == lm,
              sorted(k for k in meta if k.startswith("cryptomatte"))[:12])
        objs = json.loads(meta[ko + "manifest"])
        mats = json.loads(meta[km + "manifest"])
        R["object_ids"], R["material_ids"] = sorted(objs), sorted(mats)
        check("glass detected by material (ball, bottle body) - no IDs",
              "GlassBall" not in objs and "Glass" not in mats and "BottleGlass" not in mats,
              res["glass_materials"])
        check("bottle label gets IDs", "Bottle" in objs and "Label" in mats)
        check("particle instances named after the instanced object", "Cone" in objs, sorted(objs))

        raw, rmeta = read(os.path.join(out, "together_crypto_raw_0001.exr"))
        man = json.loads(rmeta[core.MANIFEST_ATTR])

        def lum(g):
            return sum(raw["%s.Combined_%s.%s" % (core.ID_LAYER, g, c)] * w for c, w in zip("RGB", (0.2126, 0.7152, 0.0722)))
        extra = [g for g in man["extra_groups"] if "%s.Combined_%s.R" % (core.ID_LAYER, g) in raw]
        total = sum(lum(e["group"]) for e in man["entries"]) + sum(lum(g) for g in extra)
        bg_share = sum(lum(g) for g in extra)
        lit = total > 1e-4
        for layer in (lo, lm):
            ranked = sum(b1["%s%02d.%s" % (layer, k // 2, "GA"[k % 2])] for k in range(s.ranks)
                         if "%s%02d.%s" % (layer, k // 2, "GA"[k % 2]) in b1)
            # material layer: objects without a material ID still count in the total, so <= 1
            err = (ranked + np.where(lit, bg_share / np.maximum(total, 1e-9), 0) - 1)[lit]
            ok = float(np.abs(err).max()) <= 0.01 if layer == lo else float(err.max()) <= 0.01
            check("%s coverage + background sums to 1 (±0.01)" % layer, ok,
                  {"max_error": round(float(np.abs(err).max()), 5), "pixels": int(lit.sum())})

        h, w = total.shape
        cov_c = coverage(b1, lo, "Cube_C", s.ranks)
        # the ball fully covers Cube_C in every frame of the test shot, so all its coverage is refracted
        check("Cube_C visible through glass (refracted)", float(cov_c.max()) > 0.5, round(float(cov_c.max()), 3))

        # direct views must match Blender's own Cryptomatte (positions, particles, label)
        agree = {}
        # Blender names instances after the instanced object; CryptoRay does that for particles but
        # names GN / collection instances after their instancer, so cones are compared as a union.
        cones = ("Cone", "ConeInstance", "ConeScatter")
        # (the bottle is compared by its Label material: Blender's object matte also covers the glass)
        for name, group, layer, nlayer in (
                ("all cones (particles incl.)", cones, lo, "Beauty.CryptoObject"),
                ("Label (material)", ("Label",), lm, "Beauty.CryptoMaterial"),
                ("Cube_L", ("Cube_L",), lo, "Beauty.CryptoObject"), ("Hair", ("Hair",), lo, "Beauty.CryptoObject"),
                ("ParticleEmitter", ("ParticleEmitter",), lo, "Beauty.CryptoObject")):
            nat = sum(coverage(b1, nlayer, n, 6, lower=True) for n in group)
            ours = sum(coverage(b1, layer, n, s.ranks) for n in group)
            solid = nat > 0.9
            agree[name] = {"pixels": int(solid.sum()),
                           "ours>0.5": round(float((ours[solid] > 0.5).mean()), 3) if solid.any() else None}
        check("direct views match Blender's own CryptoObject (>=95% of solid pixels)",
              agree["all cones (particles incl.)"]["pixels"] > 0
              and all(v["ours>0.5"] is None or v["ours>0.5"] >= 0.95 for v in agree.values()), agree)
        for kind, layer, key in (("CryptoObject", lo, ko), ("CryptoMaterial", lm, km)):
            nat_man = {}
            for k, v in meta.items():
                if k.endswith("/manifest") and kind in str(meta.get(k[:-len("manifest")] + "name", "")):
                    nat_man = json.loads(v)
            ours = json.loads(meta[key + "manifest"])
            common = sorted(set(nat_man) & set(ours))
            # Blender's manifest lists the raw hash where the spec's exponent fix applies (~1/128 names)
            mism = [n for n in common if nat_man[n] != ours[n]
                    and not (nat_man[n] == "%08x" % core.mm3_32(n) and ours[n] == "%08x" % core.name_to_id(n))]
            check("hashes match Blender's native %s" % kind, common and not mism,
                  {"compared": len(common), "mismatch": mism[:5]})

        # ---- Object only, IDs only, separate file ----
        s.use_material, s.embed, s.keep_raw = False, False, False
        do_build(sc, s)
        uses = [(v, v.use) for v in build.beauty_layers(sc)]
        for v, _ in uses:
            v.use = False
        sc.render.filepath = "//acceptance/idsonly_####"
        naming = render.output_naming(sc)
        render.begin(render.new_job(sc, ids_only=True, naming=naming))
        ids_file = render_still(sc, "//acceptance/idsonly_src")
        for v, u in uses:
            v.use = u
        sep = os.path.join(out, "idsonly_crypto_0001.exr")
        check("IDs-only: separate <render name>_crypto_<frame>.exr", os.path.isfile(sep), sep)
        check("IDs-only source file removed", not os.path.exists(ids_file), ids_file)
        if os.path.isfile(sep):
            d, m = read(sep)
            check("separate file has preview RGBA + object layer only",
                  "A" in d and lo + "00.R" in d and lm + "00.R" not in d, sorted(d)[:8])

        # ---- Ignore Glass Reflections: solid matte through clear and tinted glass ----
        def ids_only(label):
            do_build(sc, s)
            for v, _ in uses:
                v.use = False
            sc.render.filepath = "//acceptance/%s_####" % label
            render.begin(render.new_job(sc, ids_only=True, naming=render.output_naming(sc)))
            render_still(sc, "//acceptance/%s_src" % label)
            for v, u in uses:
                v.use = u
            return coverage(read(os.path.join(out, "%s_crypto_0001.exr" % label))[0], lo, "Cube_C", s.ranks)

        glass_bsdf = bpy.data.materials["Glass"].node_tree.nodes["Principled BSDF"].inputs["Base Color"]
        tint0 = tuple(glass_bsdf.default_value)
        refl = {}
        try:
            for tint_name, tint in (("clear", (1, 1, 1, 1)), ("tinted", (0.3, 0.6, 0.35, 1))):
                glass_bsdf.default_value = tint
                for flag in (True, False):
                    s.ignore_reflections = flag
                    refl[(tint_name, flag)] = ids_only("refl_%s_%s" % (tint_name, "on" if flag else "off"))
        finally:
            glass_bsdf.default_value = tint0
            s.ignore_reflections = True
        # Cube_C as seen through the ball: pixels the clear/on matte gives > 0.5, minus edge pixels
        mask = refl[("clear", True)] > 0.5
        mask[1:-1, 1:-1] &= mask[:-2, 1:-1] & mask[2:, 1:-1] & mask[1:-1, :-2] & mask[1:-1, 2:]
        means = {"%s, ignore %s" % (k[0], "on" if k[1] else "off"): round(float(v[mask].mean()), 4)
                 for k, v in refl.items()}
        means["pixels"] = int(mask.sum())
        check("Ignore Glass Reflections: solid matte through clear and tinted glass (mean >= 0.99)",
              mask.sum() > 100 and means["clear, ignore on"] >= 0.99 and means["tinted, ignore on"] >= 0.99
              and means["tinted, ignore off"] < means["tinted, ignore on"], means)

        # ---- Not Glass: the ball gets an ID ----
        glass_mat = bpy.data.materials["Glass"]
        glass_mat.cryptoray_not_glass = True
        try:
            res_ng = do_build(sc, s)
            man_ng = build.manifest(sc)
            check("Not Glass gives a detected glass material an ID",
                  "GlassBall" in core.layer_names(man_ng, "object") and "Glass" not in res_ng["glass_materials"],
                  res_ng["glass_materials"])
        finally:
            glass_mat.cryptoray_not_glass = False

        # ---- Clear restores the scene ----
        build.clear(sc)
        inv1 = inventory()
        check("clear removes everything it added", inv0 == inv1, {"before": inv0, "after": inv1})
        after = render_still(sc, "//acceptance/after_clear")
        a, _ = read(after)
        diff = max(float(np.abs(b0[k] - a[k]).max()) for k in b0 if k.startswith("Beauty.Combined"))
        check("beauty after clear identical", diff < 1e-4, diff)
    finally:
        beauty.use_pass_cryptomatte_object, beauty.use_pass_cryptomatte_material = native
        sc.render.filepath = orig_path
        for k, v in saved.items():
            setattr(s, k, v)
        build.clear(sc)
    R["passed"] = all(c["ok"] for c in R["checks"].values())


try:
    main()
except Exception:
    R["EXCEPTION"] = traceback.format_exc()
print(json.dumps(R, indent=1, default=str))
