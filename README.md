# Web3D Asset Compiler

> Production-ready Blender extension pipeline for automated lightmap & texture baking, mesh optimization, and decoupled Three.js animation export.

[![Blender 5.1+](https://img.shields.io/badge/Blender-5.1%2B-orange.svg)](https://www.blender.org/)
[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](LICENSE)
[![Build Status](https://img.shields.io/badge/Build-Validated-brightgreen.svg)](#building-from-source)

---

## Overview

**Web3D Asset Compiler** turns complex Blender 3D scenes into production-ready WebGL assets. Texture baking and web animation export live in a single installable extension, driven by one **Build Web Asset** action.

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

### 🎨 Automated Lightmap & Texture Baker
- **Multi-Object Atlas Modes**: `Single Atlas`, `Auto Tiles` (multi-image quality splitting), `Collection Atlases` (numbered texture packs), and `Per Material`.
- **Comprehensive Bake Types**: `Combined`, `AO`, `Diffuse`, `Glossy`, `Transmission`, `Roughness`, `Normal`, `Emit`, `Environment`, `Shadow`, and `UV`.
- **Pass Toggle Filtering**: Toggle Direct, Indirect, and Color passes independently.
- **HDR & LDR Formats**: 8/16-bit PNG, JPEG, TIFF, Radiance HDR (`.hdr`), and 32-bit float OpenEXR (`.exr`), single- or multi-layer, with compression codecs (ZIP, ZIPS, PIZ, RLE, B44, DWAA).
- **Automated UV Unwrapping**: Smart UV Project, Auto Seam Unwrap (angle-based sharp edge seam marking), Cube Project, Lightmap Pack, Standard Unwrap, or Existing UVs.
- **Exact Gutter Control**: `Pack Margin` is the real clear width between islands, honoured to the pixel. Blender applies its pack margin per island side, which previously doubled it and wasted roughly half of every atlas.
- **Verified Every Pack**: after packing, the addon measures the result and reports the island count, the achieved gutter in pixels and the UV range. UVs outside the 0-1 tile are raised as errors with the bleed distance, a too-narrow gutter is reported as a warning, and the bake filter inset is clamped to what was actually achieved.
- **World-Space Proportional Packing**: Maintains consistent texel density (pixels per meter) across small trim pieces and large walls. Includes image texture space boost multipliers.
- **Cycles Hardware Acceleration**: Automatic Cycles compute selection for NVIDIA OptiX / CUDA, AMD HIP, Intel oneAPI, and Apple Metal, and your global Cycles preferences are restored if no GPU is found.
- **Non-Destructive Material Application**: One-click toggle between pre-lit baked textures and original procedural shader graphs.
- **JSON Import/Export**: Save and reload complete material and bake metadata.
- **Safe by Construction**: bake images are tracked by ownership rather than by filename prefix, so textures of yours that happen to share the bake prefix are never overwritten or deleted.

### 🎬 Web & Animation Exporter
- **Decoupled Architecture**: Exports an animation-free base GLB (`character.glb`) alongside standalone binary animation clips (`.anim`) and manifest files (`animation-manifest.json`).
- **Responsive and Cancellable Builds**: UV setup and baking yield between steps, and the bake itself runs as a Blender job, so the interface stays live with a real progress bar. <kbd>Esc</kbd> aborts, and a **Cancel Build** button appears in the panel while a build is in flight.
- **Blender 5.1 Slotted Actions Support**: Built-in F-curve quarantine system that isolates incompatible curves to prevent glTF exporter crashes.
- **Evaluated Pose Sampling**: Samples dependency-graph poses at target FPS (1–240 FPS) to accurately capture IK, constraints, and Blender-to-glTF bone conversions.
- **Animation Quantization & Keyframe Reduction**: Tolerance-based keyframe reduction and `int16`/`uint16` quantization for minimal network payload size.
- **Draco Mesh & Texture Compression**: Integrated Draco mesh compression and real-time image format conversion (Auto, JPEG, WebP, None).
- **TypeScript Controller Generator**: Auto-generates a binary decoder, per-animation asset modules, and a type-safe `loadAnimatedModel()` controller with lazy loading, hash validation, cross-fading, loop modes, and disposal.
- **Type-Checked Client**: the generated modules and the integration examples in [examples/](examples/) are compiled against real `three` and `@react-three/fiber` types on every build, so the documented API cannot drift from the generated one.

---

## Supported Versions & Export Targets

- **Blender Version**: 5.1.0 or higher (tested against 5.2.2 LTS)
- **Maintainer**: Abdul Rafay Khalid (`ARafayKhalid`)
- **Supported Export Targets**:
  - Three.js (r150+)
  - React Three Fiber (`@react-three/fiber`)
  - Generic glTF / GLB viewers
  - Mobile & WebGL engines

---

## Installation

**Requirements**: Blender 5.1.0 or newer (built and tested against 5.2.2 LTS). Windows, macOS or Linux. A GPU is recommended but not required.

1. Download the latest `web3d_asset_compiler-<version>.zip` from [Releases](https://github.com/ARafayKhalid/web3d-asset-compiler/releases), or [build it locally](#building-from-source).
2. In Blender, open **Edit ➔ Preferences ➔ Get Extensions**.
3. Click the dropdown in the top-right corner and choose **Install from Disk...**
4. Select the downloaded `.zip`, then tick **Web3D Asset Compiler** to enable it.
5. Open the panel: press `N` in the 3D Viewport and click the **Web3D** tab, or use **Properties ➔ Render**.

There is nothing else to install. Atlas packing uses Blender's own UV packer, and
mesh compression reuses the Draco support already bundled with Blender's glTF
exporter, falling back to uncompressed data when it is unavailable.

---

## Quick Start

1. **Select Scoped Objects**: Select the mesh objects or character rig in your scene. Scope defaults to *All Selected*; it can also be *Active Only* or *All Visible*.
2. **Open Web3D Sidebar**: Press `N` in the 3D Viewport and click the **Web3D** tab.
3. **Choose Preset**: Select a target preset, for example **React Three Fiber - Balanced**. See [docs/presets.md](docs/presets.md) for what each one sets.
4. **Click Build Web Asset**: The build runs in this order —

   1. material slot verification and backup
   2. UV unwrap, island packing and **pack verification**
   3. bake-node injection and Cycles lightmap baking
   4. automatic saving of baked textures to the output directory
   5. non-destructive rewiring of the material graph for a pre-lit viewport
   6. decoupled GLB model and binary animation clip export
   7. TypeScript controller and manifest module generation

   Baking stops before export if it fails, so you never publish an unbacked model.
5. **Cancel at any time**: the bake runs as a background job, so the interface stays responsive. Press <kbd>Esc</kbd>, or click **Cancel Build** in the panel while the build is running.
6. **View Output**: files are written to your configured output folder.

Character animation exports contain `character.glb`, `animation-manifest.json`,
`animations/*.anim`, `animationClipFactory.ts`, `model_controller.ts`, and typed
modules under `animations/*.ts`. Working integration code for both Three.js and
React Three Fiber is in [examples/](examples/).

To compare a baked result against your original shaders at any time, use
**Show Original Materials** / **Show Baked Result**.

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
│       ├── __init__.py         # Extension entrypoint (registers classes)
│       ├── baking/             # UV + lightmap baking stage
│       ├── exporter/           # GLB + binary animation export stage
│       ├── presets/            # Optimization preset definitions
│       ├── ui/                 # 3D Viewport N-Panel & Render panel
│       └── utils/              # Mesh, UV, and logging utilities
│
├── docs/                       # In-depth technical guides
│   ├── architecture.md         # Package layout & execution pipeline
│   ├── baking.md               # Lightmap & texture baking
│   ├── exporting.md            # Model & binary animation export
│   └── presets.md              # What each optimization preset sets
│
├── examples/                   # Web integration code (R3F & Three.js)
│   ├── sample_r3f_component.tsx
│   ├── sample_threejs_loader.ts
│   ├── package.json            # Type-checks the examples against exporter output
│   └── tsconfig.json
│
├── scripts/
│   └── build_extension.py      # Packages dist/web3d_asset_compiler-<version>.zip
│
└── tests/                      # Blender end-to-end tests
    ├── test_blender_addon_register.py  # Registration & operator coverage
    ├── test_blender_pipeline_smoke.py  # Headless bake + export pipeline
    ├── test_ui_draw.py                 # Every panel drawn with a real layout
    └── export_fixtures.py              # Exports a rig for the examples typecheck
```

---

## Building from Source

To package the installable Extension ZIP file:

```bash
blender --command extension validate addon/web3d_asset_compiler --valid-tags=""
python scripts/build_extension.py
```

Output archive:
`dist/web3d_asset_compiler-1.1.0.zip`

Run the test suite the same way CI does:

```bash
blender --background --factory-startup -noaudio \
  --python-exit-code 1 --python tests/test_blender_pipeline_smoke.py
```

---

## License

This project is licensed under the **GNU General Public License v3.0 (GPL-3.0-or-later)**. See [LICENSE](LICENSE) for complete details.
