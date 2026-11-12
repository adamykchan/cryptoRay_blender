# CryptoRay for Blender

Cryptomatte ID mattes for objects **seen through refractive glass**, distorted exactly like the beauty render.

Blender's built-in Cryptomatte only records the first surface a camera ray hits, so everything behind
glass gets the glass's ID. CryptoRay carries the IDs as *light* instead: in a separate view layer every
target object glows white and sits in its own Cycles **light group**, while the glass keeps its real
shader. Light refracts, so each light-group pass is that object's matte, already bent by the glass.
After each frame CryptoRay converts those passes into standard Cryptomatte layers, by default embedded
in the beauty EXR, which Nuke's Cryptomatte node reads directly.

## How it works (and why your scene is safe)

**Build** (the *Cryptomatte Proxy Scene and View Layer* section at the top of the panel) adds the
proxy scene and the ID layer; **Delete** removes them. Building only adds things; it never edits anything
that already exists:

| Added | Purpose |
|---|---|
| View layer `ID_MATTES` | Excludes all your collections; uses its own white world (`id_world`) and its own sample count, denoising off |
| Collection `ID_MATTES_proxies` | Excluded from every other view layer |
| One proxy per object (or per object and material) | Geometry Nodes *Object Info* reads the original live (animation, deformation and modifiers follow) and assigns a white emission material. Glass keeps its real materials |
| Particle copies | For legacy particle systems: a copy of the emitter (same seed and settings, so the same particles) instancing duplicates of the instanced objects with an emission override |
| `CR_*` materials, world and node groups | Shared helpers |

Your materials, objects, light groups and world are not touched, so the beauty render is unchanged.
This is verified pixel by pixel. Everything added carries the custom property `cryptoray`, so Delete
removes exactly that.

Object and material IDs come from the **same** render: each object/material pair gets one light group,
an object's matte is the sum of its pairs, and a material's matte is the sum of its pairs across objects.

Coverage is normalized per pixel: each value is divided by the sum of all IDs plus the background, so
glass absorption and tint cancel out. With **Ignore Glass Reflections** (on by default) nothing in the ID
layer is visible to reflection rays, so reflections on glass carry no IDs and an object seen through
clear or tinted glass gets a solid matte (0.9996 mean coverage through the test ball, against 0.82 clear
and 0.76 tinted with reflections included).
IDs are stored as one coverage value per object rather than packed codes, so anti-aliased and blurred
edges (rough glass, defocus) stay correct.

## Requirements

* Blender **4.2+** with **Cycles**. Developed and tested on Blender 5.0 (Windows, RTX 4090).
* Nothing else. The EXR work uses the NumPy and OpenImageIO that ship with Blender, so the add-on is
  plain Python and runs on Windows, macOS and Linux.

## Install

1. Zip the `cryptoray` folder, or use `dist/cryptoray-<version>.zip` if present.
2. Blender: *Edit > Preferences > Add-ons > ⌄ > Install from Disk…*, pick the zip, and enable **CryptoRay**.
3. Open the 3D View sidebar (**N**) and select the **CryptoRay** tab.

## Use

The panel is greyed out until the proxy scene is built:

1. **Cryptomatte Proxy Scene and View Layer → Build.** The panel then shows how many IDs and light
   groups were built, and warns when settings or objects changed since (click **Rebuild**).
2. **IDs:** tick **Object** and/or **Material**. Each has its Cryptomatte layer name next to it
   (`CryptoRayObject`, `CryptoRayMaterial`). Names follow Blender's CryptoObject / CryptoMaterial.
3. **Glass** is found automatically: any material whose shader refracts (Glass or Refraction BSDF,
   Principled with Transmission above 0 or driven by a texture, also inside node groups) keeps its real
   shader and gets no ID. The detected materials are listed under **Detect Glass Materials**; tick
   **Not Glass** to give one an ID anyway. An object mixing glass and other materials is split, so a
   bottle's label gets an ID while its glass refracts. Optional extras:
   * **Glass Collection:** whole objects in it are treated as glass.
   * The custom property `matte_role` on an object: `"glass"`, `"ignore"` (left out of the ID layer
     entirely) or `"other"` (blocks objects behind it but gets no ID).
4. **Beauty + Cryptomatte:** with the proxy scene built and the output set to **OpenEXR Multilayer**
   (ZIP or PIZ codec), render with **Ctrl+F12** (or `blender -b file.blend -a`). After each frame the
   Cryptomatte layers are written **into the beauty EXR** and the raw ID passes are removed from it. With
   **Embed in Beauty EXR** off they go to `<render name>_crypto_<frame>.exr` next to the render
   instead (render output `//render/shot_####` → `shot_crypto_0001.exr`). A plain F12 isn't saved to
   disk, so it produces no Cryptomatte yet.
5. **Render Cryptomatte Only → Render Still / Render Animation** renders just the ID layer and writes
   `<render name>_crypto_<frame>.exr`, for example when only the mattes need re-rendering or the beauty
   output isn't a multilayer EXR. It first asks whether to rebuild the proxy scene (yes by default). Your
   beauty layer and output settings are switched back afterwards; the proxy scene stays until you delete it.

The proxy scene isn't built automatically when a render starts: Blender runs that step on its render
thread, where editing the scene is unsafe.

### Options

| Option | Default | |
|---|---|---|
| Object / Material | Object | Which Cryptomatte layers to write, each with its layer name |
| Targets | All visible | *Collection*: only that collection gets IDs; other objects still block what's behind them |
| Detect Glass Materials | on | Off: only the Glass Collection and `matte_role` make glass |
| Not Glass (per material) | off | Gives a detected glass material an ID |
| Ignore Glass Reflections | on | Off: reflected objects also show up in the glass, as partial coverage |
| Embed in Beauty EXR | on | Off: separate `<render name>_crypto_<frame>.exr` with a colour-coded RGBA preview |
| Levels | 6 | IDs stored per pixel (Cryptomatte depth) |
| Coverage | Normalized | *Raw Energy* keeps the darkening from glass absorption instead |
| Matte Samples | 0 (scene) | Sample count for the ID layer only. Raise it for very dark glass |
| Keep Raw ID Passes | off | Also writes `<render name>_crypto_raw_<frame>.exr` with the light-group passes and the manifest in its header, plus `<render name>_crypto_manifest.json` |

## Nuke

Read the beauty EXR (or `<render name>_crypto_####.exr`) and connect a **Cryptomatte** node. The test
scene also has Blender's own Cryptomatte on, so `Beauty.CryptoObject` sits next to `CryptoRayObject` for
comparison. Pick
objects with the eyedropper, or type names into the matte list. The layers are called `CryptoRayObject`
and `CryptoRayMaterial` unless you rename them. The separate file's RGBA is a colour-coded preview.
With **Keep Raw**, any `ID_MATTES.Combined_id_####` layer can also be shuffled out and used directly
as a matte.

## Limitations

* **Particles** are named after the instanced object, as in Blender's own Cryptomatte. Tested with hair
  particles instancing objects (including multi-material objects). Simulated emitter particles are
  untested: the copy re-simulates them, so bake them if the mattes drift.
* **Volumes** aren't supported and are left out of the ID layer.
* A collection-instance empty and Geometry Nodes instances get the **instancer's** name. Blender's own
  Cryptomatte names each instanced object instead.
* Reflections in the glass also carry IDs, so a glass surface can show partial coverage of a reflected
  object. This is physically correct, but worth knowing.
* Each light group is a full-resolution float buffer. Above 64 groups the panel warns; use the
  Collection target to reduce them. With both Object and Material on, multi-material objects need one
  group per material.
* Objects linked directly to the *Scene Collection* can't be excluded from a view layer. The panel
  offers **Move Root Objects**, which puts them into a collection; the render is unchanged.
* Blender's own Cryptomatte manifest lists the uncorrected hash for the roughly 1 in 128 names that need
  the spec's exponent fix (its pixels are correct). CryptoRay's manifest follows the
  [Cryptomatte spec](https://github.com/Psyop/Cryptomatte), so picking works for every name.

## Development

```
cryptoray/        the add-on (core.py has no Blender dependency: hashing + conversion)
tests/test_hash.py            MurmurHash3 / Cryptomatte ID unit tests:  python -m unittest discover -s tests
tests/blender/make_test_scene.py   test scene: cubes behind a glass ball, a tinted bottle with a label,
                                   instancing and particle cases; 7 frames, each with one element
                                   fully behind the ball; 256 samples; Blender's Cryptomatte on
tests/blender/acceptance.py        acceptance checks, run inside Blender on that scene
```

Acceptance results on Blender 5.0 (640×360, 64 samples, RTX 4090):

* Beauty is identical with or without the ID layer, and after Delete.
* Coverage plus background sums to 1.0 on all pixels, for both layers.
* Refracted cube coverage is 0.97 through the ball.
* Directly visible objects, including particle instances and the bottle label, match Blender's own
  Cryptomatte on 100% of their solid pixels.
* Glass (ball, bottle body) is detected by material; Not Glass gives it an ID; object and material
  hashes match Blender's.
* Ignore Glass Reflections: 0.9996 mean coverage through clear and tinted glass.
* Rough glass (0.3) gives soft mattes with no NaN or out-of-range values.
