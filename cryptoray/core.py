"""CryptoRay core: Cryptomatte hashing and light-group-pass -> Cryptomatte conversion.

This module has no Blender dependency. It needs NumPy and OpenImageIO for the
EXR work (both ship with Blender 4.x/5.x; outside Blender: ``pip install
numpy OpenImageIO``). The hashing helpers are pure Python.
"""

import json
import os
import struct
import time

ID_LAYER = "ID_MATTES"          # view layer holding the ID light groups
WORLD_GROUP = "id_world"        # background (world override) group, counts towards the total only
OTHER_GROUP = "id_other"        # visible non-target objects, counts towards the total only
MANIFEST_ATTR = "cryptoray/manifest"
LOSSY_EXR_CODECS = {"DWAA", "DWAB", "B44", "B44A", "PXR24"}


# --------------------------------------------------------------------------
# MurmurHash3 / Cryptomatte IDs
# --------------------------------------------------------------------------

def mm3_32(data, seed=0):
    """MurmurHash3 x86 32-bit. ``data`` is bytes or str (UTF-8). Returns an unsigned int."""
    if isinstance(data, str):
        data = data.encode("utf-8")
    mask = 0xFFFFFFFF
    c1, c2 = 0xCC9E2D51, 0x1B873593
    h = seed & mask
    n = len(data)
    end = n & ~3
    for i in range(0, end, 4):
        k = int.from_bytes(data[i:i + 4], "little")
        k = (k * c1) & mask
        k = ((k << 15) | (k >> 17)) & mask
        k = (k * c2) & mask
        h ^= k
        h = ((h << 13) | (h >> 19)) & mask
        h = (h * 5 + 0xE6546B64) & mask
    tail = data[end:]
    if tail:
        k = int.from_bytes(tail, "little")
        k = (k * c1) & mask
        k = ((k << 15) | (k >> 17)) & mask
        k = (k * c2) & mask
        h ^= k
    h ^= n
    h ^= h >> 16
    h = (h * 0x85EBCA6B) & mask
    h ^= h >> 13
    h = (h * 0xC2B2AE35) & mask
    h ^= h >> 16
    return h


def name_to_id(name):
    """Cryptomatte ID bits for a name: MurmurHash3 (seed 0), with bit 23 flipped when the
    float exponent would be all 0s or all 1s (denormal / inf / nan)."""
    h = mm3_32(name)
    exponent = (h >> 23) & 0xFF
    if exponent in (0, 255):
        h ^= 1 << 23
    return h


def id_to_float(bits):
    return struct.unpack("<f", struct.pack("<I", bits))[0]


def layer_key(layer_name):
    """Metadata key: first 7 hex chars of MurmurHash3 of the layer name (no exponent fix)."""
    return "%08x" % mm3_32(layer_name)


def manifest_for(names):
    return json.dumps({n: "%08x" % name_to_id(n) for n in sorted(set(names))}, separators=(",", ":"))


def check_collisions(names):
    seen = {}
    for n in set(names):
        h = name_to_id(n)
        if h in seen and seen[h] != n:
            raise ValueError("Cryptomatte hash collision between %r and %r; rename one of them." % (seen[h], n))
        seen[h] = n


# --------------------------------------------------------------------------
# Manifest (stored on the ID view layer and in raw EXRs)
# --------------------------------------------------------------------------

def group_channel(group, channel):
    return "%s.Combined_%s.%s" % (ID_LAYER, group, channel)


def make_manifest(mode, packing, entries, extra_groups=(WORLD_GROUP,)):
    """entries: list of dicts {name, group, channel} with channel in L/R/G/B."""
    return {"version": 1, "layer": ID_LAYER, "mode": mode, "packing": packing,
            "entries": list(entries), "extra_groups": list(extra_groups)}


# --------------------------------------------------------------------------
# EXR reading/writing (OpenImageIO), streaming per channel to bound memory
# --------------------------------------------------------------------------

def _oiio():
    import OpenImageIO as oiio
    return oiio


def _np():
    import numpy as np
    return np


class ExrReader:
    """Multi-part aware reader. ``locate[name] = (subimage, channel_index)``."""

    def __init__(self, path):
        oiio = _oiio()
        self.path = path
        self.inp = oiio.ImageInput.open(path)
        if self.inp is None:
            raise IOError("Cannot open %s: %s" % (path, oiio.geterror()))
        self.parts = []
        self.locate = {}
        si = 0
        while self.inp.seek_subimage(si, 0):
            spec = self.inp.spec().copy()
            names = list(spec.channelnames)
            self.parts.append((spec, names))
            for i, n in enumerate(names):
                self.locate.setdefault(n, (si, i))
            si += 1

    def channel(self, name):
        np = _np()
        si, i = self.locate[name]
        spec = self.parts[si][0]
        px = self.inp.read_image(si, 0, i, i + 1, "float")
        if px is None:
            raise IOError("Read failed for %s in %s: %s" % (name, self.path, self.inp.geterror()))
        return np.asarray(px, dtype=np.float32).reshape(spec.height, spec.width)

    def channels(self, si, idxs):
        np = _np()
        spec = self.parts[si][0]
        idxs = list(idxs)
        if idxs == list(range(idxs[0], idxs[-1] + 1)):
            px = self.inp.read_image(si, 0, idxs[0], idxs[-1] + 1, "float")
            return np.asarray(px, dtype=np.float32).reshape(spec.height, spec.width, len(idxs))
        return np.stack([self.channel(self.parts[si][1][i]) for i in idxs], -1)

    def close(self):
        if self.inp is not None:
            self.inp.close()
            self.inp = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def _temp_path(path):
    d, b = os.path.split(os.path.abspath(path))
    return os.path.join(d, ".%s.tmp%d.exr" % (os.path.splitext(b)[0], os.getpid()))


def _subset_spec(spec, idxs):
    s = spec.copy()
    names = [spec.channelnames[i] for i in idxs]
    fmts = list(spec.channelformats) if len(spec.channelformats) else []
    s.nchannels = len(idxs)
    s.channelnames = tuple(names)
    if fmts:
        s.channelformats = tuple(fmts[i] for i in idxs)
    s.alpha_channel = -1
    s.z_channel = -1
    return s


def copy_channels(src, dst, keep, attribs=None):
    """Write ``dst`` with the channels of ``src`` for which ``keep(name)`` is true, preserving the
    part structure, per-part metadata and pixel types. Atomic (temp file + replace).
    Returns the number of channels written (0 = nothing written)."""
    oiio = _oiio()
    plan = []
    with ExrReader(src) as r:
        for si, (spec, names) in enumerate(r.parts):
            idxs = [i for i, n in enumerate(names) if keep(n)]
            if idxs:
                plan.append((si, idxs, _subset_spec(spec, idxs)))
        if not plan:
            return 0
        for _, _, s in plan:
            for k, v in (attribs or {}).items():
                s.attribute(k, v)
        tmp = _temp_path(dst)
        out = oiio.ImageOutput.create(tmp)
        if out is None:
            raise IOError("Cannot create %s: %s" % (tmp, oiio.geterror()))
        try:
            specs = [s for _, _, s in plan]
            if len(specs) > 1 and not out.supports("multiimage"):
                raise IOError("EXR writer does not support multi-part output")
            ok = out.open(tmp, specs) if len(specs) > 1 else out.open(tmp, specs[0])
            if not ok:
                raise IOError(out.geterror())
            for n, (si, idxs, s) in enumerate(plan):
                if n and not out.open(tmp, s, "AppendSubimage"):
                    raise IOError(out.geterror())
                px = r.channels(si, idxs)
                if not out.write_image(_np().ascontiguousarray(px)):
                    raise IOError(out.geterror())
        except Exception:
            out.close()
            if os.path.exists(tmp):
                os.remove(tmp)
            raise
        out.close()
    os.replace(tmp, dst)
    return sum(len(i) for _, i, _ in plan)


def is_id_channel(name):
    return name.startswith(ID_LAYER + ".")


# --------------------------------------------------------------------------
# Conversion
# --------------------------------------------------------------------------

def _value(reader, group, channel):
    if channel == "L":
        r, g, b = (reader.channel(group_channel(group, c)) for c in "RGB")
        return r * 0.2126 + g * 0.7152 + b * 0.0722
    return reader.channel(group_channel(group, channel))


def _preview_rgb(bits):
    """Stable per-ID preview colour from the hash bits (uint32 array) -> 3 float arrays in 0.2..1."""
    np = _np()
    r = ((bits >> 0) & 0xFF).astype(np.float32) / 255.0
    g = ((bits >> 8) & 0xFF).astype(np.float32) / 255.0
    b = ((bits >> 16) & 0xFF).astype(np.float32) / 255.0
    return [0.2 + 0.8 * c for c in (r, g, b)]


def convert(src, manifest, crypto_path, ranks=6, layer_name="CryptoRayObject",
            normalization="SUM", raw_path=None, strip_source=False, delete_source=False):
    """Read the light-group passes of ``manifest`` from ``src`` (Blender multilayer EXR) and write a
    Cryptomatte EXR to ``crypto_path``.

    normalization: "SUM"  -> coverage = value / (sum of all IDs + world + other), so coverage sums to 1
                              wherever there is signal (undoes glass absorption / Fresnel loss).
                   "RAW"  -> coverage = value (energy actually reaching the camera, clamped to 0..1).
    raw_path:      also keep the raw ID passes (plus the manifest in the header) there.
    strip_source:  rewrite ``src`` without the ID passes (beauty stays bit-identical).
    delete_source: remove ``src`` afterwards (used when the file only holds ID passes).
    """
    np = _np()
    oiio = _oiio()
    t0 = time.time()
    if not 1 <= ranks <= 16:
        raise ValueError("ranks must be 1..16")
    entries = manifest["entries"]
    if not entries:
        raise ValueError("Manifest has no IDs")
    names = sorted({e["name"] for e in entries})
    check_collisions(names)
    count = min(ranks, len(names))

    with ExrReader(src) as r:
        missing = [group_channel(e["group"], "R" if e["channel"] == "L" else e["channel"])
                   for e in entries if group_channel(e["group"], "R" if e["channel"] == "L" else e["channel"]) not in r.locate]
        if missing:
            raise ValueError("%s has no ID passes %s... (was the %s layer rendered?)" % (src, missing[:3], ID_LAYER))
        spec0 = r.parts[r.locate[group_channel(entries[0]["group"], "R")][0]][0]
        h, w = spec0.height, spec0.width

        # accumulate per name (several entries may share a name, e.g. material across objects)
        by_name = {}
        for e in entries:
            by_name.setdefault(e["name"], []).append(e)

        total = np.zeros((h, w), np.float32)
        weights = np.zeros((count, h * w), np.float32)
        ids = np.zeros((count, h * w), np.uint32)
        for name in names:
            v = None
            for e in by_name[name]:
                x = _value(r, e["group"], e["channel"])
                v = x if v is None else v + x
            np.maximum(v, 0, out=v)
            v[~np.isfinite(v)] = 0
            total += v
            flat = v.ravel()
            active = np.flatnonzero(flat > weights[-1])
            if active.size == 0:
                continue
            cw = flat[active].copy()
            ci = np.full(active.size, name_to_id(name), np.uint32)
            for k in range(count):          # streaming insertion into the sorted top-N
                swap = cw > weights[k, active]
                if not swap.any():
                    continue
                pos = active[swap]
                ow, oi = weights[k, pos].copy(), ids[k, pos].copy()
                weights[k, pos], ids[k, pos] = cw[swap], ci[swap]
                cw[swap], ci[swap] = ow, oi
        for g in manifest.get("extra_groups", []):
            if group_channel(g, "R") in r.locate:
                x = _value(r, g, "L")
                np.maximum(x, 0, out=x)
                x[~np.isfinite(x)] = 0
                total += x
        tflat = total.ravel()

    if normalization == "SUM":
        ok = tflat > 1e-6
        weights[:, ok] /= tflat[ok]
        weights[:, ~ok] = 0
    elif normalization == "RAW":
        np.clip(weights, 0, 1, out=weights)
    else:
        raise ValueError("normalization must be SUM or RAW")
    ids[weights <= 0] = 0
    weights[weights < 0] = 0

    # -------- write the Cryptomatte EXR (single part, float32) --------
    chans = {}
    col = [np.zeros(h * w, np.float32) for _ in range(3)]
    for k in range(count):
        for c, p in zip(col, _preview_rgb(ids[k])):
            c += p * weights[k] * (ids[k] != 0)
    alpha = weights.sum(axis=0)
    chans["R"], chans["G"], chans["B"], chans["A"] = col[0], col[1], col[2], np.minimum(alpha, 1)
    for pair in range((count + 1) // 2):
        for off, (ic, wc) in enumerate((("R", "G"), ("B", "A"))):
            k = pair * 2 + off
            pre = "%s%02d." % (layer_name, pair)
            if k < count:
                chans[pre + ic] = ids[k].view(np.float32)
                chans[pre + wc] = weights[k]
            else:
                chans[pre + ic] = np.zeros(h * w, np.float32)
                chans[pre + wc] = np.zeros(h * w, np.float32)
    order = list(chans)
    spec = oiio.ImageSpec(w, h, len(order), "float")
    for a in ("x", "y", "full_x", "full_y", "full_width", "full_height"):
        setattr(spec, a, getattr(spec0, a))
    spec.channelnames = tuple(order)
    spec.alpha_channel = 3
    spec.attribute("compression", "zip")
    key = "cryptomatte/%s/" % layer_key(layer_name)[:7]
    spec.attribute(key + "name", layer_name)
    spec.attribute(key + "hash", "MurmurHash3_32")
    spec.attribute(key + "conversion", "uint32_to_float32")
    spec.attribute(key + "manifest", manifest_for(names))
    spec.attribute("cryptoray/normalization", normalization)
    spec.attribute("cryptoray/mode", manifest.get("mode", ""))
    os.makedirs(os.path.dirname(os.path.abspath(crypto_path)), exist_ok=True)
    tmp = _temp_path(crypto_path)
    out = oiio.ImageOutput.create(tmp)
    if out is None or not out.open(tmp, spec):
        raise IOError("Cannot write %s: %s" % (crypto_path, oiio.geterror() if out is None else out.geterror()))
    buf = np.ascontiguousarray(np.stack([chans[n] for n in order], -1).reshape(h, w, len(order)))
    ok = out.write_image(buf)
    err = out.geterror()
    out.close()
    if not ok:
        os.remove(tmp)
        raise IOError("Cannot write %s: %s" % (crypto_path, err))
    os.replace(tmp, crypto_path)

    # -------- raw passes / source handling --------
    if raw_path:
        os.makedirs(os.path.dirname(os.path.abspath(raw_path)), exist_ok=True)
        copy_channels(src, raw_path, is_id_channel, {MANIFEST_ATTR: json.dumps(manifest)})
    if delete_source:
        os.remove(src)
    elif strip_source:
        if copy_channels(src, src, lambda n: not is_id_channel(n)) == 0:
            os.remove(src)   # nothing but ID passes in there

    with np.errstate(all="ignore"):
        lost = float(np.max(np.where(tflat > 1e-6, 1.0 - alpha, 0))) if normalization == "SUM" else None
    return {"crypto": crypto_path, "ids": len(names), "ranks": count, "width": w, "height": h,
            "max_uncovered_share": lost, "seconds": round(time.time() - t0, 3)}


def convert_raw_file(raw_path, crypto_path, **kw):
    """Convert a kept raw file (manifest embedded in its header) - usable outside Blender."""
    oiio = _oiio()
    inp = oiio.ImageInput.open(raw_path)
    if inp is None:
        raise IOError(oiio.geterror())
    try:
        text = inp.spec().get_string_attribute(MANIFEST_ATTR)
    finally:
        inp.close()
    if not text:
        raise ValueError("%s has no %s header" % (raw_path, MANIFEST_ATTR))
    return convert(raw_path, json.loads(text), crypto_path, **kw)
