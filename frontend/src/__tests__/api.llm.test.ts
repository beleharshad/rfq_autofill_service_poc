/**
 * Tests for api.ts — LLM-related service functions.
 *
 * Covers:
 *  - autoDetectTurnedView: passes llm_analysis from backend unchanged
 *  - downloadFile: succeeds on 200, retries on 404, throws after max retries
 *  - rfqExportXlsx: parses Content-Disposition header, returns blob + filename
 *
 * All HTTP calls are intercepted via vi.spyOn(globalThis, 'fetch').
 * Zero real network requests.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { api } from '../services/api';

// ── Constants ──────────────────────────────────────────────────────────────

const JOB_ID = 'test-job-mock-001';

const LLM_ANALYSIS = {
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

const AUTO_DETECT_PAYLOAD = {
  job_id: JOB_ID,
  ranked_views: [],
  best_view: null,
  confidence_threshold: 0.65,
  total_views_analyzed: 0,
  llm_analysis: LLM_ANALYSIS,
};

// ── Helpers ────────────────────────────────────────────────────────────────

function makeJsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

function makeBlobResponse(content: string, filename: string, status = 200): Response {
  const blob = new Blob([content], { type: 'application/octet-stream' });
  return new Response(blob, {
    status,
    headers: {
      'Content-Type': 'application/octet-stream',
      'Content-Disposition': `attachment; filename="${filename}"`,
    },
  });
}

// ── Setup ──────────────────────────────────────────────────────────────────

let fetchMock: any;

beforeEach(() => {
  fetchMock = vi.spyOn(globalThis, 'fetch');
  // Prevent actual DOM manipulation from downloadFile
  vi.spyOn(document.body, 'appendChild').mockImplementation((el) => el);
  vi.spyOn(document.body, 'removeChild').mockImplementation((el) => el);
});

afterEach(() => {
  vi.restoreAllMocks();
});

// ==========================================================================
// 1. autoDetectTurnedView
// ==========================================================================

describe('api.autoDetectTurnedView', () => {
  it('calls the correct endpoint with POST', async () => {
    fetchMock.mockResolvedValueOnce(makeJsonResponse(AUTO_DETECT_PAYLOAD));
    await api.autoDetectTurnedView(JOB_ID);
    expect(fetchMock).toHaveBeenCalledWith(
      `/api/v1/jobs/${JOB_ID}/pdf/auto_detect_turned_view`,
      expect.objectContaining({ method: 'POST' })
    );
  });

  it('returns llm_analysis from the backend payload', async () => {
    fetchMock.mockResolvedValueOnce(makeJsonResponse(AUTO_DETECT_PAYLOAD));
    const result = await api.autoDetectTurnedView(JOB_ID);
    expect(result.llm_analysis).toBeDefined();
    expect(result.llm_analysis!.extracted!.od_in).toBe(1.24);
  });

  it('returns all four critical dimensions', async () => {
    fetchMock.mockResolvedValueOnce(makeJsonResponse(AUTO_DETECT_PAYLOAD));
    const { llm_analysis } = await api.autoDetectTurnedView(JOB_ID);
    const ext = llm_analysis!.extracted!;
    expect(ext.od_in).toBe(1.24);
    expect(ext.max_od_in).toBe(1.38);
    expect(ext.id_in).toBe(0.43);
    expect(ext.length_in).toBe(0.63);
  });

  it('returns part_number from llm_analysis', async () => {
    fetchMock.mockResolvedValueOnce(makeJsonResponse(AUTO_DETECT_PAYLOAD));
    const { llm_analysis } = await api.autoDetectTurnedView(JOB_ID);
    expect(llm_analysis!.extracted!.part_number).toBe('050CE0004');
  });

  it('carries ACCEPT recommendation from validator', async () => {
    fetchMock.mockResolvedValueOnce(makeJsonResponse(AUTO_DETECT_PAYLOAD));
    const { llm_analysis } = await api.autoDetectTurnedView(JOB_ID);
    expect(llm_analysis!.validation!.recommendation).toBe('ACCEPT');
    expect(llm_analysis!.validation!.overall_confidence).toBeGreaterThanOrEqual(0.85);
  });

  it('returns valid=true when agents agree', async () => {
    fetchMock.mockResolvedValueOnce(makeJsonResponse(AUTO_DETECT_PAYLOAD));
    const { llm_analysis } = await api.autoDetectTurnedView(JOB_ID);
    expect(llm_analysis!.valid).toBe(true);
  });

  it('returns error field when LLM failed on backend', async () => {
    const payload = { ...AUTO_DETECT_PAYLOAD, llm_analysis: { error: 'source.pdf not found' } };
    fetchMock.mockResolvedValueOnce(makeJsonResponse(payload));
    const { llm_analysis } = await api.autoDetectTurnedView(JOB_ID);
    expect(llm_analysis!.error).toContain('source.pdf');
  });

  it('throws on non-200 HTTP response', async () => {
    fetchMock.mockResolvedValueOnce(makeJsonResponse({ detail: 'Job not found' }, 404));
    await expect(api.autoDetectTurnedView(JOB_ID)).rejects.toThrow('Job not found');
  });

  it('cv result keys are preserved alongside llm_analysis', async () => {
    fetchMock.mockResolvedValueOnce(makeJsonResponse(AUTO_DETECT_PAYLOAD));
    const result = await api.autoDetectTurnedView(JOB_ID);
    expect(result.job_id).toBe(JOB_ID);
    expect(Array.isArray(result.ranked_views)).toBe(true);
  });
});

// ==========================================================================
// 2. downloadFile
// ==========================================================================

describe('api.downloadFile', () => {
  it('resolves immediately on a 200 response with non-empty blob', async () => {
    fetchMock.mockResolvedValueOnce(makeBlobResponse('step content here', 'model.step'));
    await expect(
      api.downloadFile(JOB_ID, 'outputs/model.step', 'part_model.step', 1, 0)
    ).resolves.toBeUndefined();
  });

  it('retries once on 404 then succeeds on second attempt', async () => {
    fetchMock
      .mockResolvedValueOnce(new Response('', { status: 404 }))
      .mockResolvedValueOnce(makeBlobResponse('step content', 'model.step'));
    await expect(
      api.downloadFile(JOB_ID, 'outputs/model.step', 'part_model.step', 2, 0)
    ).resolves.toBeUndefined();
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it('throws after exhausting all retries', async () => {
    fetchMock.mockResolvedValue(new Response('', { status: 404 }));
    await expect(
      api.downloadFile(JOB_ID, 'outputs/model.step', 'part_model.step', 2, 0)
    ).rejects.toThrow();
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it('throws if downloaded blob is empty', async () => {
    // jsdom's Response does not preserve blob.size=0, so we mock the response object directly
    const emptyBlob = { size: 0, type: 'application/octet-stream' } as Blob;
    fetchMock.mockResolvedValueOnce({
      ok: true,
      status: 200,
      blob: () => Promise.resolve(emptyBlob),
      headers: { get: () => null },
    } as unknown as Response);
    await expect(
      api.downloadFile(JOB_ID, 'outputs/model.step', 'part_model.step', 1, 0)
    ).rejects.toThrow(/empty/i);
  });

  it('triggers a DOM link click on success', async () => {
    fetchMock.mockResolvedValueOnce(makeBlobResponse('data', 'model.step'));
    const clickSpy = vi.fn();
    vi.spyOn(document, 'createElement').mockReturnValue({
      href: '',
      download: '',
      click: clickSpy,
    } as unknown as HTMLAnchorElement);
    await api.downloadFile(JOB_ID, 'outputs/model.step', 'part_model.step', 1, 0);
    expect(clickSpy).toHaveBeenCalledOnce();
  });

  it('uses the correct URL (job_id + file_path encoded)', async () => {
    fetchMock.mockResolvedValueOnce(makeBlobResponse('data', 'model.step'));
    await api.downloadFile(JOB_ID, 'outputs/model.step', 'model.step', 1, 0);
    const calledUrl = fetchMock.mock.calls[0][0] as string;
    expect(calledUrl).toContain(JOB_ID);
    expect(calledUrl).toContain(encodeURIComponent('outputs/model.step'));
  });
});

// ==========================================================================
// 3. rfqExportXlsx
// ==========================================================================

describe('api.rfqExportXlsx', () => {
  const PAYLOAD = {
    rfq_id: JOB_ID,
    part_no: '050CE0004',
    mode: 'ENVELOPE' as const,
    vendor_quote_mode: true,
    source: { job_id: JOB_ID, part_summary: null, step_metrics: null },
    tolerances: { rm_od_allowance_in: 0.1, rm_len_allowance_in: 0.35 },
    cost_inputs: null,
  };

  it('returns blob and filename from Content-Disposition header', async () => {
    const xlsxBlob = new Blob(['PK...xlsx content'], {
      type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    });
    fetchMock.mockResolvedValueOnce(
      new Response(xlsxBlob, {
        status: 200,
        headers: { 'Content-Disposition': 'attachment; filename="050CE0004_rfq.xlsx"' },
      })
    );
    const { blob, filename } = await api.rfqExportXlsx(PAYLOAD, 'master');
    // jsdom's response.blob() may not pass instanceof Blob across module boundaries;
    // verify by checking duck-type properties instead.
    expect(blob).toBeTruthy();
    expect(typeof blob.size).toBe('number');
    expect(filename).toBe('050CE0004_rfq.xlsx');
  });

  it('posts to the correct endpoint', async () => {
    const xlsxBlob = new Blob(['data']);
    fetchMock.mockResolvedValueOnce(
      new Response(xlsxBlob, {
        status: 200,
        headers: { 'Content-Disposition': 'attachment; filename="test.xlsx"' },
      })
    );
    await api.rfqExportXlsx(PAYLOAD, 'master');
    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining('export_xlsx'),
      expect.objectContaining({ method: 'POST' })
    );
  });

  it('sends rfq_id and part_no in the request body', async () => {
    const xlsxBlob = new Blob(['data']);
    fetchMock.mockResolvedValueOnce(
      new Response(xlsxBlob, {
        status: 200,
        headers: { 'Content-Disposition': 'attachment; filename="test.xlsx"' },
      })
    );
    await api.rfqExportXlsx(PAYLOAD, 'master');
    const requestInit = fetchMock.mock.calls[0][1] as RequestInit | undefined;
    const body = JSON.parse(String(requestInit?.body ?? '{}'));
    expect(body.rfq_id).toBe(JOB_ID);
    expect(body.part_no).toBe('050CE0004');
  });

  it('falls back to "rfq_export.xlsx" filename when Content-Disposition is missing', async () => {
    const xlsxBlob = new Blob(['data']);
    fetchMock.mockResolvedValueOnce(new Response(xlsxBlob, { status: 200 }));
    const { filename } = await api.rfqExportXlsx(PAYLOAD, 'master');
    expect(filename).toBe('rfq_export.xlsx');
  });

  it('throws on non-200 HTTP', async () => {
    fetchMock.mockResolvedValueOnce(
      makeJsonResponse({ detail: 'Export failed: model not found' }, 422)
    );
    await expect(api.rfqExportXlsx(PAYLOAD, 'master')).rejects.toThrow('Export failed');
  });
});

// ==========================================================================
// 4. fetch3dPreviewBlobUrl
// ==========================================================================

describe('api.fetch3dPreviewBlobUrl', () => {
  it('fetches the preview endpoint and returns a blob URL on success', async () => {
    fetchMock.mockResolvedValueOnce(makeBlobResponse('glb-binary', 'model.glb'));
    const url = await api.fetch3dPreviewBlobUrl(JOB_ID);
    expect(typeof url).toBe('string');
    expect(url.startsWith('blob:')).toBe(true);
    const calledUrl = fetchMock.mock.calls[0][0] as string;
    expect(calledUrl).toContain(`/jobs/${JOB_ID}/3d-preview`);
  });

  it('surfaces backend preview reason when the preview endpoint fails', async () => {
    fetchMock.mockResolvedValueOnce(makeJsonResponse({ detail: { reason: 'STEP→GLB conversion failed' } }, 503));
    await expect(api.fetch3dPreviewBlobUrl(JOB_ID)).rejects.toThrow('STEP→GLB conversion failed');
  });
});

// ==========================================================================
// 4. LLM analysis helper validation (dimension sanity rules)
// ==========================================================================

describe('LLM dimension sanity rules (pure logic)', () => {
  const dims = LLM_ANALYSIS.extracted;

  it('od_in > id_in (finish OD must exceed bore)', () => {
    expect(dims.od_in).toBeGreaterThan(dims.id_in);
  });

  it('max_od_in >= od_in (raw stock never smaller than finish)', () => {
    expect(dims.max_od_in).toBeGreaterThanOrEqual(dims.od_in);
  });

  it('all critical dimensions are positive', () => {
    expect(dims.od_in).toBeGreaterThan(0);
    expect(dims.id_in).toBeGreaterThan(0);
    expect(dims.length_in).toBeGreaterThan(0);
    expect(dims.max_od_in).toBeGreaterThan(0);
  });

  it('overall_confidence from validator is ≥ 0.85 for ACCEPT', () => {
    expect(LLM_ANALYSIS.validation.overall_confidence).toBeGreaterThanOrEqual(0.85);
  });

  it('code_issues list is empty for clean extraction', () => {
    expect(LLM_ANALYSIS.code_issues).toHaveLength(0);
  });
});

// ── effectiveDims pure function ────────────────────────────────────────────

import { effectiveDims } from '../components/AutoConvertResults/AutoConvertResults';

describe('effectiveDims', () => {
  const llm = {
    extracted: { od_in: 1.24, max_od_in: 1.38, id_in: 0.43, length_in: 0.63 },
  };

  it('returns LLM values when all overrides are null', () => {
    const d = effectiveDims(llm, { od: null, maxOd: null, id: null, len: null });
    expect(d.od_in).toBeCloseTo(1.24);
    expect(d.max_od_in).toBeCloseTo(1.38);
    expect(d.id_in).toBeCloseTo(0.43);
    expect(d.length_in).toBeCloseTo(0.63);
  });

  it('applies override values when provided', () => {
    const d = effectiveDims(llm, { od: 2.0, maxOd: null, id: null, len: null });
    expect(d.od_in).toBeCloseTo(2.0);
    expect(d.max_od_in).toBeCloseTo(1.38);
  });

  it('clamps id_in to 0 minimum and od_in to 0.001 minimum', () => {
    const d = effectiveDims(null, { od: -1, maxOd: -5, id: -0.5, len: -2 });
    expect(d.od_in).toBe(0.001);
    expect(d.max_od_in).toBe(0.001);
    expect(d.id_in).toBe(0);
    expect(d.length_in).toBe(0.001);
  });
});
