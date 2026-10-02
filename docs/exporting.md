# Web Animation & Model Export Guide

The export stage packages character models and animation clips for
high-performance web deployment.

## Decoupled Architecture

Rather than embedding heavy animation tracks into a single massive `.glb` file, the exporter generates:
- `character.glb`: Mesh geometry, materials, skeletons, shape keys (animation-free).
- `animations/*.anim`: Compact standalone binary animation tracks.
- `animation-manifest.json`: Index file listing all available animation clips and durations.
- `animationClipFactory.ts`: T3AN decoder and Three.js `AnimationClip` factory.
- `model_controller.ts`: Lazy-loading model and animation controller.
- `animations/*.ts` and `animations/index.ts`: Type-safe animation asset modules.

The model-only exporter can write GLB or glTF. Character plus binary animation
export always uses GLB so the target mapping is stable and self-contained.

## Animation Optimization Techniques

1. **Keyframe Reduction**: Removes redundant keyframes within position (`0.0001`), rotation (`0.05°`), scale (`0.0001`), and morph target (`0.0005`) error tolerances. All four are configurable under Export.
2. **Rest-Pose Track Removal**: Automatically detects and strips tracks that do not depart from rest-pose transforms.
3. **Data Quantization**: Converts 32-bit floating point keyframes into `int16`/`uint16` fixed-point representations, saving up to 70% in animation file size.

## Draco Mesh Compression

Enable **Draco Mesh Compression** to compress vertex positions, normals, UVs, and colors.
- Set **Compression Level** (default: 6). Per-stream bit budgets are also exposed.
- Compression reuses the Draco support already bundled with Blender's glTF exporter. If it is unavailable the export falls back to uncompressed mesh data and says so rather than failing.
- Requires `DRACOLoader` in Three.js on the client side.

## Three.js Usage

Import the generated controller from application source while preserving its
relative paths to the generated model, manifest, and `animations` directory:

```ts
import { loadAnimatedModel } from './generated/model_controller.js';

const controller = await loadAnimatedModel({ preloadAnimations: ['Idle'] });
scene.add(controller.root);
await controller.play('Idle');

function frame(deltaSeconds: number) {
  controller.update(deltaSeconds);
}
```

Call `controller.dispose()` when the model is removed. When Draco is enabled,
pass a configured `DRACOLoader` through `loadAnimatedModel({ dracoLoader })`.

Portfolio one-click mode uses the linked `portfolio/public/models` directory.
Set `WEB3D_PORTFOLIO_MODELS_DIR` before launching Blender to override it.

## Client Types

The generated `model_controller.ts` exports `loadAnimatedModel()` and the
`AnimatedModelController` interface, with `root`, `play()`, `stop()`,
`preload()`, `update(deltaSeconds)` and `dispose()`.

`animations/index.ts` exports `AnimationName`, `animationNames` and
`animationByName`. Because `AnimationName` is derived from the exported clips,
referencing a name that does not exist is a **compile error**, not a runtime
failure.

Both the generated modules and the examples in `examples/` are type-checked
against real `three` and `@react-three/fiber` types on every build:

```bash
blender --background --factory-startup --python-exit-code 1 \
  --python tests/export_fixtures.py
cd examples && npm install && npm run check
```
