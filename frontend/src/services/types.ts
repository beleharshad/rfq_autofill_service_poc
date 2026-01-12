/**TypeScript types for API responses and requests.*/

export interface OutputFileInfo {
  exists: boolean;
  path: string;
  download_url: string;
  size?: number;
}

export interface OutputsInfo {
  part_summary_json?: OutputFileInfo;
  step_model?: OutputFileInfo;
  glb_model?: OutputFileInfo;
  scale_report?: OutputFileInfo;
  inferred_stack?: OutputFileInfo;
  turned_stack?: OutputFileInfo;
  run_report?: OutputFileInfo;
}

export interface JobResponse {
  job_id: string;
  name?: string;
  description?: string;
  status: string;
  mode?: string;
  input_files: string[];
  output_files: string[];
  created_at: string;
  updated_at: string;
  run_report?: RunReportSummary;
  outputs_info?: OutputsInfo;
}

export interface RunReportSummary {
  has_report: boolean;
  status?: string;
  started_at?: string;
  finished_at?: string;
  duration_ms?: number;
  stage_count: number;
  completed_stages: number;
  failed_stages: number;
  output_count: number;
  error_count: number;
}

export interface PipelineStage {
  name: string;
  status: string;
  started_at?: string;
  finished_at?: string;
  duration_ms?: number;
  error?: string;
  warning?: string;
}

export interface RunReport {
  job_id: string;
  started_at: string;
  finished_at?: string;
  duration_ms?: number;
  status: string;
  stages: PipelineStage[];
  outputs: string[];
  errors: string[];
  warnings: string[];
}

export interface FileInfo {
  path: string;
  name: string;
  size: number;
  url: string;
}

export interface JobFilesResponse {
  job_id: string;
  files: FileInfo[];
}

export interface SegmentInput {
  z_start: number;
  z_end: number;
  od_diameter: number;
  id_diameter: number;
}

export interface StackInputRequest {
  units: string;
  segments: SegmentInput[];
}

export interface StackInputResponse {
  job_id: string;
  units: string;
  segments: SegmentInput[];
  saved: boolean;
}

export interface PartSummary {
  schema_version: string;
  generated_at_utc: string;
  units: {
    length: string;
    area: string;
    volume: string;
  };
  z_range: [number, number];
  segments: Array<{
    z_start: number;
    z_end: number;
    od_diameter: number;
    id_diameter: number;
    wall_thickness: number;
    volume_in3: number;
    od_area_in2: number;
    id_area_in2: number;
    confidence?: number;
  }>;
  totals: {
    volume_in3: number;
    od_area_in2: number;
    id_area_in2: number;
    end_face_area_start_in2: number;
    end_face_area_end_in2: number;
    od_shoulder_area_in2: number;
    id_shoulder_area_in2: number;
    planar_ring_area_in2: number;
    total_surface_area_in2: number;
  };
  feature_counts: {
    external_cylinders: number;
    internal_bores: number;
    planar_faces: number;
    total_faces: number;
  };
  inference_metadata?: {
    mode?: 'reference_only' | 'auto_detect';
    overall_confidence?: number;
    source?: string;
  };
}

export interface RFQFieldValue {
  value: number | null;
  confidence: number;
  source: string;
}

export interface RFQAutofillRequest {
  rfq_id: string;
  part_no: string;
  source: {
    part_summary: any | null;
    job_id?: string | null;
    step_metrics: Record<string, any> | null;
  };
  tolerances: {
    rm_od_allowance_in: number;
    rm_len_allowance_in: number;
  };
}

export interface RFQAutofillResponse {
  part_no: string;
  fields: {
    finish_od_in: RFQFieldValue;
    finish_len_in: RFQFieldValue;
    finish_id_in: RFQFieldValue;
    rm_od_in: RFQFieldValue;
    rm_len_in: RFQFieldValue;
  };
  status: 'AUTO_FILLED' | 'NEEDS_REVIEW' | 'REJECTED';
  reasons: string[];
  debug: {
    max_od_in: number;
    overall_len_in: number;
    scale_method: string;
    overall_confidence: number;
    min_len_gate_in: number;
  };
}
