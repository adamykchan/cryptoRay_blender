# CryptoRay for Blender

Cryptomatte ID mattes for objects **seen through refractive glass**, distorted exactly like the beauty render.

Blender's built-in Cryptomatte only records the first surface a camera ray hits, so everything behind
glass gets the glass's ID. CryptoRay carries the IDs as *light* instead: in a separate view layer every
target object glows white and sits in its own Cycles **light group**, while the glass keeps its real
shader. Light refracts, so each light-group pass is that object's matte, already bent by the glass.
After each frame CryptoRay converts those passes into a standard Cryptomatte EXR that Nuke's
Cryptomatte node reads directly.

## How it works (and why your scene is safe)

**Build** adds things; it never edits anything that already exists:

| Added | Purpose |
|---|---|
| View layer `ID_MATTES` | Excludes all your collections; uses its own white world (`id_world`) and its own sample count, denoising off |
| Collection `ID_MATTES_proxies` | Excluded from every other view layer |
| One proxy object per ID | Geometry Nodes *Object Info* reads the original live (animation, deformation and modifiers follow) and assigns a white emission material. Glass proxies keep the original materials |
| `CR_*` materials, world and node groups | Shared helpers |

Your materials, objects, light groups and world are not touched, so the beauty render is unchanged.
This was verified pixel by pixel. **Clear** deletes exactly what Build added; everything is tagged with
the custom property `cryptoray`.

Coverage is normalized per pixel: each value is divided by the sum of all IDs plus the background.
Coverage therefore sums to 1 even where the glass absorbs light or loses some to Fresnel reflection.
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

1. Put glass (and liquids) in a collection and pick it as **Glass**. Alternatively, give an object the custom
   property `matte_role = "glass"`. Other roles:
   * `"ignore"`: left out of the ID layer entirely.
   * `"other"`: blocks objects behind it but gets no ID.
2. Choose **ID Type**: *Object* (named like Blender's CryptoObject) or *Material* (named like CryptoMaterial).
3. Set the beauty output to **OpenEXR Multilayer** (ZIP or PIZ codec), then click **Still** or **Animation**.
   Beauty and IDs render in one job. After each frame:
   * `<prefix>_<frame>.exr`, the Cryptomatte, is written to the output folder (default: next to the beauty).
   * The ID passes are stripped from the beauty EXR (option **Strip IDs from Beauty**).
4. **IDs Only** renders just the ID layer, for example when the beauty output isn't a multilayer EXR or
   only the mattes need re-rendering. Your beauty layer and output settings are switched back afterwards.

Plain **Ctrl+F12** and command-line renders (`blender -b file.blend -a`) are converted too, as long as the
add-on is enabled and the ID layer is built. A plain **F12** still isn't saved to disk, so use the
panel's **Still** button instead.

### Options

| Option | Default | |
|---|---|---|
| Packing | 1 ID per group | *3 IDs per group* puts three IDs in each light group (R, G, B), so about a third of the passes and GPU memory. Only correct through untinted glass |
| Targets | All visible | *Collection*: only that collection gets IDs; other objects still block what's behind them |
| Levels | 6 | IDs stored per pixel (Cryptomatte depth) |
| Coverage | Normalized | *Raw Energy* keeps the darkening from glass absorption instead |
| Matte Samples | 0 (scene) | Sample count for the ID layer only |
| Keep Raw ID Passes | off | Also writes `<prefix>_raw_<frame>.exr` with the light-group passes and the manifest in its header, plus `<prefix>_manifest.json` |
| Rebuild Before Render | on | The render buttons rebuild first, so new or hidden objects are always current |

## Nuke

Read `<prefix>_####.exr` and connect a **Cryptomatte** node. Pick objects with the eyedropper, or type
names into the matte list. The layer is called `CryptoRayObject` / `CryptoRayMaterial` unless you rename it.
The RGBA of the file is a colour-coded preview. With **Keep Raw**, any `ID_MATTES.Combined_id_####` layer
can also be shuffled out and used directly as a matte.

## Limitations

* **Legacy particle systems** (the Particle Properties tab) aren't captured; the build warns about them.
  Convert them to Geometry Nodes instances, or *Make Instances Real* on a copy. Collection instances,
  Geometry Nodes instances and hair curves work.
* **Volumes** aren't supported and are left out of the ID layer.
* A collection-instance empty gets **one** ID, named after the empty. Blender's own Cryptomatte names
  each instanced object instead.
* Reflections in the glass also carry IDs, so a glass surface can show partial coverage of a reflected
  object. This is physically correct, but worth knowing.
* Each light group is a full-resolution float buffer. Above 64 groups the panel warns; use RGB packing
  or the Collection target to reduce them.
* Objects linked directly to the *Scene Collection* can't be excluded from a view layer. The panel
  offers **Move Root Objects**, which puts them into a collection; the render is unchanged.
* Blender's own Cryptomatte manifest lists the uncorrected hash for the roughly 1 in 128 names that need
  the spec's exponent fix (its pixels are correct). CryptoRay's manifest follows the
  [Cryptomatte spec](https://github.com/Psyop/Cryptomatte), so picking works for every name.

## Development

```
cryptoray/        the add-on (core.py has no Blender dependency: hashing + conversion)
tests/test_hash.py            MurmurHash3 / Cryptomatte ID unit tests:  python -m unittest discover -s tests
tests/blender/make_test_scene.py   SPEC acceptance scene (3 cubes behind a glass ball + edge cases)
tests/blender/acceptance.py        acceptance checks, run inside Blender on that scene
```

Acceptance results on Blender 5.0 (640×360, 64 samples, RTX 4090):

* Beauty is identical with or without the ID layer, and after Clear.
* Coverage plus background sums to 1.0 on all pixels.
* Refracted cube coverage is 0.97 through the ball.
* Rough glass (0.3) gives soft mattes with no NaN or out-of-range values.
* Object and material hashes match the IDs in Blender's native Cryptomatte pixels.
* 52 objects build in 0.01 s and render plus convert in 1.5 s (1.3 s with RGB packing, 18 groups).
