import { useRef, useMemo, useState, useEffect, Suspense, useCallback } from 'react';
import { Canvas, useLoader, useThree, useFrame } from '@react-three/fiber';
import { OrbitControls, PerspectiveCamera, Grid, GizmoHelper, GizmoViewcube, Line } from '@react-three/drei';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import * as THREE from 'three';
import { api } from '../../services/api';
import type { PartSummary } from '../../services/types';
import { getSegments, type Segment } from '../../state/segmentStore';
import { SceneLights, type ViewMode } from './SceneLights';
import { EdgeOutlines } from './EdgeOutlines';
import './ThreeJSViewer.css';

const VIEWER_BG = '#101418';
const VIEWER_GRID_CELL = '#1A2A38';
const VIEWER_GRID_SECTION = '#1E3448';
const VIEWER_METAL = 0xc7d3df;
const VIEWER_METAL_DARK = 0x7f93aa;
const VIEWER_BORE = 0x142231;
const VIEWER_HIGHLIGHT = 0xe9eef5;
const DIM_GOLD = '#FFD700';
const DIM_CYAN = '#00D4FF';

type CameraPreset = 'full' | 'section' | 'od' | 'id' | 'xray';

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
        color: new THREE.Color(odColor).lerp(new THREE.Color(VIEWER_METAL), 0.72),
        metalness: 0.9,
        roughness: 0.16,
        clearcoat: 0.72,
        clearcoatRoughness: 0.08,
        reflectivity: 0.95,
        envMapIntensity: 1.35,
        specularIntensity: 1,
        specularColor: new THREE.Color(VIEWER_HIGHLIGHT),
        transparent: false,
        opacity: 1.0,
        side: THREE.FrontSide,
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
      return new THREE.MeshPhysicalMaterial({
        color: new THREE.Color(odColor).lerp(new THREE.Color(VIEWER_METAL_DARK), 0.55),
        metalness: 0.82,
        roughness: 0.24,
        clearcoat: 0.48,
        clearcoatRoughness: 0.12,
        envMapIntensity: 1.1,
        transparent: false,
        opacity: 1.0,
        side: THREE.FrontSide,
        wireframe: isThinWall && highlightThinWall,
        depthWrite: true,
      });
    }
  }, [odColor, isThinWall, highlightThinWall, viewMode]);

  const idMaterial = useMemo(() => {
    if (viewMode === 'realistic') {
      return new THREE.MeshPhysicalMaterial({
        color: new THREE.Color(idColor).lerp(new THREE.Color(VIEWER_BORE), 0.75),
        metalness: 0.72,
        roughness: 0.34,
        clearcoat: 0.28,
        clearcoatRoughness: 0.18,
        envMapIntensity: 0.85,
        transparent: false,
        opacity: 1.0,
        side: THREE.BackSide,
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
      return new THREE.MeshPhysicalMaterial({
        color: new THREE.Color(idColor).lerp(new THREE.Color(VIEWER_BORE), 0.68),
        metalness: 0.7,
        roughness: 0.38,
        clearcoat: 0.2,
        clearcoatRoughness: 0.2,
        envMapIntensity: 0.8,
        transparent: false,
        opacity: 1.0,
        side: THREE.BackSide,
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

function SegmentBoundaryRings({
  segments,
}: {
  segments: PartSummary['segments'];
}) {
  if (!segments || segments.length < 2) return null;

  return (
    <>
      {segments.slice(0, -1).map((segment, index) => {
        const boundaryZ = segment.z_end;
        const ringRadius = Math.max((segment.od_diameter || 0) / 2 + 0.018, 0.02);
        const tubeRadius = Math.max(Math.min(ringRadius * 0.012, 0.03), 0.008);
        return (
          <mesh
            key={`segment-boundary-${index}`}
            position={[0, 0, boundaryZ]}
            rotation={[Math.PI / 2, 0, 0]}
            renderOrder={5000 + index}
            frustumCulled={false}
          >
            <torusGeometry args={[ringRadius, tubeRadius, 10, 48]} />
            <meshStandardMaterial
              color={index % 2 === 0 ? '#9fb7d1' : '#6b8198'}
              metalness={0.25}
              roughness={0.75}
              transparent
              opacity={0.9}
            />
          </mesh>
        );
      })}
    </>
  );
}

interface GlbModelProps {
  url: string;
  viewMode: ViewMode;
  cameraPreset: CameraPreset;
  cameraVersion: number;
  dims: ViewerDimensions;
}

/**
 * After the GLB scene is in the render tree, compute its real axis-aligned
 * bounding box and reposition the camera so the longest axis (the turning
 * axis) lies horizontally across the view.  This is robust to any world-space
 * orientation the STEP exporter chose for the part.
 */
function GlbFitCamera({
  scene,
  preset,
  version,
  dims,
}: {
  scene: THREE.Object3D;
  preset: CameraPreset;
  version: number;
  dims: ViewerDimensions;
}) {
  const { camera, controls } = useThree();
  // Re-run whenever version changes (user clicked a preset button or reset)
  const fittedKey = useRef<string>('');

  useEffect(() => {
    const key = `${scene.uuid}-${preset}-${version}`;
    if (fittedKey.current === key) return;
    fittedKey.current = key;

    const box = new THREE.Box3().setFromObject(scene);
    if (box.isEmpty()) return;

    const center = box.getCenter(new THREE.Vector3());
    const size = box.getSize(new THREE.Vector3());

    const axes = [
      { axis: 'x' as const, size: size.x, min: box.min.x, max: box.max.x },
      { axis: 'y' as const, size: size.y, min: box.min.y, max: box.max.y },
      { axis: 'z' as const, size: size.z, min: box.min.z, max: box.max.z },
    ].sort((a, b) => b.size - a.size);

    const longestAxis = axes[0].axis;
    const shortestAxis = axes[2].axis;

    // For arbitrary STEP solids, the most complete presentation is usually
    // looking along the SHORTEST axis so the two largest extents fill the view.
    // Keep longest-axis viewing only for the explicit ID preset.
    const viewAxis = preset === 'id' ? longestAxis : shortestAxis;
    const visibleAxes = (['x', 'y', 'z'] as const).filter((axis) => axis !== viewAxis);
    const visibleSizeA = size[visibleAxes[0]];
    const visibleSizeB = size[visibleAxes[1]];
    const depthSize = size[viewAxis];

    const fovRad = 18 * Math.PI / 180;
    const halfA = visibleSizeA / 2;
    const halfB = visibleSizeB / 2;
    const halfDepth = depthSize / 2;
    const sceneSphere = Math.sqrt(halfA ** 2 + (halfB * 1.05) ** 2 + halfDepth ** 2);
    const dist = (sceneSphere / Math.tan(fovRad)) * 1.12;

    const axisVector = (axis: 'x' | 'y' | 'z', amount: number) =>
      axis === 'x'
        ? new THREE.Vector3(amount, 0, 0)
        : axis === 'y'
        ? new THREE.Vector3(0, amount, 0)
        : new THREE.Vector3(0, 0, amount);

    let camPos = center.clone();
    if (preset === 'id') {
      camPos.add(axisVector(viewAxis, dist));
    } else if (preset === 'od') {
      camPos.add(axisVector(viewAxis, dist));
    } else if (preset === 'section') {
      camPos
        .add(axisVector(viewAxis, dist * 0.94))
        .add(axisVector(visibleAxes[0], dist * 0.26))
        .add(axisVector(visibleAxes[1], dist * 0.16));
    } else if (preset === 'xray') {
      camPos
        .add(axisVector(viewAxis, dist * 0.92))
        .add(axisVector(visibleAxes[0], dist * 0.22))
        .add(axisVector(visibleAxes[1], dist * 0.10));
    } else {
      // Full: face-on to shortest axis with a mild 3/4 lift.
      camPos
        .add(axisVector(viewAxis, dist * 0.96))
        .add(axisVector(visibleAxes[0], dist * 0.18))
        .add(axisVector(visibleAxes[1], dist * 0.12));
    }

    // Keep camera roll stable by choosing an up vector from one visible axis.
    const upAxis = visibleAxes.includes('y') ? 'y' : visibleAxes[0];
    camera.up.copy(axisVector(upAxis, 1));

    camera.position.copy(camPos);
    camera.lookAt(center);
    camera.near = Math.max(dist * 0.0001, 0.0002);
    camera.far = dist * 25;
    camera.updateProjectionMatrix();

    if (controls) {
      const orbit = controls as any;
      orbit.target.copy(center);
      const wasEnabled = orbit.enableDamping;
      orbit.enableDamping = false;
      orbit.update();
      orbit.enableDamping = wasEnabled;
    }
  }, [scene, preset, version, camera, controls, dims.length, dims.maxOd, dims.maxRadius]);

  return null;
}

interface ViewerDimensions {
  units: string;
  minZ: number;
  maxZ: number;
  midZ: number;
  length: number | null;
  maxOd: number | null;
  boreId: number | null;
  maxRadius: number;
  featureLines: string[];
}

interface ViewerHudProps {
  dims: ViewerDimensions;
  visible: boolean;
  onHover: (dim: string | null) => void;
}

interface PdfViewerToolbarProps {
  cameraPreset: CameraPreset;
  onCameraPresetChange: (preset: CameraPreset) => void;
  showDims: boolean;
  onShowDimsChange: (show: boolean) => void;
  onResetView: () => void;
}

interface DimOverlaysProps {
  dims: ViewerDimensions;
  visible: boolean;
}

interface DimFocusHighlightProps {
  activeDim: string | null;
  dims: ViewerDimensions;
}

interface SceneCameraDriverProps {
  position: [number, number, number];
  target: [number, number, number];
  version: number;
}

function formatPrimaryDimension(value: number | null | undefined, units: string): string {
  if (value == null || !Number.isFinite(value)) return '—';
  return units === 'in' ? `${value.toFixed(4)}"` : `${value.toFixed(3)} ${units}`;
}

function formatMetricDimension(value: number | null | undefined, units: string): string {
  if (value == null || !Number.isFinite(value)) return '—';
  const mmValue = units === 'in' ? value * 25.4 : value;
  return `${mmValue.toFixed(2)} mm`;
}

function buildFeatureList(summary: PartSummary): string[] {
  const features = summary.features;
  const lines: string[] = [];

  if (features?.holes?.length) lines.push(`${features.holes.length}× holes`);
  if (features?.slots?.length) lines.push(`${features.slots.length}× slots`);
  if (features?.chamfers?.length) lines.push(`${features.chamfers.length}× chamfers`);
  if (features?.fillets?.length) lines.push(`${features.fillets.length}× fillets`);

  if (!lines.length && summary.feature_counts?.internal_bores) {
    lines.push(`${summary.feature_counts.internal_bores}× internal bores`);
  }
  if (!lines.length && summary.feature_counts?.external_cylinders) {
    lines.push(`${summary.feature_counts.external_cylinders}× external cylinders`);
  }

  return lines.slice(0, 4);
}

function getViewerDimensions(summary: PartSummary): ViewerDimensions {
  const units = summary.units?.length || 'in';
  const selected = summary.selected_body?.dimensions;
  const positiveIds = summary.segments
    .map((segment) => segment.id_diameter)
    .filter((value) => Number.isFinite(value) && value > 0);
  const zSpan =
    summary.z_range && summary.z_range.length >= 2
      ? summary.z_range[1] - summary.z_range[0]
      : null;

  const length = selected?.length_in ?? summary.totals?.total_length_in ?? zSpan;
  const maxOd = selected?.od_in ?? summary.totals?.max_od_in ?? null;
  const boreId = selected?.id_in ?? (positiveIds.length ? Math.min(...positiveIds) : null);
  const featureLines = buildFeatureList(summary);
  const minZ = summary.z_range?.[0] ?? 0;
  const maxZ = summary.z_range?.[1] ?? ((length ?? 1) + minZ);
  const maxRadius = Math.max(...summary.segments.map((segment) => (segment.od_diameter || 0) / 2), (maxOd ?? 1) / 2, 1);

  return {
    units,
    minZ,
    maxZ,
    midZ: (minZ + maxZ) / 2,
    length,
    maxOd,
    boreId,
    maxRadius,
    featureLines,
  };
}

function ViewerHud({ dims, visible, onHover }: ViewerHudProps) {
  const [hoveredRow, setHoveredRow] = useState<string | null>(null);

  if (!visible) return null;

  const rows = [
    { label: 'LENGTH', value: dims.length, colorClass: 'viewer-hud-value--gold' },
    { label: 'MAX OD', value: dims.maxOd, colorClass: 'viewer-hud-value--gold' },
    { label: 'BORE ID', value: dims.boreId, colorClass: 'viewer-hud-value--cyan' },
  ].filter((row) => row.value != null);

  const handleEnter = (label: string) => {
    setHoveredRow(label);
    onHover(label);
  };

  const handleLeave = () => {
    setHoveredRow(null);
    onHover(null);
  };

  return (
    <div className="viewer-hud-panel">
      <div className="viewer-hud-title">✦ DIMENSIONS</div>
      {rows.map((row) => {
        const active = hoveredRow === row.label;
        return (
          <div
            key={row.label}
            className={`viewer-hud-row${active ? ' viewer-hud-row--active' : ''}`}
            onMouseEnter={() => handleEnter(row.label)}
            onMouseLeave={handleLeave}
          >
            <span className={`viewer-hud-label${active ? ' viewer-hud-label--active' : ''}`}>{row.label}</span>
            <span className={`viewer-hud-value ${row.colorClass}`}>{formatPrimaryDimension(row.value, dims.units)}</span>
            <span className={`viewer-hud-metric${active ? ' viewer-hud-metric--active' : ''}`}>{formatMetricDimension(row.value, dims.units)}</span>
            {active && <span className="viewer-hud-arrow">◀</span>}
          </div>
        );
      })}

      {dims.featureLines.length > 0 && (
        <>
          <div className="viewer-hud-title viewer-hud-title--secondary">✦ FEATURES</div>
          <div className="viewer-hud-features">
            {dims.featureLines.map((line) => (
              <div key={line} className="viewer-hud-feature">{line}</div>
            ))}
          </div>
        </>
      )}
    </div>
  );
}

function ToolbarButton({
  label,
  active = false,
  onClick,
  title,
}: {
  label: string;
  active?: boolean;
  onClick: () => void;
  title?: string;
}) {
  return (
    <button
      onClick={onClick}
      title={title}
      className={`pdf-toolbar-btn${active ? ' pdf-toolbar-btn--active' : ''}`}
    >
      {label}
    </button>
  );
}

function PdfViewerToolbar({
  cameraPreset,
  onCameraPresetChange,
  showDims,
  onShowDimsChange,
  onResetView,
}: PdfViewerToolbarProps) {
  return (
    <div className="pdf-toolbar">
      <ToolbarButton label="Full" active={cameraPreset === 'full'} onClick={() => onCameraPresetChange('full')} title="OD exterior + open bore face" />
      <ToolbarButton label="Section" active={cameraPreset === 'section'} onClick={() => onCameraPresetChange('section')} title="Half-section style camera" />
      <ToolbarButton label="OD" active={cameraPreset === 'od'} onClick={() => onCameraPresetChange('od')} title="Side profile" />
      <ToolbarButton label="ID" active={cameraPreset === 'id'} onClick={() => onCameraPresetChange('id')} title="End-on bore" />
      <ToolbarButton label="X-Ray" active={cameraPreset === 'xray'} onClick={() => onCameraPresetChange('xray')} title="Transparent shell" />
      <div className="pdf-toolbar-divider" />
      <ToolbarButton label="Dims" active={showDims} onClick={() => onShowDimsChange(!showDims)} title="Toggle dim overlays" />
      <ToolbarButton label="⟳" onClick={onResetView} title="Reset camera" />
    </div>
  );
}

function DimOverlays({ dims, visible }: DimOverlaysProps) {
  if (!visible || !dims.length || !dims.maxOd) return null;

  const halfLength = dims.length / 2;
  const topY = dims.maxRadius * 1.75;
  const tick = Math.max(dims.maxRadius * 0.1, 0.04);
  const odZ = dims.midZ + halfLength * 0.35;
  const idZ = dims.midZ - halfLength * 0.35;
  const boreRadius = (dims.boreId ?? 0) / 2;

  return (
    <>
      <Line points={[[0, -dims.maxRadius * 1.18, odZ], [0, dims.maxRadius * 1.18, odZ]]} color={DIM_GOLD} lineWidth={1.2} />
      <Line points={[[-tick, -dims.maxRadius, odZ], [tick, -dims.maxRadius, odZ]]} color={DIM_GOLD} lineWidth={1.6} />
      <Line points={[[-tick, dims.maxRadius, odZ], [tick, dims.maxRadius, odZ]]} color={DIM_GOLD} lineWidth={1.6} />

      {boreRadius > 0.002 && (
        <>
          <Line points={[[0, -boreRadius * 1.28, idZ], [0, boreRadius * 1.28, idZ]]} color={DIM_CYAN} lineWidth={1.2} />
          <Line points={[[-tick * 0.7, -boreRadius, idZ], [tick * 0.7, -boreRadius, idZ]]} color={DIM_CYAN} lineWidth={1.6} />
          <Line points={[[-tick * 0.7, boreRadius, idZ], [tick * 0.7, boreRadius, idZ]]} color={DIM_CYAN} lineWidth={1.6} />
        </>
      )}

      <Line points={[[0, topY, dims.minZ], [0, topY, dims.maxZ]]} color={DIM_GOLD} lineWidth={1.2} />
      <Line points={[[-tick, topY, dims.minZ], [tick, topY, dims.minZ]]} color={DIM_GOLD} lineWidth={1.6} />
      <Line points={[[-tick, topY, dims.maxZ], [tick, topY, dims.maxZ]]} color={DIM_GOLD} lineWidth={1.6} />
      <Line points={[[0, dims.maxRadius, dims.minZ], [0, topY, dims.minZ]]} color={DIM_GOLD} lineWidth={0.5} dashed dashSize={dims.maxRadius * 0.09} gapSize={dims.maxRadius * 0.06} />
      <Line points={[[0, dims.maxRadius, dims.maxZ], [0, topY, dims.maxZ]]} color={DIM_GOLD} lineWidth={0.5} dashed dashSize={dims.maxRadius * 0.09} gapSize={dims.maxRadius * 0.06} />
    </>
  );
}

function DimFocusHighlight({ activeDim, dims }: DimFocusHighlightProps) {
  const matsRef = useRef<THREE.MeshStandardMaterial[]>([]);

  useFrame(({ clock }) => {
    const pulse = Math.sin(clock.getElapsedTime() * 4) * 0.5 + 0.5;
    matsRef.current.forEach((material) => {
      if (material) material.emissiveIntensity = 0.4 + pulse * 1.4;
    });
  });

  matsRef.current = [];
  const register = (material: THREE.MeshStandardMaterial | null) => {
    if (material) matsRef.current.push(material);
  };

  if (!activeDim || !dims.length || !dims.maxOd) return null;

  const halfLength = dims.length / 2;
  const color = activeDim === 'BORE ID' ? DIM_CYAN : DIM_GOLD;
  const boreRadius = (dims.boreId ?? 0) / 2;

  if (activeDim === 'LENGTH') {
    const ringRadius = dims.maxRadius * 1.09;
    const tubeRadius = Math.max(dims.maxRadius * 0.055, 0.012);
    return (
      <group>
        <mesh position={[0, 0, dims.minZ]} rotation={[Math.PI / 2, 0, 0]}>
          <torusGeometry args={[ringRadius, tubeRadius, 12, 64]} />
          <meshStandardMaterial ref={register} color={color} emissive={color} emissiveIntensity={0.8} transparent opacity={0.6} depthWrite={false} />
        </mesh>
        <mesh position={[0, 0, dims.maxZ]} rotation={[Math.PI / 2, 0, 0]}>
          <torusGeometry args={[ringRadius, tubeRadius, 12, 64]} />
          <meshStandardMaterial ref={register} color={color} emissive={color} emissiveIntensity={0.8} transparent opacity={0.6} depthWrite={false} />
        </mesh>
      </group>
    );
  }

  if (activeDim === 'MAX OD') {
    return (
      <mesh position={[0, 0, dims.midZ]} rotation={[Math.PI / 2, 0, 0]}>
        <cylinderGeometry args={[dims.maxRadius * 1.013, dims.maxRadius * 1.013, halfLength * 2, 64, 1, true]} />
        <meshStandardMaterial ref={register} color={color} emissive={color} emissiveIntensity={0.8} transparent opacity={0.25} depthWrite={false} side={THREE.DoubleSide} />
      </mesh>
    );
  }

  if (activeDim === 'BORE ID' && boreRadius > 0.002) {
    return (
      <mesh position={[0, 0, dims.midZ]} rotation={[Math.PI / 2, 0, 0]}>
        <cylinderGeometry args={[boreRadius * 0.982, boreRadius * 0.982, halfLength * 2, 64, 1, true]} />
        <meshStandardMaterial ref={register} color={color} emissive={color} emissiveIntensity={0.8} transparent opacity={0.35} depthWrite={false} side={THREE.DoubleSide} />
      </mesh>
    );
  }

  return null;
}

function SceneCameraDriver({ position, target, version }: SceneCameraDriverProps) {
  const { camera, controls } = useThree();
  const targetPositionRef = useRef(position);
  const targetLookAtRef = useRef(target);
  const movingRef = useRef(true);
  const frameCountRef = useRef(0);

  useEffect(() => {
    targetPositionRef.current = position;
    targetLookAtRef.current = target;
    movingRef.current = true;
    frameCountRef.current = 0;
  }, [position, target, version]);

  useFrame(() => {
    if (!movingRef.current) return;

    const [tx, ty, tz] = targetPositionRef.current;
    const [lx, ly, lz] = targetLookAtRef.current;
    const lerp = 0.13;

    camera.position.x += (tx - camera.position.x) * lerp;
    camera.position.y += (ty - camera.position.y) * lerp;
    camera.position.z += (tz - camera.position.z) * lerp;
    camera.lookAt(lx, ly, lz);

    frameCountRef.current += 1;

    const arrived =
      Math.abs(tx - camera.position.x) < 0.0001 &&
      Math.abs(ty - camera.position.y) < 0.0001 &&
      Math.abs(tz - camera.position.z) < 0.0001;

    if (arrived || frameCountRef.current > 90) {
      camera.position.set(tx, ty, tz);
      camera.lookAt(lx, ly, lz);
      movingRef.current = false;

      if (controls) {
        const orbit = controls as any;
        orbit.target.set(lx, ly, lz);
        const damping = orbit.enableDamping;
        orbit.enableDamping = false;
        orbit.update();
        orbit.enableDamping = damping;
      }
    }
  }, 1);

  return null;
}

function GlbModel({ url, viewMode, cameraPreset, cameraVersion, dims }: GlbModelProps) {
  const gltf = useLoader(GLTFLoader, url);
  
  // Traverse the scene and update materials based on view mode
  useEffect(() => {
    gltf.scene.traverse((child: any) => {
      if (child.isMesh) {
        // Disable frustum culling so no part of the model gets clipped
        // when the camera is close or the bounding sphere is off-centre.
        child.frustumCulled = false;
        child.castShadow = viewMode === 'realistic';
        child.receiveShadow = viewMode === 'realistic';

        // Ensure material is opaque and solid-looking
        if (child.material) {
          // Handle both single materials and arrays
          const materials = Array.isArray(child.material) ? child.material : [child.material];
          const newMaterials: THREE.Material[] = [];
          
          materials.forEach((material: any) => {
            if (material) {
              // Extract color if available
              let color = VIEWER_METAL;
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
                  color: new THREE.Color(color).lerp(new THREE.Color(VIEWER_METAL), 0.78),
                  metalness: 0.88,
                  roughness: 0.14,
                  clearcoat: 0.8,
                  clearcoatRoughness: 0.08,
                  reflectivity: 0.9,
                  envMapIntensity: 1.4,
                  specularIntensity: 1,
                  specularColor: new THREE.Color(VIEWER_HIGHLIGHT),
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
                newMaterial = new THREE.MeshPhysicalMaterial({
                  color: new THREE.Color(color).lerp(new THREE.Color(VIEWER_METAL_DARK), 0.72),
                  metalness: 0.78,
                  roughness: 0.22,
                  clearcoat: 0.45,
                  clearcoatRoughness: 0.12,
                  envMapIntensity: 1.1,
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
              color: VIEWER_METAL,
              metalness: 0.88,
              roughness: 0.14,
              clearcoat: 0.8,
              clearcoatRoughness: 0.08,
              reflectivity: 0.9,
              envMapIntensity: 1.4,
              specularIntensity: 1,
              specularColor: new THREE.Color(VIEWER_HIGHLIGHT),
              transparent: false,
              opacity: 1.0,
              side: THREE.DoubleSide,
            });
          } else if (viewMode === 'xray') {
            child.material = new THREE.MeshStandardMaterial({
              color: VIEWER_METAL,
              metalness: 0.0,
              roughness: 1.0,
              transparent: true,
              opacity: 0.7,
              side: THREE.DoubleSide,
            });
          } else {
            child.material = new THREE.MeshPhysicalMaterial({
              color: VIEWER_METAL_DARK,
              metalness: 0.78,
              roughness: 0.22,
              clearcoat: 0.45,
              clearcoatRoughness: 0.12,
              envMapIntensity: 1.1,
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
    <>
      <primitive object={gltf.scene} />
      <GlbFitCamera scene={gltf.scene} preset={cameraPreset} version={cameraVersion} dims={dims} />
    </>
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
  forceGlbOnly = false,
  cameraPreset = 'full',
  cameraVersion = 0,
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
  forceGlbOnly?: boolean;
  cameraPreset?: CameraPreset;
  cameraVersion?: number;
}) {
  // Enable hover detection
  useHoverDetection(jobId, onHoverChange);

  const maxOD = Math.max(...summary.segments.map((segment) => segment.od_diameter || 0), 1);
  const partLength = Math.max((summary.z_range?.[1] ?? 1) - (summary.z_range?.[0] ?? 0), 1);
  const sceneSize = Math.max(partLength, maxOD, 1);
  const floorY = -(maxOD / 2) * 1.3;

  // Get hovered segment
  const segments = getSegments(jobId);
  const hoveredSegment = hoveredSegmentIndex !== null && segments
    ? segments[hoveredSegmentIndex]
    : null;

  return (
    <>
      {/* Dynamic Lighting based on view mode */}
      <SceneLights viewMode={viewMode} enableShadows={viewMode === 'realistic'} />

      {/* Manufacturing floor/grid to match the PDF-side 3D viewer styling */}
      {viewMode !== 'xray' && (
        <group position={[0, floorY, 0]}>
          <Grid
            args={[sceneSize * 8, sceneSize * 8]}
            cellSize={sceneSize * 0.12}
            cellThickness={0.4}
            cellColor={VIEWER_GRID_CELL}
            sectionSize={sceneSize * 0.5}
            sectionThickness={0.8}
            sectionColor={VIEWER_GRID_SECTION}
            fadeDistance={sceneSize * 10}
            fadeStrength={2.5}
            infiniteGrid
          />
        </group>
      )}

      {/* Shadow-receiving ground plane (only in realistic mode) */}
      {viewMode === 'realistic' && (
        <mesh rotation={[-Math.PI / 2, 0, 0]} position={[0, floorY, 0]} receiveShadow>
          <planeGeometry args={[sceneSize * 10, sceneSize * 10]} />
          <shadowMaterial opacity={0.24} />
        </mesh>
      )}

      {/* Render GLB model if available, otherwise procedural */}
      {glbUrl ? (
        <Suspense fallback={null}>
          <GlbModel url={glbUrl} viewMode={viewMode} cameraPreset={cameraPreset} cameraVersion={cameraVersion} dims={getViewerDimensions(summary)} />
        </Suspense>
      ) : !forceGlbOnly ? (
        <>
          {summary.segments.map((segment, index) => (
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
          ))}
          <SegmentBoundaryRings segments={summary.segments} />
        </>
      ) : null}

      {/* Feature Overlays */}
      {(glbUrl || !forceGlbOnly) && (
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
      )}

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
  const dims = useMemo(() => getViewerDimensions(summary), [summary]);
  const [cameraPreset, setCameraPreset] = useState<CameraPreset>('full');
  const renderViewMode: ViewMode = cameraPreset === 'xray' ? 'xray' : 'realistic';
  const [cameraVersion, setCameraVersion] = useState(1);
  
  const [showOD] = useState(true);
  const [showID] = useState(true);
  const [highlightThinWall] = useState(false);
  const [thinWallThreshold] = useState(0.1); // Default 0.1 units
  const [glbUrl, setGlbUrl] = useState<string | null>(null);
  const [, setHasGlb] = useState(false);
  const [glbLoading, setGlbLoading] = useState(false);
  const [glbError, setGlbError] = useState<string | null>(null);
  const [showDims, setShowDims] = useState(true);
  const [hoveredHudDim, setHoveredHudDim] = useState<string | null>(null);
  
  // Overlay toggles
  const [showODOverlay] = useState(false);
  const [showIDOverlay] = useState(false);
  const [showShoulderPlanes] = useState(false);
  
  // Hover state
  const [hoveredSegmentIndex, setHoveredSegmentIndex] = useState<number | null>(null);
  const [mousePosition, setMousePosition] = useState({ x: 0, y: 0 });
  const canvasContainerRef = useRef<HTMLDivElement>(null);
  
  const controlsRef = useRef<any>(null);
  const glbObjectUrlRef = useRef<string | null>(null);
  const isStepBacked = (summary.inference_metadata?.source || '').startsWith('uploaded_step');

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

  // Load GLB preview.
  useEffect(() => {
    let cancelled = false;

    const revokeBlobUrl = () => {
      if (glbObjectUrlRef.current) {
        URL.revokeObjectURL(glbObjectUrlRef.current);
        glbObjectUrlRef.current = null;
      }
    };

    const assignBlobUrl = (url: string | null) => {
      revokeBlobUrl();
      glbObjectUrlRef.current = url;
      setGlbUrl(url);
    };

    const loadStepBackedGlb = async () => {
      setGlbLoading(true);
      setGlbError(null);
      try {
        const blobUrl = await api.fetch3dPreviewBlobUrl(jobId);
        if (cancelled) {
          URL.revokeObjectURL(blobUrl);
          return;
        }
        setHasGlb(true);
        assignBlobUrl(blobUrl);
      } catch (err) {
        if (!cancelled) {
          setHasGlb(false);
          assignBlobUrl(null);
          setGlbError(err instanceof Error ? err.message : 'Failed to load STEP-based 3D preview');
        }
      } finally {
        if (!cancelled) setGlbLoading(false);
      }
    };

    const checkGlbFile = async () => {
      try {
        const files = await api.getJobFiles(jobId);
        const glbFile = files.files.find((f) => f.name === 'model.glb');
        if (glbFile) {
          setHasGlb(true);
          setGlbError(null);
          try {
            const blobUrl = await api.fetchBlobUrl(jobId, 'outputs/model.glb');
            if (cancelled) {
              URL.revokeObjectURL(blobUrl);
              return;
            }
            assignBlobUrl(blobUrl);
          } catch {
            // glb unavailable
          }
        }
      } catch {
        // Ignore errors - will fall back to procedural
      }
    };

    if (isStepBacked) {
      void loadStepBackedGlb();
    } else {
      void checkGlbFile();
    }

    const interval = setInterval(() => {
      if (isStepBacked) {
        if (!glbObjectUrlRef.current) void loadStepBackedGlb();
      } else {
        void checkGlbFile();
      }
    }, 2000);

    return () => {
      cancelled = true;
      clearInterval(interval);
      revokeBlobUrl();
    };
  }, [jobId, isStepBacked]);

  const handleResetView = useCallback(() => {
    setCameraVersion((version) => version + 1);
  }, []);

  const handleCameraPresetChange = useCallback((preset: CameraPreset) => {
    setCameraPreset(preset);
    setCameraVersion((version) => version + 1);
  }, []);

  const zRange = Math.max(dims.maxZ - dims.minZ, 1);
  const maxRadius = dims.maxRadius;
  const sceneRadius = Math.sqrt((zRange / 2) ** 2 + (maxRadius * 1.9) ** 2 + maxRadius ** 2);
  const cameraDistance = (sceneRadius / Math.tan((18 * Math.PI) / 180)) * 1.12;
  const focusTarget = useMemo<[number, number, number]>(() => {
    if (cameraPreset === 'id') return [0, 0, dims.minZ];
    return [0, 0, dims.midZ];
  }, [cameraPreset, dims.minZ, dims.midZ]);
  const cameraPosition = useMemo<[number, number, number]>(() => {
    // IMPORTANT: For lathe/turned parts the turning axis is Z. To see the side profile
    // the camera Z must stay near dims.midZ so the look vector has near-zero Z component.
    // Any large Z offset turns the view end-on (circle face) especially for disc shapes.
    switch (cameraPreset) {
      case 'od':
        // Pure side profile — orthogonal to turning axis
        return [-cameraDistance, 0, dims.midZ];
      case 'section':
        // Elevated side view — see both OD height and ID depth
        return [-cameraDistance * 0.55, cameraDistance * 0.70, dims.midZ];
      case 'id':
        // Intentionally end-on: look straight down the bore
        return [cameraDistance * 0.02, cameraDistance * 0.02, dims.minZ - cameraDistance];
      case 'xray':
        // 3/4 side view — slightly elevated side, no Z offset
        return [-cameraDistance * 0.9, cameraDistance * 0.28, dims.midZ];
      case 'full':
      default:
        // 3/4 view from the side: mostly -X, slight elevation, camera Z exactly at midZ
        return [-cameraDistance * 0.9, cameraDistance * 0.28, dims.midZ];
    }
  }, [cameraPreset, cameraDistance, dims.midZ, dims.minZ]);

  useEffect(() => {
    setCameraVersion((version) => version + 1);
  }, [jobId]);

  const cameraFov = renderViewMode === 'realistic' ? 34 : 38;

  return (
    <div className={`threejs-viewer${isStepBacked ? ' acr-viewer-wrap threejs-viewer--step' : ''}`}>
      <div className="viewer-header">
        <h3>
          3D View {glbUrl && <span className="glb-badge">(GLB)</span>}
          {isStepBacked && !glbUrl && <span className="glb-badge">(STEP)</span>}
        </h3>
      </div>
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
          onCreated={({ gl }) => {
            gl.toneMapping = THREE.ACESFilmicToneMapping;
            gl.toneMappingExposure = 1.1;
            gl.outputColorSpace = THREE.SRGBColorSpace;
          }}
          shadows={renderViewMode === 'realistic'}
          style={{ background: VIEWER_BG }}
        >
          <color attach="background" args={[VIEWER_BG]} />
          {renderViewMode !== 'xray' && <fog attach="fog" args={[VIEWER_BG, cameraDistance * 1.8, cameraDistance * 7]} />}
          <PerspectiveCamera
            makeDefault
            position={cameraPosition}
            fov={cameraFov}
            near={0.01}
            far={cameraDistance * 20}
          />
          {!glbUrl && <SceneCameraDriver position={cameraPosition} target={focusTarget} version={cameraVersion} />}
          {glbUrl ? (
            <OrbitControls
              ref={controlsRef}
              enablePan
              enableDamping
              dampingFactor={0.06}
              minDistance={0.01}
              maxDistance={10000}
            />
          ) : (
            <OrbitControls
              ref={controlsRef}
              enablePan
              enableDamping
              dampingFactor={0.06}
              minDistance={maxRadius * 0.5}
              maxDistance={cameraDistance * 8}
              target={focusTarget}
            />
          )}
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
            viewMode={renderViewMode}
            showHoles={showHoles}
            showSlots={showSlots}
            showChamfers={showChamfers}
            showFillets={showFillets}
            forceGlbOnly={isStepBacked}
            cameraPreset={cameraPreset}
            cameraVersion={cameraVersion}
          />
          {/* DimOverlays use part_summary coordinate space — only valid for procedural (non-GLB) mode */}
          {!glbUrl && <DimOverlays dims={dims} visible={showDims} />}
          {!glbUrl && <DimFocusHighlight activeDim={hoveredHudDim} dims={dims} />}
          <GizmoHelper alignment="bottom-left" margin={[72, 72]}>
            <GizmoViewcube />
          </GizmoHelper>
        </Canvas>
        {isStepBacked && !glbUrl && (
          <div className="viewer-loading-overlay" style={{
            position: 'absolute',
            inset: 0,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            background: 'rgba(5, 8, 12, 0.78)',
            color: '#f5f7fa',
            textAlign: 'center',
            padding: '1rem',
            pointerEvents: 'none',
          }}>
            <div>
              <div style={{ fontWeight: 600, marginBottom: '0.5rem' }}>
                {glbLoading ? 'Loading STEP-based 3D preview…' : 'STEP-based 3D preview not ready'}
              </div>
              {glbError && <div style={{ color: '#fca5a5' }}>{glbError}</div>}
            </div>
          </div>
        )}
        <ViewerHud dims={dims} visible={showDims} onHover={setHoveredHudDim} />
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
      <PdfViewerToolbar
        cameraPreset={cameraPreset}
        onCameraPresetChange={handleCameraPresetChange}
        showDims={showDims}
        onShowDimsChange={setShowDims}
        onResetView={handleResetView}
      />
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

