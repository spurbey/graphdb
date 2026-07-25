import React, { useRef } from 'react';
import { useFrame } from '@react-three/fiber';
import { Points, PointMaterial } from '@react-three/drei';
import * as THREE from 'three';

export default function Scene() {
  const groupRef = useRef();

  const nodeCount = 3000;
  const positions = new Float32Array(nodeCount * 3);
  const radius = 12;

  for (let i = 0; i < nodeCount; i++) {
    const u = Math.random();
    const v = Math.random();
    const theta = 2 * Math.PI * u;
    const phi = Math.acos(2 * v - 1);
    
    const r = radius + (Math.random() - 0.5) * 4;

    positions[i * 3] = r * Math.sin(phi) * Math.cos(theta);
    positions[i * 3 + 1] = r * Math.sin(phi) * Math.sin(theta);
    positions[i * 3 + 2] = r * Math.cos(phi);
  }

  useFrame((state, delta) => {
    if (groupRef.current) {
      // Passive rotation
      groupRef.current.rotation.y += delta * 0.05;
      
      // Active rotation mapped to window scroll
      const scrollY = window.scrollY;
      groupRef.current.rotation.x = scrollY * 0.0005;
      groupRef.current.position.z = scrollY * 0.001; // subtle zoom as you scroll down
    }
  });

  return (
    <group ref={groupRef}>
      <Points positions={positions} stride={3}>
        <PointMaterial 
          transparent 
          color="#00ffcc" 
          size={0.03} 
          sizeAttenuation={true} 
          depthWrite={false} 
          opacity={0.6}
        />
      </Points>
    </group>
  );
}
