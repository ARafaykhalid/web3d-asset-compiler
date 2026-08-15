# Addon Architecture & Internal Design

This document details the architectural layout of **Web3D Asset Compiler**.

## Package Structure

```
addon/web3d_asset_compiler/
├── __init__.py         # Addon entrypoint & idempotent class registration
├── blender_manifest.toml # Extension platform manifest
├── version.py          # Version metadata (Single Source of Truth)
├── baking/             # Auto HDR Baker system
│   ├── properties.py   # AHB_Properties definition
│   ├── pipeline.py     # Baking execution, Cycles compute, UV packing, image saving
│   ├── json_io.py      # Serialization of materials & PBR settings to JSON
│   └── operators.py   # AHB_OT_* Blender operators
├── exporter/           # Three.js Exporter system
│   ├── properties.py   # TJS_Properties definition
│   ├── character_exporter.py # Animation-free GLB model export
│   ├── binary_exporter.py    # Binary animation encoding (.anim) & manifest
│   ├── quarantine.py   # F-curve quarantine for Blender 5.1 slotted actions
│   ├── ts_generator.py # Type-safe TypeScript module generator
│   └── operators.py   # TJS_OT_* Blender operators
├── presets/            # Presets module
│   ├── preset_data.py  # Dictionary definitions for target presets
│   └── operators.py   # WEB3D_OT_ApplyPreset operator
├── ui/                 # User Interface module
│   ├── sidebar.py      # 3D Viewport N-Panel (Web3D tab)
│   ├── render_panel.py # Properties Editor render properties panel
│   └── operators.py   # WEB3D_OT_BuildWebAsset primary action operator
└── utils/              # Helper utilities
    ├── mesh_utils.py   # Mesh data uniqueness & slot preparation
    ├── uv_utils.py     # UV map backups & implicit coordinate preservation
    └── logging_utils.py# Safe filenames & UI redraw helpers

scripts/
├── build_extension.py    # Packages dist/web3d_asset_compiler-1.0.0.zip
└── validate_extension.py # Validates manifest, metadata, and syntax

tests/
├── test_blender_addon_register.py # Registration & operator coverage test
├── test_blender_pipeline_smoke.py  # Headless baking & export pipeline smoke test
├── test_extension_manifest.py     # Manifest validation unittest
└── test_package_structure.py      # Package file layout unittest
```

## Data Flow & Execution Pipeline

```
[ User Action: BUILD WEB ASSET ]
               │
               ▼
   [ 1. Preserve Source UVs & Materials ]
               │
               ▼
   [ 2. Generate UVs & Pack Islands ] ──► UVPackmaster / Blender Native
               │
               ▼
   [ 3. Configure Cycles Compute ] ──► OptiX / CUDA / HIP / Metal / CPU
               │
               ▼
   [ 4. Execute Lightmap Bake ]
               │
               ▼
   [ 5. Save Images & Rewire Shaders ] ──► Emission / Base Color Shader
               │
               ▼
   [ 6. Quarantine Slotted F-Curves ]
               │
               ▼
   [ 7. Export Base GLB & Binary Animations ] ──► Draco Compression
               │
               ▼
   [ 8. Generate TS Controller & Manifest ]
```
