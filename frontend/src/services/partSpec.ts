/** v0.1 legacy bridge: candidate values cannot authorize geometry or quoting. */
export interface PartSpec {
  schema_version: '0.1';
  adapter_version: 'legacy-summary-v1';
  job_id: string;
  part_id: string | null;
  revision: string | null;
  manufacturing_state: 'unresolved';
  selected_body_ids: string[];
  snapshot_id: string;
  sources: { document_id: string; path: string; sha256: string;
    role: 'unclassified' | 'legacy_summary'; revision: string | null }[];
  dimensions: { name: 'maximum_outer_diameter' | 'axial_length'; value: number;
    unit: 'mm'; source_value: number; source_unit: 'in' | 'mm';
    meaning: 'legacy_model_extent'; acceptance: 'unverified';
    evidence: { document_id: string; json_pointer: string; method: 'legacy_adapter' }[] }[];
  readiness: { facts: 'unresolved' | 'needs_review'; measurements: 'unresolved' | 'needs_review';
    geometry: 'not_validated'; quote: 'not_validated' };
  issues: { code: string; message: string }[];
}
