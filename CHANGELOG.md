# Changelog

All notable changes to the **Web3D Asset Compiler** project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.1.0] - 2026-10-03

Behaviour changes to be aware of when re-baking with unchanged settings:
- The requested gutter is now honoured exactly. Blender applies the pack margin
  per island side, so `Pack Margin (px)` used to produce roughly double the
  gutter you asked for and waste about half the atlas.
- The bake filter inset is clamped to half the gutter the pack actually
  achieved, so it can no longer exceed the gap it sits in. On a dense atlas
  this reduces the inset and reports the shortfall instead of bleeding.
- `Shape Method` now defaults to Concave, Blender's own default. Convex remains
  available. Convex is the least-tested packer path and tripped an assert on a
  plain cube in 5.2.0.
- Atlas packing is measurably slower and is now verified after every pack.
  Reliability was chosen over speed; see the Fixed sections below.

Verified against Blender 5.2.2 LTS. Opening a `.blend` saved by 1.0.0 works:
the removed settings drop silently, scene data is untouched, and the pipeline
bakes normally.


Fixed against Blender 5.2.2 LTS.

### Fixed — responsiveness
- **UV packing no longer locks the interface for minutes.** `run_blender_pack` hardcoded `scale=True` while the defaults already enabled free rotation and convex-hull shapes, which sends Blender's packer into an exhaustive scaling-and-rotation search. Measured on a single 8-vertex cube: **59.6 s per call**, multiplied by the default `pack_iterations = 3`. Now 1.9 s.
- **Baking now runs as a Blender job instead of blocking.** `bpy.ops.object.bake()` was called from `execute()`, which resolves to the blocking `bake_exec` path: no progress bar and, by design in the C source, no ESC handling. It is now invoked with `INVOKE_DEFAULT`, driven batch-by-batch from `bpy.app.timers`, and observes `object_bake_cancel` so ESC aborts the whole run.
- `force_ui_redraw` no longer performs a synchronous `wm.redraw_timer` window swap, which measured 0.79 s per call — longer than the work it was announcing. It tags the area redraw instead.
- **UV setup no longer blocks either.** `bpy.ops.uv.*` has no invoke path in Blender, so a single `pack_islands` call is atomic. The unwrap and pack stages are now generators that yield between passes and between groups, driven from the same timer as the bake. `Build Web Asset` returns in ~1 ms instead of holding the window for the whole setup.
- **Added a Cancel button** to the sidebar, visible only while a build is running. Both runners check it between steps, so cancelling works even where Blender gives us no ESC event (the bake itself still honours ESC through `object_bake_cancel`).

Progress now reports per pack pass, e.g. `Packing 2 object(s) — pass 2/3`.

One `pack_islands` pass is the floor and is not avoidable from Python. Measured on an idle machine at 512 px, four tiles of two cubes, `pack_iterations = 3`: 12 passes at 7.5 s growing to 15.7 s, mean 11.1 s, 133 s total. Blender searches island scaling and free rotation together, so `pack_iterations = 3` costs ~3x and `pack_rotation_step = 'ANY'` costs ~6x on top.

Those are quality knobs rather than bugs, so the defaults were deliberately left alone. The UI already exposes `pack_iterations`, `pack_rotate`, `pack_rotation_step` and `pack_shape_method`; the status line now shows the pass number so the cost is visible while it runs, and Cancel stops between passes. For fast iteration, set `pack_iterations = 1` or `pack_shape_method = 'AABB'` (a pass then takes milliseconds).

End-to-end build for four 512² tiles: **54.9 s → 8.1 s**, with the bake portion now cancellable.

### Fixed — data loss
- Bake images are tagged with an ownership property. Previously the addon scanned `bpy.data.images` for the user-editable `output_prefix` and could overwrite, re-point, or delete unrelated user textures named e.g. `bake_dirt.png`. `get_or_create_image` also removed a colliding user datablock outright, leaving every material node pointing at nothing. Colliding names now get a numeric suffix instead.
- `save_image` restores `image.filepath_raw`, which was permanently re-pointed at the output directory.
- `remove_tile_uv_offsets` / `offset_tile_uvs` validated `uv_layer_name` and then edited the *active* UV layer, silently shifting the user's production render UVs.
- `enter_edit_select_all` and the UV operators deselect other objects before `mode_set`, so they no longer run in accidental multi-object edit mode and re-unwrap the whole selection once per object.
- `clear_and_renew_auto_seams` snapshots seams and restores them if the unwrap fails, instead of returning a mesh with every seam stripped.
- `isolate_materials_between_atlas_groups` records the original material name after copying, stopping one duplicate material per extra atlas.

### Fixed — UV atlas reliability
The packer was silently producing invalid atlases. Both root causes were in `run_blender_pack`, and both are verified by a new post-pack check:

- **`pixel_margin_to_uv` was double the intended gutter.** Blender applies `margin_method='FRACTION'` *per island side*, so `16px / 512 = 0.03125` produced a 32px gap and wasted roughly half the atlas. It now halves the value. This also matches `configure_bake_settings`, which already clamps the bake margin to `pack_margin_px // 2`.
- **UVs overflowed the tile.** Blender only runs the margin line search when at least one island is scalable (`uv_pack.cc`: `can_scale_count > 0`). With `scale=False` the packer filled the space first and then added margin on top, pushing results past 1.0 — measured at 512px, a two-cube tile, 16px requested: UVs reached **1.042**, bleeding ~21px into the neighbouring tile, and `run_setup` still reported "Setup done". `scale=True` is now set because it is required for the result to fit, not as a density preference:

  | config | gutter | coverage | UV range | |
  |---|---|---|---|---|
  | `FRACTION scale=False` | 16.0px | 65.3% | **1.042** | overflow |
  | `FRACTION scale=True` | **16.0px** | 57.4% | 0.984 | correct |
  | `SCALED margin=0.056` | 16.0px | 65.3% | **1.043** | overflow |

- **Added `verify_uv_pack`**, run after every pack. It reports UVs outside the 0–1 tile with the bleed in pixels, and the achieved gutter against the requested `pack_margin_px`, then sets `props.pack_verified`. A positive gutter between island bounds also proves no two islands intersect, so it covers overlap detection too. Blender's `bpy.ops.uv.select_overlap` is deliberately *not* used: in 5.2 the UV selection moved to bmesh, is not readable from Python, and `tool_settings.use_uv_select_sync` does not carry the result back — it returns zero even for fully coincident islands.
- **`pack_islands` no longer leaks into user settings.** Passing `margin=` writes `scene.tool_settings.uvcalc_margin`, the user's *unpack* margin; it is saved and restored around every pass.

This trades time for correctness: the correct configuration is materially slower than the broken one. Progress is reported per pass (`Packing 2 object(s) — pass 2/3`) and Cancel works between passes.

### Fixed — correctness
- `run_apply` no longer falls back to the single-atlas image in `Per Material` mode, which silently wired materials to an unrelated UV layout and still reported success.
- `do_bake_batch` surfaces the retry's error rather than re-raising the stale first one.
- `configure_cycles_device` restores the user's global Cycles preferences when no GPU is found.
- `distribute_tiles` weights tiles by world area, matching the world-space UV path (it used object-local area, so scaled objects got equal shares).
- `OPEN_EXR_MULTILAYER` is written as a real multilayer EXR instead of being silently flattened.
- `restore_image_uv_backups` receives the configured bake-node tag.
- Bake images set `ImageFormatSettings.media_type` before `file_format`, required from Blender 5.0.

### Fixed — second pass
- **`safe_set_colorspace` no longer fails silently.** On failure it returned `None` and left data bakes (AO, Normal, Roughness, Shadow, UV) on the default sRGB curve — every stored value gamma-shifted, producing a lightmap that looks plausible in the viewport and is wrong in the render. It now raises with the list of curves it tried.
- **`Preview Bake UVs` no longer lies about the result.** Both tile and collection-atlas previews shifted UVs horizontally by the tile index, but the pipeline never does, and each tile/atlas has its own image (`bake_tile_01`, …) — so the shift pushed UVs outside the image they bake into. The collection-atlas branch also offset by `enumerate` index instead of atlas number, drifting whenever a collection was missing. Offsets removed along with the now-unused helper.
- **Objects in two atlas collections are no longer dropped silently.** `get_atlas_groups` collected the information and never reported it, so the object count came back lower than the scene with no hint a texture pack was short.
- **Material backups no longer leak or hijack user data.** Backups were found by the name `M_AHB_backup`. When a user already owned that name Blender uniquified our copy to `M_AHB_backup.001`, so the lookup never found it and *every* call created another backup; without a collision it adopted the user's own material as ours. Backups are now keyed on an ownership marker plus a recorded source name.
- **`restore_material_backups` leaves out-of-scope users alone.** It renamed a material to `M_AHB_modified` even when `users > 0`, leaving objects outside the restored scope pointing at a mangled name with no way back.
- **Object renaming is idempotent.** Renaming from a name-only check stacked suffixes whenever the tile count changed (`Chair_bake_tile_03_bake_tile_01`), and `str.replace("Tile", "tile")` rewrote the user's own words (`CeramicTile_Mat` → `Ceramic_tile_Mat`). The pre-rename name is now recorded on the object, so later runs are exact and a real name containing the prefix is never truncated.
- **`stack_similar_islands` measures in UV space.** It gated a UV-shape comparison with `face.calc_area()` / `calc_perimeter()`, which are 3D — so identical UV islands on differently scaled meshes never stacked and the function silently did nothing.
- **Successful bakes no longer report failure.** `apply_grouped_bake_images` counted `skipped` in material *slots* while `applied` counted materials, so one material used twice sank the build; a failed image *write* also incremented the bake error count and aborted over a filesystem problem. Save failures are now counted and reported separately.
- **`validate_bake_ready` propagates its reason** instead of a generic "missing UV layer", and its return contract is documented (`None` = ready, string = why not) after the switch briefly inverted three call sites into always-fail.
- **`pack_shape_method` now defaults to `CONCAVE`**, Blender's own default. `CONVEX` is the least-tested packer path and tripped a `BLI_assert` on a plain cube in 5.2.0 (upstream #157473).

### Fixed — UV measurement and a hang, found by testing at realistic scale
The pack verifier reported a zero gutter on every non-trivial scene while the packing itself was correct. Four defects in the measuring code, each caught by running a real scene instead of a cube:

- **Island detection compared faces, not islands.** Faces sharing an edge inside one island touch, so every island reported a zero gutter. It now flood-fills actual UV islands.
- **`smart_project` marks no seams in Blender 5.2.** Measured on a plain cube: zero `use_seam`, zero sharp edges, after a successful unwrap. It splits islands purely by separating coincident UVs, so seam-walking merges every face of an object into one island. Island detection now uses UV connectivity — two faces joined when they share a mesh edge whose UVs are identical on both sides. The same defect silently disabled `stack_similar_islands`.
- **`BMEdge` exposes no loop accessor in 5.2**, so adjacency is built from the face loops directly.
- **The spatial hash was sized wrong.** Its cell was ~7.7 px while the gutter being validated was 16 px, so neighbouring islands sat two cells apart and the 3×3 scan never compared them — a correctly packed 16.00 px atlas measured as `inf`, then printed as `0.0`. Cells are now sized to the distance actually being checked, and the no-target path falls back to a brute-force scan instead of returning a misleading zero. Cross-checked against brute force: 16.00 px vs 15.99995 px.
- **`_BakeRunner.run()` hung in an interactive session.** `do_bake_batch` returns `RUNNING_MODAL` outside `--background`, and nothing would ever service the job, so the driver spun forever. It now raises instead of hanging, and `run_bake()` always takes the async path outside `--background`.

### Added — the two untested surfaces are now covered
- **`tests/test_ui_draw.py`** drives every sidebar and render draw function with a genuine `UILayout`, across 44 property and scene configurations (every image and UV mode, packer options, export formats, an empty scene, a mesh with no material, a rigged animated character, and the post-bake, cancelled, bad-pack, and build-in-flight states). It exists because Blender swallows exceptions raised inside `Panel.draw`: a panel referencing a removed property still registers cleanly and only breaks when a user opens that tab. Properties-space panels are not category-gated, so a throwaway panel there is guaranteed to be drawn and gets a real layout; the addon's own `WEB3D_PT_RenderProps` is drawn in the same pass. Verified to report 528 draw calls and to exit non-zero on an injected fault.
- **`tests/export_fixtures.py` plus `examples/package.json` and `examples/tsconfig.json`** type-check the web examples against real exporter output with real `three`, `react` and `@react-three/fiber` types, under `strict` plus `noUnusedLocals` and `noUnusedParameters`. The examples are the only documentation of the generated controller's API and drift silently when it changes. Both currently compile clean.

### Added — the bake inset follows the real gutter
A dense atlas genuinely cannot hold the requested gutter: 1920 islands at 1024 px achieved 0 px against a 16 px request, which the verifier reports as a warning telling the user to raise the resolution. The achieved gutter is recorded per build (worst group wins, since the bake margin is global) and `bake.margin` is clamped to half of it, so the filter inset can never exceed the gap it sits in. `-1` marks "not measured" so a real 0 px gutter is honoured rather than treated as unknown.

### Removed
- The optional third-party UV packer integration, in full: enum entries, three silent fallback branches per pack call, and every mention in the docs. It could never run unless a separate closed-source binary was installed, and calling such a binary from a GPL-3.0 addon puts the extension's licence in question. Atlas packing now has exactly one code path.
- A dead save/restore of `scene.tool_settings.uvcalc_margin` — that property does not exist in Blender 5.2, so the guard never fired.

### Changed
- **Removed** `old/` (6206 lines) — a byte-for-byte duplicate of the upstream snapshot that also replaced the original author's name in `bl_info`.
- **Removed** the optional third-party UV packer integration. It could never run unless
  a separate proprietary binary was installed, added three silent fallback branches to
  every pack call, and calling a closed-source engine from a GPL-3.0 addon puts the whole
  extension's licence in question. Atlas packing now has exactly one code path.
- **Removed** `imported_source/` (3861 lines) — the pre-rewrite upstream scripts. Nothing imported, built, or tested them. Still recoverable from commits `e141313` and `a68b2d6`.
- **Removed** `scripts/build_addon.py` and `scripts/validate_addon.py`; CI built the legacy-layout artifact while the README documented a different one.
- **Removed** `scripts/validate_extension.py`. It reimplemented Blender's manifest validator while missing the tagline trailing-character rule, and CI never ran it. CI now calls `blender --command extension validate`.
- **Removed** `version.py` and `bl_info`; the manifest is the single source of truth and `bl_info` is ignored for extensions.
- **Removed** the `quantize_vectors` compatibility shim for a property that never shipped, and the legacy `action.fcurves` fallback removed in Blender 5.0.
- Collapsed the duplicated UV-unwrap dispatch and edit-mode selection preludes into `unwrap_dispatch`, `enter_edit_select_all`, and `enter_edit_multi_select_all`.
- `registration` no longer swallows every exception, which could leave the addon half-registered and silently broken.

### Added
- CI downloads and caches Blender, validates the manifest with Blender's own validator, rejects hardcoded local paths, and runs the end-to-end suite with `--python-exit-code 1`.

## [1.0.0] - 2026-08-14

### Added
- **Unified Blender Addon Identity**: Consolidated **Auto HDR Baker** and **Three.js Animation Exporter** into a single installable package `web3d_asset_compiler`.
- **Automated Lightmap & Texture Baking**:
  - Multi-object UV packing modes: `Single Atlas`, `Auto Tiles`, `Collection Atlases`, and `Per Material`.
  - Pass filter selection (Direct, Indirect, Color) for passes including Combined, Diffuse, Glossy, Transmission, AO, Normal, Roughness, Emission, Environment, and Shadow.
  - Formats: 8/16-bit PNG, JPEG, TIFF, Radiance HDR (`.hdr`), and 32-bit float OpenEXR (`.exr`) with codec support (ZIP, ZIPS, PIZ, RLE, B44, DWAA).
  - Automated UV unwrapping (Smart UV Project, Auto Seam Unwrap, Cube Project, Lightmap Pack, Standard, Existing UVs).
  - Texel density control with World-Space Proportional packing and Image Texture Boost multipliers.
  - Cycles hardware compute support for NVIDIA OptiX / CUDA, AMD HIP, Intel oneAPI, and Apple Metal.
  - Non-destructive material application (`Show Original Materials` vs. `Show Baked Textures`).
  - Material and bake settings export/import via JSON files.
- **Three.js & Web 3D Asset Export**:
  - Decoupled export architecture generating animation-free base GLB models (`character.glb`) alongside standalone binary animation clips (`.anim`) and manifest files (`animation-manifest.json`).
  - Blender 5.1 slotted action support with F-curve safety quarantine to prevent glTF exporter crashes.
  - Evaluated pose sampling at target FPS (1-240 FPS) capturing IK, constraints, and bone conversions.
  - Keyframe reduction (position, rotation, scale, morph tolerances) and rest-pose track removal.
  - Data quantization (`int16`/`uint16` values) for minimal network payload size.
  - Built-in Draco mesh compression and real-time image format conversion (Auto, JPEG, WebP, None).
  - Automated TypeScript controller generator (`loadAnimatedModel()`, `AnimatedModelController`).
- **Repository & Tooling Infrastructure**:
  - Build script (`scripts/build_extension.py`) generating production `dist/web3d_asset_compiler-1.0.0.zip`.
  - Extension platform validation script (`scripts/validate_extension.py`) verifying manifest metadata, syntax, and directory layout.
  - Headless Blender registration test script (`tests/test_blender_addon_register.py`) verifying all 25 registered operators and scene properties.
  - Runtime pipeline smoke test script (`tests/test_blender_pipeline_smoke.py`) testing registration symmetry, quick baking, model export, and binary character animation export.
  - Comprehensive documentation in `docs/` and GitHub Actions CI workflow in `.github/workflows/ci.yml`.
