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
    total_length_in?: number;
    max_od_in?: number;
    max_id_in?: number;
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
    selected_body_index?: number;
    body_count?: number;
  };
  selected_body?: {
    body_index: number;
    score: number;
    extraction_method: string;
    segment_count: number;
    selection_reasons: string[];
    dimensions: {
      od_in: number | null;
      id_in: number | null;
      max_id_in: number | null;
      length_in: number | null;
    };
    z_range?: [number, number];
    totals?: {
      volume_in3?: number;
      max_od_in?: number;
      max_id_in?: number;
      total_length_in?: number;
    };
    feature_counts?: {
      external_cylinders?: number;
      internal_bores?: number;
      planar_faces?: number;
      total_faces?: number;
    };
  };
  body_candidates?: Array<{
    body_index: number;
    score: number;
    extraction_method: string;
    segment_count: number;
    selection_reasons: string[];
    dimensions: {
      od_in: number | null;
      id_in: number | null;
      max_id_in: number | null;
      length_in: number | null;
    };
    z_range?: [number, number];
    totals?: {
      volume_in3?: number;
      max_od_in?: number;
      max_id_in?: number;
      total_length_in?: number;
    };
    feature_counts?: {
      external_cylinders?: number;
      internal_bores?: number;
      planar_faces?: number;
      total_faces?: number;
    };
    inference_metadata?: {
      source?: string;
      overall_confidence?: number;
    };
  }>;
  warnings?: string[];
  step_metadata?: {
    file_name?: string;
    representation_context?: string;
    body_count?: number;
  };
  features?: {
    holes: Array<{
      type: string;
      confidence: number;
      source_page: number;
      source_view_index?: number;
      diameter: number;
      depth?: number;
      kind: string;
      count?: number;
      pattern?: string;
      notes?: string;
    }>;
    slots: Array<{
      type: string;
      confidence: number;
      source_page: number;
      source_view_index?: number;
      width: number;
      length: number;
      depth?: number;
      orientation: string;
      count?: number;
      pattern?: string;
      notes?: string;
    }>;
    chamfers: Array<{
      type: string;
      confidence: number;
      source_page: number;
      source_view_index?: number;
      size: number;
      angle: number;
      edge_location: string;
      notes?: string;
    }>;
    fillets: Array<{
      type: string;
      confidence: number;
      source_page: number;
      source_view_index?: number;
      radius: number;
      edge_location: string;
      notes?: string;
    }>;
    threads: Array<{
      type: string;
      confidence: number;
      source_page: number;
      source_view_index?: number;
      designation: string;
      length?: number;
      kind: string;
      notes?: string;
    }>;
    meta: {
      model_version: string;
      detector_version: string;
      timestamp_utc: string;
      warnings: string[];
    };
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
  mode?: 'ENVELOPE' | 'GEOMETRY';
  vendor_quote_mode?: boolean;
  source: {
    part_summary: any | null;
    job_id?: string | null;
    step_metrics: Record<string, any> | null;
  };
  tolerances: {
    rm_od_allowance_in: number;
    rm_len_allowance_in: number;
  };
  cost_inputs?: {
    rm_rate_per_kg: number;
    turning_rate_per_min: number;
    vmc_rate_per_min?: number;
    roughing_cost?: number;
    inspection_cost?: number;
    special_process_cost?: number | null;
    others_cost?: number;
    material_density_kg_m3?: number;
    pf_pct?: number;
    oh_profit_pct?: number;
    rejection_pct?: number;
    exchange_rate?: number | null;
    currency?: string;
    use_live_rate?: boolean;
    qty_moq?: number;
    annual_potential_qty?: number;
    drawing_number?: string | null;
    part_name?: string | null;
    part_revision?: string | null;
    rfq_type?: string | null;
    material_grade?: string | null;
    material_spec?: string | null;
    coating_spec?: string | null;
    special_process?: string | null;
    special_machining_process?: string | null;
    rfq_status?: string | null;
    part_type?: string | null;
    part_category?: string | null;
  } | null;
  /** LLM-extracted or user-supplied dimension overrides — bypass geometry-computed values */
  dimension_overrides?: Record<string, number>;
}

export interface RFQAutofillEstimate {
  rm_weight_kg: RFQFieldValue;
  material_cost: RFQFieldValue;
  roughing_cost: RFQFieldValue;
  inspection_cost: RFQFieldValue;
  special_process_cost: RFQFieldValue;
  turning_minutes: RFQFieldValue;
  turning_cost: RFQFieldValue;
  subtotal: RFQFieldValue;
  total_estimate: RFQFieldValue;
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
    bore_coverage_pct?: number;
    max_od_seg_conf?: number | null;
    used_z_range?: boolean | null;
    od_pool_count?: number | null;
    od_pool_dropped_low_conf?: boolean | null;
    id_auto_clamped?: boolean | null;
    od_spike_suspect?: boolean | null;
  };
  estimate?: RFQAutofillEstimate | null;
}

export interface RFQExportFileInfo {
  filename: string;
  size_bytes: number;
  mtime_utc: string;
}

export interface RFQExportsListResponse {
  rfq_id: string;
  files: RFQExportFileInfo[];
}

export interface RFQEnvelopeFields {
  finish_max_od_in: RFQFieldValue;
  finish_len_in: RFQFieldValue;
  raw_max_od_in: RFQFieldValue;
  raw_len_in: RFQFieldValue;
}

export interface RFQEnvelopeDebug {
  max_od_in: number;
  overall_len_in: number;
  min_len_gate_in: number;
  scale_method: string;
  overall_confidence: number;
  validation_passed?: boolean | null;
  notes: string[];
}

export interface RFQEnvelopeRequest {
  rfq_id: string;
  part_no: string;
  source: { job_id: string } | { part_summary: any };
  allowances: { od_in: number; len_in: number };
  rounding?: { od_step: number; len_step: number };
}

export interface RFQEnvelopeResponse {
  part_no: string;
  fields: RFQEnvelopeFields;
  status: 'AUTO_FILLED' | 'NEEDS_REVIEW' | 'REJECTED';
  reasons: string[];
  debug: RFQEnvelopeDebug;
}

export interface RFQVendorQuoteExtractField {
  value: string | null;
  confidence: number;
  source: string;
}

export interface RFQVendorQuoteExtractResponse {
  job_id: string;
  fields: Record<string, RFQVendorQuoteExtractField>;
  pdf_hint?: Record<string, RFQVendorQuoteExtractField>;
  debug: Record<string, any>;
}

// ---- LLM Two-Agent Pipeline ----

export interface LLMExtractedSpecs {
  part_number: string | null;
  part_name: string | null;
  material: string | null;
  quantity: number | null;
  od_in: number | null;
  max_od_in: number | null;
  id_in: number | null;
  max_id_in: number | null;
  length_in: number | null;
  max_length_in: number | null;
  tolerance_od: string | null;
  tolerance_id: string | null;
  tolerance_length: string | null;
  finish: string | null;
  revision: string | null;
}

export interface LLMValidationField {
  value: number | string | null;
  confidence: number;
  issue: string | null;
}

export interface LLMValidationReport {
  fields: Record<string, LLMValidationField>;
  cross_checks: string[];
  overall_confidence: number;
  recommendation: 'ACCEPT' | 'REVIEW' | 'REJECT';
}

// ---- Human-in-the-Loop Dimension Corrections ----

export interface DimCorrection {
  field: string;
  value: number | string;
  original_value: number | string | null;
  corrected_at: string;
}

export type CorrectionsMap = Record<string, DimCorrection>;

export interface LLMAnalysisResult {
  available?: boolean;
  /** True while the background LLM thread is still running. Poll until false/absent. */
  pending?: boolean;
  /** Set when the pipeline failed (rate limit or other error). */
  error?: string | null;
  error_type?: 'rate_limit' | 'pipeline_error' | null;
  rate_limit_info?: Record<string, unknown> | null;
  pdf_text_length?: number;
  extracted: LLMExtractedSpecs;
  validation: LLMValidationReport;
  code_issues: string[];
  valid: boolean;
}
