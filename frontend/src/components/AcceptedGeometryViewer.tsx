import { useEffect, useState } from 'react';
import { Canvas } from '@react-three/fiber';
import { Bounds, OrbitControls, GizmoHelper, GizmoViewport } from '@react-three/drei';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import * as THREE from 'three';
import { api } from '../services/api';

export default function AcceptedGeometryViewer({ jobId, buildId }: { jobId: string; buildId: string }) {
  const [scene, setScene] = useState<THREE.Group | null>(null);
  const [error, setError] = useState('');
  const [rotate, setRotate] = useState(false);
  const [section, setSection] = useState(false);
  useEffect(() => {
    const controller = new AbortController();
    let loaded: THREE.Group | null = null;
    setScene(null); setError('');
    api.getGeometryArtifact(jobId, buildId, 'model.glb', controller.signal).then(b => b.arrayBuffer()).then(async bytes => {
      const result = await new GLTFLoader().parseAsync(bytes, '');
      loaded = result.scene;
      if (controller.signal.aborted) { dispose(loaded); return; }
      const box = new THREE.Box3().setFromObject(loaded);
      loaded.position.sub(box.getCenter(new THREE.Vector3()));
      setScene(loaded);
    }).catch(e => { if (!controller.signal.aborted) setError(e.message); });
    return () => { controller.abort(); if (loaded) dispose(loaded); };
  }, [jobId, buildId]);
  useEffect(() => {
    scene?.traverse(object => {
      if (object instanceof THREE.Mesh) {
        const materials = Array.isArray(object.material) ? object.material : [object.material];
        for (const material of materials) {
          material.clippingPlanes = section ? [new THREE.Plane(new THREE.Vector3(1, 0, 0), 0)] : [];
          material.side = THREE.DoubleSide; material.needsUpdate = true;
        }
      }
    });
  }, [scene, section]);
  return <div>
    <label><input type="checkbox" checked={rotate} onChange={e => setRotate(e.target.checked)} /> Auto rotate</label>{' '}
    <label><input type="checkbox" checked={section} onChange={e => setSection(e.target.checked)} /> Section at centre</label>
    <p>Drag to rotate, right-drag to pan, scroll to zoom. Section clipping exposes the actual cavities; cut faces are not capped.</p>
    {error && <p role="alert">{error}</p>}
    {!scene && !error && <p>Loading constructed geometry…</p>}
    {scene && <div style={{ height: 420, background: '#18212b' }}>
      <Canvas camera={{ position: [0.1, 0.1, 0.1], near: 0.00001, far: 1000 }} onCreated={({ gl }) => { gl.localClippingEnabled = true; }}>
        <ambientLight intensity={1.5} /><directionalLight position={[2, 3, 4]} intensity={3} />
        <Bounds fit clip observe margin={1.3}><primitive object={scene} /></Bounds>
        <OrbitControls makeDefault autoRotate={rotate} />
        <GizmoHelper alignment="bottom-right" margin={[70, 70]}><GizmoViewport /></GizmoHelper>
      </Canvas>
    </div>}
  </div>;
}

function dispose(scene: THREE.Group) {
  scene.traverse(object => {
    if (object instanceof THREE.Mesh) {
      object.geometry.dispose();
      const materials = Array.isArray(object.material) ? object.material : [object.material];
      materials.forEach(material => material.dispose());
    }
  });
}
