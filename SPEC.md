# Refracted ID Mattes: Blender Light Group Tool Spec

## Problem

We need per-object ID mattes for objects seen through refractive glass, distorted exactly like the beauty render. Blender's Cryptomatte can't do this: Cycles writes Cryptomatte IDs only on the camera ray's first hit, so every pixel behind glass gets the glass's ID. Shader AOVs have the same first-hit limit, and a Python add-on cannot change the Cycles kernel.

The workaround is to carry IDs as light, which does travel through refraction. This spec describes a tool that does that at scale and outputs mattes usable in Nuke.

## Approach

Each target object becomes an emitter in its own Cycles Light Group, rendered once in a dedicated matte view layer while the glass keeps its real refractive shader. Light Groups (Cycles, Blender 3.4+) split the render into one pass per group, including indirect light, so each pass is that object's matte, already refracted.

1. Duplicate the scene setup into a matte view layer.
2. Override target objects with white emission (strength 1.0); keep glass shaders untouched.
3. Assign each target object to a unique light group; set the world to black or its own group.
4. Render once to multilayer EXR, denoising off.
5. Post-process: normalize per pixel and convert to a standard Cryptomatte EXR, or use passes directly in Nuke.

IDs must be stored linearly. A pixel is an average of many samples (antialiasing, pixel filter, glass roughness), so packing IDs as bits or float codes breaks at every edge: a 50/50 mix of two codes decodes as a third, wrong ID. One coverage value per object per channel survives averaging; that is why we use separate passes and not packed values.

Optional density mode: put up to 3 objects in one light group with pure R, G, B emission. This triples IDs per pass but is only accurate through neutral (untinted) glass.

## Part 1: Blender add-on

A Python add-on (bpy, Blender 4.x, Cycles) that builds the matte view layer non-destructively and records a manifest.

- **UI:** an N-panel or Render Properties panel with "Build Matte Layer", "Clear Matte Layer", and a mode switch: one object per group (default) or RGB x3 per group.
- **Target selection:** all mesh objects, or a chosen collection. Objects tagged as glass (by collection or a custom property, e.g. `matte_role = "glass"`) are excluded from IDs and keep their materials.
- **Matte view layer:** create a view layer named `ID_MATTES`. The beauty layer must stay untouched. Blender's built-in material override replaces every material (including glass), so use another method, e.g. swapping materials on a duplicated/linked scene or a shader-level switch that leaves glass intact. Pick the most robust option and document it.
- **Emission material:** one shared emission shader, color white (or R/G/B in density mode), strength 1.0, no other BSDF. Shadow and other ray visibility stay on so occlusion is correct.
- **Light groups:** one light group per ID (names like `id_0001`), assign each target object, enable the light group passes on `ID_MATTES`. Put the world in group `id_world` with black emission.
- **Render settings for this layer:** denoising off, multilayer EXR 32-bit float output. Small Film filter size (~1.0 px) optional.
- **Manifest:** JSON next to the render mapping light group name (and channel in RGB mode) to object name, plus the Cryptomatte hash of each name (see Part 2).
- **Cleanup:** "Clear" removes the view layer, light groups, temporary materials/scenes, restoring original assignments.
- **Performance guard:** warn when the group count is high (e.g. over 64); each pass is a full float buffer and GPU memory can run out.

## Part 2: EXR to Cryptomatte converter and Nuke use

A standalone Python script (NumPy + OpenEXR, runnable outside Blender, batch over frame sequences) that turns the light group passes into a spec-compliant Cryptomatte EXR.

1. **Read** every `id_*` light group layer from the multilayer EXR, plus the manifest JSON. In RGB mode, split each layer into 3 IDs.
2. **Per-ID value:** luminance of the pass in single-ID mode, or the single channel in RGB mode.
3. **Normalize:** coverage_i = value_i / sum of all values (including world) per pixel; pixels whose sum is below a small epsilon get zero coverage. This removes darkening from glass absorption and Fresnel loss.
4. **Rank:** keep the top N coverages per pixel (N = 6 default, configurable), sorted descending.
5. **Encode IDs per the Cryptomatte spec:** MurmurHash3_32 (seed 0) of the object name; if the float exponent bits are all 0 or all 1, flip bit 23; reinterpret the uint32 as float32.
6. **Write** layers `<name>00`, `<name>01`, … as RGBA: R = ID rank k, G = coverage k, B = ID rank k+1, A = coverage k+1. Header metadata: `cryptomatte/<key>/name`, `/hash` = `MurmurHash3_32`, `/conversion` = `uint32_to_float32`, `/manifest` = JSON of name to hex hash. Key = first 7 hex chars of the layer name's hash.
7. **Optionally** copy the beauty RGBA into the same EXR so artists load one file.

In Nuke, the output loads into the standard Cryptomatte gizmo with click-to-pick. For quick checks without the converter, Shuffle out any `id_*` layer and use it directly as a matte.

Reference: Cryptomatte specification (Psyop): https://github.com/Psyop/Cryptomatte. Verify spec details against it.

## Caveats

- **Reflections on glass:** the glass surface also reflects other emissive objects, so a pixel can show partial coverage of a reflected object. Usually acceptable; document it.
- **Tinted glass:** breaks RGB density mode; use single-ID mode or neutral glass in the matte layer.
- **Memory:** every light group adds a full-resolution float buffer. Test the practical maximum on our GPUs.
- **Version drift:** verify light group behavior (indirect light included, world assignment) on the installed Blender version first.
- **Denoising:** must stay off on the matte layer, or mattes bleed into each other.

## Acceptance tests

- [ ] Test scene: 3 cubes behind a glass sphere (IOR 1.45). Each cube's matte is visibly refracted and lines up with the beauty pass.
- [ ] Coverage of all IDs plus world sums to 1.0 (within 0.01) on every pixel with nonzero light.
- [ ] Output EXR loads in Nuke's Cryptomatte gizmo; picking each cube isolates it.
- [ ] Rough glass (roughness 0.3) gives soft, blurred mattes with no hard artifacts.
- [ ] 50-object scene renders in a single pass and converts without errors.
- [ ] "Clear Matte Layer" restores the scene exactly (beauty render unchanged before vs after).

## Build plan

- **Stage 1:** Blender add-on (single installable file with `bl_info`). Before coding, propose the method for applying emission only to the matte layer while keeping beauty and glass untouched, with trade-offs.
- **Stage 2:** Converter CLI: input EXR or frame pattern, manifest JSON, output path, `--ranks N` (default 6), `--layer-name`. Include a pure-Python MurmurHash3_32 with unit tests against known values.
- Verify Blender API names against the installed 4.x version (Claude Code can run `blender --background --python` to check). Don't guess properties.
- Add a README with install and usage steps. Stop after each stage for testing.
