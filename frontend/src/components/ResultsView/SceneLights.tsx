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

    const backdropTop = new THREE.Color('#3b4652');
    const backdropMid = new THREE.Color('#1f2731');
    const backdropBottom = new THREE.Color('#0f141a');
    const fillColor = new THREE.Color('#cfd8e3');
    const coolSoftbox = new THREE.Color('#f4f8ff');
    const warmSoftbox = new THREE.Color('#fff1d9');

    const gaussian = (value: number, center: number, width: number) => {
      const delta = (value - center) / width;
      return Math.exp(-(delta * delta));
    };

    for (let y = 0; y < size; y++) {
      const v = y / (size - 1);
      const base = backdropBottom
        .clone()
        .lerp(backdropMid, Math.min(v * 1.35, 1))
        .lerp(backdropTop, Math.max((v - 0.58) / 0.42, 0));

      for (let x = 0; x < size; x++) {
        const u = x / (size - 1);
        const c = base.clone();

        const leftPanel = gaussian(u, 0.21, 0.055) * gaussian(v, 0.64, 0.17);
        const rightPanel = gaussian(u, 0.78, 0.06) * gaussian(v, 0.60, 0.16);
        const topStrip = gaussian(v, 0.84, 0.08) * (0.45 + gaussian(u, 0.52, 0.24));
        const frontFill = gaussian(u, 0.5, 0.22) * gaussian(v, 0.48, 0.26);
        const horizonGlow = gaussian(v, 0.35, 0.1) * gaussian(u, 0.52, 0.35);

        c.add(coolSoftbox.clone().multiplyScalar(leftPanel * 2.8));
        c.add(warmSoftbox.clone().multiplyScalar(rightPanel * 2.35));
        c.add(fillColor.clone().multiplyScalar(topStrip * 1.35));
        c.add(fillColor.clone().multiplyScalar(frontFill * 0.85));
        c.add(new THREE.Color('#8fa6bd').multiplyScalar(horizonGlow * 0.5));

        c.r = Math.min(c.r, 1);
        c.g = Math.min(c.g, 1);
        c.b = Math.min(c.b, 1);

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
        <ambientLight intensity={0.24} color="#d8e2ee" />

        {/* Key */}
        <directionalLight
          position={[6, 8, 11]}
          intensity={2.8}
          color="#ffffff"
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
        <directionalLight position={[-8, 6, -3]} intensity={1.15} color="#dbe8f8" />

        {/* Rim */}
        <directionalLight position={[2, 5, -12]} intensity={0.95} color="#f7fbff" />

        {/* Subtle top highlight */}
        <pointLight position={[0, 12, 2]} intensity={0.68} color="#f7fbff" />

        {/* Front sparkle */}
        <pointLight position={[3.5, 2.5, 9]} intensity={0.72} color="#ffffff" />

        {/* Opposite sparkle for edge rolloff */}
        <pointLight position={[-5, 1.5, 7]} intensity={0.32} color="#d5e5ff" />
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
