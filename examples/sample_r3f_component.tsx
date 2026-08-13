import React, { useEffect, useRef } from 'react';
import { useFrame } from '@react-three/fiber';
import { useGLTF, useAnimations } from '@react-three/drei';
import * as THREE from 'three';

export interface ModelProps {
  url?: string;
  activeAnimation?: string;
}

export const Web3DModel: React.FC<ModelProps> = ({
  url = '/models/character.glb',
  activeAnimation = 'Idle',
}) => {
  const groupRef = useRef<THREE.Group>(null);
  const { scene, animations } = useGLTF(url);
  const { actions } = useAnimations(animations, groupRef);

  useEffect(() => {
    if (!actions || !activeAnimation) return;
    const action = actions[activeAnimation];
    if (action) {
      action.reset().fadeIn(0.3).play();
      return () => {
        action.fadeOut(0.3);
      };
    }
  }, [actions, activeAnimation]);

  return <primitive ref={groupRef} object={scene} dispose={null} />;
};

useGLTF.preload('/models/character.glb');
