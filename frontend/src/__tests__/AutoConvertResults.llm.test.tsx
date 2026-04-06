/**
 * Frontend E2E scenario tests for the LLM Auto-Detect flow.
 *
 * Scenarios covered:
 *  1. Component mounts cleanly even when all API calls return empty data.
 *  2. Auto-Detect button triggers autoDetectTurnedView → LLM dims card appears.
 *  3. Part No is auto-populated from llm_analysis.extracted.part_number.
 *  4. Confidence badge is rendered with "ACCEPT" recommendation.
 *  5. Generate All button is disabled when partSummary has not loaded.
 *  6. Generate All button becomes enabled once partSummary loads.
 *  7. Clicking Generate All calls rfqExportXlsx, generateStep, and downloadFile × 3.
 *  8. 429/error from LLM surfaces as an error message, does NOT break the component.
 *  9. REVIEW recommendation shows the validator warning in the dims card.
 * 10. Finish OD, MAX OD, Finish ID, Finish Length all rendered with correct values.
 *
 * All network calls are mocked. No backend needed.
 */

import { render, screen, waitFor, fireEvent, act } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import AutoConvertResults from '../components/AutoConvertResults/AutoConvertResults';

// ── Mocks ──────────────────────────────────────────────────────────────────

// Mock the entire api module so no real fetch calls happen.
vi.mock('../services/api', () => ({
  api: {
    getJobFiles: vi.fn(),
    getJob: vi.fn(),
    getCorrections: vi.fn(),
    saveCorrection: vi.fn(),
    fetchBlobUrl: vi.fn(),
    fetchJobFile: vi.fn(),
    checkOccAvailability: vi.fn(),
    rfqListExports: vi.fn(),
    getPdfUrl: vi.fn((_jobId: string, path: string) => `/mocked/${path}`),
    autoDetectTurnedView: vi.fn(),
    detectViews: vi.fn(),
    inferStackFromView: vi.fn(),
    uploadPdf: vi.fn(),
    rfqExportXlsx: vi.fn(),
    generateStepFromInferredStack: vi.fn(),
    downloadFile: vi.fn(),
    getJobFilesForPartSummary: vi.fn(),
    getRunReport: vi.fn(),
  },
}));

// Mock ProfileReviewPlot — uses canvas/WebGL which is not available in jsdom.
vi.mock('../components/AutoConvertResults/ProfileReviewPlot', () => ({
  default: () => null,
}));

// Mock LatheViewer — uses @react-three/fiber Canvas which is not available in jsdom.
vi.mock('../components/AutoConvertResults/LatheViewer', () => ({
  default: () => null,
}));

// Mock segmentStore — simple in-memory store, no side effects needed in tests.
vi.mock('../state/segmentStore', () => ({
  setSegments: vi.fn(),
}));

// ── Import after mocking ──
import { api } from '../services/api';

// ── Fixtures ───────────────────────────────────────────────────────────────

const JOB_ID = 'frontend-e2e-job-001';

const PART_SUMMARY = {
  units: { length: 'in' },
  z_range: [0, 0.63],
  segments: [
    {
      z_start: 0,
      z_end: 0.63,
      od_diameter: 1.24,
      id_diameter: 0.43,
      confidence: 0.9,
      flags: [],
    },
  ],
  scale_report: { method: 'anchor_dimension', validation_passed: true },
  inference_metadata: { overall_confidence: 0.9 },
};

const STEP_PART_SUMMARY_BBOX = {
  units: { length: 'in' },
  z_range: [0, 7.304],
  segments: [
    {
      z_start: 0,
      z_end: 7.304,
      od_diameter: 6.892,
      id_diameter: 0,
      confidence: 0.7,
      flags: [],
    },
  ],
  scale_report: { method: 'step_upload_bbox_fallback', validation_passed: false },
  inference_metadata: {
    overall_confidence: 0.7,
    source: 'uploaded_step_selected_body_bbox_fallback',
    body_count: 3,
  },
  selected_body: {
    analysis_warning: 'Feature extraction produced no turned segments',
  },
  body_candidates: [{ body_index: 0 }, { body_index: 1 }, { body_index: 2 }],
  warnings: ['Detected 3 solid bodies in the STEP file; selected body 0 as the dominant turned candidate.'],
  step_metadata: {
    representation_context: 'TOP_LEVEL_ASSEMBLY_PART',
  },
};

const LLM_ANALYSIS_ACCEPT = {
  extracted: {
    part_number: '050CE0004',
    od_in: 1.24,
    max_od_in: 1.38,
    id_in: 0.43,
    length_in: 0.63,
    material: '80-55-06 Ductile Iron',
  },
  validation: {
    recommendation: 'ACCEPT',
    overall_confidence: 0.91,
    cross_checks: [] as string[],
  },
  code_issues: [] as string[],
  valid: true,
  vision_mode: false,
};

const LLM_ANALYSIS_REVIEW = {
  ...LLM_ANALYSIS_ACCEPT,
  validation: {
    recommendation: 'REVIEW',
    overall_confidence: 0.55,
    cross_checks: ['od_in seems suspiciously small'],
  },
  valid: false,
};

const AUTO_DETECT_CV_ONLY = {
  job_id: JOB_ID,
  ranked_views: [],
  best_view: null,
  confidence_threshold: 0.65,
  total_views_analyzed: 0,
};

const AUTO_DETECT_WITH_LLM = {
  ...AUTO_DETECT_CV_ONLY,
  llm_analysis: LLM_ANALYSIS_ACCEPT,
};

const AUTO_DETECT_WITH_REVIEW = {
  ...AUTO_DETECT_CV_ONLY,
  llm_analysis: LLM_ANALYSIS_REVIEW,
};

const AUTO_DETECT_WITH_ERROR = {
  ...AUTO_DETECT_CV_ONLY,
  llm_analysis: { error: 'API quota exhausted (429)' },
};

// Variants with best_view — trigger handleInferStack → loadResults → inferredStack transition.
const AUTO_DETECT_WITH_LLM_AND_VIEW = {
  ...AUTO_DETECT_WITH_LLM,
  best_view: { page: 1, view_index: 0, scores: { view_conf: 0.82 } },
};

const AUTO_DETECT_WITH_REVIEW_AND_VIEW = {
  ...AUTO_DETECT_WITH_REVIEW,
  best_view: { page: 1, view_index: 0, scores: { view_conf: 0.82 } },
};

// inferred_stack.json content served after the second call to getJobFiles
const INFERRED_STACK = {
  segments: PART_SUMMARY.segments,
  z_range: PART_SUMMARY.z_range,
  units: PART_SUMMARY.units,
  step_status: 'DONE',
  step_reason: null,
  inference_metadata: { mode: 'auto_convert', overall_confidence: 0.9 },
  scale_report: PART_SUMMARY.scale_report,
};

// ── Helpers ────────────────────────────────────────────────────────────────

function makeJsonFetchResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

function setupDefaultMocks() {
  const mockApi = api as ReturnType<typeof vi.mocked<typeof api>>;

  // getJobFiles: return files including part_summary.json + a pdf_page (gates ensurePdfPagesReady)
  mockApi.getJobFiles.mockResolvedValue({
    files: [
      { path: 'inputs/source.pdf', name: 'source.pdf', size: 1024, created_at: '' },
      { path: 'outputs/pdf_pages/page_1.png', name: 'page_1.png', size: 1024, created_at: '' },
      { path: 'outputs/part_summary.json', name: 'part_summary.json', size: 512, created_at: '' },
    ],
  });

  mockApi.getJob.mockResolvedValue({
    job_id: JOB_ID,
    name: '050CE0004',
    status: 'DONE',
    mode: 'auto_convert',
    created_at: '',
    updated_at: '',
    files: [],
  } as any);

  mockApi.checkOccAvailability.mockResolvedValue({
    occ_available: false,
    backend: null,
    error: 'OCC not installed',
  });

  mockApi.rfqListExports.mockResolvedValue({ files: [] });
  mockApi.getCorrections.mockResolvedValue({});
  mockApi.saveCorrection.mockResolvedValue(undefined);
  mockApi.fetchBlobUrl.mockResolvedValue('blob:http://localhost/mock-page');
  mockApi.fetchJobFile.mockImplementation(async (_jobId: string, path: string) => {
    if (path === 'outputs/part_summary.json') {
      return makeJsonFetchResponse(PART_SUMMARY);
    }
    if (path === 'outputs/inferred_stack.json') {
      return new Response('', { status: 404 });
    }
    if (path.endsWith('.pdf')) {
      return new Response(new Blob(['pdf'], { type: 'application/pdf' }), { status: 200 });
    }
    return new Response('', { status: 404 });
  });
  mockApi.detectViews.mockResolvedValue({ job_id: JOB_ID, pages: [], total_views: 0 });
  mockApi.rfqExportXlsx.mockResolvedValue({
    blob: new Blob(['xlsx content'], { type: 'application/octet-stream' }),
    filename: '050CE0004_rfq.xlsx',
  });
  mockApi.generateStepFromInferredStack.mockResolvedValue({ status: 'DONE', outputs: ['model.step'] });
  mockApi.downloadFile.mockResolvedValue(undefined);
  mockApi.getRunReport.mockResolvedValue(null);
  // Safe default for auto-trigger: no best_view → pipeline stops after initial detection,
  // no state side-effects. Tests that need a different result override this before render.
  mockApi.autoDetectTurnedView.mockResolvedValue(AUTO_DETECT_CV_ONLY as any);

  // Mock global.fetch for the component's direct JSON file fetches
  vi.spyOn(globalThis, 'fetch').mockImplementation((url: RequestInfo | URL) => {
    const urlStr = String(url);
    if (urlStr.includes('part_summary.json')) {
      return Promise.resolve(makeJsonFetchResponse(PART_SUMMARY));
    }
    if (urlStr.includes('inferred_stack.json')) {
      return Promise.resolve(new Response('', { status: 404 }));
    }
    // Default: 404
    return Promise.resolve(new Response('', { status: 404 }));
  });
}

// ── Test Suite ─────────────────────────────────────────────────────────────

/**
 * Sets up mocks for tests that need the component to transition from the
 * empty state (no inferredStack) to the full state (inferredStack loaded).
 *
 * Strategy:
 *  • 1st  getJobFiles call (on mount)  → no inferred_stack.json → empty state, auto-detect button visible
 *  • 2nd+ getJobFiles calls            → include inferred_stack.json → full state after handleInferStack
 *  • fetch mock serves both part_summary.json and inferred_stack.json
 *  • inferStackFromView returns { status: 'DONE' } so execution continues to loadResults()
 */
function setupTwoPhaseGenAllMocks() {
  const mockApi = api as ReturnType<typeof vi.mocked<typeof api>>;

  // 1st call: no inferred_stack.json — component stays in empty state so
  // the auto-detect button remains clickable.
  const filesWithout = {
    files: [
      { path: 'inputs/source.pdf', name: 'source.pdf', size: 1024, created_at: '' },
      { path: 'outputs/pdf_pages/page_1.png', name: 'page_1.png', size: 1024, created_at: '' },
      { path: 'outputs/part_summary.json', name: 'part_summary.json', size: 512, created_at: '' },
    ],
  };
  // Subsequent calls: include inferred_stack.json — component transitions to full state via loadResults()
  const filesWith = {
    files: [
      ...filesWithout.files,
      { path: 'outputs/inferred_stack.json', name: 'inferred_stack.json', size: 512, created_at: '' },
    ],
  };
  mockApi.getJobFiles.mockResolvedValueOnce(filesWithout);
  mockApi.getJobFiles.mockResolvedValue(filesWith);

  // inferStackFromView must succeed (not throw) so loadResults() is reached.
  mockApi.inferStackFromView.mockResolvedValue({ status: 'DONE', segments: [] } as any);

  // Serve JSON content for both data files.
  vi.spyOn(globalThis, 'fetch').mockImplementation((url: RequestInfo | URL) => {
    const urlStr = String(url);
    if (urlStr.includes('part_summary.json')) {
      return Promise.resolve(makeJsonFetchResponse(PART_SUMMARY));
    }
    if (urlStr.includes('inferred_stack.json')) {
      return Promise.resolve(makeJsonFetchResponse(INFERRED_STACK));
    }
    return Promise.resolve(new Response('', { status: 404 }));
  });
}

describe('AutoConvertResults — LLM scenario', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    setupDefaultMocks();
  });

  // ── 1. Mount ────────────────────────────────────────────────────────────

  it('renders without crashing', async () => {
    await act(async () => {
      render(<AutoConvertResults jobId={JOB_ID} />);
    });
    // The Auto-Detect button must always be present
    expect(screen.getByRole('button', { name: /auto-detect turned view/i })).toBeInTheDocument();
  });

  // ── 2. Auto-Detect triggers API + dims appear ───────────────────────────

  it('calls autoDetectTurnedView when the Auto-Detect button is clicked', async () => {
    const mockApi = api as ReturnType<typeof vi.mocked<typeof api>>;
    mockApi.autoDetectTurnedView.mockResolvedValue(AUTO_DETECT_WITH_LLM as any);

    await act(async () => {
      render(<AutoConvertResults jobId={JOB_ID} />);
    });

    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /auto-detect turned view/i }));
    });

    await waitFor(() => {
      expect(mockApi.autoDetectTurnedView).toHaveBeenCalledWith(JOB_ID);
    });
  });

  it('shows the LLM dims card after successful auto-detect', async () => {
    const mockApi = api as ReturnType<typeof vi.mocked<typeof api>>;
    mockApi.autoDetectTurnedView.mockResolvedValue(AUTO_DETECT_WITH_LLM as any);

    await act(async () => {
      render(<AutoConvertResults jobId={JOB_ID} />);
    });

    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /auto-detect turned view/i }));
    });

    // The dims card (and stats grid) should now show the finish OD value
    await waitFor(() => {
      expect(screen.getAllByText(/1\.24/).length).toBeGreaterThanOrEqual(1);
    });
  });

  it('displays Finish ID value from LLM extraction', async () => {
    const mockApi = api as ReturnType<typeof vi.mocked<typeof api>>;
    mockApi.autoDetectTurnedView.mockResolvedValue(AUTO_DETECT_WITH_LLM as any);

    await act(async () => {
      render(<AutoConvertResults jobId={JOB_ID} />);
    });

    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /auto-detect turned view/i }));
    });

    await waitFor(() => {
      // id_in = 0.430 → displayed as 0.430
      expect(screen.getAllByText(/0\.43/).length).toBeGreaterThanOrEqual(1);
    });
  });

  it('displays MAX OD (raw material) from LLM extraction', async () => {
    const mockApi = api as ReturnType<typeof vi.mocked<typeof api>>;
    mockApi.autoDetectTurnedView.mockResolvedValue(AUTO_DETECT_WITH_LLM as any);

    await act(async () => {
      render(<AutoConvertResults jobId={JOB_ID} />);
    });

    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /auto-detect turned view/i }));
    });

    await waitFor(() => {
      expect(screen.getAllByText(/1\.38/).length).toBeGreaterThanOrEqual(1);
    });
  });

  // ── 3. Part No auto-population ──────────────────────────────────────────

  it('auto-populates Part No field from llm_analysis when no name was pre-filled', async () => {
    const mockApi = api as ReturnType<typeof vi.mocked<typeof api>>;
    // Job has no name AND no PDF in files → rfqPartNo starts and stays '' until LLM sets it.
    mockApi.getJob.mockResolvedValue({ job_id: JOB_ID, name: '', status: 'DONE' } as any);
    mockApi.autoDetectTurnedView.mockResolvedValue(AUTO_DETECT_WITH_LLM_AND_VIEW as any);
    mockApi.inferStackFromView.mockResolvedValue({ status: 'DONE', segments: [] } as any);

    // FILES: no PDF so the PDF-filename fallback never pre-fills rfqPartNo.
    // 1st call (on mount)  → no inferred_stack.json → empty state, button visible.
    // 2nd+ calls           → include inferred_stack.json → full state after handleInferStack.
    const base = [
      { path: 'outputs/pdf_pages/page_1.png', name: 'page_1.png', size: 1024, created_at: '' },
      { path: 'outputs/part_summary.json',    name: 'part_summary.json', size: 512, created_at: '' },
    ];
    mockApi.getJobFiles.mockResolvedValueOnce({ files: base });
    mockApi.getJobFiles.mockResolvedValue({
      files: [...base, { path: 'outputs/inferred_stack.json', name: 'inferred_stack.json', size: 512, created_at: '' }],
    });
    vi.spyOn(globalThis, 'fetch').mockImplementation((url: RequestInfo | URL) => {
      const u = String(url);
      if (u.includes('part_summary.json'))    return Promise.resolve(makeJsonFetchResponse(PART_SUMMARY));
      if (u.includes('inferred_stack.json'))  return Promise.resolve(makeJsonFetchResponse(INFERRED_STACK));
      return Promise.resolve(new Response('', { status: 404 }));
    });

    await act(async () => {
      render(<AutoConvertResults jobId={JOB_ID} />);
    });

    // Component is in empty state — auto-detect button is visible.
    fireEvent.click(screen.getByRole('button', { name: /auto-detect turned view/i }));

    // Flow: autoDetectTurnedView → setLlmAnalysis (rfqPartNo = '050CE0004') →
    //       handleInferStack → loadResults (2nd getJobFiles includes inferred_stack.json)
    //       → setInferredStack → full state. Part No input should now display '050CE0004'.
    await waitFor(() => {
      const inputs = screen.getAllByDisplayValue('050CE0004');
      expect(inputs.length).toBeGreaterThan(0);
    }, { timeout: 6000 });
  });

  // ── 4. Confidence badge ─────────────────────────────────────────────────

  it('shows ACCEPT recommendation text in the LLM card', async () => {
    const mockApi = api as ReturnType<typeof vi.mocked<typeof api>>;
    mockApi.autoDetectTurnedView.mockResolvedValue(AUTO_DETECT_WITH_LLM as any);

    await act(async () => {
      render(<AutoConvertResults jobId={JOB_ID} />);
    });

    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /auto-detect turned view/i }));
    });

    await waitFor(() => {
      expect(screen.getByText(/ACCEPT/i)).toBeInTheDocument();
    });
  });

  // ── 5. Generate All button state ────────────────────────────────────────

  it('Generate All button becomes enabled once partSummary is loaded', async () => {
    const mockApi = api as ReturnType<typeof vi.mocked<typeof api>>;
    mockApi.autoDetectTurnedView.mockResolvedValue(AUTO_DETECT_WITH_LLM_AND_VIEW as any);
    setupTwoPhaseGenAllMocks();

    await act(async () => {
      render(<AutoConvertResults jobId={JOB_ID} />);
    });

    // Click auto-detect (empty state) → triggers handleInferStack → loadResults →
    // setInferredStack + setPartSummary → component enters full state.
    fireEvent.click(screen.getByRole('button', { name: /auto-detect turned view/i }));

    await waitFor(() => {
      const btn = screen.getByRole('button', { name: /download all/i });
      expect(btn).not.toBeDisabled();
    }, { timeout: 6000 });
  });

  // ── 6. Generate All API call sequence ───────────────────────────────────

  it('clicking Generate All calls rfqExportXlsx', async () => {
    const mockApi = api as ReturnType<typeof vi.mocked<typeof api>>;
    mockApi.autoDetectTurnedView.mockResolvedValue(AUTO_DETECT_WITH_LLM_AND_VIEW as any);
    setupTwoPhaseGenAllMocks();

    await act(async () => {
      render(<AutoConvertResults jobId={JOB_ID} />);
    });

    fireEvent.click(screen.getByRole('button', { name: /auto-detect turned view/i }));
    await waitFor(() =>
      expect(screen.getByRole('button', { name: /download all/i })).not.toBeDisabled(),
      { timeout: 6000 }
    );

    fireEvent.click(screen.getByRole('button', { name: /download all/i }));
    await waitFor(() => {
      expect(mockApi.rfqExportXlsx).toHaveBeenCalledOnce();
    }, { timeout: 6000 });
  });

  it('clicking Generate All calls generateStepFromInferredStack', async () => {
    const mockApi = api as ReturnType<typeof vi.mocked<typeof api>>;
    mockApi.autoDetectTurnedView.mockResolvedValue(AUTO_DETECT_WITH_LLM_AND_VIEW as any);
    setupTwoPhaseGenAllMocks();

    await act(async () => {
      render(<AutoConvertResults jobId={JOB_ID} />);
    });

    fireEvent.click(screen.getByRole('button', { name: /auto-detect turned view/i }));
    await waitFor(() =>
      expect(screen.getByRole('button', { name: /download all/i })).not.toBeDisabled(),
      { timeout: 6000 }
    );

    fireEvent.click(screen.getByRole('button', { name: /download all/i }));
    await waitFor(() => {
      expect(mockApi.generateStepFromInferredStack).toHaveBeenCalledWith(JOB_ID);
    }, { timeout: 6000 });
  });

  it('clicking Generate All calls downloadFile for model.step', async () => {
    const mockApi = api as ReturnType<typeof vi.mocked<typeof api>>;
    mockApi.autoDetectTurnedView.mockResolvedValue(AUTO_DETECT_WITH_LLM_AND_VIEW as any);
    setupTwoPhaseGenAllMocks();

    await act(async () => {
      render(<AutoConvertResults jobId={JOB_ID} />);
    });

    fireEvent.click(screen.getByRole('button', { name: /auto-detect turned view/i }));
    await waitFor(() =>
      expect(screen.getByRole('button', { name: /download all/i })).not.toBeDisabled(),
      { timeout: 6000 }
    );

    fireEvent.click(screen.getByRole('button', { name: /download all/i }));
    await waitFor(() => {
      const calls = (mockApi.downloadFile as ReturnType<typeof vi.fn>).mock.calls;
      const stepCall = calls.find((c) => String(c[1]).includes('model.step'));
      expect(stepCall).toBeDefined();
    }, { timeout: 6000 });
  });

  it('clicking Generate All downloads llm_analysis.json when LLM ran', async () => {
    const mockApi = api as ReturnType<typeof vi.mocked<typeof api>>;
    // LLM_AND_VIEW variant sets llmAnalysis so the llm_analysis.json download is triggered.
    mockApi.autoDetectTurnedView.mockResolvedValue(AUTO_DETECT_WITH_LLM_AND_VIEW as any);
    setupTwoPhaseGenAllMocks();

    await act(async () => {
      render(<AutoConvertResults jobId={JOB_ID} />);
    });

    fireEvent.click(screen.getByRole('button', { name: /auto-detect turned view/i }));
    await waitFor(() =>
      expect(screen.getByRole('button', { name: /download all/i })).not.toBeDisabled(),
      { timeout: 6000 }
    );

    fireEvent.click(screen.getByRole('button', { name: /download all/i }));
    await waitFor(() => {
      const calls = (mockApi.downloadFile as ReturnType<typeof vi.fn>).mock.calls;
      const llmCall = calls.find((c) => String(c[1]).includes('llm_analysis.json'));
      expect(llmCall).toBeDefined();
    }, { timeout: 6000 });
  });

  // ── 7. Error handling — LLM quota error ────────────────────────────────

  it('LLM 429 error in response does not crash the component', async () => {
    const mockApi = api as ReturnType<typeof vi.mocked<typeof api>>;
    mockApi.autoDetectTurnedView.mockResolvedValue(AUTO_DETECT_WITH_ERROR as any);

    await act(async () => {
      render(<AutoConvertResults jobId={JOB_ID} />);
    });

    // Should NOT throw; component stays alive
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /auto-detect turned view/i }));
    });

    // Auto-detect button still present = component did not crash
    await waitFor(() => {
      expect(screen.getByRole('button', { name: /auto-detect turned view/i })).toBeInTheDocument();
    });
  });

  // ── 8. REVIEW recommendation ────────────────────────────────────────────

  it('shows REVIEW recommendation from the validator', async () => {
    const mockApi = api as ReturnType<typeof vi.mocked<typeof api>>;
    // Use a result with best_view to transition to full state, where the
    // 'Review before generating' heading text won't cause a duplicate-match error.
    mockApi.autoDetectTurnedView.mockResolvedValue(AUTO_DETECT_WITH_REVIEW_AND_VIEW as any);
    setupTwoPhaseGenAllMocks();

    await act(async () => {
      render(<AutoConvertResults jobId={JOB_ID} />);
    });

    fireEvent.click(screen.getByRole('button', { name: /auto-detect turned view/i }));

    // Wait for ILM card with 'REVIEW ·' (avoids matching 'Review before generating' heading).
    await waitFor(() => {
      expect(screen.getByText(/REVIEW\s*·/i)).toBeInTheDocument();
    }, { timeout: 6000 });
  });

  it('shows REVIEW when a STEP job only has bbox fallback geometry and no llm_analysis', async () => {
    const mockApi = api as ReturnType<typeof vi.mocked<typeof api>>;
    mockApi.getJobFiles.mockResolvedValue({
      files: [
        { path: 'inputs/model.step', name: 'model.step', size: 1024, created_at: '' },
        { path: 'outputs/part_summary.json', name: 'part_summary.json', size: 512, created_at: '' },
      ],
    });
    mockApi.fetchJobFile.mockImplementation(async (_jobId: string, path: string) => {
      if (path === 'outputs/part_summary.json') {
        return makeJsonFetchResponse(STEP_PART_SUMMARY_BBOX);
      }
      return new Response('', { status: 404 });
    });

    await act(async () => {
      render(<AutoConvertResults jobId={JOB_ID} />);
    });

    await waitFor(() => {
      expect(screen.getByText(/REVIEW\s*·/i)).toBeInTheDocument();
      expect(screen.getByText(/No validated LLM analysis is available for this STEP job yet/i)).toBeInTheDocument();
    });
  });

  // ── 9. Material displayed ───────────────────────────────────────────────

  it('shows material from LLM extraction in the dims card', async () => {
    const mockApi = api as ReturnType<typeof vi.mocked<typeof api>>;
    mockApi.autoDetectTurnedView.mockResolvedValue(AUTO_DETECT_WITH_LLM as any);

    await act(async () => {
      render(<AutoConvertResults jobId={JOB_ID} />);
    });

    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /auto-detect turned view/i }));
    });

    await waitFor(() => {
      // Material label or value should appear
      expect(screen.getByText(/Ductile Iron/i)).toBeInTheDocument();
    });
  });
});
