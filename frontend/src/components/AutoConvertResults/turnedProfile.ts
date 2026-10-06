import { Vector2 } from 'three';

export interface ProfileSegment { z_start: number; z_end: number; od_diameter: number; id_diameter: number }

export function validProfile(input: ProfileSegment[]): ProfileSegment[] {
  if (!Array.isArray(input) || !input.length) return [];
  if (input.some(s => !s || typeof s !== 'object')) return [];
  const sorted = [...input].sort((a, b) => a.z_start - b.z_start);
  if (sorted.some((s, i) => !s ||
    ![s.z_start, s.z_end, s.od_diameter, s.id_diameter].every(Number.isFinite) ||
    s.z_end <= s.z_start || s.od_diameter <= 0 || s.id_diameter < 0 || s.id_diameter >= s.od_diameter ||
    (i > 0 && Math.abs(s.z_start - sorted[i - 1].z_end) > 1e-6))) return [];
  return sorted;
}

// Preserve every shoulder and blind-bore floor. Overall edits scale the profile;
// they never replace it with a single cylinder or turn external steps into holes.
export function resizeProfile(input: ProfileSegment[], od?: number | null, length?: number | null, solid = false): ProfileSegment[] {
  const segs = validProfile(input);
  if (!segs.length) return [];
  const start = segs[0].z_start;
  const oldLength = segs[segs.length - 1].z_end - start;
  const oldOd = Math.max(...segs.map(s => s.od_diameter));
  const radial = Number.isFinite(od) && od! > 0.001 ? od! / oldOd : 1;
  const axial = Number.isFinite(length) && length! > 0.001 ? length! / oldLength : 1;
  return validProfile(segs.map(s => ({ ...s,
    z_start: (s.z_start - start) * axial, z_end: (s.z_end - start) * axial,
    od_diameter: s.od_diameter * radial,
    id_diameter: solid ? 0 : s.id_diameter * radial,
  })));
}

export function profilePoints(input: ProfileSegment[]): Vector2[] {
  const segs = validProfile(input);
  if (!segs.length) return [];
  const center = (segs[0].z_start + segs[segs.length - 1].z_end) / 2;
  const points: Vector2[] = [];
  for (const s of segs) points.push(new Vector2(s.od_diameter / 2, s.z_start - center), new Vector2(s.od_diameter / 2, s.z_end - center));
  for (const s of [...segs].reverse()) points.push(new Vector2(s.id_diameter / 2, s.z_end - center), new Vector2(s.id_diameter / 2, s.z_start - center));
  points.push(points[0].clone());
  return points;
}
