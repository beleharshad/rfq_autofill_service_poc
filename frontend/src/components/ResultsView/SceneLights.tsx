import { useThree } from '@react-three/fiber';
import { useMemo, useEffect } from 'react';
import * as THREE from 'three';

export type ViewMode = 'standard' | 'realistic' | 'xray';
export type RenderQuality = 'normal' | 'high';

interface SceneLightsProps {
  viewMode: ViewMode;
  quality?: RenderQuality;
  enableShadows?: boolean;
}

export function SceneLights({
  viewMode,
  quality = 'normal',
  enableShadows = false,
}: SceneLightsProps) {
  const { gl, scene } = useThree();

  // --- Environment / HDR-ish neutral studio lighting for Realistic mode
  useEffect(() => {
    if (viewMode !== 'realistic') {
      if (scene.environment) scene.environment = null;
      return;
    }

    const pmremGenerator = new THREE.PMREMGenerator(gl);
    pmremGenerator.compileEquirectangularShader();

    const size = quality === 'high' ? 512 : 256;
    const data = new Uint8Array(4 * size * size);

    // Slight gradient-ish studio neutral (light grey)
    const top = new THREE.Color(0.78, 0.78, 0.78);
    const bottom = new THREE.Color(0.62, 0.62, 0.62);

    for (let y = 0; y < size; y++) {
      const t = y / (size - 1);
      const c = bottom.clone().lerp(top, t);
      for (let x = 0; x < size; x++) {
        const i = (y * size + x) * 4;
        data[i] = Math.floor(c.r * 255);
        data[i + 1] = Math.floor(c.g * 255);
        data[i + 2] = Math.floor(c.b * 255);
        data[i + 3] = 255;
      }
    }

    const texture = new THREE.DataTexture(data, size, size);
    texture.needsUpdate = true;
    texture.mapping = THREE.EquirectangularReflectionMapping;

    const rt = pmremGenerator.fromEquirectangular(texture);

    scene.environment = rt.texture;
    // NOTE: in three.js, environment intensity is material-level; we rely on light balance.
    // But you can scale perceived reflection via metalness/roughness in materials.

    return () => {
      texture.dispose();
      rt.dispose();
      pmremGenerator.dispose();
      if (scene.environment === rt.texture) scene.environment = null;
    };
  }, [viewMode, quality, gl, scene]);

  // --- Shadows config
  useEffect(() => {
    if (viewMode === 'realistic' && enableShadows) {
      gl.shadowMap.enabled = true;
      gl.shadowMap.type = quality === 'high' ? THREE.PCFSoftShadowMap : THREE.PCFShadowMap;
    } else {
      gl.shadowMap.enabled = false;
    }
  }, [viewMode, enableShadows, quality, gl]);

  // Standard mode lighting (lightweight)
  const standardLights = useMemo(() => {
    if (viewMode !== 'standard') return null;

    return (
      <>
        <ambientLight intensity={0.6} />
        <directionalLight position={[10, 10, 5]} intensity={1.1} />
        <directionalLight position={[-10, 10, -5]} intensity={0.8} />
        <directionalLight position={[0, -10, 0]} intensity={0.35} />
        <pointLight position={[0, 10, 0]} intensity={0.45} />
      </>
    );
  }, [viewMode]);

  // Realistic mode (studio-ish 3-point + soft fill)
  const realisticLights = useMemo(() => {
    if (viewMode !== 'realistic') return null;

    const shadowMapSize = quality === 'high' ? 4096 : 2048;

    return (
      <>
        {/* Base ambient */}
        <ambientLight intensity={0.32} />

        {/* Key */}
        <directionalLight
          position={[7, 10, 7]}
          intensity={2.15}
          castShadow={enableShadows}
          shadow-mapSize-width={shadowMapSize}
          shadow-mapSize-height={shadowMapSize}
          shadow-camera-left={-12}
          shadow-camera-right={12}
          shadow-camera-top={12}
          shadow-camera-bottom={-12}
          shadow-bias={-0.00012}
        />

        {/* Fill */}
        <directionalLight position={[-6, 7, -4]} intensity={0.8} color="#b8d4f0" />

        {/* Rim */}
        <directionalLight position={[0, 4, -10]} intensity={0.62} color="#dbe8f6" />

        {/* Subtle top highlight */}
        <pointLight position={[0, 12, 0]} intensity={0.42} color="#f7fbff" />

        {/* Front sparkle */}
        <pointLight position={[4, 2, 8]} intensity={0.24} color="#ffffff" />
      </>
    );
  }, [viewMode, enableShadows, quality]);

  // X-ray mode lighting (flat)
  const xrayLights = useMemo(() => {
    if (viewMode !== 'xray') return null;

    return (
      <>
        <ambientLight intensity={1.0} />
        <directionalLight position={[0, 0, 1]} intensity={0.5} />
        <directionalLight position={[0, 0, -1]} intensity={0.5} />
      </>
    );
  }, [viewMode]);

  return (
    <>
      {standardLights}
      {realisticLights}
      {xrayLights}
    </>
  );
}
