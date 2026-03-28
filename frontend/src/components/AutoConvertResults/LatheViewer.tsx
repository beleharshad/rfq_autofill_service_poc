/**
 * LatheViewer.tsx — Realistic CAD/manufacturing 3-D preview for lathe-turned parts.
 *
 * Geometry built analytically from LLM-extracted parameters:
 *   finishOd     — finish outer diameter (in)
 *   maxOd        — max outer diameter (in)
 *   boreDiameter — bore inner diameter (in)
 *   lengthIn     — overall length (in)
 *   features[]   — grooves, chamfers from LLM
 *
 * Realistic details:
 *   • Chamfered 45° entries on both ends
 *   • Thread helix on OD body
 *   • O-ring / groove rings from features[]
 *   • PBR MeshPhysicalMaterial with clearcoat (machined aluminium)
 *   • Studio 3-point + rim + bore lighting with HemisphereLight
 *   • Manufacturing grid floor
 *   • ACESFilmic tone mapping
 *
 * Part axis = world X  (LatheGeometry Y after group rotation [0,0,π/2])
 */
import { useState, useEffect, useMemo, useRef, useCallback } from 'react';
import { Canvas, useThree, useFrame } from '@react-three/fiber';
import { OrbitControls, GizmoHelper, GizmoViewcube, Line, Grid } from '@react-three/drei';
import * as THREE from 'three';

// ─── Palette ───────────────────────────────────────────────────────────────────
const BG           = '#101418';
const METAL_COLOR  = '#B8C8D8';   // machined aluminium body
const METAL_DARK   = '#7A9BB5';   // step / shoulder variant
const BORE_COLOR   = '#1C2A38';   // dark bore cavity
const GROOVE_COL   = '#0E1A26';   // O-ring groove
const THREAD_COL   = '#8AACC0';   // thread crests
const BORE_EDGE    = '#2A4A68';   // bore entry torus
const AXIS_COLOR   = '#FF4444';
const DIM_GOLD     = '#FFD700';
const DIM_CYAN     = '#00D4FF';
const SEG_MIN_OD   = 0.05;
const SEGS         = 128;

// ─── Types ────────────────────────────────────────────────────────────────────
interface Segment { z_start: number; z_end: number; od_diameter: number; id_diameter: number; }
interface Feature { type: string; z_start?: number; z_end?: number; z_pos?: number;
                    od_diameter?: number; id_diameter?: number; size_in?: number; face?: string;
                    count?: number; angle_deg?: number; spec_text?: string; }
type ViewMode = 'full' | 'section' | 'od' | 'id' | 'xray';

// ─── PBR Materials (module-scope, shared) ─────────────────────────────────────
/** Machined aluminium shell */
const matMetal = new THREE.MeshPhysicalMaterial({
  color: new THREE.Color(METAL_COLOR), metalness: 0.88, roughness: 0.18,
  clearcoat: 0.55, clearcoatRoughness: 0.10, reflectivity: 0.9,
  envMapIntensity: 1.0, side: THREE.FrontSide,
});
/** Step / shoulder — slightly darker */
const matMetalDark = new THREE.MeshPhysicalMaterial({
  color: new THREE.Color(METAL_DARK), metalness: 0.85, roughness: 0.22,
  clearcoat: 0.40, clearcoatRoughness: 0.12, side: THREE.FrontSide,
});
/** X-ray ghost */
const matXray = new THREE.MeshPhysicalMaterial({
  color: new THREE.Color(METAL_COLOR), metalness: 0.5, roughness: 0.4,
  transparent: true, opacity: 0.12, depthWrite: false, side: THREE.DoubleSide,
});
/** Bore interior, BackSide so normals face inward */
const matBore = new THREE.MeshStandardMaterial({
  color: new THREE.Color(BORE_COLOR), metalness: 0.35, roughness: 0.70, side: THREE.BackSide,
});
/** End-face annular ring */
const matFace = new THREE.MeshPhysicalMaterial({
  color: new THREE.Color(METAL_DARK), metalness: 0.80, roughness: 0.25,
  clearcoat: 0.30, side: THREE.DoubleSide,
});
/** Section cut flat */
const matCut = new THREE.MeshPhysicalMaterial({
  color: new THREE.Color('#1E3A52'), metalness: 0.60, roughness: 0.40,
  clearcoat: 0.20, side: THREE.DoubleSide,
});
/** Groove / O-ring undercut */
const matGroove = new THREE.MeshStandardMaterial({
  color: new THREE.Color(GROOVE_COL), metalness: 0.40, roughness: 0.55, side: THREE.DoubleSide,
});
/** Thread crest */
const matThread = new THREE.MeshPhysicalMaterial({
  color: new THREE.Color(THREAD_COL), metalness: 0.90, roughness: 0.12,
  clearcoat: 0.60, side: THREE.FrontSide,
});
/** Bore entry lip torus */
const matBoreEdge = new THREE.MeshStandardMaterial({
  color: new THREE.Color(BORE_EDGE), metalness: 0.50, roughness: 0.35, side: THREE.DoubleSide,
});
/** Chamfer highlight ring */
const matChamfer = new THREE.MeshPhysicalMaterial({
  color: new THREE.Color('#E8B84B'), metalness: 0.75, roughness: 0.20,
  clearcoat: 0.70, emissive: new THREE.Color('#4A2E00'), emissiveIntensity: 0.15, side: THREE.DoubleSide,
});
/** Cross-hole/axial-hole indicator */
const matHole = new THREE.MeshStandardMaterial({
  color: new THREE.Color('#C04040'), metalness: 0.30, roughness: 0.55,
  emissive: new THREE.Color('#600000'), emissiveIntensity: 0.30, side: THREE.DoubleSide,
});
/** Fillet concave radius indicator */
const matFillet = new THREE.MeshPhysicalMaterial({
  color: new THREE.Color('#4A9FD8'), metalness: 0.70, roughness: 0.25,
  clearcoat: 0.60, emissive: new THREE.Color('#001A2E'), emissiveIntensity: 0.20, side: THREE.DoubleSide,
});

// ─── Shell profile with chamfers ──────────────────────────────────────────────
function buildShellGeo(
  finishR: number, maxR: number, boreR: number, halfL: number,
  isStep: boolean, stepFrac: number, phiLength: number,
): THREE.LatheGeometry {
  const r0    = Math.max(boreR + 0.001, 0.001);
  const chamW = Math.min(halfL * 0.06, maxR * 0.07);  // 45° chamfer width
  const pts: THREE.Vector2[] = [];

  // Near end: bore wall → chamfer → OD
  pts.push(new THREE.Vector2(r0,          -halfL));
  pts.push(new THREE.Vector2(maxR - chamW,-halfL));
  pts.push(new THREE.Vector2(maxR,        -halfL + chamW));

  if (isStep) {
    const stepY     = -halfL + halfL * 2 * (1 - stepFrac);
    const neckR     = finishR;
    const stepChamW = Math.min(chamW * 0.7, Math.abs(maxR - neckR) * 0.5);
    pts.push(new THREE.Vector2(maxR,              stepY));
    pts.push(new THREE.Vector2(neckR + stepChamW, stepY));
    pts.push(new THREE.Vector2(neckR,             stepY + stepChamW));
    pts.push(new THREE.Vector2(neckR,             halfL - chamW));
    pts.push(new THREE.Vector2(Math.max(neckR, maxR - chamW), halfL - chamW));
    pts.push(new THREE.Vector2(neckR,             halfL));
  } else {
    pts.push(new THREE.Vector2(maxR,        halfL - chamW));
    pts.push(new THREE.Vector2(maxR - chamW,halfL));
  }
  pts.push(new THREE.Vector2(r0, halfL));

  const g = new THREE.LatheGeometry(pts, SEGS, 0, phiLength);
  g.computeVertexNormals();
  return g;
}

// ─── Thread Helix ─────────────────────────────────────────────────────────────
function ThreadHelix({ r, zStart, zEnd, pitch, phiLen }: {
  r: number; zStart: number; zEnd: number; pitch: number; phiLen: number;
}) {
  const geo = useMemo(() => {
    const turns  = Math.max(1, Math.floor((zEnd - zStart) / pitch));
    const steps  = turns * 36;
    const tR     = pitch * 0.30;
    const pts: THREE.Vector3[] = [];
    for (let i = 0; i <= steps; i++) {
      const t   = i / steps;
      const y   = zStart + t * (zEnd - zStart);
      const ang = t * turns * Math.PI * 2;
      pts.push(new THREE.Vector3((r + tR) * Math.cos(ang), y, (r + tR) * Math.sin(ang)));
    }
    const curve = new THREE.CatmullRomCurve3(pts);
    return new THREE.TubeGeometry(curve, steps, tR * 0.38, 8, false);
  }, [r, zStart, zEnd, pitch, phiLen]);
  return <mesh geometry={geo} material={matThread} />;
}

// ─── Groove Rings from features[] ─────────────────────────────────────────────
function GrooveRings({ features, maxR, halfL, phiLen }: {
  features: Feature[]; maxR: number; halfL: number; phiLen: number;
}) {
  const rings = useMemo(() => features
    // Accept grooves on OD, or grooves without a face field (LLM often omits face)
    .filter(f => f.type === 'groove' && f.face !== 'id')
    .map((f, i) => {
      const zMid = f.z_pos != null ? f.z_pos
        : (f.z_start != null && f.z_end != null) ? (f.z_start + f.z_end) / 2 : null;
      if (zMid == null) return null;
      const localY = zMid - halfL;
      const grR    = (f.od_diameter != null ? f.od_diameter / 2 : maxR) - (f.size_in ?? 0) * 0.5;
      const grW    = f.size_in ?? maxR * 0.04;
      return { key: i, localY, majorR: Math.max(grR, 0.002), minorR: Math.min(grW * 0.55, grR * 0.5) };
    })
    .filter(Boolean) as { key: number; localY: number; majorR: number; minorR: number }[],
  [features, maxR, halfL]);
  return <>
    {rings.map(({ key, localY, majorR, minorR }) => (
      <mesh key={key} material={matGroove} position={[0, localY, 0]} rotation={[Math.PI/2, 0, 0]}>
        <torusGeometry args={[majorR, minorR, 10, SEGS, phiLen]} />
      </mesh>
    ))}
  </>;
}
// ─── Chamfer feature rings ──────────────────────────────────────────────────
// Renders a golden highlight ring at each LLM-extracted chamfer position.
function ChamferBands({ features, maxR, halfL, phiLen }: {
  features: Feature[]; maxR: number; halfL: number; phiLen: number;
}) {
  const bands = useMemo(() => features
    .filter(f => f.type === 'chamfer')
    .map((f, i) => {
      const z = f.z_pos != null ? f.z_pos
        : f.z_start != null ? f.z_start
        : f.z_end   != null ? f.z_end : null;
      if (z == null) return null;
      const localY = z - halfL;
      const size   = Math.min(f.size_in ?? maxR * 0.04, maxR * 0.15);
      const r      = maxR + size * 0.3; // slightly proud of OD
      return { key: i, localY, r, tR: Math.max(size * 0.40, 0.003) };
    })
    .filter(Boolean) as { key: number; localY: number; r: number; tR: number }[],
  [features, maxR, halfL]);
  return <>
    {bands.map(({ key, localY, r, tR }) => (
      <mesh key={key} material={matChamfer} position={[0, localY, 0]} rotation={[Math.PI/2, 0, 0]}>
        <torusGeometry args={[r, tR, 8, SEGS, phiLen]} />
      </mesh>
    ))}
  </>;
}

// ─── Cross-hole indicators ─────────────────────────────────────────────────
// Renders small ring indicators on OD surface for drilled cross holes and orifice ports.
// Accepts:
//   type='hole'       — new LLM schema, size_in = hole diameter
//   type='counterbore'— old LLM schema orifice fallback (has z_pos + id_diameter for small holes)
//   any type with spec_text containing "NX" pattern (e.g. "12X R.06 X .09") — inferred holes
function CrossHoles({ features, maxR, halfL, phiLen }: {
  features: Feature[]; maxR: number; halfL: number; phiLen: number;
}) {
  const NX_RE = /(\d+)\s*[Xx]/;  // matches "12X", "4x", "2 X", etc.

  const holes = useMemo(() => {
    const results: { key: string; localY: number; hR: number }[] = [];

    features.forEach((f, i) => {
      const isExplicitHole = f.type === 'hole';
      const isCounterboreOrifice = f.type === 'counterbore' && f.z_pos != null;

      // Detect "NX" multiplier pattern in spec_text on non-hole types (fillet, chamfer, etc.)
      // This catches callouts like "12X R.06 X .09" which are really orifice holes with fillets
      const specCount = (() => {
        if (isExplicitHole || isCounterboreOrifice) return null; // already handled
        const m = NX_RE.exec(f.spec_text ?? '');
        return m ? parseInt(m[1], 10) : null;
      })();
      const isInferredHole = specCount != null && specCount > 1;

      if (!isExplicitHole && !isCounterboreOrifice && !isInferredHole) return;

      const zRaw = f.z_pos != null ? f.z_pos
        : (f.z_start != null && f.z_end != null) ? (f.z_start + f.z_end) / 2
        : null;
      // For NX-inferred holes without a z_pos, place them in the middle third of the part
      const z = zRaw != null ? zRaw : halfL; // halfL = centre of part corridor
      const localY = z - halfL;

      // Size: explicit size_in (hole type) → id_diameter (counterbore) → spec_text "R.XX" → fallback
      let diam = f.size_in ?? f.id_diameter ?? f.od_diameter ?? null;
      if (diam == null && isInferredHole) {
        // Try to extract R value from spec_text → treat as hole mouth diameter
        const rMatch = /[Rr]\.?(\d+(?:\.\d+)?)/i.exec(f.spec_text ?? '');
        diam = rMatch ? parseFloat(rMatch[1]) * 2 : maxR * 0.12;
      }
      if (diam == null) diam = maxR * 0.12;

      const hR = Math.min(Math.max(diam * 0.5, maxR * 0.04), maxR * 0.30);

      if (isInferredHole) {
        // Spread NX holes evenly along centre third of part body
        const lo = halfL * 0.2;
        const hi = halfL * 1.8;
        const step = specCount! > 1 ? (hi - lo) / (specCount! - 1) : 0;
        for (let n = 0; n < specCount!; n++) {
          const ly = (lo + n * step) - halfL;
          results.push({ key: `${i}-nx${n}`, localY: ly, hR });
        }
      } else {
        results.push({ key: `${i}`, localY, hR });
      }
    });

    return results;
  }, [features, maxR, halfL]);

  return <>
    {holes.map(({ key, localY, hR }) => (
      <mesh key={key} material={matHole} position={[0, localY, 0]} rotation={[Math.PI/2, 0, 0]}>
        <torusGeometry args={[maxR + hR * 0.6, Math.max(hR * 0.60, maxR * 0.025), 10, SEGS, phiLen]} />
      </mesh>
    ))}
  </>;
}

// ─── Fillet feature rings ─────────────────────────────────────────────────────
// Thin blue rings mark concave fillet transitions.
function FilletBands({ features, maxR, halfL, phiLen }: {
  features: Feature[]; maxR: number; halfL: number; phiLen: number;
}) {
  const bands = useMemo(() => features
    .filter(f => f.type === 'fillet' && f.face !== 'id')
    .map((f, i) => {
      const z = f.z_pos != null ? f.z_pos
        : f.z_start != null ? f.z_start
        : f.z_end   != null ? f.z_end : null;
      if (z == null) return null;
      const localY = z - halfL;
      const tR = Math.max((f.size_in ?? maxR * 0.02) * 0.5, 0.003);
      return { key: i, localY, tR };
    })
    .filter(Boolean) as { key: number; localY: number; tR: number }[],
  [features, maxR, halfL]);
  return <>
    {bands.map(({ key, localY, tR }) => (
      <mesh key={key} material={matFillet} position={[0, localY, 0]} rotation={[Math.PI/2, 0, 0]}>
        <torusGeometry args={[maxR + tR * 0.4, Math.max(tR, maxR * 0.018), 8, SEGS, phiLen]} />
      </mesh>
    ))}
  </>;
}

// suppress unused-variable warning for matMetalDark (used in future step rendering)
void matMetalDark;

// ─── Hover Highlight ─────────────────────────────────────────────────────────
// Pulsing emissive overlay rendered inside Canvas when user hovers a HUD row.
function HoverHighlight({ dim, halfL, maxR, finishR, boreR, phiLen }: {
  dim: string | null; halfL: number; maxR: number;
  finishR: number; boreR: number; phiLen: number;
}) {
  const matsRef = useRef<THREE.MeshStandardMaterial[]>([]);
  useFrame(({ clock }) => {
    const t = Math.sin(clock.getElapsedTime() * 4) * 0.5 + 0.5;
    matsRef.current.forEach(m => { if (m) m.emissiveIntensity = 0.4 + t * 1.4; });
  });
  // Reset collector on every render so stale refs don't accumulate
  matsRef.current = [];
  const reg = (m: THREE.MeshStandardMaterial | null) => { if (m) matsRef.current.push(m); };

  if (!dim) return null;
  const GOLD = '#FFD700';
  const CYAN = '#00DFFF';
  const col  = dim === 'BORE ID' ? CYAN : GOLD;

  if (dim === 'LENGTH') {
    const rr = maxR * 1.09;
    const tr = Math.max(maxR * 0.055, 0.012);
    return (
      <group>
        <mesh position={[0, -halfL, 0]} rotation={[Math.PI / 2, 0, 0]}>
          <torusGeometry args={[rr, tr, 12, 64, phiLen]} />
          <meshStandardMaterial ref={reg} color={GOLD} emissive={GOLD}
            emissiveIntensity={0.8} transparent opacity={0.6} depthWrite={false} />
        </mesh>
        <mesh position={[0, halfL, 0]} rotation={[Math.PI / 2, 0, 0]}>
          <torusGeometry args={[rr, tr, 12, 64, phiLen]} />
          <meshStandardMaterial ref={reg} color={GOLD} emissive={GOLD}
            emissiveIntensity={0.8} transparent opacity={0.6} depthWrite={false} />
        </mesh>
      </group>
    );
  }
  if (dim === 'MAX OD') {
    return (
      <mesh>
        <cylinderGeometry args={[maxR * 1.013, maxR * 1.013, halfL * 2, 64, 1, true, 0, phiLen]} />
        <meshStandardMaterial ref={reg} color={col} emissive={col}
          emissiveIntensity={0.8} transparent opacity={0.25} depthWrite={false}
          side={THREE.DoubleSide} />
      </mesh>
    );
  }
  if (dim === 'FINISH OD') {
    return (
      <mesh>
        <cylinderGeometry args={[finishR * 1.013, finishR * 1.013, halfL * 2, 64, 1, true, 0, phiLen]} />
        <meshStandardMaterial ref={reg} color={col} emissive={col}
          emissiveIntensity={0.8} transparent opacity={0.25} depthWrite={false}
          side={THREE.DoubleSide} />
      </mesh>
    );
  }
  if (dim === 'BORE ID' && boreR > 0.002) {
    return (
      <mesh>
        <cylinderGeometry args={[boreR * 0.982, boreR * 0.982, halfL * 2, 64, 1, true, 0, phiLen]} />
        <meshStandardMaterial ref={reg} color={col} emissive={col}
          emissiveIntensity={0.8} transparent opacity={0.35} depthWrite={false}
          side={THREE.DoubleSide} />
      </mesh>
    );
  }
  return null;
}

// ─── Core TurnedPart ──────────────────────────────────────────────────────────
function TurnedPart({ finishR, maxR, boreR, halfL, viewMode, features }: {
  finishR: number; maxR: number; boreR: number; halfL: number;
  viewMode: ViewMode; features: Feature[];
}) {
  const isStep   = maxR - finishR > 0.005;
  const stepFrac = isStep ? 0.38 : 0;
  const section  = viewMode === 'section';
  const xray     = viewMode === 'xray';
  const full     = viewMode === 'full';
  const phiLen   = section ? Math.PI : Math.PI * 2;
  const hasBore  = boreR > 0.002;
  const partLen  = halfL * 2;

  const shellGeo = useMemo(
    () => buildShellGeo(finishR, maxR, boreR, halfL, isStep, stepFrac, phiLen),
    [finishR, maxR, boreR, halfL, isStep, stepFrac, phiLen],
  );
  const capGeo = useMemo(() => {
    const inner = hasBore ? boreR : 0.001;
    const g = new THREE.RingGeometry(inner, maxR, 96, 1, 0, phiLen);
    g.computeVertexNormals(); return g;
  }, [maxR, boreR, hasBore, phiLen]);
  const boreEdgeGeo = useMemo(() => {
    if (!hasBore) return null;
    return new THREE.TorusGeometry(boreR, Math.min(boreR * 0.06, 0.012), 12, 96, phiLen);
  }, [boreR, hasBore, phiLen]);
  const sectionCapGeo = useMemo(() => {
    if (!section) return null;
    const inner = hasBore ? boreR : 0.001;
    const g = new THREE.RingGeometry(inner, maxR, 96, 1, 0, Math.PI * 2);
    g.computeVertexNormals(); return g;
  }, [section, maxR, boreR, hasBore]);

  // Thread zone — only render when LLM explicitly detected thread features.
  // The old length-ratio heuristic (partLen > maxR*2.5) was removed because it
  // incorrectly wrapped every elongated part in full-body threads.
  const threadPitch = Math.max(maxR * 0.12, 0.04);
  const threadFeats = useMemo(
    () => features.filter(f => f.type === 'thread'),
    [features],
  );
  const showThread = !xray && threadFeats.length > 0;

  const axisExt = partLen * 0.18;
  const axisPts: [number,number,number][] = [[0,-halfL-axisExt,0],[0,halfL+axisExt,0]];

  return (
    <group rotation={[0, 0, Math.PI / 2]}>
      {/* Outer shell with chamfered ends */}
      <mesh geometry={shellGeo} material={xray ? matXray : matMetal} castShadow receiveShadow />

      {/* Bore interior */}
      {hasBore && (
        <mesh material={matBore}>
          <cylinderGeometry args={[boreR*.995, boreR*.995, partLen*1.001, SEGS, 1, true, 0, phiLen]} />
        </mesh>
      )}

      {/* End faces — near face omitted in 'full' mode to expose bore opening */}
      {!full && (
        <mesh geometry={capGeo} material={matFace} position={[0,-halfL,0]} rotation={[Math.PI/2,0,0]} />
      )}
      <mesh geometry={capGeo} material={matFace} position={[0,halfL,0]} rotation={[-Math.PI/2,0,0]} />

      {/* Bore entrance accent tori */}
      {hasBore && boreEdgeGeo && <>
        <mesh geometry={boreEdgeGeo} material={matBoreEdge} position={[0,-halfL,0]} rotation={[Math.PI/2,0,0]} />
        <mesh geometry={boreEdgeGeo} material={matBoreEdge} position={[0, halfL,0]} rotation={[Math.PI/2,0,0]} />
      </>}

      {/* Section cut flat ring */}
      {section && sectionCapGeo && (
        <mesh geometry={sectionCapGeo} material={matCut} />
      )}

      {/* Thread helix on OD — driven by LLM thread features */}
      {showThread && threadFeats.map((tf, ti) => {
        const zS = tf.z_start != null ? tf.z_start - halfL : -halfL * 0.55;
        const zE = tf.z_end   != null ? tf.z_end   - halfL :  halfL * 0.55;
        return <ThreadHelix key={ti} r={maxR} zStart={zS} zEnd={zE} pitch={threadPitch} phiLen={phiLen} />;
      })}

      {/* Groove rings from features[] */}
      <GrooveRings features={features} maxR={maxR} halfL={halfL} phiLen={phiLen} />

      {/* Chamfer edge highlights from LLM features */}
      <ChamferBands features={features} maxR={maxR} halfL={halfL} phiLen={phiLen} />

      {/* Fillet transition rings from LLM features */}
      <FilletBands features={features} maxR={maxR} halfL={halfL} phiLen={phiLen} />

      {/* Cross-hole / drilled hole indicators */}
      <CrossHoles features={features} maxR={maxR} halfL={halfL} phiLen={phiLen} />

      {/* Centre-line axis dash */}
      <Line points={axisPts} color={AXIS_COLOR} lineWidth={0.8}
        dashed dashSize={partLen*.04} gapSize={partLen*.025} />
    </group>
  );
}

// ─── Dimension overlays — WORLD SPACE ────────────────────────────────────────
// Rendered OUTSIDE the rotated <group> so coords map directly to world space.
//
// World-space layout after group rotation [0,0,π/2]  Rz(90°):
//   world_x = -local_y   →  bore axis runs along WORLD X  (−halfL … +halfL)
//   world_y = +local_x   →  radial up/down is WORLD Y      (−maxR … +maxR)
//   world_z =  local_z   →  depth unchanged
//
// OD line  — vertical (world Y) at a point partway along the bore (world X)
// ID line  — vertical (world Y), shorter, at the opposite bore side
// Length   — horizontal (world X) above the part (world Y = yOff)
function DimOverlays({
  halfL, maxR, boreR, showDims,
}: { halfL: number; maxR: number; boreR: number; showDims: boolean }) {
  if (!showDims) return null;
  const hasBore = boreR > 0.002;
  const tick    = maxR * 0.10;
  const yOff    = maxR * 1.75;         // length line Y offset above part
  const xOD     =  halfL * 0.40;       // OD line position along world X (right side)
  const xID     = -halfL * 0.40;       // ID line position along world X (left side)
  return (
    <>
      {/* ── OD line: vertical across OD at xOD ── */}
      <Line points={[[xOD, -maxR * 1.18, 0],[xOD, maxR * 1.18, 0]]} color={DIM_GOLD} lineWidth={1.2} />
      <Line points={[[xOD - tick, -maxR, 0],[xOD + tick, -maxR, 0]]} color={DIM_GOLD} lineWidth={1.6} />
      <Line points={[[xOD - tick,  maxR, 0],[xOD + tick,  maxR, 0]]} color={DIM_GOLD} lineWidth={1.6} />

      {/* ── ID line: vertical across bore at xID ── */}
      {hasBore && (
        <>
          <Line points={[[xID, -boreR * 1.28, 0],[xID, boreR * 1.28, 0]]} color={DIM_CYAN} lineWidth={1.2} />
          <Line points={[[xID - tick * 0.7, -boreR, 0],[xID + tick * 0.7, -boreR, 0]]} color={DIM_CYAN} lineWidth={1.6} />
          <Line points={[[xID - tick * 0.7,  boreR, 0],[xID + tick * 0.7,  boreR, 0]]} color={DIM_CYAN} lineWidth={1.6} />
        </>
      )}

      {/* ── Length line: horizontal along bore above part ── */}
      <Line points={[[-halfL, yOff, 0],[halfL, yOff, 0]]} color={DIM_GOLD} lineWidth={1.2} />
      <Line points={[[-halfL, yOff - tick, 0],[-halfL, yOff + tick, 0]]} color={DIM_GOLD} lineWidth={1.6} />
      <Line points={[[ halfL, yOff - tick, 0],[ halfL, yOff + tick, 0]]} color={DIM_GOLD} lineWidth={1.6} />
      {/* leader lines from part surface to dim bar */}
      <Line points={[[-halfL, maxR, 0],[-halfL, yOff, 0]]} color={DIM_GOLD} lineWidth={0.5}
        dashed dashSize={maxR * 0.09} gapSize={maxR * 0.06} />
      <Line points={[[ halfL, maxR, 0],[ halfL, yOff, 0]]} color={DIM_GOLD} lineWidth={0.5}
        dashed dashSize={maxR * 0.09} gapSize={maxR * 0.06} />
    </>
  );
}

// ─── Studio Lighting ──────────────────────────────────────────────────────────
function Lights({ size, boreR, halfL }: { size: number; boreR: number; halfL: number }) {
  const g = size;
  return (
    <>
      {/* Sky/ground hemisphere — warm workshop ceiling, cool concrete floor */}
      <hemisphereLight args={['#C8D8E8', '#1A2830', 0.55]} />
      {/* Key: upper front-right — drives specular streak along OD curve */}
      <directionalLight position={[g*1.4, g*3.2, g*2.8]} intensity={3.2} castShadow
        shadow-mapSize-width={2048} shadow-mapSize-height={2048} shadow-bias={-0.0004}
        shadow-camera-near={g*.1} shadow-camera-far={g*20}
        shadow-camera-left={-g*3} shadow-camera-right={g*3}
        shadow-camera-top={g*3} shadow-camera-bottom={-g*3} />
      {/* Rim: top-left, OD silhouette edge */}
      <directionalLight position={[-g*3, g*2.5, -g*1.5]} intensity={1.2} color="#A8C8E8" />
      {/* Cool fill: softens underside shadows */}
      <directionalLight position={[-g*1.0, -g*1.5, g*2]} intensity={0.55} color="#5588AA" />
      {/* Back separator */}
      <directionalLight position={[g*.5, -g*.5, -g*3]} intensity={0.45} color="#8899AA" />
      {/* Bore interior illuminator */}
      {boreR > 0.002 && (
        <pointLight position={[halfL*.5, 0, 0]} intensity={1.2}
          distance={halfL*4} color="#3399CC" decay={1.5} />
      )}
    </>
  );
}

// ─── Camera driver ───────────────────────────────────────────────────────────
// Drives the camera via useFrame at priority 1, which fires AFTER OrbitControls
// (priority 0). This means our lerp always overrides OC's spherical update,
// guaranteeing the camera reaches the target regardless of OC damping state.
function CameraDriver({ pos, version }: { pos: [number,number,number]; version: number }) {
  const { camera, controls } = useThree();
  const targetRef  = useRef<[number,number,number]>(pos);
  const movingRef  = useRef(true);   // start moving immediately on mount
  const frameCount = useRef(0);

  // When version changes (view-mode switch or reset) → arm a new move
  useEffect(() => {
    targetRef.current = pos;
    movingRef.current = true;
    frameCount.current = 0;
  // dep on version ensures this fires each time the caller bumps the counter
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [version]);

  // priority 1 → runs after OrbitControls (priority 0) every frame
  useFrame(() => {
    if (!movingRef.current) return;
    const [tx, ty, tz] = targetRef.current;
    const p = camera.position;
    const LERP = 0.13;
    p.x += (tx - p.x) * LERP;
    p.y += (ty - p.y) * LERP;
    p.z += (tz - p.z) * LERP;
    camera.lookAt(0, 0, 0);
    frameCount.current += 1;
    const arrived =
      Math.abs(tx - p.x) < 0.0001 &&
      Math.abs(ty - p.y) < 0.0001 &&
      Math.abs(tz - p.z) < 0.0001;
    if (arrived || frameCount.current > 90) {
      p.set(tx, ty, tz);
      camera.lookAt(0, 0, 0);
      movingRef.current = false;
      // Sync OrbitControls' internal spherical so it doesn't fight us on the
      // first user drag after arrival.
      if (controls) {
        const ctrl = controls as any;
        ctrl.target.set(0, 0, 0);
        const dam = ctrl.enableDamping;
        ctrl.enableDamping = false;
        ctrl.update();
        ctrl.enableDamping = dam;
      }
    }
  }, 1);

  return null;
}

// ─── HUD panel ────────────────────────────────────────────────────────────────
function HUD({ finishOd, maxOd, boreId, lengthIn, features, onHover }: {
  finishOd: number; maxOd: number; boreId: number; lengthIn: number;
  features: Feature[]; onHover: (dim: string | null) => void;
}) {
  const [hoveredRow, setHoveredRow] = useState<string | null>(null);
  const enter = (label: string) => { setHoveredRow(label); onHover(label); };
  const leave = ()              => { setHoveredRow(null);  onHover(null);  };

  const isStep = maxOd - finishOd > 0.005;
  const fIn = (v: number) => `${v.toFixed(4)}"`;
  const fMm = (v: number) => `${(v * 25.4).toFixed(2)} mm`;
  const rows = [
    { label: 'LENGTH',    v: lengthIn, mm: fMm(lengthIn), color: DIM_GOLD },
    { label: 'MAX OD',    v: maxOd,    mm: fMm(maxOd),    color: DIM_GOLD },
    ...(isStep ? [{ label: 'FINISH OD', v: finishOd, mm: fMm(finishOd), color: DIM_GOLD }] : []),
    ...(boreId > 0.01 ? [{ label: 'BORE ID', v: boreId, mm: fMm(boreId), color: DIM_CYAN }] : []),
  ];
  // Count features by type for badge; also detect NX-inferred holes
  const NX_RE_HUD = /(\d+)\s*[Xx]/;
  const featCounts: Record<string, number> = {};
  let inferredHoleCount = 0;
  features.forEach(f => {
    featCounts[f.type] = (featCounts[f.type] ?? 0) + 1;
    if (f.type !== 'hole' && f.type !== 'counterbore') {
      const m = NX_RE_HUD.exec(f.spec_text ?? '');
      if (m && parseInt(m[1], 10) > 1) inferredHoleCount += parseInt(m[1], 10);
    }
  });
  if (inferredHoleCount > 0) featCounts['hole (inferred)'] = inferredHoleCount;
  const featLines = Object.entries(featCounts).map(([t, n]) => `${n}× ${t}`);
  return (
    <div style={{
      position: 'absolute', top: 8, right: 8, zIndex: 10,
      background: 'rgba(4,8,14,0.90)', border: '1px solid rgba(255,215,0,0.22)',
      borderRadius: 6, padding: '9px 13px', backdropFilter: 'blur(14px)',
      fontFamily: '"Courier New", monospace',
      display: 'flex', flexDirection: 'column', gap: 6,
      minWidth: 175, pointerEvents: 'auto',
    }}>
      <div style={{ fontSize: '7px', color: '#3A5566', letterSpacing: '0.16em',
        borderBottom: '1px solid #1a2a3a', paddingBottom: 5 }}>✦ DIMENSIONS</div>
      {rows.map(r => {
        const active = hoveredRow === r.label;
        return (
          <div key={r.label}
            onMouseEnter={() => enter(r.label)}
            onMouseLeave={leave}
            style={{
              display: 'flex', alignItems: 'baseline', gap: 6,
              borderRadius: 4, padding: '3px 5px', margin: '0 -5px',
              cursor: 'crosshair',
              background: active ? 'rgba(255,215,0,0.10)' : 'transparent',
              boxShadow: active ? 'inset 0 0 0 1px rgba(255,215,0,0.30)' : 'none',
              transition: 'background 0.12s, box-shadow 0.12s',
            }}>
            <span style={{
              fontSize: '7px', letterSpacing: '0.07em', width: 62, flexShrink: 0,
              color: active ? '#FFD700' : '#3A5A70',
              transition: 'color 0.12s',
            }}>{r.label}</span>
            <span style={{ fontSize: '12px', fontWeight: 700, color: r.color, flexGrow: 1 }}>{fIn(r.v)}</span>
            <span style={{ fontSize: '8px', color: active ? '#6A9AB0' : '#2A4455', transition: 'color 0.12s' }}>{r.mm}</span>
            {active && <span style={{ fontSize: '8px', color: '#FFD700', marginLeft: 2 }}>◀</span>}
          </div>
        );
      })}
      {featLines.length > 0 && (
        <>
          <div style={{ fontSize: '7px', color: '#3A5566', letterSpacing: '0.16em',
            borderTop: '1px solid #1a2a3a', paddingTop: 5, marginTop: 2 }}>✦ FEATURES</div>
          {featLines.map(fl => (
            <div key={fl} style={{ fontSize: '9px', color: '#7AB0C8', letterSpacing: '0.04em' }}>{fl}</div>
          ))}
        </>
      )}
    </div>
  );
}

//  Toolbar button 
function Btn({ label, active, onClick, title }: { label: string; active?: boolean; onClick: () => void; title?: string }) {
  return (
    <button onClick={onClick} title={title} style={{
      fontSize: '11px', fontWeight: active ? 700 : 500,
      padding: '4px 11px', borderRadius: 4, lineHeight: 1.3,
      border: `1px solid ${active ? '#FFD700' : '#2E4860'}`,
      background: active ? 'rgba(255,215,0,0.18)' : 'rgba(18,26,38,0.92)',
      color: active ? '#FFD700' : '#A0C4DC',
      cursor: 'pointer',
      userSelect: 'none', transition: 'color 0.1s, background 0.1s, border-color 0.1s',
      whiteSpace: 'nowrap',
    }}>{label}</button>
  );
}

// ─── LatheViewer (main export) ────────────────────────────────────────────────
export default function LatheViewer({
  segments     = [],
  jobId:       _jobId,
  boreDiameter,
  features     = [],
  finishOd,
  maxOd,
  lengthIn,
}: {
  segments?:     Segment[];
  jobId?:        string;
  boreDiameter?: number;
  features?:     unknown[];
  finishOd?:     number;
  maxOd?:        number;
  lengthIn?:     number;
}) {
  const typedFeatures = features as Feature[];
  // ── Resolve dimensions ──────────────────────────────────────────────────────
  const cleanSegs = useMemo(() => segments.filter(s => s.od_diameter >= SEG_MIN_OD), [segments]);
  const segMaxOd = cleanSegs.length > 0 ? Math.max(...cleanSegs.map(s => s.od_diameter)) : 0;
  const segMinOd = cleanSegs.length > 0 ? Math.min(...cleanSegs.map(s => s.od_diameter)) : 0;
  const segLen   = cleanSegs.length > 0
    ? Math.max(...cleanSegs.map(s => s.z_end)) - Math.min(...cleanSegs.map(s => s.z_start)) : 0;

  const resolvedMaxOd    = (maxOd        && maxOd        > SEG_MIN_OD) ? maxOd        : (segMaxOd > 0 ? segMaxOd : 1.0);
  const resolvedFinishOd = (finishOd     && finishOd     > SEG_MIN_OD) ? finishOd     : (segMinOd > SEG_MIN_OD ? segMinOd : resolvedMaxOd);
  const resolvedBoreId   = (boreDiameter && boreDiameter > 0.01)       ? boreDiameter : 0;
  const resolvedLen      = (lengthIn     && lengthIn     > 0.001)      ? lengthIn     : (segLen > 0 ? segLen : 1.0);

  const finishR  = Math.max(resolvedFinishOd / 2, 0.002);
  const maxR     = Math.max(resolvedMaxOd    / 2, finishR);
  const boreR    = resolvedBoreId > 0.01 ? resolvedBoreId / 2 : 0;
  const halfL    = Math.max(resolvedLen / 2, 0.01);
  const safeMaxR  = maxR;
  const safeBoreR = boreR < maxR * 0.98 ? boreR : maxR * 0.40;
  const hasData   = resolvedMaxOd > 0.001 && resolvedLen > 0.001;

  // ── Camera positions ────────────────────────────────────────────────────────
  const { camFullPos, camSectionPos, camOdPos, camIdPos, camXrayPos, size } = useMemo(() => {
    const dimExtY = maxR * 1.90;
    const sceneR  = Math.sqrt(halfL*halfL + dimExtY*dimExtY + maxR*maxR);
    const camDist = (sceneR / Math.tan(18 * Math.PI / 180)) * 1.12;
    const norm = (dx: number, dy: number, dz: number): [number,number,number] => {
      const m = Math.sqrt(dx*dx + dy*dy + dz*dz);
      return [dx/m*camDist, dy/m*camDist, dz/m*camDist];
    };
    return {
      camFullPos:    norm(-0.50, 0.42, 0.75),
      camSectionPos: norm(-0.12, 0.68, 0.72),
      camOdPos:      norm(-0.08, 0.26, 0.96),
      camIdPos:      norm(-1.00, 0.02, 0.02),
      camXrayPos:    norm(-0.18, 0.50, 0.85),
      size:          Math.max(halfL * 2, maxR * 2),
    };
  }, [halfL, maxR]);

  const [viewMode, setViewMode] = useState<ViewMode>('full');
  const [showDims, setShowDims] = useState(true);
  const [camVer,   setCamVer]   = useState(1);
  const [hoveredDim, setHoveredDim] = useState<string | null>(null);
  // phiLen mirrors TurnedPart's section logic so HoverHighlight matches the visible shell
  const phiLen = viewMode === 'section' ? Math.PI : Math.PI * 2;

  const activePos: [number,number,number] =
    viewMode === 'section' ? camSectionPos :
    viewMode === 'od'      ? camOdPos      :
    viewMode === 'id'      ? camIdPos      :
    viewMode === 'xray'    ? camXrayPos    :
    camFullPos;

  // Bump camVer whenever the target position changes (mode switch or part dims change)
  const prevPosKey = useRef('');
  const posKey = `${viewMode}|${halfL.toFixed(4)}|${maxR.toFixed(4)}`;
  if (posKey !== prevPosKey.current) { prevPosKey.current = posKey; }
  useEffect(() => { setCamVer(v => v + 1); }, [posKey]);

  // Clicking the active button or the reset icon also re-arms the animation
  const handleModeClick = useCallback((mode: ViewMode) => {
    setViewMode(mode);
    setCamVer(v => v + 1);
  }, []);
  const handleReset = useCallback(() => setCamVer(v => v + 1), []);
  const floorY = -(safeMaxR * 1.30);

  return (
    <div style={{
      width: '100%', flex: '1 1 0', minHeight: 0,
      background: BG, borderRadius: '8px',
      display: 'flex', flexDirection: 'column',
    }}>
      {hasData ? (
        <>
          {/* Canvas area grows to fill available height; overflow:hidden contains the WebGL canvas */}
          <div style={{ position: 'relative', flex: '1 1 0', minHeight: 0, overflow: 'hidden', borderRadius: '8px 8px 0 0' }}>
          <Canvas
            gl={{
              antialias: true,
              toneMapping: THREE.ACESFilmicToneMapping,
              toneMappingExposure: 1.15,
              outputColorSpace: THREE.SRGBColorSpace,
            }}
            frameloop="always"
            shadows
            resize={{ scroll: false, debounce: { scroll: 50, resize: 0 } }}
            style={{ position: 'absolute', top: 0, left: 0, width: '100%', height: '100%' }}
            camera={{ position: activePos, fov: 34, near: size * 0.002, far: size * 150 }}
          >
            <color attach="background" args={[BG]} />
            <fog attach="fog" args={[BG, size * 18, size * 80]} />

            <CameraDriver pos={activePos} version={camVer} />
            <Lights size={size} boreR={safeBoreR} halfL={halfL} />

            {/* ── Part geometry ── */}
            <TurnedPart
              finishR={finishR} maxR={safeMaxR} boreR={safeBoreR}
              halfL={halfL} viewMode={viewMode} features={typedFeatures}
            />

            {/* ── Dimension overlays ── */}
            <DimOverlays halfL={halfL} maxR={safeMaxR} boreR={safeBoreR} showDims={showDims} />

            {/* ── HUD hover highlight overlay ── */}
            <HoverHighlight dim={hoveredDim} halfL={halfL} maxR={safeMaxR}
              finishR={finishR} boreR={safeBoreR} phiLen={phiLen} />

            {/* ── Manufacturing grid floor ── */}
            <group position={[0, floorY, 0]}>
              <Grid
                args={[size * 8, size * 8]}
                cellSize={size * 0.25} cellThickness={0.4} cellColor="#1A2A38"
                sectionSize={size * 1.0} sectionThickness={0.8} sectionColor="#1E3448"
                fadeDistance={size * 12} fadeStrength={2.5} infiniteGrid
              />
            </group>
            <mesh rotation={[-Math.PI/2, 0, 0]} position={[0, floorY, 0]} receiveShadow>
              <planeGeometry args={[size * 10, size * 10]} />
              <shadowMaterial opacity={0.28} />
            </mesh>

            <OrbitControls makeDefault enablePan enableDamping dampingFactor={0.06}
              minDistance={size * 0.05} maxDistance={size * 12} />
            <GizmoHelper alignment="bottom-left" margin={[72, 72]}>
              <GizmoViewcube />
            </GizmoHelper>
          </Canvas>

          <HUD finishOd={resolvedFinishOd} maxOd={resolvedMaxOd}
               boreId={resolvedBoreId} lengthIn={resolvedLen} features={typedFeatures}
               onHover={setHoveredDim} />
          </div>{/* end canvas-area */}

          {/* ── Toolbar — normal flow below canvas, always visible ── */}
          <div style={{
            display: 'flex', alignItems: 'center', gap: 5, flexWrap: 'wrap',
            padding: '6px 10px', flexShrink: 0,
            background: 'rgba(8,12,18,0.97)',
            borderTop: '1px solid #1a2a3a',
          }}>
            <Btn label="Full"    active={viewMode==='full'}    onClick={()=>handleModeClick('full')}    title="OD exterior + open bore face" />
            <Btn label="Section" active={viewMode==='section'} onClick={()=>handleModeClick('section')} title="Half-section: bore interior" />
            <Btn label="OD"      active={viewMode==='od'}      onClick={()=>handleModeClick('od')}      title="Side profile" />
            <Btn label="ID"      active={viewMode==='id'}      onClick={()=>handleModeClick('id')}      title="End-on bore" />
            <Btn label="X-Ray"   active={viewMode==='xray'}    onClick={()=>handleModeClick('xray')}   title="Transparent shell" />
            <div style={{ width:1, height:16, background:'#1e3048', alignSelf:'center', margin:'0 3px' }} />
            <Btn label="Dims" active={showDims} onClick={()=>setShowDims(d=>!d)} title="Toggle dim overlays" />
            <Btn label="⟳"   onClick={handleReset} title="Reset camera" />
          </div>
        </>
      ) : (
        <div style={{ color:'#1F3045', fontSize:13, display:'flex', alignItems:'center', justifyContent:'center', height:'100%' }}>
          Run Auto-Detect to generate 3D preview
        </div>
      )}
    </div>
  );
}
