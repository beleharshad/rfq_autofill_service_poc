/**API client for backend communication.*/

import {
  JobResponse,
  JobFilesResponse,
  StackInputRequest,
  StackInputResponse,
  PartSummary,
  RFQAutofillRequest,
  RFQAutofillResponse,
  RFQExportsListResponse,
  RFQVendorQuoteExtractResponse,
  RFQEnvelopeResponse,
  RFQEnvelopeRequest,
  RunReport,
  LLMAnalysisResult,
} from './types';

const API_BASE_URL = '/api/v1';

async function handleResponse<T>(response: Response): Promise<T> {
  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: response.statusText }));
    throw new Error(error.detail || `HTTP error! status: ${response.status}`);
  }
  return response.json();
}

async function handleBlobResponse(response: Response): Promise<{ blob: Blob; filename: string }> {
  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: response.statusText }));
    const message = (error && (error.detail || error.message)) ? (error.detail || error.message) : response.statusText;
    throw new Error(message);
  }

  const cd = response.headers.get('content-disposition') || response.headers.get('Content-Disposition') || '';
  const m = /filename\*?=(?:UTF-8''|\"?)([^\";]+)\"?/i.exec(cd);
  const filename = m?.[1] ? decodeURIComponent(m[1]) : 'rfq_export.xlsx';
  const blob = await response.blob();
  return { blob, filename };
}

export const api = {
  /**
   * Create a new job with file uploads.
   */
  async createJob(
    files: File[],
    name?: string,
    description?: string,
    mode?: string
  ): Promise<JobResponse> {
    const formData = new FormData();
    
    files.forEach((file) => {
      formData.append('files', file);
    });
    
    if (name) {
      formData.append('name', name);
    }
    
    if (description) {
      formData.append('description', description);
    }
    
    if (mode) {
      formData.append('mode', mode);
    }

    const response = await fetch(`${API_BASE_URL}/jobs`, {
      method: 'POST',
      body: formData,
    });

    return handleResponse<JobResponse>(response);
  },

  /**
   * Get job details.
   */
  async getJob(jobId: string): Promise<JobResponse> {
    const response = await fetch(`${API_BASE_URL}/jobs/${jobId}`);
    return handleResponse<JobResponse>(response);
  },

  /**
   * Get run report for a job.
   * Returns null if the report doesn't exist yet.
   */
  async getRunReport(jobId: string): Promise<RunReport | null> {
    try {
      const response = await fetch(`${API_BASE_URL}/jobs/${jobId}/download?path=outputs/run_report.json`);
      if (response.status === 404) {
        // Report doesn't exist yet - this is normal for jobs that haven't been processed
        return null;
      }
      return handleResponse<RunReport>(response);
    } catch (err) {
      // If it's a 404, return null; otherwise re-throw
      if (err instanceof Error && err.message.includes('404')) {
        return null;
      }
      throw err;
    }
  },

  /**
   * List all jobs.
   */
  async listJobs(): Promise<JobResponse[]> {
    const response = await fetch(`${API_BASE_URL}/jobs`);
    return handleResponse<JobResponse[]>(response);
  },

  /**
   * Get job files with download URLs.
   */
  async getJobFiles(jobId: string): Promise<JobFilesResponse> {
    const response = await fetch(`${API_BASE_URL}/jobs/${jobId}/files`);
    return handleResponse<JobFilesResponse>(response);
  },

  /**
   * Get PDF file URL for viewing.
   */
  getPdfUrl(jobId: string, filePath: string): string {
    return `${API_BASE_URL}/jobs/${jobId}/download?path=${encodeURIComponent(filePath)}`;
  },

  /**
   * Download a file programmatically to ensure it goes to Downloads folder.
   * Fetches the file as a blob and triggers browser download.
   * Includes retry logic for files that might still be writing to disk.
   */
  async downloadFile(jobId: string, filePath: string, filename: string, maxRetries: number = 3, retryDelay: number = 1000): Promise<void> {
    const url = this.getPdfUrl(jobId, filePath);
    
    for (let attempt = 0; attempt < maxRetries; attempt++) {
      try {
        const response = await fetch(url);
        
        if (response.ok) {
          const blob = await response.blob();
          
          // Check if blob is actually valid (not an error page)
          if (blob.size === 0) {
            throw new Error('File is empty or not yet available');
          }
          
          const blobUrl = window.URL.createObjectURL(blob);
          
          // Create a temporary anchor element and trigger download
          const link = document.createElement('a');
          link.href = blobUrl;
          link.download = filename;
          document.body.appendChild(link);
          link.click();
          
          // Clean up
          document.body.removeChild(link);
          window.URL.revokeObjectURL(blobUrl);
          
          console.log(`[API] Successfully downloaded ${filename} on attempt ${attempt + 1}`);
          return; // Success, exit function
        } else if (response.status === 404 && attempt < maxRetries - 1) {
          // File not found yet, wait and retry
          console.log(`[API] File not found (404), retrying in ${retryDelay}ms... (attempt ${attempt + 1}/${maxRetries})`);
          await new Promise(resolve => setTimeout(resolve, retryDelay));
          continue; // Retry
        } else {
          // Other error or last attempt failed
          throw new Error(`Failed to download file: ${response.status} ${response.statusText}`);
        }
      } catch (error) {
        if (attempt < maxRetries - 1) {
          // Not the last attempt, wait and retry
          console.log(`[API] Download error, retrying in ${retryDelay}ms... (attempt ${attempt + 1}/${maxRetries})`, error);
          await new Promise(resolve => setTimeout(resolve, retryDelay));
          continue; // Retry
        } else {
          // Last attempt failed
          console.error('[API] Error downloading file after all retries:', error);
          throw error;
        }
      }
    }
  },

  /**
   * Save stack input for a job.
   */
  async saveStackInput(jobId: string, input: StackInputRequest): Promise<StackInputResponse> {
    const response = await fetch(`${API_BASE_URL}/jobs/${jobId}/stack-input`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(input),
    });

    return handleResponse<StackInputResponse>(response);
  },

  /**
   * Get saved stack input for a job.
   */
  async getStackInput(jobId: string): Promise<StackInputResponse> {
    const response = await fetch(`${API_BASE_URL}/jobs/${jobId}/stack-input`);
    return handleResponse<StackInputResponse>(response);
  },

  /**
   * Get part summary JSON.
   */
  async getPartSummary(jobId: string): Promise<PartSummary> {
    const response = await fetch(`${API_BASE_URL}/jobs/${jobId}/download?path=outputs/part_summary.json`);
    return handleResponse<PartSummary>(response);
  },

  /**
   * Run analysis pipeline.
   */
  async runAnalysis(jobId: string): Promise<{ status: string; outputs: string[]; job_id: string }> {
    const response = await fetch(`${API_BASE_URL}/jobs/${jobId}/run`, {
      method: 'POST',
    });
    return handleResponse<{ status: string; outputs: string[]; job_id: string }>(response);
  },

  /**
   * Process Profile2D input.
   */
  async processProfile2D(
    jobId: string,
    input: {
      primitives: Array<{ type: string; start: { x: number; y: number }; end: { x: number; y: number } }>;
      axis_point: { x: number; y: number };
    }
  ): Promise<{ status: string; outputs: string[]; validation_errors: string[] }> {
    const response = await fetch(`${API_BASE_URL}/jobs/${jobId}/profile2d`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(input),
    });
    return handleResponse<{ status: string; outputs: string[]; validation_errors: string[] }>(response);
  },

  /**
   * Set job processing mode.
   */
  async setJobMode(jobId: string, mode: 'assisted_manual' | 'auto_convert'): Promise<JobResponse> {
    const response = await fetch(`${API_BASE_URL}/jobs/${jobId}/mode`, {
      method: 'PUT',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({ mode }),
    });

    return handleResponse<JobResponse>(response);
  },

  /**
   * Upload PDF for Assisted Manual mode.
   */
  async uploadPdf(jobId: string, file: File): Promise<{
    page_count: number;
    page_images: string[];
    source_pdf: string;
  }> {
    const formData = new FormData();
    formData.append('file', file);

    const response = await fetch(`${API_BASE_URL}/jobs/${jobId}/pdf/upload`, {
      method: 'POST',
      body: formData,
    });

    return handleResponse<{
      page_count: number;
      page_images: string[];
      source_pdf: string;
    }>(response);
  },

  /**
   * Detect views on uploaded PDF pages.
   */
  async detectViews(jobId: string): Promise<{
    job_id: string;
    pages: Array<{
      page: number;
      views: Array<{
        bbox: [number, number, number, number];
        bbox_pixels: [number, number, number, number];
        area: number;
        confidence: number;
      }>;
      image_size: [number, number];
    }>;
    total_views: number;
  }> {
    const response = await fetch(`${API_BASE_URL}/jobs/${jobId}/pdf/detect_views`, {
      method: 'POST',
    });

    return handleResponse<{
      job_id: string;
      pages: Array<{
        page: number;
        views: Array<{
          bbox: [number, number, number, number];
          bbox_pixels: [number, number, number, number];
          area: number;
          confidence: number;
        }>;
        image_size: [number, number];
      }>;
      total_views: number;
    }>(response);
  },

  /**
   * Save selected view to job state.
   */
  async saveSelectedView(jobId: string, page: number, viewIndex: number): Promise<void> {
    const response = await fetch(`${API_BASE_URL}/jobs/${jobId}/selected-view`, {
      method: 'PUT',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({ page, view_index: viewIndex }),
    });
    if (!response.ok) {
      const error = await response.json().catch(() => ({ detail: response.statusText }));
      throw new Error(error.detail || `HTTP error! status: ${response.status}`);
    }
  },

  /**
   * Auto-detect turned view from PDF.
   */
  async autoDetectTurnedView(jobId: string): Promise<{
    job_id: string;
    ranked_views: Array<{
      page: number;
      view_index: number;
      scores: {
        axis_conf: number;
        sym_conf: number;
        dia_text_conf: number;
        section_conf: number;
        view_conf: number;
      };
    }>;
    best_view?: {
      page: number;
      view_index: number;
      scores: {
        view_conf: number;
      };
    } | null;
    confidence_threshold: number;
    total_views_analyzed: number;
    llm_analysis?: {
      extracted?: Record<string, any>;
      validation?: { recommendation: string; overall_confidence: number; cross_checks: string[] };
      code_issues?: string[];
      valid?: boolean;
      vision_mode?: boolean;
      error?: string;
    };
  }> {
    console.log('[API] Calling autoDetectTurnedView for job:', jobId);
    const response = await fetch(`${API_BASE_URL}/jobs/${jobId}/pdf/auto_detect_turned_view`, {
      method: 'POST',
    });
    console.log('[API] autoDetectTurnedView response status:', response.status);
    const result = await handleResponse<{
      job_id: string;
      ranked_views: Array<{
        page: number;
        view_index: number;
        scores: {
          axis_conf: number;
          sym_conf: number;
          dia_text_conf: number;
          section_conf: number;
          view_conf: number;
        };
      }>;
      best_view?: {
        page: number;
        view_index: number;
        scores: {
          view_conf: number;
        };
      } | null;
      confidence_threshold: number;
      total_views_analyzed: number;
      llm_analysis?: Record<string, any>;
    }>(response);
    console.log('[API] autoDetectTurnedView result:', result);
    return result;
  },

  /**
   * Infer stack from detected view.
   */
  async inferStackFromView(jobId: string, page?: number, viewIndex?: number): Promise<{
    job_id: string;
    status: string;
    segments?: Array<{
      z_start: number;
      z_end: number;
      od_diameter: number;
      id_diameter: number;
      confidence?: number;
    }>;
    totals?: {
      volume_in3: number;
      od_area_in2: number;
      id_area_in2: number;
      total_surface_area_in2: number;
    };
    overall_confidence?: number;
    warnings?: string[];
    outputs?: string[];
    error?: string;
    validation_errors?: string[];
    scale_report?: any;
    derived_values?: {
      total_length_inches: number;
      max_od_inches: number;
    };
    message?: string;
  }> {
    const body: any = {};
    if (page !== undefined) body.page = page;
    if (viewIndex !== undefined) body.view_index = viewIndex;

    console.log('[API] Calling inferStackFromView for job:', jobId, 'body:', body);
    const response = await fetch(`${API_BASE_URL}/jobs/${jobId}/pdf/infer_stack`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: Object.keys(body).length > 0 ? JSON.stringify(body) : undefined,
    });
    console.log('[API] inferStackFromView response status:', response.status);
    const result = await handleResponse<{
      job_id: string;
      status: string;
      segments?: Array<{
        z_start: number;
        z_end: number;
        od_diameter: number;
        id_diameter: number;
        confidence?: number;
      }>;
      totals?: {
        volume_in3: number;
        od_area_in2: number;
        id_area_in2: number;
        total_surface_area_in2: number;
      };
      overall_confidence?: number;
      warnings?: string[];
      outputs?: string[];
      error?: string;
      validation_errors?: string[];
      scale_report?: any;
      derived_values?: {
        total_length_inches: number;
        max_od_inches: number;
      };
      message?: string;
    }>(response);
    console.log('[API] inferStackFromView result:', result);
    return result;
  },

  /**
   * Process turned stack input (Assisted Manual mode).
   */
  async processTurnedStack(
    jobId: string,
    input: {
      units: string;
      segments: Array<{
        z_start: number;
        z_end: number;
        od_diameter: number;
        id_diameter: number;
      }>;
      notes?: string;
    }
  ): Promise<{
    job_id: string;
    status: string;
    summary: any;
    totals: {
      volume_in3: number;
      od_area_in2: number;
      id_area_in2: number;
      total_surface_area_in2: number;
      [key: string]: number;
    };
    warnings: string[];
    errors?: string[];
    outputs: string[];
  }> {
    const response = await fetch(`${API_BASE_URL}/jobs/${jobId}/manual/turned_stack`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(input),
    });

    return handleResponse<{
      job_id: string;
      status: string;
      summary: any;
      totals: {
        volume_in3: number;
        od_area_in2: number;
        id_area_in2: number;
        total_surface_area_in2: number;
        [key: string]: number;
      };
      warnings: string[];
      errors?: string[];
      outputs: string[];
    }>(response);
  },

  /**
   * Generate STEP file from existing turned stack (manual mode).
   */
  async generateStepFromStack(jobId: string): Promise<{
    job_id: string;
    status: string;
    outputs: string[];
    warnings: string[];
    error?: string;
  }> {
    const response = await fetch(`${API_BASE_URL}/jobs/${jobId}/manual/generate_step`, {
      method: 'POST',
    });

    return handleResponse<{
      job_id: string;
      status: string;
      outputs: string[];
      warnings: string[];
      error?: string;
    }>(response);
  },

  /**
   * Generate STEP file from inferred stack (auto-detect mode).
   */
  async generateStepFromInferredStack(jobId: string): Promise<{
    status: string;
    output_step_path?: string;
    message: string;
    debug?: any;
    outputs_info?: any;
  }> {
    console.log('[API] generateStepFromInferredStack called for job:', jobId);
    const response = await fetch(`${API_BASE_URL}/jobs/${jobId}/generate_step_from_stack`, {
      method: 'POST',
    });
    
    console.log('[API] generateStepFromInferredStack response status:', response.status);
    
    // Handle 500 errors with detailed error info
    if (response.status === 500) {
      const errorData = await response.json();
      console.error('[API] STEP generation failed with 500:', errorData);
      return {
        status: 'FAILED',
        message: errorData.detail?.message || 'STEP generation failed',
        debug: errorData.detail?.debug || {}
      };
    }
    
    if (!response.ok) {
      const errorText = await response.text();
      console.error('[API] STEP generation failed:', response.status, errorText);
      throw new Error(`HTTP ${response.status}: ${errorText}`);
    }
    
    const result = await handleResponse<{
      status: string;
      output_step_path?: string;
      message: string;
      debug?: any;
      outputs_info?: any;
    }>(response);
    console.log('[API] generateStepFromInferredStack result:', result);
    return result;
  },

  /**
   * Generate STEP file from auto-converted inferred stack.
   */
  async approveStep(jobId: string): Promise<{
    job_id: string;
    status: string;
    message?: string;
  }> {
    const response = await fetch(`${API_BASE_URL}/jobs/${jobId}/auto/approve_step`, {
      method: 'POST',
    });
    return handleResponse(response);
  },

  async autoGenerateStep(jobId: string): Promise<{
    job_id: string;
    status: string;
    outputs: string[];
    warnings: string[];
    overall_confidence?: number;
    error?: string;
    reasons?: string[];
    message?: string;
  }> {
    const response = await fetch(`${API_BASE_URL}/jobs/${jobId}/auto/generate_step`, {
      method: 'POST',
    });

    return handleResponse<{
      job_id: string;
      status: string;
      outputs: string[];
      warnings: string[];
      overall_confidence?: number;
      error?: string;
    }>(response);
  },

  /**
   * Check OCC (OpenCASCADE) availability in the backend environment.
   */
  async checkOccAvailability(): Promise<{
    occ_available: boolean;
    backend: string | null;
    error: string | null;
  }> {
    const response = await fetch(`${API_BASE_URL}/health/occ`);
    return handleResponse<{
      occ_available: boolean;
      backend: string | null;
      error: string | null;
    }>(response);
  },

  /**
   * RFQ AutoFill (v1): compute suggested RFQ fields from part_summary.
   */
  async rfqAutofill(request: RFQAutofillRequest): Promise<RFQAutofillResponse> {
    const response = await fetch(`${API_BASE_URL}/rfq/autofill`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(request),
    });

    return handleResponse<RFQAutofillResponse>(response);
  },

  async rfqEnvelope(params: RFQEnvelopeRequest): Promise<RFQEnvelopeResponse> {
    const response = await fetch(`${API_BASE_URL}/rfq/envelope`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(params),
    });
    return handleResponse<RFQEnvelopeResponse>(response);
  },

  /**
   * RFQ AutoFill (v1): server-side load `outputs/part_summary.json` via source.job_id.
   * This avoids sending the full part_summary payload from the browser.
   */
  async rfqAutofillForJob(params: {
    rfq_id: string;
    job_id: string;
    part_no: string;
    mode?: 'ENVELOPE' | 'GEOMETRY';
    vendor_quote_mode?: boolean;
    tolerances: { rm_od_allowance_in: number; rm_len_allowance_in: number };
    step_metrics?: Record<string, any> | null;
    cost_inputs?: RFQAutofillRequest['cost_inputs'];
  }): Promise<RFQAutofillResponse> {
    return this.rfqAutofill({
      rfq_id: params.rfq_id,
      part_no: params.part_no,
      mode: params.mode,
      vendor_quote_mode: params.vendor_quote_mode,
      source: {
        job_id: params.job_id,
        part_summary: null,
        step_metrics: params.step_metrics ?? null,
      },
      tolerances: params.tolerances,
      cost_inputs: params.cost_inputs ?? null,
    });
  },

  /**
   * RFQ Excel export with mode selection.
   * @param request - RFQ autofill request
   * @param exportMode - "master" (update master file), "new_file" (create/update custom file), "copy" (timestamped copies)
   * @param customFilename - For new_file mode: filename (creates if new, updates if exists)
   */
  async rfqExportXlsx(
    request: RFQAutofillRequest,
    exportMode: 'master' | 'new_file' | 'copy' = 'master',
    customFilename?: string
  ): Promise<{ blob: Blob; filename: string }> {
    const params = new URLSearchParams();
    params.append('export_mode', exportMode);
    if (customFilename) {
      params.append('custom_filename', customFilename);
    }
    
    const response = await fetch(`${API_BASE_URL}/rfq/export_xlsx?${params.toString()}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(request),
    });
    return handleBlobResponse(response);
  },

  async rfqExportXlsxForJob(params: {
    rfq_id: string;
    job_id: string;
    part_no: string;
    mode?: 'ENVELOPE' | 'GEOMETRY';
    tolerances: { rm_od_allowance_in: number; rm_len_allowance_in: number };
    step_metrics?: Record<string, any> | null;
    cost_inputs?: RFQAutofillRequest['cost_inputs'];
    exportMode?: 'master' | 'new_file' | 'copy';
    customFilename?: string;
  }): Promise<{ blob: Blob; filename: string }> {
    return this.rfqExportXlsx(
      {
        rfq_id: params.rfq_id,
        part_no: params.part_no,
        mode: params.mode,
        source: {
          job_id: params.job_id,
          part_summary: null,
          step_metrics: params.step_metrics ?? null,
        },
        tolerances: params.tolerances,
        cost_inputs: params.cost_inputs ?? null,
      },
      params.exportMode ?? 'master',
      params.customFilename
    );
  },

  /**
   * List generated XLSX exports (newest first).
   */
  async rfqListExports(rfq_id: string): Promise<RFQExportsListResponse> {
    const response = await fetch(`${API_BASE_URL}/rfq/exports?rfq_id=${encodeURIComponent(rfq_id)}`);
    return handleResponse<RFQExportsListResponse>(response);
  },

  /**
   * Download a previously generated export file.
   */
  async rfqDownloadExport(rfq_id: string, filename: string): Promise<{ blob: Blob; filename: string }> {
    const response = await fetch(`${API_BASE_URL}/rfq/exports/${encodeURIComponent(rfq_id)}/${encodeURIComponent(filename)}`);
    // Content-Disposition should already include a filename; fall back to the provided name.
    const res = await handleBlobResponse(response);
    return { blob: res.blob, filename: res.filename || filename };
  },

  /**
   * Vendor Quote extraction (OCR): extract RFQ/Excel-like fields directly from the job PDF.
   * Uses existing job artifacts (pdf_pages + auto_detect_results + pdf_views).
   */
  async rfqVendorQuoteExtract(job_id: string): Promise<RFQVendorQuoteExtractResponse> {
    const response = await fetch(`${API_BASE_URL}/rfq/vendor_quote_extract?job_id=${encodeURIComponent(job_id)}`, {
      method: 'POST',
    });
    return handleResponse<RFQVendorQuoteExtractResponse>(response);
  },

  // Backwards-compatible alias (older UI code)
  async autofillRFQ(request: RFQAutofillRequest): Promise<RFQAutofillResponse> {
    return this.rfqAutofill(request);
  },

  /**
   * Run two-agent LLM analysis on the job's uploaded PDF.
   * Extracts part specs (Agent 1) and validates them (Agent 2).
   */
  async llmAnalyzeJob(jobId: string): Promise<LLMAnalysisResult> {
    const response = await fetch(`${API_BASE_URL}/llm/jobs/${encodeURIComponent(jobId)}/llm-analyze`, {
      method: 'POST',
    });
    return handleResponse<LLMAnalysisResult>(response);
  },

  /**
   * Return the cached LLM analysis result for a job (from last run).
   * Returns null when no analysis has been run yet (server returns {available:false}).
   */
  async getLlmAnalysis(jobId: string): Promise<LLMAnalysisResult | null> {
    const response = await fetch(`${API_BASE_URL}/llm/jobs/${encodeURIComponent(jobId)}/llm-analysis`);
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const data = await response.json();
    if (data?.available === false) return null;
    return data as LLMAnalysisResult;
  },

  /**
   * Download an Excel (.xlsx) summary of the last LLM analysis result.
   */
  async llmAnalysisExportExcel(jobId: string): Promise<{ blob: Blob; filename: string }> {
    const response = await fetch(
      `${API_BASE_URL}/llm/jobs/${encodeURIComponent(jobId)}/llm-analysis/export-excel`
    );
    return handleBlobResponse(response);
  },

  // ── 3D Preview (OCC B-Rep → STEP → GLB) ─────────────────────────────────

  /**
   * Check whether a GLB 3D preview is available / can be generated for a job.
   */
  async get3dPreviewStatus(jobId: string): Promise<{
    occ_available: boolean;
    stack_ready: boolean;
    step_ready: boolean;
    glb_ready: boolean;
    can_generate: boolean;
  }> {
    const response = await fetch(`${API_BASE_URL}/jobs/${encodeURIComponent(jobId)}/3d-preview/status`);
    return handleResponse(response);
  },

  /**
   * Returns the URL for the GLB 3D preview binary (for use with useGLTF / GLTFLoader).
   * The backend generates the GLB on demand (OCC BRep → STEP → GLB).
   * @param boreDiameter - inner diameter in inches; when >0 the backend overrides all segment
   *                       id_diameter values so the GLB renders as a proper hollow part.
   */
  get3dPreviewUrl(jobId: string, force = false, boreDiameter?: number): string {
    const params = new URLSearchParams();
    if (force) params.set('force', 'true');
    if (boreDiameter && boreDiameter > 0.001) params.set('bore_diameter', String(boreDiameter));
    const qs = params.toString();
    return `${API_BASE_URL}/jobs/${encodeURIComponent(jobId)}/3d-preview${qs ? `?${qs}` : ''}`;
  },
};

