# Web3D Asset Compiler

> Production-ready Blender extension pipeline for automated lightmap & texture baking, mesh optimization, and decoupled Three.js animation export.

[![Blender 5.1+](https://img.shields.io/badge/Blender-5.1%2B-orange.svg)](https://www.blender.org/)
[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](LICENSE)
[![Build Status](https://img.shields.io/badge/Build-Validated-brightgreen.svg)](#building-from-source)

---

## Overview

**Web3D Asset Compiler** turns complex Blender 3D scenes into production-ready WebGL assets. It unifies high-end texture baking (**Auto HDR Baker**) and optimized web animation export (**Three.js Exporter**) into a single, installable Blender extension.

```
Blender Scene ➔ Analyze ➔ Auto UV & Atlas ➔ Lightmap Bake ➔ Compress ➔ Decoupled Export ➔ Web 3D Asset
```

Designed specifically for:
- **Three.js** & **React Three Fiber (R3F)** applications
- Interactive 3D portfolio & marketing websites
- Mobile WebGL applications requiring strict payload budgets
- ArchViz lightmapped web walkthroughs

---

## Key Features

### 🎨 Automated Lightmap & Texture Baker (Auto HDR Baker v3.1)
- **Multi-Object Atlas Modes**: `Single Atlas`, `Auto Tiles` (multi-image quality splitting), `Collection Atlases` (numbered texture packs), and `Per Material`.
- **Comprehensive Bake Types**: `Combined`, `AO`, `Diffuse`, `Glossy`, `Transmission`, `Roughness`, `Normal`, `Emit`, `Environment`, `Shadow`, and `UV`.
- **Pass Toggle Filtering**: Toggle Direct, Indirect, and Color passes independently.
- **HDR & LDR Formats**: 8/16-bit PNG, JPEG, TIFF, Radiance HDR (`.hdr`), and 32-bit float OpenEXR (`.exr`) with compression codecs (ZIP, ZIPS, PIZ, RLE, B44, DWAA).
- **Automated UV Unwrapping**: Smart UV Project, Auto Seam Unwrap (angle-based sharp edge seam marking), Cube Project, Lightmap Pack, Standard Unwrap, or Existing UVs.
- **World-Space Proportional Packing**: Maintains consistent texel density (pixels per meter) across small trim pieces and large walls. Includes image texture space boost multipliers.
- **Third-Party Packer Integration**: Native support for **UVPackmaster 2 / 3** with automatic fallback to Blender native packing.
- **Cycles Hardware Acceleration**: Automatic Cycles compute selection for NVIDIA OptiX / CUDA, AMD HIP, Intel oneAPI, and Apple Metal.
- **Non-Destructive Material Application**: One-click toggle between pre-lit baked textures and original procedural shader graphs.
- **JSON Import/Export**: Save and reload complete material and bake metadata.

### 🎬 Web & Animation Exporter (Three.js Exporter v2.2)
- **Decoupled Architecture**: Exports an animation-free base GLB (`character.glb`) alongside standalone binary animation clips (`.anim`) and manifest files (`animation-manifest.json`).
- **Blender 5.1 Slotted Actions Support**: Built-in F-curve quarantine system that isolates incompatible curves to prevent glTF exporter crashes.
- **Evaluated Pose Sampling**: Samples dependency-graph poses at target FPS (1–240 FPS) to accurately capture IK, constraints, and Blender-to-glTF bone conversions.
- **Animation Quantization & Keyframe Reduction**: Tolerance-based keyframe reduction and `int16`/`uint16` quantization for minimal network payload size.
- **Draco Mesh & Texture Compression**: Integrated Draco mesh compression and real-time image format conversion (Auto, JPEG, WebP, None).
- **TypeScript Controller Generator**: Auto-generates a binary decoder, per-animation asset modules, and a type-safe `loadAnimatedModel()` controller with lazy loading, hash validation, cross-fading, loop modes, and disposal.

---

## Supported Versions & Export Targets

- **Blender Version**: 5.1.0 or higher
- **Maintainer**: Abdul Rafay Khalid (`ARafayKhalid`)
- **Supported Export Targets**:
  - Three.js (r150+)
  - React Three Fiber (`@react-three/fiber`)
  - Generic glTF / GLB viewers
  - Mobile & WebGL engines

---

## Installation

1. Download `web3d_asset_compiler-1.0.0.zip` from [Releases](https://github.com/ARafayKhalid/web3d-asset-compiler/releases) (or build locally).
2. Open Blender 5.1+.
3. Navigate to **Edit ➔ Preferences ➔ Extensions** (or **Get Extensions**).
4. Click the top-right menu and select **Install from Disk...**
5. Select `web3d_asset_compiler-1.0.0.zip` and enable the extension.
6. Access the panel in the 3D Viewport N-Panel under the **Web3D** tab or in **Properties ➔ Render**.

---

## Quick Start

1. **Select Scoped Objects**: Select the mesh objects or character rig in your scene.
2. **Open Web3D Sidebar**: Press `N` in the 3D Viewport and click the **Web3D** tab.
3. **Choose Preset**: Select a target preset (for example, `React Three Fiber - Balanced`).
4. **Click Build Web Asset**: Click **Build Web Asset**. The build stops before export if baking fails.
5. **View Output**: The extension will automatically unwrap, pack UVs, bake lightmaps, apply pre-lit shaders, compress the geometry, and write the output files into your specified output folder.

Character animation exports contain `character.glb`, `animation-manifest.json`,
`animations/*.anim`, `animationClipFactory.ts`, `model_controller.ts`, and typed
modules under `animations/*.ts`.

---

## Architecture & Repository Structure

```
web3d-asset-compiler/
├── README.md                   # Product documentation & instructions
├── LICENSE                     # GNU GPL v3 license & third-party attributions
├── CHANGELOG.md                # Release history
├── CONTRIBUTING.md             # Developer guidelines
├── .gitignore                  # Git ignore rules
│
├── addon/
│   └── web3d_asset_compiler/   # Complete installable Blender extension package
│       ├── blender_manifest.toml# Official Blender extension manifest
│       ├── __init__.py         # Addon entrypoint
│       ├── version.py          # Version metadata (v1.0.0, Abdul Rafay Khalid)
│       ├── baking/             # Auto HDR Baker system
│       ├── exporter/           # Three.js Exporter system
│       ├── presets/            # Optimization preset definitions
│       ├── ui/                 # 3D Viewport N-Panel & Render panel
│       └── utils/              # Mesh, UV, and logging utilities
│
├── docs/                       # In-depth technical guides
│   ├── installation.md
│   ├── usage.md
│   ├── baking.md
│   ├── exporting.md
│   ├── presets.md
│   ├── architecture.md
│   └── blender-extensions-submission.md
│
├── examples/                   # Web integration code examples (R3F & Three.js)
│   ├── sample_r3f_component.tsx
│   └── sample_threejs_loader.ts
│
├── scripts/                    # Extension build & validation tooling
│   ├── build_extension.py      # Packages dist/web3d_asset_compiler-1.0.0.zip
│   └── validate_extension.py   # Validates TOML manifest, syntax, and cleanliness
│
└── tests/                      # Automated Python and headless Blender tests
    ├── test_package_structure.py
    ├── test_extension_manifest.py
    └── test_blender_addon_register.py
```

---

## Building from Source

To package the installable Extension ZIP file:

```bash
python scripts/build_extension.py
```

Output archive:
`dist/web3d_asset_compiler-1.0.0.zip`

---

## License

This project is licensed under the **GNU General Public License v3.0 (GPL-3.0-or-later)**. See [LICENSE](LICENSE) for complete details.
