# Web Animation & Model Export Guide

Three.js Exporter packages character models and animation clips for high-performance web deployment.

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

1. **Keyframe Reduction**: Removes redundant keyframes within position (`0.0001`), rotation (`0.05°`), scale (`0.0001`), and morph target (`0.0005`) error tolerances.
2. **Rest-Pose Track Removal**: Automatically detects and strips tracks that do not depart from rest-pose transforms.
3. **Data Quantization**: Converts 32-bit floating point keyframes into `int16`/`uint16` fixed-point representations, saving up to 70% in animation file size.

## Draco Mesh Compression

Enable **Draco Mesh Compression** to compress vertex positions, normals, UVs, and colors.
- Set **Compression Level** (default: 6).
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
