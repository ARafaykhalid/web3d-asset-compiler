import { DRACOLoader } from 'three/addons/loaders/DRACOLoader.js';
import type * as THREE from 'three';

// Keep this module beside the generated model, manifest, and animations folder.
import {
  loadAnimatedModel,
  type AnimatedModelController,
} from './generated/model_controller.js';

export async function loadWeb3DAsset(
  scene: THREE.Scene,
  initialAnimation = 'Idle',
): Promise<AnimatedModelController> {
  const dracoLoader = new DRACOLoader();
  dracoLoader.setDecoderPath(
    'https://www.gstatic.com/draco/versioned/decoders/1.5.7/',
  );

  const controller = await loadAnimatedModel({
    dracoLoader,
    preloadAnimations: [initialAnimation],
  });
  scene.add(controller.root);
  await controller.play(initialAnimation);
  return controller;
}

// Call controller.update(deltaSeconds) in the render loop and
// controller.dispose() when the asset is removed.
