import { useRef, useMemo, useState, useEffect, Suspense, useCallback } from 'react';
import { Canvas, useLoader, useThree, useFrame } from '@react-three/fiber';
import { OrbitControls, PerspectiveCamera } from '@react-three/drei';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import * as THREE from 'three';
import { api } from '../../services/api';
import type { PartSummary } from '../../services/types';
import { getSegments, type Segment } from '../../state/segmentStore';
import { SceneLights, type ViewMode } from './SceneLights';
import { ViewerToolbar } from './ViewerToolbar';
import { EdgeOutlines } from './EdgeOutlines';
import './ThreeJSViewer.css';

interface ThreeJSViewerProps {
  summary: PartSummary;
  jobId: string;
  onHoveredSegmentChange?: (index: number | null) => void;
  showHoles?: boolean;
  showSlots?: boolean;
  showChamfers?: boolean;
  showFillets?: boolean;
}

interface SegmentMeshProps {
  segment: PartSummary['segments'][0];
  index: number;
  showOD: boolean;
  showID: boolean;
  highlightThinWall: boolean;
  thinWallThreshold: number;
  viewMode: ViewMode;
}

function SegmentMesh({
  segment,
  index,
  showOD,
  showID,
  highlightThinWall,
  thinWallThreshold,
  viewMode,
}: SegmentMeshProps) {
  const odMeshRef = useRef<THREE.Mesh>(null);
  const idMeshRef = useRef<THREE.Mesh>(null);

  // Add large overlap to eliminate gaps - ensure seamless connection
  // Use a fixed minimum overlap that's large enough to be visible
  const baseLength = segment.z_end - segment.z_start;
  // Use a larger fixed overlap (0.1 inches = 2.54mm) to ensure no visible gaps
  // For very short segments, use 20% of length
  const overlap = Math.max(0.1, baseLength * 0.2);
  const length = baseLength + (overlap * 2); // Extend both ends
  const odRadius = segment.od_diameter / 2;
  const idRadius = segment.id_diameter / 2;
  const wallThickness = segment.wall_thickness;
  const isThinWall = wallThickness > 0 && wallThickness < thinWallThreshold;

  // Position: center the cylinder at the midpoint of the segment
  // The overlap extends equally in both directions, so center stays the same
  const zCenter = (segment.z_start + segment.z_end) / 2;

  // OD cylinder - use higher segment count for smoother appearance
  const odGeometry = useMemo(() => {
    if (!showOD) return null;
    return new THREE.CylinderGeometry(odRadius, odRadius, length, 64);
  }, [odRadius, length, showOD]);

  // ID cylinder - use higher segment count for smoother appearance
  const idGeometry = useMemo(() => {
    if (!showID || idRadius <= 0) return null;
    return new THREE.CylinderGeometry(idRadius, idRadius, length, 64);
  }, [idRadius, length, showID]);

  // Color logic
  const odColor = isThinWall && highlightThinWall ? '#ff0000' : '#4a9eff'; // Blue or red
  const idColor = isThinWall && highlightThinWall ? '#ff0000' : '#ff8c42'; // Orange or red

  // Material based on view mode - ensure opaque for solid appearance
  const odMaterial = useMemo(() => {
    if (viewMode === 'realistic') {
      return new THREE.MeshPhysicalMaterial({
        color: odColor,
        metalness: 0.2,
        roughness: 0.35,
        clearcoat: 0.1,
        clearcoatRoughness: 0.2,
        transparent: false,
        opacity: 1.0,
        side: THREE.DoubleSide,
        wireframe: isThinWall && highlightThinWall,
        depthWrite: true,
      });
    } else if (viewMode === 'xray') {
      return new THREE.MeshStandardMaterial({
        color: odColor,
        metalness: 0.0,
        roughness: 1.0,
        transparent: true,
        opacity: 0.7,
        side: THREE.DoubleSide,
        wireframe: isThinWall && highlightThinWall,
      });
    } else {
      // Standard mode - make fully opaque to eliminate gaps
      return new THREE.MeshStandardMaterial({
        color: odColor,
        transparent: false,
        opacity: 1.0,
        side: THREE.DoubleSide,
        wireframe: isThinWall && highlightThinWall,
        depthWrite: true,
      });
    }
  }, [odColor, isThinWall, highlightThinWall, viewMode]);

  const idMaterial = useMemo(() => {
    if (viewMode === 'realistic') {
      return new THREE.MeshPhysicalMaterial({
        color: idColor,
        metalness: 0.2,
        roughness: 0.35,
        clearcoat: 0.1,
        clearcoatRoughness: 0.2,
        transparent: false,
        opacity: 1.0,
        side: THREE.DoubleSide,
        wireframe: isThinWall && highlightThinWall,
        depthWrite: true,
      });
    } else if (viewMode === 'xray') {
      return new THREE.MeshStandardMaterial({
        color: idColor,
        metalness: 0.0,
        roughness: 1.0,
        transparent: true,
        opacity: 0.7,
        side: THREE.DoubleSide,
        wireframe: isThinWall && highlightThinWall,
      });
    } else {
      // Standard mode - make fully opaque to eliminate gaps
      return new THREE.MeshStandardMaterial({
        color: idColor,
        transparent: false,
        opacity: 1.0,
        side: THREE.DoubleSide,
        wireframe: isThinWall && highlightThinWall,
        depthWrite: true,
        depthTest: true,
        polygonOffset: true,
        polygonOffsetFactor: 0,
        polygonOffsetUnits: 0,
      });
    }
  }, [idColor, isThinWall, highlightThinWall, viewMode]);

  return (
    <group position={[0, 0, zCenter]}>
      {showOD && odGeometry && (
        <mesh
          ref={odMeshRef}
          geometry={odGeometry}
          material={odMaterial}
          rotation={[Math.PI / 2, 0, 0]}
          renderOrder={index}
          frustumCulled={false}
        />
      )}
      {showID && idGeometry && (
        <mesh
          ref={idMeshRef}
          geometry={idGeometry}
          material={idMaterial}
          rotation={[Math.PI / 2, 0, 0]}
          renderOrder={index + 1000}
          frustumCulled={false}
        />
      )}
    </group>
  );
}

interface GlbModelProps {
  url: string;
  viewMode: ViewMode;
}

function GlbModel({ url, viewMode }: GlbModelProps) {
  const gltf = useLoader(GLTFLoader, url);
  
  // Traverse the scene and update materials based on view mode
  useEffect(() => {
    gltf.scene.traverse((child: any) => {
      if (child.isMesh) {
        // Ensure material is opaque and solid-looking
        if (child.material) {
          // Handle both single materials and arrays
          const materials = Array.isArray(child.material) ? child.material : [child.material];
          const newMaterials: THREE.Material[] = [];
          
          materials.forEach((material: any) => {
            if (material) {
              // Extract color if available
              let color = 0x808080; // Neutral grey for CAD look
              if (material.color) {
                if (material.color.isColor) {
                  color = material.color.getHex();
                } else if (typeof material.color === 'number') {
                  color = material.color;
                }
              }
              
              // Use MeshPhysicalMaterial for realistic mode, MeshStandardMaterial otherwise
              let newMaterial: THREE.Material;
              if (viewMode === 'realistic') {
                newMaterial = new THREE.MeshPhysicalMaterial({
                  color: color,
                  metalness: 0.2,
                  roughness: 0.35,
                  clearcoat: 0.1,
                  clearcoatRoughness: 0.2,
                  transparent: false,
                  opacity: 1.0,
                  side: THREE.DoubleSide,
                });
              } else if (viewMode === 'xray') {
                newMaterial = new THREE.MeshStandardMaterial({
                  color: color,
                  metalness: 0.0,
                  roughness: 1.0,
                  transparent: true,
                  opacity: 0.7,
                  side: THREE.DoubleSide,
                });
              } else {
                // Standard mode
                newMaterial = new THREE.MeshStandardMaterial({
                  color: color,
                  metalness: 0.1,
                  roughness: 0.7,
                  transparent: false,
                  opacity: 1.0,
                  side: THREE.DoubleSide,
                });
              }
              
              newMaterials.push(newMaterial);
            }
          });
          
          // Update the mesh material
          if (Array.isArray(child.material)) {
            child.material = newMaterials;
          } else {
            child.material = newMaterials[0] || new THREE.MeshStandardMaterial({ color: 0x808080 });
          }
        } else {
          // No material, create a default material based on view mode
          if (viewMode === 'realistic') {
            child.material = new THREE.MeshPhysicalMaterial({
              color: 0x808080,
              metalness: 0.2,
              roughness: 0.35,
              clearcoat: 0.1,
              clearcoatRoughness: 0.2,
              transparent: false,
              opacity: 1.0,
              side: THREE.DoubleSide,
            });
          } else if (viewMode === 'xray') {
            child.material = new THREE.MeshStandardMaterial({
              color: 0x808080,
              metalness: 0.0,
              roughness: 1.0,
              transparent: true,
              opacity: 0.7,
              side: THREE.DoubleSide,
            });
          } else {
            child.material = new THREE.MeshStandardMaterial({
              color: 0x808080,
              metalness: 0.1,
              roughness: 0.7,
              transparent: false,
              opacity: 1.0,
              side: THREE.DoubleSide,
            });
          }
        }
      }
    });
  }, [gltf, viewMode]);
  
  return (
    <primitive object={gltf.scene} />
  );
}

// Overlay components for feature visualization
interface ODOverlayCylinderProps {
  segment: PartSummary['segments'][0];
  highlightThinWall: boolean;
  thinWallThreshold: number;
}

function ODOverlayCylinder({ segment, highlightThinWall, thinWallThreshold }: ODOverlayCylinderProps) {
  const length = segment.z_end - segment.z_start;
  const odRadius = segment.od_diameter / 2;
  const wallThickness = segment.wall_thickness;
  const isThinWall = wallThickness > 0 && wallThickness < thinWallThreshold;
  const zCenter = (segment.z_start + segment.z_end) / 2;

  const geometry = useMemo(() => {
    return new THREE.CylinderGeometry(odRadius, odRadius, length, 32);
  }, [odRadius, length]);

  const material = useMemo(() => {
    const color = isThinWall && highlightThinWall ? '#ff0000' : '#4a9eff';
    return new THREE.MeshStandardMaterial({
      color,
      transparent: true,
      opacity: 0.3,
      side: THREE.DoubleSide,
    });
  }, [isThinWall, highlightThinWall]);

  return (
    <mesh
      geometry={geometry}
      material={material}
      position={[0, 0, zCenter]}
      rotation={[Math.PI / 2, 0, 0]}
    />
  );
}

interface IDOverlayCylinderProps {
  segment: PartSummary['segments'][0];
  highlightThinWall: boolean;
  thinWallThreshold: number;
}

function IDOverlayCylinder({ segment, highlightThinWall, thinWallThreshold }: IDOverlayCylinderProps) {
  // Add large overlap to eliminate gaps
  const baseLength = segment.z_end - segment.z_start;
  const overlap = Math.max(0.1, baseLength * 0.2); // At least 0.1" or 20% of segment length
  const length = baseLength + (overlap * 2);
  const idRadius = segment.id_diameter / 2;
  const wallThickness = segment.wall_thickness;
  const isThinWall = wallThickness > 0 && wallThickness < thinWallThreshold;
  const zCenter = (segment.z_start + segment.z_end) / 2;

  if (idRadius <= 0) return null;

  const geometry = useMemo(() => {
    return new THREE.CylinderGeometry(idRadius, idRadius, length, 64);
  }, [idRadius, length]);

  const material = useMemo(() => {
    const color = isThinWall && highlightThinWall ? '#ff0000' : '#ff8c42';
    return new THREE.MeshStandardMaterial({
      color,
      transparent: true,
      opacity: 0.3,
      side: THREE.DoubleSide,
    });
  }, [isThinWall, highlightThinWall]);

  return (
    <mesh
      geometry={geometry}
      material={material}
      position={[0, 0, zCenter]}
      rotation={[Math.PI / 2, 0, 0]}
    />
  );
}

interface ShoulderDiscProps {
  z: number;
  odRadius: number;
  idRadius: number;
}

function ShoulderDisc({ z, odRadius, idRadius }: ShoulderDiscProps) {
  const geometry = useMemo(() => {
    // If idRadius is 0 or very small, create a solid disc
    // Otherwise create a ring
    if (idRadius <= 0.001) {
      return new THREE.CircleGeometry(odRadius, 32);
    } else {
      // Create a ring geometry using RingGeometry
      // Parameters: innerRadius, outerRadius, thetaSegments, phiSegments, thetaStart, thetaLength
      return new THREE.RingGeometry(idRadius, odRadius, 32);
    }
  }, [odRadius, idRadius]);

  const material = useMemo(() => {
    return new THREE.MeshStandardMaterial({
      color: '#ffff00',
      transparent: true,
      opacity: 0.4,
      side: THREE.DoubleSide,
    });
  }, []);

  return (
    <mesh
      geometry={geometry}
      material={material}
      position={[0, 0, z]}
      rotation={[-Math.PI / 2, 0, 0]} // Rotate to be perpendicular to Z axis
    />
  );
}

interface FeatureOverlaysProps {
  summary: PartSummary;
  showODOverlay: boolean;
  showIDOverlay: boolean;
  showShoulderPlanes: boolean;
  highlightThinWall: boolean;
  thinWallThreshold: number;
  showHoles?: boolean;
  showSlots?: boolean;
  showChamfers?: boolean;
  showFillets?: boolean;
}

function FeatureOverlays({
  summary,
  showODOverlay,
  showIDOverlay,
  showShoulderPlanes,
  highlightThinWall,
  thinWallThreshold,
  showHoles = false,
  showSlots = false,
  showChamfers = false,
  showFillets = false,
}: FeatureOverlaysProps) {
  // Collect all Z boundaries for shoulder discs
  const zBoundaries = useMemo(() => {
    const boundaries: Array<{ z: number; odRadius: number; idRadius: number }> = [];
    
    // Start face (first segment)
    if (summary.segments.length > 0) {
      const first = summary.segments[0];
      boundaries.push({
        z: first.z_start,
        odRadius: first.od_diameter / 2,
        idRadius: first.id_diameter / 2,
      });
    }
    
    // Internal boundaries between segments
    for (let i = 0; i < summary.segments.length - 1; i++) {
      const current = summary.segments[i];
      const next = summary.segments[i + 1];
      // Use the larger radius at the boundary
      const odRadius = Math.max(current.od_diameter, next.od_diameter) / 2;
      const idRadius = Math.max(current.id_diameter, next.id_diameter) / 2;
      boundaries.push({
        z: current.z_end, // Should be same as next.z_start
        odRadius,
        idRadius,
      });
    }
    
    // End face (last segment)
    if (summary.segments.length > 0) {
      const last = summary.segments[summary.segments.length - 1];
      boundaries.push({
        z: last.z_end,
        odRadius: last.od_diameter / 2,
        idRadius: last.id_diameter / 2,
      });
    }
    
    return boundaries;
  }, [summary.segments]);

  return (
    <>
      {/* OD Overlay Cylinders */}
      {showODOverlay &&
        summary.segments.map((segment, index) => (
          <ODOverlayCylinder
            key={`od-overlay-${index}`}
            segment={segment}
            highlightThinWall={highlightThinWall}
            thinWallThreshold={thinWallThreshold}
          />
        ))}
      
      {/* ID Overlay Cylinders */}
      {showIDOverlay &&
        summary.segments.map((segment, index) => (
          <IDOverlayCylinder
            key={`id-overlay-${index}`}
            segment={segment}
            highlightThinWall={highlightThinWall}
            thinWallThreshold={thinWallThreshold}
          />
        ))}
      
      {/* Shoulder Discs */}
      {showShoulderPlanes &&
        zBoundaries.map((boundary, index) => (
          <ShoulderDisc
            key={`shoulder-${index}`}
            z={boundary.z}
            odRadius={boundary.odRadius}
            idRadius={boundary.idRadius}
          />
        ))}

      {/* Detected Feature Overlays */}
      {summary.features && (
        <>
          {/* Holes - limit to 15 for cleaner visualization */}
          {showHoles && summary.features.holes && (() => {
            // Calculate part dimensions for positioning
            const maxOD = Math.max(...summary.segments.map(s => s.od_diameter));
            const partLength = summary.z_range ? 
              (summary.z_range[1] - summary.z_range[0]) : 
              (summary.segments.length > 0 ? 
                summary.segments[summary.segments.length - 1].z_end - summary.segments[0].z_start : 2);
            
            // Limit displayed holes to avoid clutter, show highest confidence first
            const sortedHoles = [...summary.features.holes]
              .sort((a: any, b: any) => (b.confidence || 0) - (a.confidence || 0))
              .slice(0, 15);
            const totalHoles = sortedHoles.length;
            
            return sortedHoles.map((hole: any, index: number) => (
              <FeatureHole
                key={`hole-${index}`}
                hole={hole}
                index={index}
                totalHoles={totalHoles}
                partMaxOD={maxOD}
                partLength={partLength}
              />
            ));
          })()}

          {/* Slots */}
          {showSlots && summary.features.slots && (() => {
            const maxOD = Math.max(...summary.segments.map(s => s.od_diameter));
            const partLength = summary.z_range ? 
              (summary.z_range[1] - summary.z_range[0]) : 
              (summary.segments.length > 0 ? 
                summary.segments[summary.segments.length - 1].z_end - summary.segments[0].z_start : 2);
            const totalSlots = summary.features.slots.length;
            
            return summary.features.slots.map((slot: any, index: number) => (
              <FeatureSlot
                key={`slot-${index}`}
                slot={slot}
                index={index}
                totalSlots={totalSlots}
                partMaxOD={maxOD}
                partLength={partLength}
              />
            ));
          })()}

          {/* Chamfers - Simple markers for now */}
          {showChamfers && summary.features.chamfers &&
            summary.features.chamfers.map((chamfer: any, index: number) => (
              <FeatureChamfer
                key={`chamfer-${index}`}
                chamfer={chamfer}
                index={index}
              />
            ))}

          {/* Fillets - Simple markers for now */}
          {showFillets && summary.features.fillets &&
            summary.features.fillets.map((fillet: any, index: number) => (
              <FeatureFillet
                key={`fillet-${index}`}
                fillet={fillet}
                index={index}
              />
            ))}
        </>
      )}
    </>
  );
}

// Hover highlight overlay component
interface HoverHighlightProps {
  segment: Segment | null;
  visible: boolean;
}

function HoverHighlight({ segment, visible }: HoverHighlightProps) {
  const odMeshRef = useRef<THREE.Mesh>(null);
  const idMeshRef = useRef<THREE.Mesh>(null);

  // Calculate values (safe even if segment is null)
  // Add overlap to match segment rendering
  const baseLength = segment ? segment.z_end - segment.z_start : 0;
  const overlap = segment ? Math.max(0.1, baseLength * 0.2) : 0; // At least 0.1" or 20% of segment length
  const length = baseLength + (overlap * 2);
  const odRadius = segment ? (segment.od_diameter / 2) * 1.01 : 0; // Slight pop-out
  const idRadius = segment ? segment.id_diameter / 2 : 0;
  const zCenter = segment ? (segment.z_start + segment.z_end) / 2 : 0;

  // All hooks must be called before any conditional returns
  const odGeometry = useMemo(() => {
    if (!segment || !visible || odRadius <= 0) return null;
    return new THREE.CylinderGeometry(odRadius, odRadius, length, 64);
  }, [segment, visible, odRadius, length]);

  const idGeometry = useMemo(() => {
    if (!segment || !visible || idRadius <= 0) return null;
    return new THREE.CylinderGeometry(idRadius, idRadius, length, 64);
  }, [segment, visible, idRadius, length]);

  const highlightMaterial = useMemo(() => {
    // Use a material that renders above base material
    return new THREE.MeshStandardMaterial({
      color: '#00ff88', // Cyan-green for highlight
      transparent: true,
      opacity: 0.6,
      emissive: '#00ff88',
      emissiveIntensity: 0.5,
      side: THREE.DoubleSide,
      depthWrite: false, // Render above base material
    });
  }, []);

  // Conditional render after all hooks
  if (!segment || !visible) return null;

  return (
    <group position={[0, 0, zCenter]}>
      {odGeometry && (
        <mesh
          ref={odMeshRef}
          geometry={odGeometry}
          material={highlightMaterial}
          rotation={[Math.PI / 2, 0, 0]}
        />
      )}
      {idGeometry && (
        <mesh
          ref={idMeshRef}
          geometry={idGeometry}
          material={highlightMaterial}
          rotation={[Math.PI / 2, 0, 0]}
        />
      )}
    </group>
  );
}

// Hover detection hook
function useHoverDetection(
  jobId: string,
  onHoverChange: (segmentIndex: number | null) => void
) {
  const { camera, raycaster, pointer, scene } = useThree();
  const hoveredSegmentIndexRef = useRef<number | null>(null);
  const frameCountRef = useRef(0);

  useFrame(() => {
    // Throttle to ~30fps (update every 2 frames at 60fps)
    frameCountRef.current++;
    if (frameCountRef.current % 2 !== 0) return;

    const segments = getSegments(jobId);
    if (!segments || segments.length === 0) {
      if (hoveredSegmentIndexRef.current !== null) {
        hoveredSegmentIndexRef.current = null;
        onHoverChange(null);
      }
      return;
    }

    // Update raycaster with current pointer position
    raycaster.setFromCamera(pointer, camera);

    // Find all intersected objects
    const intersects = raycaster.intersectObjects(scene.children, true);

    if (intersects.length > 0) {
      const hit = intersects[0];
      const hitPoint = hit.point;

      // Extract Z coordinate (assuming model is axisymmetric along Z)
      const z = hitPoint.z;

      // Find segment that contains this Z coordinate
      let foundIndex: number | null = null;
      for (let i = 0; i < segments.length; i++) {
        const seg = segments[i];
        // Use a small tolerance for boundary cases
        const tolerance = 0.001;
        if (z >= seg.z_start - tolerance && z <= seg.z_end + tolerance) {
          foundIndex = i;
          break;
        }
      }

      // Only update if segment index changed
      if (foundIndex !== hoveredSegmentIndexRef.current) {
        hoveredSegmentIndexRef.current = foundIndex;
        onHoverChange(foundIndex);
      }
    } else {
      // No intersection, clear hover
      if (hoveredSegmentIndexRef.current !== null) {
        hoveredSegmentIndexRef.current = null;
        onHoverChange(null);
      }
    }
  });
}

function Scene({
  summary,
  showOD,
  showID,
  highlightThinWall,
  thinWallThreshold,
  glbUrl,
  showODOverlay,
  showIDOverlay,
  showShoulderPlanes,
  jobId,
  hoveredSegmentIndex,
  onHoverChange,
  viewMode,
  showHoles = false,
  showSlots = false,
  showChamfers = false,
  showFillets = false,
}: {
  summary: PartSummary;
  showOD: boolean;
  showID: boolean;
  highlightThinWall: boolean;
  thinWallThreshold: number;
  glbUrl: string | null;
  showODOverlay: boolean;
  showIDOverlay: boolean;
  showShoulderPlanes: boolean;
  jobId: string;
  hoveredSegmentIndex: number | null;
  onHoverChange: (index: number | null) => void;
  viewMode: ViewMode;
  showHoles?: boolean;
  showSlots?: boolean;
  showChamfers?: boolean;
  showFillets?: boolean;
}) {
  // Enable hover detection
  useHoverDetection(jobId, onHoverChange);

  // Get hovered segment
  const segments = getSegments(jobId);
  const hoveredSegment = hoveredSegmentIndex !== null && segments
    ? segments[hoveredSegmentIndex]
    : null;

  return (
    <>
      {/* Dynamic Lighting based on view mode */}
      <SceneLights viewMode={viewMode} enableShadows={viewMode === 'realistic'} />

      {/* Grid helper (only in standard mode) */}
      {viewMode === 'standard' && <gridHelper args={[20, 20, '#444', '#222']} />}

      {/* Axes helper (only in standard mode) */}
      {viewMode === 'standard' && <axesHelper args={[5]} />}

      {/* Shadow-receiving ground plane (only in realistic mode) */}
      {viewMode === 'realistic' && (
        <mesh rotation={[-Math.PI / 2, 0, 0]} position={[0, -10, 0]} receiveShadow>
          <planeGeometry args={[100, 100]} />
          <meshStandardMaterial color={0xffffff} transparent opacity={0} />
        </mesh>
      )}

      {/* Render GLB model if available, otherwise procedural */}
      {glbUrl ? (
        <Suspense fallback={null}>
          <GlbModel url={glbUrl} viewMode={viewMode} />
        </Suspense>
      ) : (
        summary.segments.map((segment, index) => (
          <SegmentMesh
            key={index}
            segment={segment}
            index={index}
            showOD={showOD}
            showID={showID}
            highlightThinWall={highlightThinWall}
            thinWallThreshold={thinWallThreshold}
            viewMode={viewMode}
          />
        ))
      )}

      {/* Feature Overlays (work on both GLB and procedural) */}
      <FeatureOverlays
        summary={summary}
        showODOverlay={showODOverlay}
        showIDOverlay={showIDOverlay}
        showShoulderPlanes={showShoulderPlanes}
        highlightThinWall={highlightThinWall}
        thinWallThreshold={thinWallThreshold}
        showHoles={showHoles}
        showSlots={showSlots}
        showChamfers={showChamfers}
        showFillets={showFillets}
      />

      {/* Hover Highlight Overlay */}
      <HoverHighlight segment={hoveredSegment} visible={hoveredSegmentIndex !== null} />
      
      {/* Edge Outlines (only in realistic mode) */}
      <EdgeOutlines viewMode={viewMode} enabled={viewMode === 'realistic'} />
    </>
  );
}

// Tooltip component
interface TooltipProps {
  segment: Segment | null;
  segmentIndex: number | null;
  visible: boolean;
  mouseX: number;
  mouseY: number;
  containerRef: React.RefObject<HTMLDivElement>;
  units: string;
}

function Tooltip({ segment, segmentIndex, visible, mouseX, mouseY, containerRef, units }: TooltipProps) {
  const [tooltipPosition, setTooltipPosition] = useState({ x: 0, y: 0 });

  useEffect(() => {
    if (!visible || !segment || !containerRef.current) {
      return;
    }

    const container = containerRef.current;
    const rect = container.getBoundingClientRect();
    const tooltipWidth = 320;
    const tooltipHeight = 280;

    // Position tooltip within the container, preferring corners to avoid the 3D model
    // Calculate mouse position relative to container
    const mouseXRelative = mouseX - rect.left;
    const mouseYRelative = mouseY - rect.top;

    // Define safe zones - avoid the center area where the 3D model is displayed
    const centerX = rect.width / 2;
    const centerY = rect.height / 2;
    const safeZoneMargin = 100; // pixels from center to avoid

    let x: number;
    let y: number;

    // Determine best corner based on mouse position and safe zones
    if (mouseXRelative < centerX - safeZoneMargin) {
      // Mouse is in left safe zone - position on far right
      x = rect.width - tooltipWidth - 10;
    } else if (mouseXRelative > centerX + safeZoneMargin) {
      // Mouse is in right safe zone - position on far left
      x = 10;
    } else {
      // Mouse is in center X - choose based on available space
      if (rect.width - mouseXRelative > tooltipWidth + 20) {
        x = mouseXRelative + 20;
      } else {
        x = mouseXRelative - tooltipWidth - 20;
      }
    }

    if (mouseYRelative < centerY - safeZoneMargin) {
      // Mouse is in top safe zone - position at bottom
      y = rect.height - tooltipHeight - 10;
    } else if (mouseYRelative > centerY + safeZoneMargin) {
      // Mouse is in bottom safe zone - position at top
      y = 10;
    } else {
      // Mouse is in center Y - choose based on available space
      if (rect.height - mouseYRelative > tooltipHeight + 20) {
        y = mouseYRelative + 20;
      } else {
        y = mouseYRelative - tooltipHeight - 20;
      }
    }

    // Final bounds checking to ensure tooltip stays within container
    x = Math.max(10, Math.min(x, rect.width - tooltipWidth - 10));
    y = Math.max(10, Math.min(y, rect.height - tooltipHeight - 10));

    setTooltipPosition({ x, y });
  }, [visible, segment, mouseX, mouseY, containerRef]);

  if (!visible || !segment || segmentIndex === null) return null;

  const wallThickness = segment.wall_thickness ?? (segment.od_diameter - segment.id_diameter) / 2;
  
  // Generate segment description
  const generateDescription = (seg: Segment): string => {
    const parts: string[] = [];
    
    // Determine if solid or hollow
    if (seg.id_diameter <= 0.001 || seg.id_diameter === 0) {
      parts.push('Solid cylindrical section');
    } else {
      parts.push('Hollow cylindrical section');
    }
    
    // Wall thickness characteristics
    if (wallThickness > 0) {
      if (wallThickness < 0.05) {
        parts.push('with very thin wall');
      } else if (wallThickness < 0.1) {
        parts.push('with thin wall');
      } else if (wallThickness > 0.5) {
        parts.push('with thick wall');
      }
    }
    
    // Size characteristics
    const length = seg.z_end - seg.z_start;
    if (length < 0.02) {
      parts.push('(very short segment)');
    } else if (length < 0.1) {
      parts.push('(short segment)');
    }
    
    // OD characteristics
    if (seg.od_diameter > 2.0) {
      parts.push('Large diameter');
    } else if (seg.od_diameter < 0.5) {
      parts.push('Small diameter');
    }
    
    // Flags-based descriptions
    if (seg.flags && seg.flags.length > 0) {
      if (seg.flags.includes('id_assumed_solid')) {
        parts.push('(ID assumed solid)');
      }
      if (seg.flags.includes('auto_merged')) {
        parts.push('(auto-merged)');
      }
      if (seg.flags.includes('thin_wall')) {
        parts.push('(thin wall detected)');
      }
      if (seg.flags.includes('short_segment')) {
        parts.push('(short segment)');
      }
    }
    
    return parts.join(' ');
  };

  const description = generateDescription(segment);

  return (
    <div
      className="segment-tooltip"
      style={{
        left: `${tooltipPosition.x}px`,
        top: `${tooltipPosition.y}px`,
      }}
    >
      <div className="tooltip-header">Segment {segmentIndex + 1}</div>
      <div className="tooltip-content">
        <div className="tooltip-description">{description}</div>
        <div className="tooltip-divider"></div>
        <div className="tooltip-row">
          <span className="tooltip-label">Z:</span>
          <span className="tooltip-value">
            {segment.z_start.toFixed(4)} → {segment.z_end.toFixed(4)} {units}
          </span>
        </div>
        <div className="tooltip-row">
          <span className="tooltip-label">OD:</span>
          <span className="tooltip-value">{segment.od_diameter.toFixed(4)} {units}</span>
        </div>
        <div className="tooltip-row">
          <span className="tooltip-label">ID:</span>
          <span className="tooltip-value">{segment.id_diameter.toFixed(4)} {units}</span>
        </div>
        <div className="tooltip-row">
          <span className="tooltip-label">Wall:</span>
          <span className="tooltip-value">{wallThickness.toFixed(4)} {units}</span>
        </div>
        {segment.confidence !== undefined && (
          <div className="tooltip-row">
            <span className="tooltip-label">Confidence:</span>
            <span className="tooltip-value">{(segment.confidence * 100).toFixed(1)}%</span>
          </div>
        )}
        {segment.flags && segment.flags.length > 0 && (
          <div className="tooltip-row">
            <span className="tooltip-label">Flags:</span>
            <span className="tooltip-value">{segment.flags.join(', ')}</span>
          </div>
        )}
      </div>
    </div>
  );
}

function ThreeJSViewer({
  summary,
  jobId,
  onHoveredSegmentChange,
  showHoles = false,
  showSlots = false,
  showChamfers = false,
  showFillets = false
}: ThreeJSViewerProps) {
  // View mode state
  const [viewMode, setViewMode] = useState<ViewMode>('standard');
  
  const [showOD, setShowOD] = useState(true);
  const [showID, setShowID] = useState(true);
  const [highlightThinWall, setHighlightThinWall] = useState(false);
  const [thinWallThreshold, setThinWallThreshold] = useState(0.1); // Default 0.1 units
  const [glbUrl, setGlbUrl] = useState<string | null>(null);
  const [hasGlb, setHasGlb] = useState(false);
  
  // Overlay toggles
  const [showODOverlay, setShowODOverlay] = useState(false);
  const [showIDOverlay, setShowIDOverlay] = useState(false);
  const [showShoulderPlanes, setShowShoulderPlanes] = useState(false);
  
  // Hover state
  const [hoveredSegmentIndex, setHoveredSegmentIndex] = useState<number | null>(null);
  const [mousePosition, setMousePosition] = useState({ x: 0, y: 0 });
  const canvasContainerRef = useRef<HTMLDivElement>(null);
  
  const controlsRef = useRef<any>(null);

  // Handle mouse move for tooltip positioning
  const handleMouseMove = useCallback((e: React.MouseEvent<HTMLDivElement>) => {
    setMousePosition({ x: e.clientX, y: e.clientY });
  }, []);

  // Handle hover change from Scene
  const handleHoverChange = useCallback((index: number | null) => {
    setHoveredSegmentIndex(index);
    // Notify parent component if callback provided
    if (onHoveredSegmentChange) {
      onHoveredSegmentChange(index);
    }
  }, [onHoveredSegmentChange]);

  // Check if GLB file exists
  useEffect(() => {
    const checkGlbFile = async () => {
      try {
        const files = await api.getJobFiles(jobId);
        const glbFile = files.files.find((f) => f.name === 'model.glb');
        if (glbFile) {
          setHasGlb(true);
          setGlbUrl(api.getPdfUrl(jobId, 'outputs/model.glb'));
        }
      } catch (err) {
        // Ignore errors - will fall back to procedural
      }
    };
    checkGlbFile();
    // Check periodically
    const interval = setInterval(checkGlbFile, 2000);
    return () => clearInterval(interval);
  }, [jobId]);

  const handleResetView = () => {
    if (controlsRef.current) {
      controlsRef.current.reset();
    }
  };

  // Calculate bounds for camera positioning
  const [minZ, maxZ] = summary.z_range;
  const zRange = maxZ - minZ;
  const maxRadius = Math.max(...summary.segments.map((s) => s.od_diameter / 2), 1);
  const cameraDistance = Math.max(zRange * 1.2, maxRadius * 3);
  
  // Camera FOV: 35-40 for CAD feel in realistic mode
  const cameraFov = viewMode === 'realistic' ? 38 : 50;

  return (
    <div className="threejs-viewer">
      <div className="viewer-header">
        <h3>3D View {hasGlb && <span className="glb-badge">(GLB)</span>}</h3>
      </div>
      
      {/* Viewer Toolbar */}
      <ViewerToolbar
        viewMode={viewMode}
        onViewModeChange={setViewMode}
        showOD={showOD}
        onShowODChange={setShowOD}
        showID={showID}
        onShowIDChange={setShowID}
        highlightThinWall={highlightThinWall}
        onHighlightThinWallChange={setHighlightThinWall}
        thinWallThreshold={thinWallThreshold}
        onThinWallThresholdChange={setThinWallThreshold}
        showODOverlay={showODOverlay}
        onShowODOverlayChange={setShowODOverlay}
        showIDOverlay={showIDOverlay}
        onShowIDOverlayChange={setShowIDOverlay}
        showShoulderPlanes={showShoulderPlanes}
        onShowShoulderPlanesChange={setShowShoulderPlanes}
        hasGlb={hasGlb}
        glbUrl={glbUrl}
        units={summary.units.length}
        onResetView={handleResetView}
      />
      <div 
        className="viewer-canvas" 
        ref={canvasContainerRef}
        onMouseMove={handleMouseMove}
      >
        <Canvas
          gl={{
            antialias: true,
            alpha: false,
            powerPreference: 'high-performance',
          }}
          style={{
            background: viewMode === 'realistic' 
              ? 'linear-gradient(to bottom, #e0e0e0, #f5f5f5)' 
              : '#0a0a0a',
          }}
        >
          <PerspectiveCamera
            makeDefault
            position={[cameraDistance, cameraDistance, cameraDistance]}
            fov={cameraFov}
          />
          <OrbitControls
            ref={controlsRef}
            enableDamping
            dampingFactor={0.05}
            minDistance={maxRadius * 0.5}
            maxDistance={cameraDistance * 3}
          />
          <Scene
            summary={summary}
            showOD={showOD}
            showID={showID}
            highlightThinWall={highlightThinWall}
            thinWallThreshold={thinWallThreshold}
            glbUrl={glbUrl}
            showODOverlay={showODOverlay}
            showIDOverlay={showIDOverlay}
            showShoulderPlanes={showShoulderPlanes}
            jobId={jobId}
            hoveredSegmentIndex={hoveredSegmentIndex}
            onHoverChange={handleHoverChange}
            viewMode={viewMode}
            showHoles={showHoles}
            showSlots={showSlots}
            showChamfers={showChamfers}
            showFillets={showFillets}
          />
        </Canvas>
        <Tooltip
          segment={hoveredSegmentIndex !== null ? (getSegments(jobId)?.[hoveredSegmentIndex] ?? null) : null}
          segmentIndex={hoveredSegmentIndex}
          visible={hoveredSegmentIndex !== null}
          mouseX={mousePosition.x}
          mouseY={mousePosition.y}
          containerRef={canvasContainerRef}
          units={summary.units.length}
        />
      </div>
      <div className="viewer-legend">
        <div className="legend-item">
          <div className="legend-color" style={{ backgroundColor: '#4a9eff' }}></div>
          <span>OD Surface</span>
        </div>
        <div className="legend-item">
          <div className="legend-color" style={{ backgroundColor: '#ff8c42' }}></div>
          <span>ID Surface</span>
        </div>
        {highlightThinWall && (
          <div className="legend-item">
            <div className="legend-color" style={{ backgroundColor: '#ff0000' }}></div>
            <span>Thin Wall (&lt; {thinWallThreshold} {summary.units.length})</span>
          </div>
        )}
      </div>
    </div>
  );
}

// Feature overlay components
interface FeatureHoleProps {
  hole: any;
  index: number;
  totalHoles?: number;
  partMaxOD?: number;
  partLength?: number;
}

function FeatureHole({ hole, index, totalHoles = 1, partMaxOD = 2, partLength = 2 }: FeatureHoleProps) {
  // Show holes as small ring indicators on the part surface
  // Much more subtle visualization
  
  const holeRadius = Math.min((hole.diameter * 0.5) || 0.05, 0.15); // Cap size
  const kind = hole.kind || 'cross';
  
  // Position on OD surface with better distribution
  const odRadius = (partMaxOD / 2) + 0.01; // Slightly outside OD
  
  // Use golden ratio for better angular distribution (less clustering)
  const goldenAngle = Math.PI * (3 - Math.sqrt(5)); // ~137.5 degrees
  const angle = index * goldenAngle;
  
  // Distribute along Z with some randomization based on index
  const zBase = (index / Math.max(totalHoles - 1, 1)) * partLength;
  const zPosition = Math.max(0.05, Math.min(partLength - 0.05, zBase));
  
  if (kind === 'axial') {
    // Axial holes - show as ring on end face
    return (
      <group position={[0, 0, partLength + 0.02]}>
        <mesh rotation={[Math.PI / 2, 0, 0]}>
          <torusGeometry args={[holeRadius, 0.01, 8, 24]} />
          <meshStandardMaterial
            color="#ff4444"
            emissive="#ff2222"
            emissiveIntensity={0.8}
          />
        </mesh>
      </group>
    );
  }
  
  // Cross holes - show as small ring on OD surface
  const xPos = Math.cos(angle) * odRadius;
  const yPos = Math.sin(angle) * odRadius;
  
  return (
    <group position={[xPos, yPos, zPosition]}>
      {/* Ring indicator on surface - oriented to face outward */}
      <mesh rotation={[0, -angle + Math.PI/2, Math.PI/2]}>
        <torusGeometry args={[holeRadius, 0.008, 8, 24]} />
        <meshStandardMaterial
          color="#ff4444"
          emissive="#ff2222"
          emissiveIntensity={0.8}
        />
      </mesh>
      {/* Small dot at center */}
      <mesh>
        <sphereGeometry args={[0.015, 8, 8]} />
        <meshStandardMaterial
          color="#ff6666"
          emissive="#ff4444"
          emissiveIntensity={1.0}
        />
      </mesh>
    </group>
  );
}

interface FeatureSlotProps {
  slot: any;
  index: number;
  totalSlots?: number;
  partMaxOD?: number;
  partLength?: number;
}

function FeatureSlot({ slot, index, totalSlots = 1, partMaxOD = 2, partLength = 2 }: FeatureSlotProps) {
  const orientation = slot.orientation || 'axial';
  const slotLength = Math.min(slot.length || 0.3, 0.5); // Cap size
  const slotWidth = Math.min(slot.width || 0.05, 0.1);
  
  // Position slots along the part with offset from holes
  const goldenAngle = Math.PI * (3 - Math.sqrt(5));
  const angle = index * goldenAngle + Math.PI / 3; // Offset from holes
  
  const zBase = (index / Math.max(totalSlots - 1, 1)) * partLength;
  const zPosition = Math.max(0.1, Math.min(partLength - 0.1, zBase));
  const odRadius = (partMaxOD / 2) + 0.01;
  
  const xPos = Math.cos(angle) * odRadius;
  const yPos = Math.sin(angle) * odRadius;
  
  // Show slots as wireframe rectangles on surface
  return (
    <group position={[xPos, yPos, zPosition]}>
      <mesh rotation={[0, -angle + Math.PI/2, Math.PI/2]}>
        <boxGeometry args={[slotLength, 0.02, slotWidth]} />
        <meshStandardMaterial
          color="#44ff44"
          emissive="#22ff22"
          emissiveIntensity={0.8}
          wireframe={orientation === 'radial'}
        />
      </mesh>
      {/* Small indicator sphere */}
      <mesh>
        <sphereGeometry args={[0.012, 8, 8]} />
        <meshStandardMaterial
          color="#66ff66"
          emissive="#44ff44"
          emissiveIntensity={1.0}
        />
      </mesh>
    </group>
  );
}

interface FeatureChamferProps {
  chamfer: any;
  index: number;
}

function FeatureChamfer({ chamfer, index }: FeatureChamferProps) {
  // Simple marker for chamfer - small sphere
  const zPosition = chamfer.source_view_index * 0.5 || 0;
  const radius = chamfer.size || 0.02;

  return (
    <group position={[0, radius * 2, zPosition]}>
      <mesh>
        <sphereGeometry args={[radius, 8, 8]} />
        <meshStandardMaterial
          color="#ffff44"
          emissive="#ffff44"
          emissiveIntensity={0.5}
        />
      </mesh>
      <FeatureLabel text={`C${index + 1}`} position={[0, radius * 3, 0]} />
    </group>
  );
}

interface FeatureFilletProps {
  fillet: any;
  index: number;
}

function FeatureFillet({ fillet, index }: FeatureFilletProps) {
  // Simple marker for fillet - small torus
  const zPosition = fillet.source_view_index * 0.5 || 0;
  const radius = fillet.radius || 0.02;

  return (
    <group position={[0, radius * 2, zPosition]}>
      <mesh>
        <torusGeometry args={[radius * 1.5, radius * 0.5, 8, 16]} />
        <meshStandardMaterial
          color="#ff44ff"
          emissive="#ff44ff"
          emissiveIntensity={0.5}
        />
      </mesh>
      <FeatureLabel text={`F${index + 1}`} position={[0, radius * 4, 0]} />
    </group>
  );
}

interface FeatureLabelProps {
  text: string;
  position: [number, number, number];
}

function FeatureLabel({ text: _text, position: _position }: FeatureLabelProps) {
  // For now, we'll skip text labels as they require additional Three.js setup
  // This could be implemented with TextGeometry or HTML overlays
  return null;
}

export default ThreeJSViewer;

