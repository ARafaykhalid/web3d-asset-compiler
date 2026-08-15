import { useFrame } from '@react-three/fiber';
import { useEffect, useRef, useState } from 'react';
import type * as THREE from 'three';

import {
  loadAnimatedModel,
  type AnimatedModelController,
} from './generated/model_controller.js';

export interface ModelProps {
  activeAnimation?: string;
}

export function Web3DModel({
  activeAnimation = 'Idle',
}: ModelProps) {
  const controllerRef = useRef<AnimatedModelController | null>(null);
  const [root, setRoot] = useState<THREE.Group | null>(null);

  useEffect(() => {
    let cancelled = false;

    void loadAnimatedModel().then((controller) => {
      if (cancelled) {
        controller.dispose();
        return;
      }
      controllerRef.current = controller;
      setRoot(controller.root);
    });

    return () => {
      cancelled = true;
      controllerRef.current?.dispose();
      controllerRef.current = null;
    };
  }, []);

  useEffect(() => {
    const controller = controllerRef.current;
    if (!controller || !activeAnimation) return;
    void controller.play(activeAnimation);
  }, [activeAnimation, root]);

  useFrame((_state, deltaSeconds) => {
    controllerRef.current?.update(deltaSeconds);
  });

  return root ? <primitive object={root} /> : null;
}
