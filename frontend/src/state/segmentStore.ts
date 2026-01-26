/**
 * Simple segment store for current job segments.
 * Used to share segment data between components (e.g., 3D viewer and table).
 */

export interface Segment {
  z_start: number;
  z_end: number;
  od_diameter: number;
  id_diameter: number;
  wall_thickness?: number;
  confidence?: number;
  flags?: string[];
  volume_in3?: number;
  od_area_in2?: number;
  id_area_in2?: number;
}

interface SegmentStore {
  [jobId: string]: Segment[];
}

let store: SegmentStore = {};

/**
 * Set segments for a job.
 */
export function setSegments(jobId: string, segments: Segment[]): void {
  store[jobId] = segments;
}

/**
 * Get segments for a job.
 */
export function getSegments(jobId: string): Segment[] | null {
  return store[jobId] || null;
}

/**
 * Clear segments for a job.
 */
export function clearSegments(jobId: string): void {
  delete store[jobId];
}

/**
 * Clear all segments.
 */
export function clearAllSegments(): void {
  store = {};
}








