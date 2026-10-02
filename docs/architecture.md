# Addon Architecture & Internal Design

This document details the architectural layout of **Web3D Asset Compiler**.

## Package Structure

```
addon/web3d_asset_compiler/
├── __init__.py         # Extension entrypoint; registers every class
├── blender_manifest.toml # Extension platform manifest
├── baking/             # UV + lightmap baking stage
│   ├── properties.py   # AHB_Properties definition
│   ├── pipeline.py     # Baking execution, Cycles compute, UV packing, image saving
│   ├── json_io.py      # Serialization of materials & PBR settings to JSON
│   └── operators.py   # AHB_OT_* Blender operators
├── exporter/           # GLB + binary animation export stage
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
    └── logging_utils.py# Safe filenames & cheap redraw requests

scripts/
└── build_extension.py    # Packages dist/web3d_asset_compiler-<version>.zip

examples/
├── sample_threejs_loader.ts # Three.js integration example
├── sample_r3f_component.tsx # React Three Fiber integration example
└── package.json / tsconfig.json # Type-checks both against exporter output

tests/
├── test_blender_addon_register.py # Registration & operator coverage
├── test_blender_pipeline_smoke.py  # Headless bake + export pipeline
├── test_ui_draw.py                 # Every panel drawn with a real UILayout
└── export_fixtures.py              # Exports a rig for the examples typecheck
```

## Data Flow & Execution Pipeline

```
[ User Action: BUILD WEB ASSET ]
               │
               ▼
   [ 1. Preserve Source UVs & Materials ]
               │
               ▼
   [ 2. Generate UVs & Pack Islands ] ──► measure gutter + bounds
               │
               ▼
   [ 3. Configure Cycles Compute ] ──► OptiX / CUDA / HIP / oneAPI / Metal
               │
               ▼
   [ 4. Execute Lightmap Bake ] ──► runs as a WM job, cancellable
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

Stages 1-5 only run if every earlier stage succeeded: a failed or cancelled bake
never reaches export.

## Why the build is staged rather than one blocking call

Blender exposes asynchronous paths for very few things. Baking has one
(`object.bake` can start a WM job via `INVOKE_DEFAULT`, which also brings the
progress bar and ESC handling). UV operators have none at all — a single
`bpy.ops.uv.pack_islands` call is atomic and cannot be interrupted.

So the two halves are handled differently:

- **Baking** runs one WM job at a time. `_BakeRunner` starts a job, polls
  `bpy.app.is_job_running('OBJECT_BAKE')` from a timer, and starts the next batch
  when the previous one ends. Cancellation is observed through
  `bpy.app.handlers.object_bake_cancel`.
- **UV work** is a generator (`run_setup_steps`, `atlas_pack_phase_steps`) that
  yields between pack passes and between groups, driven from the same timer. The
  longest uninterruptible unit is therefore a single pack pass.

Both halves share one cancellation flag, surfaced as a **Cancel Build** button
that is only drawn while a build is in flight.

Deferred work must never touch the operator that started it: by the time a timer
callback runs, Blender has freed the operator's RNA struct. Reporting therefore
goes through `status_reporter()`, which writes to the scene properties instead of
`self.report`.
