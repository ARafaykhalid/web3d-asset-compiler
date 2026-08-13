import * as THREE from 'three';
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js';
import { DRACOLoader } from 'three/examples/jsm/loaders/DRACOLoader.js';

export async function loadWeb3DAsset(
  modelPath: string,
  dracoDecoderPath: string = 'https://www.gstatic.com/draco/versioned/decoders/1.5.6/',
): Promise<{ scene: THREE.Group; mixer: THREE.AnimationMixer; clips: THREE.AnimationClip[] }> {
  const dracoLoader = new DRACOLoader();
  dracoLoader.setDecoderPath(dracoDecoderPath);

  const loader = new GLTFLoader();
  loader.setDRACOLoader(dracoLoader);

  const gltf = await loader.loadAsync(modelPath);
  const scene = gltf.scene;
  const clips = gltf.animations;
  const mixer = new THREE.AnimationMixer(scene);

  if (clips.length > 0) {
    const action = mixer.clipAction(clips[0]);
    action.play();
  }

  return { scene, mixer, clips };
}
