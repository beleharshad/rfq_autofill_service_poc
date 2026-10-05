import type { GeometryBuild } from './acceptedParts';
export interface AutomaticItem {
  document_id: string; name: string; status: string; issues: string[];
  part_number?: string; revision?: string; spec_id?: string; build_id?: string;
  measurements?: NonNullable<GeometryBuild['manifest']>['measurements']; warnings?: string[];
}
export interface AutomaticRun {
  run_id: string; status: string; registry_version: number; stale?: boolean; error?: string | null;
  result: { phase: string; items: AutomaticItem[]; validation?: string };
}
