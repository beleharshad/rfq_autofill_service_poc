export type Vec3 = [number, number, number];
export type BaseRecipe = { kind: 'cylinder'; diameter: number; length: number }
  | { kind: 'box'; width: number; height: number; length: number }
  | { kind: 'revolve'; profile: [number, number][] }
  | { kind: 'step'; document_id: string; body_index: number };
type Positioned = { feature_id: string; origin: Vec3; axis?: Vec3 };
export type FeatureRecipe = Positioned & (
  { kind: 'hole'; diameter: number; depth: number; termination: 'through' | 'blind' }
  | { kind: 'pocket'; width: number; height: number; depth: number }
  | { kind: 'cone_cut'; entry_diameter: number; end_diameter: number; depth: number }
  | { kind: 'thread'; side: 'internal' | 'external'; major_diameter: number; minor_diameter: number;
    pitch: number; length: number; groove_width: number; handedness: 'right' | 'left'; callout: string });
export interface Recipe {
  part_number: string; revision: string; governing_document_id: string;
  evidence: { document_id: string; page: number | null; locator: string }[];
  base: BaseRecipe; features: FeatureRecipe[]; review_note: string;
  completeness: 'complete' | 'partial'; unresolved: string[]; material?: string | null;
}
export interface AcceptedPart {
  part_key: string; version: number; registry_version: number; spec_id: string; payload: Recipe; stale?: boolean;
}
export interface GeometryBuild {
  build_id: string; spec_id: string; status: 'queued' | 'running' | 'complete' | 'failed' | 'cancelled';
  stale: boolean; error: string | null;
  manifest: null | { status: string; completeness: string; warnings: string[]; measurements: {
    envelope_width_mm: number; envelope_height_mm: number; axial_length_mm: number;
    maximum_outer_cylindrical_diameter_mm: number | null; volume_mm3: number; frame: string;
    minimum_coaxial_through_bore_mm: number | null;
    coaxial_bore_stations: {diameter_mm:number;z_start_mm:number;z_end_mm:number}[];
  } };
}

export interface SourceAnalysis {
  analysis_id: string; document_id: string; sha256: string; registry_version: number;
  candidates: {name: 'maximum_outer_diameter' | 'axial_length'; value_mm: number; acceptance: 'unverified'}[];
  limitations: string[];
}
