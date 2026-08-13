"""
TypeScript helper module generator for Three.js web application integration.
"""

import json


def typescript_module(glb_filename, animation_names):
    names_json = json.dumps(animation_names, ensure_ascii=False, indent=2)
    return f"""import * as THREE from 'three';
import {{ GLTFLoader, type GLTF }} from 'three/addons/loaders/GLTFLoader.js';

export const modelUrl = new URL('./{glb_filename}', import.meta.url).href;
export const animationNames = {names_json} as const;
export type AnimationName = (typeof animationNames)[number] | (string & {{}});

export interface LoadAnimatedModelOptions {{
  manager?: THREE.LoadingManager;
  dracoLoader?: Parameters<GLTFLoader['setDRACOLoader']>[0];
  meshoptDecoder?: Parameters<GLTFLoader['setMeshoptDecoder']>[0];
  crossFadeDuration?: number;
}}

export interface PlayOptions {{
  fade?: number;
  loop?: typeof THREE.LoopOnce | typeof THREE.LoopRepeat | typeof THREE.LoopPingPong;
  repetitions?: number;
  clampWhenFinished?: boolean;
  timeScale?: number;
}}

export interface AnimatedModelController {{
  gltf: GLTF;
  root: THREE.Group;
  mixer: THREE.AnimationMixer;
  clips: ReadonlyMap<string, THREE.AnimationClip>;
  animationNames: string[];
  getAction(name: AnimationName): THREE.AnimationAction;
  play(name: AnimationName, options?: PlayOptions): THREE.AnimationAction;
  stop(fade?: number): void;
  update(deltaSeconds: number): void;
  dispose(): void;
}}

export async function loadAnimatedModel(
  options: LoadAnimatedModelOptions = {{}},
): Promise<AnimatedModelController> {{
  const {{
    manager,
    dracoLoader,
    meshoptDecoder,
    crossFadeDuration = 0.25,
  }} = options;

  const loader = new GLTFLoader(manager);
  if (dracoLoader) loader.setDRACOLoader(dracoLoader);
  if (meshoptDecoder) loader.setMeshoptDecoder(meshoptDecoder);

  const gltf = await loader.loadAsync(modelUrl);
  const root = gltf.scene;
  const mixer = new THREE.AnimationMixer(root);
  const clips = new Map<string, THREE.AnimationClip>(
    gltf.animations.map((clip) => [clip.name, clip]),
  );
  const actions = new Map<string, THREE.AnimationAction>();
  let currentAction: THREE.AnimationAction | null = null;

  function getAction(name: AnimationName): THREE.AnimationAction {{
    const clip = clips.get(name);
    if (!clip) {{
      throw new Error(
        `Unknown animation "${{name}}". Available: ${{[...clips.keys()].join(', ') || 'none'}}`
      );
    }}
    if (!actions.has(name)) actions.set(name, mixer.clipAction(clip));
    return actions.get(name)!;
  }}

  function play(
    name: AnimationName,
    playOptions: PlayOptions = {{}},
  ): THREE.AnimationAction {{
    const {{
      fade = crossFadeDuration,
      loop = THREE.LoopRepeat,
      repetitions = Infinity,
      clampWhenFinished = false,
      timeScale = 1,
    }} = playOptions;

    const nextAction = getAction(name);
    nextAction.enabled = true;
    nextAction.clampWhenFinished = clampWhenFinished;
    nextAction.setLoop(loop, repetitions);
    nextAction.setEffectiveTimeScale(timeScale);
    nextAction.setEffectiveWeight(1);

    if (currentAction && currentAction !== nextAction) {{
      currentAction.fadeOut(fade);
    }}

    nextAction.reset().fadeIn(fade).play();
    currentAction = nextAction;
    return nextAction;
  }}

  function stop(fade = crossFadeDuration): void {{
    if (!currentAction) return;
    currentAction.fadeOut(fade);
    currentAction = null;
  }}

  function update(deltaSeconds: number): void {{
    mixer.update(deltaSeconds);
  }}

  function dispose(): void {{
    mixer.stopAllAction();
    mixer.uncacheRoot(root);
    root.traverse((object) => {{
      if (!(object instanceof THREE.Mesh)) return;
      object.geometry.dispose();
      const materials: THREE.Material[] = Array.isArray(object.material)
        ? object.material
        : [object.material];
      for (const material of materials) {{
        if (!material) continue;
        for (const value of Object.values(material)) {{
          if (value instanceof THREE.Texture) value.dispose();
        }}
        material.dispose();
      }}
    }});
  }}

  return {{
    gltf,
    root,
    mixer,
    clips,
    animationNames: [...clips.keys()],
    getAction,
    play,
    stop,
    update,
    dispose,
  }};
}}
"""
