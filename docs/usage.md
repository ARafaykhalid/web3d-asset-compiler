# User Guide

The **Web3D Asset Compiler** provides a unified workflow for preparing Blender scenes for WebGL deployment.

## Workflow Overview

```
1. Prepare Scene  ➔  2. Choose Scope  ➔  3. Select Preset  ➔  4. Execute Build  ➔  5. Deploy Web Asset
```

### 1. Preparing Scoped Objects
- Select the objects you wish to bake and export in the 3D Viewport.
- Set the scope in the Web3D panel to `All Selected`, `Active Only`, or `All Visible`.

### 2. Selecting an Optimization Preset
Under **Optimization Presets**, click any preset button to instantly configure all baking and export settings for your target platform:
- **React Three Fiber (R3F) Optimized**: Balanced settings for React Three Fiber apps (2K texture atlas, Int16 quantization, WebP, Draco level 6).
- **Three.js Portfolio High-Quality**: High visual fidelity (4K texture atlas, uncompressed GLB, high precision keyframes).
- **Mobile Web Ultra-Light**: Low payload size (1K tile atlas, 15 FPS keyframe reduction, Draco level 8, WebP 75%).
- **ArchViz Lightmap Bake**: Architectural lightmap baking (4K EXR float, Auto Seam Unwrap, World-Space packing).

### 3. Running the Primary Build
Click **⚡ BUILD WEB ASSET (Full Pipeline)**. The compiler executes:
1. Material slot verification & backup.
2. UV unwrapping & multi-object island packing.
3. Node injection & Cycles GPU lightmap baking.
4. Auto-saving baked textures to the output directory.
5. Non-destructive material graph rewiring (pre-lit viewport preview).
6. Decoupled GLB model & animation clip export.

### 4. Toggle Baked View vs Original Materials
You can toggle between your original procedural shaders and your pre-lit baked maps at any time using **Show Original Materials / Show Baked Textures**.
