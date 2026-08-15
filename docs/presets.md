# Optimization Presets Guide

Web3D Asset Compiler provides four built-in optimization presets.

| Preset | Target Use Case | Baking Resolution | Image Mode | Animation Quantization | Mesh Compression |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **React Three Fiber - Balanced** | Modern web apps | 2K (2048) | Single Atlas | Int16 / Uint16 Enabled | Draco Level 6 |
| **Three.js - High Quality** | Portfolio showcases | 4K (4096) | Single Atlas | High Precision Float | Uncompressed GLB |
| **Mobile Web - Lightweight** | Mobile WebGL | 1K (1024) | Auto Tiles (4) | Int16 / Uint16 Enabled | Draco Level 8 |
| **ArchViz - Lightmap Quality** | ArchViz walkthroughs | 4K (4096) EXR | Single Atlas | Uncompressed | Uncompressed |

## Customizing Presets

To override preset values, simply select a preset and adjust individual parameters in the **Texture & Lightmap Baker** or **Web Animation & Model Exporter** sections.
