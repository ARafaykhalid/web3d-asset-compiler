# Changelog

All notable changes to the **Web3D Asset Compiler** project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.0.0] - 2026-08-14

### Added
- **Unified Blender Addon Identity**: Consolidated **Auto HDR Baker** and **Three.js Animation Exporter** into a single installable package `web3d_asset_compiler`.
- **Automated Lightmap & Texture Baking**:
  - Multi-object UV packing modes: `Single Atlas`, `Auto Tiles`, `Collection Atlases`, and `Per Material`.
  - Pass filter selection (Direct, Indirect, Color) for passes including Combined, Diffuse, Glossy, Transmission, AO, Normal, Roughness, Emission, Environment, and Shadow.
  - Formats: 8/16-bit PNG, JPEG, TIFF, Radiance HDR (`.hdr`), and 32-bit float OpenEXR (`.exr`) with codec support (ZIP, ZIPS, PIZ, RLE, B44, DWAA).
  - Automated UV unwrapping (Smart UV Project, Auto Seam Unwrap, Cube Project, Lightmap Pack, Standard, Existing UVs).
  - Texel density control with World-Space Proportional packing and Image Texture Boost multipliers.
  - External UV packer integration (UVPackmaster 2 / 3) with graceful fallback to Blender native packing.
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
  - Build script (`scripts/build_addon.py`) generating production `dist/web3d_asset_compiler.zip`.
  - Validation script (`scripts/validate_addon.py`) verifying syntax, directory layout, and metadata.
  - Headless Blender integration test script (`tests/test_blender_addon_register.py`).
  - Comprehensive documentation in `docs/` and GitHub Actions CI workflow in `.github/workflows/ci.yml`.
