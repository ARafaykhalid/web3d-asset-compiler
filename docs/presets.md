# Optimization Presets Guide

Web3D Asset Compiler provides four built-in optimization presets.

| Preset | Target Use Case | Baking Resolution | Image Mode | Animation Quantization | Mesh Compression |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **React Three Fiber - Balanced** | Modern web apps | 2K (2048) | Single Atlas | Int16 / Uint16 Enabled | Draco Level 6 |
| **Three.js - High Quality** | Portfolio showcases | 4K (4096) | Single Atlas | High Precision Float | Uncompressed GLB |
| **Mobile Web - Lightweight** | Mobile WebGL | 1K (1024) | Auto Tiles (4) | Int16 / Uint16 Enabled | Draco Level 8 |
| **ArchViz - Lightmap Quality** | ArchViz walkthroughs | 4K (4096) EXR | Single Atlas | Uncompressed | Uncompressed |

## Customizing Presets

Apply a preset, then adjust any individual parameter — presets only set values,
they do not lock them. Baking settings live under **Texture & Lightmap Baker**,
export settings under **Web Animation & Model Exporter**.

Presets change the atlas resolution and image mode, so they also change the
expected gutter and texel density. After a preset-driven bake the pack is
verified like any other: check the reported gutter in the status line before
shipping.

## Bake Time

Resolution is the dominant cost. 4K (`4096`) presets such as **Three.js - High
Quality** and **ArchViz - Lightmap Quality** take considerably longer than 1K,
and the pack step alone scales with the square of the resolution. Bake as a
background job: the interface stays responsive and <kbd>Esc</kbd> cancels.
