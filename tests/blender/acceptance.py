"""CryptoRay acceptance checks (SPEC "Acceptance tests"), run inside Blender on the open scene
made by make_test_scene.py:

    blender cryptoray_test.blend --background --python tests/blender/acceptance.py

Renders frame 1 three ways (before build, built, after clear) into //acceptance/ and prints a
JSON report. The scene is left as it was (ID layer cleared). Needs the CryptoRay add-on enabled.
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


def inventory():
    return {k: len(getattr(bpy.data, k)) for k in ("objects", "meshes", "materials", "node_groups", "worlds",
                                                    "collections", "hair_curves")}


def main():
    sc = bpy.context.scene
    s = sc.cryptoray
    out = bpy.path.abspath("//acceptance/")
    os.makedirs(out, exist_ok=True)
    beauty = build.beauty_layers(sc)[0]
    orig_path = sc.render.filepath
    native = (beauty.use_pass_cryptomatte_object, beauty.use_pass_cryptomatte_material)
    beauty.use_pass_cryptomatte_object = True       # Blender's own Cryptomatte, for the hash check
    beauty.use_pass_cryptomatte_material = True
    sc.frame_set(1)
    try:
        build.clear(sc)
        inv0 = inventory()
        before = render_still(sc, "//acceptance/before")

        # ---- Object mode, render together, keep raw ----
        s.mode, s.packing, s.keep_raw, s.strip_beauty, s.output_dir, s.prefix = "OBJECT", "SINGLE", True, True, out, "obj"
        t = time.time()
        res = build.build(bpy.context, sc, s)
        R["build"] = {k: res[k] for k in ("ids", "groups", "glass", "others")}
        R["build"]["warnings"] = res["warnings"]
        R["build"]["seconds"] = round(time.time() - t, 3)
        render.begin(render.new_job(sc, out_dir=out))
        t = time.time()
        together = render_still(sc, "//acceptance/together")
        R["render_together_seconds"] = round(time.time() - t, 2)
        R["status"] = render.STATUS.get(sc.name)

        b0, _ = read(before)
        b1, _ = read(together)
        ids_left = [k for k in b1 if k.startswith(core.ID_LAYER + ".")]
        check("beauty file stripped of ID passes", not ids_left, ids_left[:3])
        diff = max(float(np.abs(b0[k] - b1[k]).max()) for k in b0 if k.startswith("Beauty.Combined"))
        check("beauty unchanged by ID layer", diff < 1e-4, diff)

        crypto, cmeta = read(os.path.join(out, "obj_0001.exr"))
        raw, rmeta = read(os.path.join(out, "obj_raw_0001.exr"))
        man = json.loads(rmeta[core.MANIFEST_ATTR])
        lname = render.layer_name(s)
        key = "cryptomatte/%s/" % core.layer_key(lname)[:7]
        check("cryptomatte metadata", cmeta.get(key + "name") == lname and cmeta.get(key + "hash") == "MurmurHash3_32",
              {k: v for k, v in cmeta.items() if k.startswith("cryptomatte")} if key + "name" not in cmeta else None)
        ours = json.loads(cmeta[key + "manifest"])

        # coverage + background must sum to 1 wherever there is light
        def lum(d, g):
            return sum(d["%s.Combined_%s.%s" % (core.ID_LAYER, g, c)] * w for c, w in zip("RGB", (0.2126, 0.7152, 0.0722)))
        total = sum(lum(raw, e["group"]) for e in man["entries"]) + sum(lum(raw, g) for g in man["extra_groups"]
                                                                         if "%s.Combined_%s.R" % (core.ID_LAYER, g) in raw)
        bg_share = sum(lum(raw, g) for g in man["extra_groups"] if "%s.Combined_%s.R" % (core.ID_LAYER, g) in raw)
        ranked = sum(crypto["%s%02d.%s" % (lname, k // 2, "GA"[k % 2])] for k in range(s.ranks))
        lit = total > 1e-4
        err = np.abs(ranked + np.where(lit, bg_share / np.maximum(total, 1e-9), 0) - 1)[lit]
        check("coverage + background sums to 1 (±0.01)", float(err.max()) <= 0.01,
              {"max_error": round(float(err.max()), 5), "pixels": int(lit.sum())})

        # refraction: Cube_C seen through the ball (centre of frame)
        h, w = total.shape
        cid = core.id_to_float(core.name_to_id("Cube_C"))
        cov_c = np.zeros_like(total)
        for k in range(s.ranks):
            idc = crypto["%s%02d.%s" % (lname, k // 2, "RB"[k % 2])]
            cov_c += np.where(idc == np.float32(cid), crypto["%s%02d.%s" % (lname, k // 2, "GA"[k % 2])], 0)
        centre = cov_c[h // 2 - 20:h // 2 + 20, w // 2 - 20:w // 2 + 20]
        check("Cube_C visible through glass (refracted)", float(centre.max()) > 0.5, round(float(centre.max()), 3))

        # hashes vs Blender's own Cryptomatte manifest (same object names -> same hex)
        _, bmeta = read(together)
        native_obj = {}
        for k, v in bmeta.items():
            if k.endswith("/manifest") and "CryptoObject" in str(bmeta.get(k[:-len("manifest")] + "name", "")):
                native_obj = json.loads(v)
        # Blender's manifest lists the raw hash even when the pixel ID needed the exponent fix
        # (~1/128 names); its pixels carry the fixed ID, which is what our manifest lists (per spec).
        common = sorted(set(native_obj) & set(ours))
        mism = [n for n in common if native_obj[n] != ours[n]
                and not (native_obj[n] == "%08x" % core.mm3_32(n) and ours[n] == "%08x" % core.name_to_id(n))]
        check("hashes match Blender's native CryptoObject", common and not mism,
              {"compared": len(common), "mismatch": mism[:5], "native_only": sorted(set(native_obj) - set(ours))[:8],
               "ours_only": sorted(set(ours) - set(native_obj))[:8]})
        R["ids"] = sorted(ours)

        # ---- Material mode, IDs only (beauty layer disabled), RGB packing ----
        s.mode, s.packing, s.prefix, s.keep_raw = "MATERIAL", "RGB", "mat", False
        build.build(bpy.context, sc, s)
        uses = [(v, v.use) for v in build.beauty_layers(sc)]
        for v, _ in uses:
            v.use = False
        render.begin(render.new_job(sc, ids_only=True, out_dir=out))
        ids_file = render_still(sc, "//acceptance/ids_only")
        for v, u in uses:
            v.use = u
        mat, mmeta = read(os.path.join(out, "mat_0001.exr"))
        mkey = "cryptomatte/%s/" % core.layer_key("CryptoRayMaterial")[:7]
        mats = json.loads(mmeta.get(mkey + "manifest", "{}"))
        check("material mode IDs", {"Red", "Blue", "Green", "Yellow"} <= set(mats), sorted(mats))
        check("IDs-only source file removed", not os.path.exists(ids_file), ids_file)
        native_mat = {}
        for k, v in bmeta.items():
            if k.endswith("/manifest") and "CryptoMaterial" in str(bmeta.get(k[:-len("manifest")] + "name", "")):
                native_mat = json.loads(v)
        common = sorted(set(native_mat) & set(mats))
        check("hashes match Blender's native CryptoMaterial", common and all(native_mat[n] == mats[n] for n in common),
              {"compared": common})

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
        s.keep_raw, s.output_dir, s.prefix, s.mode, s.packing = False, "", "crypto", "OBJECT", "SINGLE"
    R["passed"] = all(c["ok"] for c in R["checks"].values())


try:
    main()
except Exception:
    R["EXCEPTION"] = traceback.format_exc()
print(json.dumps(R, indent=1, default=str))
