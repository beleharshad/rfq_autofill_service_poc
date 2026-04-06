import { useRef, useEffect } from 'react';
import { useThree, useFrame } from '@react-three/fiber';
import * as THREE from 'three';

interface EdgeOutlinesProps {
  viewMode: 'standard' | 'realistic' | 'xray';
  enabled: boolean;
}

export function EdgeOutlines({ viewMode, enabled }: EdgeOutlinesProps) {
  const { scene } = useThree();
  const edgeLinesRef = useRef<THREE.LineSegments[]>([]);
  const meshToEdgeMapRef = useRef<Map<THREE.Mesh, THREE.LineSegments>>(new Map());

  useEffect(() => {
    if (!enabled || viewMode !== 'realistic') {
      // Remove all edge lines
      edgeLinesRef.current.forEach((line) => {
        scene.remove(line);
        line.geometry.dispose();
        if (Array.isArray(line.material)) {
          line.material.forEach((m) => m.dispose());
        } else {
          line.material.dispose();
        }
      });
      edgeLinesRef.current = [];
      meshToEdgeMapRef.current.clear();
      return;
    }

    // Function to create edge lines for a mesh
    const createEdgeLines = (mesh: THREE.Mesh) => {
      if (meshToEdgeMapRef.current.has(mesh)) {
        return; // Already has edge lines
      }

      try {
        const edges = new THREE.EdgesGeometry(mesh.geometry);
        const edgeMaterial = new THREE.LineBasicMaterial({
          color: 0x7f93aa,
          linewidth: 1,
          transparent: true,
          opacity: 0.28,
        });
        const edgeLines = new THREE.LineSegments(edges, edgeMaterial);
        
        // Match mesh transform by making edgeLines a child of mesh's parent or matching world transform
        // For simplicity, we'll add it to the scene and update transform in useFrame
        edgeLines.userData.sourceMesh = mesh;
        scene.add(edgeLines);
        edgeLinesRef.current.push(edgeLines);
        meshToEdgeMapRef.current.set(mesh, edgeLines);
      } catch (e) {
        // Skip if geometry is invalid
        console.warn('Failed to create edge geometry:', e);
      }
    };

    // Find all meshes and add edge outlines
    const meshes: THREE.Mesh[] = [];
    scene.traverse((child) => {
      if (child instanceof THREE.Mesh && child.geometry && !child.userData.isEdgeLine) {
        meshes.push(child);
      }
    });

    // Create edge geometry for each mesh
    meshes.forEach(createEdgeLines);

    return () => {
      // Cleanup
      edgeLinesRef.current.forEach((line) => {
        scene.remove(line);
        line.geometry.dispose();
        if (Array.isArray(line.material)) {
          line.material.forEach((m) => m.dispose());
        } else {
          line.material.dispose();
        }
      });
      edgeLinesRef.current = [];
      meshToEdgeMapRef.current.clear();
    };
  }, [enabled, viewMode, scene]);

  // Update edge line transforms to match meshes
  useFrame(() => {
    if (!enabled || viewMode !== 'realistic') return;
    
    edgeLinesRef.current.forEach((edgeLine) => {
      const sourceMesh = edgeLine.userData.sourceMesh as THREE.Mesh | undefined;
      if (sourceMesh) {
        // Update world transform to match mesh
        sourceMesh.updateMatrixWorld();
        edgeLine.matrixWorld.copy(sourceMesh.matrixWorld);
      }
    });
  });

  return null; // This component doesn't render anything directly
}

