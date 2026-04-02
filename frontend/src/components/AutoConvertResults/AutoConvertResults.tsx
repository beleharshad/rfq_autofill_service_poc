// AutoConvertResults.tsx — clean single-panel UI with in-browser 3D preview
import { useEffect, useMemo, useRef, useState } from 'react';
import { api } from '../../services/api';
import type { FileInfo, RFQAutofillRequest, CorrectionsMap } from '../../services/types';
import LatheViewer from './LatheViewer';
import { setSegments, type Segment as SegmentStoreType } from '../../state/segmentStore';
import './AutoConvertResults.css';

// ── Types ──────────────────────────────────────────────────────────────────

interface Segment {
  z_start: number;
  z_end: number;
  od_diameter: number;
  id_diameter: number;
  wall_thickness?: number;
  volume_in3?: number;
  od_area_in2?: number;
  id_area_in2?: number;
  confidence?: number;
  flags?: string[];
}

interface AutoConvertResultsProps {
  jobId: string;
  onSwitchToManual?: () => void;
  pdfPageUrl?: string;
}

// ── Pure helpers ───────────────────────────────────────────────────────────

const normalizePartNo = (s: string) => (s || '').trim().replace(/([_-])[a-zA-Z]$/, '');

const sleep = (ms: number) => new Promise<void>((r) => setTimeout(r, ms));

const downloadBlob = (blob: Blob, filename: string) => {
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename || 'rfq_export.xlsx';
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 5000);
};

/** Merge LLM extracted dims with optional user overrides (null = use LLM). */
export function effectiveDims(
  llm: any,
  overrides: { od: number | null; maxOd: number | null; id: number | null; len: number | null }
) {
  const odIn    = overrides.od    ?? llm?.extracted?.od_in     ?? 0;
  // max_od_in is often null when the part has a single OD — fall back to od_in
  const maxOdIn = overrides.maxOd ?? llm?.extracted?.max_od_in ?? odIn ?? 0;
  return {
    od_in:     Math.max(0.001, odIn),
    max_od_in: Math.max(0.001, maxOdIn),
    id_in:     Math.max(0,     overrides.id    ?? llm?.extracted?.id_in     ?? 0),
    length_in: Math.max(0.001, overrides.len   ?? llm?.extracted?.length_in ?? 0),
  };
}

// ── Component ──────────────────────────────────────────────────────────────

function AutoConvertResults({
  jobId,
  onSwitchToManual: _onSwitchToManual,
  pdfPageUrl: propPdfPageUrl,
}: AutoConvertResultsProps) {
  // Core state
  const [loading, setLoading]               = useState(true);
  const [error, setError]                   = useState<string | null>(null);
  const [detecting, setDetecting]           = useState(false);
  const [inferring, setInferring]           = useState(false);
  const [detectionError, setDetectionError] = useState<string | null>(null);

  // Result state
  const [inferredStack, setInferredStack] = useState<any>(null);
  const [partSummary, setPartSummary]     = useState<any>(null);
  const [llmAnalysis, setLlmAnalysis]     = useState<any>(null);
  const [pdfPageUrl, setPdfPageUrl]       = useState<string | null>(propPdfPageUrl ?? null);

  // Cache the last-fetched files list so ensurePdfPagesReady doesn't need an extra getJobFiles call
  const cachedFilesRef = useRef<{ files: FileInfo[] } | null>(null);

  // Part No
  const [rfqPartNo, setRfqPartNo] = useState('');

  // Viewer options
  const [mergeSegments, setMergeSegments] = useState(false);

  // Dim overrides (null = use LLM value)
  const [odOvr,    setOdOvr]    = useState<number | null>(null);
  const [maxOdOvr, setMaxOdOvr] = useState<number | null>(null);
  const [idOvr,    setIdOvr]    = useState<number | null>(null);
  const [lenOvr,   setLenOvr]   = useState<number | null>(null);

  // Track whether server-side corrections have been loaded into the override states
  // so we don't immediately re-save them on first render.
  const correctionsLoadedRef = useRef(false);

  // Generate state
  const [generating, setGenerating] = useState(false);
  const [genStatus, setGenStatus]   = useState<string | null>(null);
  const [genError, setGenError]     = useState<string | null>(null);
  const [genDropOpen, setGenDropOpen] = useState(false);
  const genDropRef = useRef<HTMLDivElement>(null);

  // Close dropdown on outside click
  useEffect(() => {
    if (!genDropOpen) return;
    const handler = (e: MouseEvent) => {
      if (genDropRef.current && !genDropRef.current.contains(e.target as Node)) {
        setGenDropOpen(false);
      }
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, [genDropOpen]);

  // ── On mount ─────────────────────────────────────────────────────────────

  useEffect(() => {
    // On mount: load existing outputs, then auto-run detection if none exist.
    // This makes Create Job → Job Page a fully automatic end-to-end pipeline.
    (async () => {
      const result = await loadResults();
      // result === null         → backend error during load, skip auto-detect
      // result === true         → existing stack found, LLM done, nothing to do
      // result === false        → fresh job, no outputs yet → run full pipeline
      // result === 'interrupted'→ stack may exist but LLM was killed → re-run LLM only
      if (result === false || result === 'interrupted') {
        await handleAutoDetect();
      }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [jobId]);

  // Load persisted corrections on mount and pre-populate dim overrides.
  useEffect(() => {
    correctionsLoadedRef.current = false;
    api.getCorrections(jobId)
      .then((corrs: CorrectionsMap) => {
        if (corrs['od_in']?.value != null)     setOdOvr(Number(corrs['od_in'].value));
        if (corrs['max_od_in']?.value != null) setMaxOdOvr(Number(corrs['max_od_in'].value));
        if (corrs['id_in']?.value != null)     setIdOvr(Number(corrs['id_in'].value));
        if (corrs['length_in']?.value != null) setLenOvr(Number(corrs['length_in'].value));
      })
      .catch(() => { /* no corrections saved yet — ignore */ })
      .finally(() => { correctionsLoadedRef.current = true; });
  }, [jobId]);

  // Persist override changes to the server.
  // Fires after every user-driven change; the correctionsLoadedRef guard prevents
  // saving during the initial hydration from the server.
  useEffect(() => {
    if (!correctionsLoadedRef.current) return;
    const llmExt = (llmAnalysis as any)?.extracted ?? {};
    const pairs: [string, number | null, number | null][] = [
      ['od_in',      odOvr,    llmExt.od_in     ?? null],
      ['max_od_in',  maxOdOvr, llmExt.max_od_in ?? null],
      ['id_in',      idOvr,    llmExt.id_in     ?? null],
      ['length_in',  lenOvr,   llmExt.length_in ?? null],
    ];
    for (const [field, value, original] of pairs) {
      // Save the value (even null — null tells the server the override was cleared).
      api.saveCorrection(jobId, field, value as any, original).catch(() => {});
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [odOvr, maxOdOvr, idOvr, lenOvr]);

  // Subscribe to the /llm-stream endpoint while the background LLM thread is running.
  // Uses fetch + ReadableStream so the API key stays in the X-API-Key header
  // and never appears in the URL or server access logs.
  useEffect(() => {
    if (!llmAnalysis?.pending) return;
    const _apiKey = import.meta.env.VITE_INTERNAL_API_KEY as string | undefined;
    const url = `${import.meta.env.VITE_API_URL ?? ''}/api/v1/jobs/${jobId}/llm-stream`;
    const headers: Record<string, string> = {};
    if (_apiKey) headers['X-API-Key'] = _apiKey;
    const controller = new AbortController();
    let active = true;

    (async () => {
      try {
        const resp = await globalThis.fetch(url, { headers, signal: controller.signal });
        if (!resp.ok || !resp.body) return;
        const reader = resp.body.getReader();
        const decoder = new TextDecoder();
        let buf = '';
        while (active) {
          const { done, value } = await reader.read();
          if (done) break;
          buf += decoder.decode(value, { stream: true });
          const lines = buf.split('\n');
          buf = lines.pop() ?? '';
          for (const line of lines) {
            if (!line.startsWith('data: ')) continue;
            try {
              const data = JSON.parse(line.slice(6).trim());
              if (data?.error_type === 'interrupted') {
                handleAutoDetect();
                return;
              }
              setLlmAnalysis(data);
              if (!rfqPartNo.trim() && (data as any).extracted?.part_number) {
                setRfqPartNo((data as any).extracted.part_number);
              }
            } catch { /* ignore malformed message */ }
          }
        }
      } catch { /* aborted or network error */ }
    })();

    return () => { active = false; controller.abort(); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [llmAnalysis?.pending, jobId]);

  // ── Loaders ───────────────────────────────────────────────────────────────

  // Returns true         = existing stack found, LLM done (skip auto-detect)
  //         false        = backend OK but no stack yet (trigger auto-detect)
  //         'interrupted'= stack may exist but LLM was killed mid-run (re-trigger LLM only)
  //         null         = backend error (skip auto-detect — nothing to call)
  const loadResults = async (): Promise<boolean | 'interrupted' | null> => {
    console.log('[AutoConvertResults] Loading results for job:', jobId);
    let foundInferredStack = false;
    let llmInterrupted = false;
    try {
      setLoading(true);
      setError(null);

      const files = await api.getJobFiles(jobId);
      cachedFilesRef.current = files;

      // Determine PDF page url (first page image — pages are 0-indexed: page_0.png, page_1.png …)
      if (!propPdfPageUrl) {
        const pageImg = files.files.find((f) => /outputs\/pdf_pages\/page_\d+\.(png|jpg|jpeg)$/i.test(f.path));
        if (pageImg) {
          try {
            const blobUrl = await api.fetchBlobUrl(jobId, pageImg.path);
            setPdfPageUrl(blobUrl);
          } catch { /* drawing preview unavailable */ }
        }
      }

      // Prefill Part No from job name or PDF filename
      if (!rfqPartNo.trim()) {
        try {
          const job = await api.getJob(jobId);
          const jobName = (job?.name || '').trim();
          if (jobName) {
            setRfqPartNo(normalizePartNo(jobName));
          } else {
            const pdf = files.files.find((f) => f.name.toLowerCase().endsWith('.pdf'));
            if (pdf?.name) setRfqPartNo(normalizePartNo(pdf.name.replace(/\.pdf$/i, '')));
          }
        } catch (e) {
          console.warn('[AutoConvertResults] Failed to prefill part no:', e);
        }
      }

      // inferred_stack.json
      const inferredStackFile = files.files.find((f) => f.path === 'outputs/inferred_stack.json');
      if (inferredStackFile) {
        const res = await api.fetchJobFile(jobId, inferredStackFile.path);
        if (res.ok) {
          const data = await res.json();
          foundInferredStack = true;
          setInferredStack(data);
          if (data.segments && Array.isArray(data.segments)) {
            const segsForStore: SegmentStoreType[] = data.segments.map((seg: any) => ({
              z_start: seg.z_start, z_end: seg.z_end,
              od_diameter: seg.od_diameter, id_diameter: seg.id_diameter,
              wall_thickness: seg.wall_thickness, confidence: seg.confidence,
              flags: seg.flags, volume_in3: seg.volume_in3,
              od_area_in2: seg.od_area_in2, id_area_in2: seg.id_area_in2,
            }));
            setSegments(jobId, segsForStore);
          }
        }
      } else {
        setInferredStack(null);
      }

      // part_summary.json
      const partSummaryFile = files.files.find((f) => f.path === 'outputs/part_summary.json');
      if (partSummaryFile) {
        const res = await api.fetchJobFile(jobId, partSummaryFile.path);
        if (res.ok) {
          const data = await res.json();
          setPartSummary(data);
          if (data.segments && Array.isArray(data.segments)) {
            const segsForStore: SegmentStoreType[] = data.segments.map((seg: any) => ({
              z_start: seg.z_start, z_end: seg.z_end,
              od_diameter: seg.od_diameter, id_diameter: seg.id_diameter,
              wall_thickness: seg.wall_thickness, confidence: seg.confidence,
              flags: seg.flags, volume_in3: seg.volume_in3,
              od_area_in2: seg.od_area_in2, id_area_in2: seg.id_area_in2,
            }));
            setSegments(jobId, segsForStore);
          }
        }
      } else {
        setPartSummary(null);
      }

      // llm_analysis — read from static file if it exists; if not yet present,
      // handleAutoDetect will set it from the response body. No API call needed here.
      // If interrupted (server restarted mid-run), don't surface the error — auto-retry instead.
      const llmFile = files.files.find((f) => f.path === 'outputs/llm_analysis.json');
      if (llmFile) {
        try {
          const res = await api.fetchJobFile(jobId, llmFile.path, { cache: 'no-store' });
          if (res.ok) {
            const data = await res.json();
            if (data?.error_type === 'interrupted') {
              llmInterrupted = true; // silently re-trigger — don't show error to user
            } else {
              setLlmAnalysis(data);
            }
          }
        } catch { /* optional */ }
      }
    } catch (err) {
      console.error('[AutoConvertResults] Error loading results:', err);
      setError(err instanceof Error ? err.message : 'Failed to load results');
      return null; // signal: backend error — caller must not attempt auto-detect
    } finally {
      setLoading(false);
    }
    // Successful load — clear any stale detectionError from a prior failed auto-detect attempt
    setDetectionError(null);
    return llmInterrupted ? 'interrupted' : foundInferredStack;
  };

  // ── PDF pages gate ────────────────────────────────────────────────────────

  const ensurePdfPagesReady = async () => {
    // Reuse cached files from loadResults if available — avoids redundant getJobFiles call
    const files = cachedFilesRef.current ?? await api.getJobFiles(jobId);
    const pageImages = files.files.filter((f) => f.path.startsWith('outputs/pdf_pages/'));
    if (pageImages.length > 0) return;

    const pdfFile = files.files.find((f) => f.name.toLowerCase().endsWith('.pdf'));
    if (!pdfFile) throw new Error('No PDF file found. Please upload a PDF first.');

    const pdfResponse = await api.fetchJobFile(jobId, pdfFile.path);
    const pdfBlob = await pdfResponse.blob();
    const pdfFileObj = new File([pdfBlob], pdfFile.name, { type: 'application/pdf' });
    await api.uploadPdf(jobId, pdfFileObj);

    const timeoutMs = 30_000;
    const start = Date.now();
    while (Date.now() - start < timeoutMs) {
      const check = await api.getJobFiles(jobId);
      const imgs = check.files.filter((f) => f.path.startsWith('outputs/pdf_pages/'));
      if (imgs.length > 0) return;
      await sleep(1000);
    }
    throw new Error('Timed out waiting for PDF pages to render (outputs/pdf_pages/).');
  };

  // ── Handlers ──────────────────────────────────────────────────────────────

  const handleAutoDetect = async () => {
    console.log('[AutoConvertResults] Starting auto-detection:', jobId);
    setDetecting(true);
    setDetectionError(null);
    try {
      await ensurePdfPagesReady();

      await api.detectViews(jobId).catch((err: unknown) => {
        throw new Error(`View detection failed: ${err instanceof Error ? err.message : 'network error'}`);
      });

      const result = await api.autoDetectTurnedView(jobId).catch((err: unknown) => {
        throw new Error(`Auto-detect request failed: ${err instanceof Error ? err.message : 'network error'}`);
      });

      if (result.llm_analysis && !result.llm_analysis.error) {
        setLlmAnalysis(result.llm_analysis);
        if (!rfqPartNo.trim() && result.llm_analysis.extracted?.part_number) {
          setRfqPartNo(result.llm_analysis.extracted.part_number);
        }
      }

      if (result.best_view && (result.best_view.scores?.view_conf ?? 0) >= 0.65) {
        await handleInferStack(result.best_view.page, result.best_view.view_index);
      }
    } catch (err) {
      console.error('[AutoConvertResults] Error during auto-detection:', err);
      setDetectionError(err instanceof Error ? err.message : 'Failed to detect turned view');
    } finally {
      setDetecting(false);
    }
  };

  const handleInferStack = async (page?: number, viewIndex?: number) => {
    console.log('[AutoConvertResults] Stack inference:', jobId, page, viewIndex);
    setInferring(true);
    setDetectionError(null);
    try {
      const result = await api.inferStackFromView(jobId, page, viewIndex);
      if (result.status === 'VALIDATION_FAILED') {
        setDetectionError(result.message || 'Auto-detect validation failed.');
        return;
      }
      await loadResults();
    } catch (err) {
      console.error('[AutoConvertResults] Error during stack inference:', err);
      setDetectionError(err instanceof Error ? err.message : 'Failed to infer stack');
    } finally {
      setInferring(false);
    }
  };

  // ── Shared helpers for individual download actions ─────────────────────

  const resolvedPartNo = () =>
    normalizePartNo(rfqPartNo.trim() || llmAnalysis?.extracted?.part_number || jobId.slice(0, 8));

  const buildPayload = (partNo: string): RFQAutofillRequest => {
    // Merge user dim overrides (odOvr etc.) with raw LLM extracted values.
    // Priority: user override → LLM extracted → 0 (handled by effectiveDims).
    const dims = effectiveDims(llmAnalysis, { od: odOvr, maxOd: maxOdOvr, id: idOvr, len: lenOvr });
    const llmExt = llmAnalysis?.extracted;
    const dimensionOverrides: Record<string, number> | undefined = (llmExt || odOvr != null || idOvr != null || lenOvr != null)
      ? Object.fromEntries(
          [
            ['finish_od_in',  dims.od_in     > 0.001 ? dims.od_in     : null],
            ['finish_id_in',  dims.id_in     > 0     ? dims.id_in     : null],
            ['finish_len_in', dims.length_in > 0.001 ? dims.length_in : null],
          ].filter(([, v]) => v != null) as [string, number][]
        )
      : undefined;

    return {
      rfq_id: jobId,
      part_no: partNo,
      mode: 'ENVELOPE',
      vendor_quote_mode: true,
      source: { job_id: jobId, part_summary: partSummary as any, step_metrics: null },
      tolerances: { rm_od_allowance_in: 0.10, rm_len_allowance_in: 0.35 },
      cost_inputs: null,
      ...(dimensionOverrides && Object.keys(dimensionOverrides).length > 0
        ? { dimension_overrides: dimensionOverrides }
        : {}),
    };
  };

  const handleDownloadExcel = async () => {
    if (!partSummary) return;
    setGenDropOpen(false);
    setGenerating(true); setGenError(null); setGenStatus('Generating Excel…');
    try {
      const { blob, filename } = await api.rfqExportXlsx(buildPayload(resolvedPartNo()), 'master');
      downloadBlob(blob, filename);
      setGenStatus('✅ Excel downloaded.');
    } catch (err) {
      setGenError(err instanceof Error ? err.message : 'Excel export failed');
      setGenStatus(null);
    } finally { setGenerating(false); }
  };

  const handleDownloadStep = async () => {
    if (!partSummary) return;
    setGenDropOpen(false);
    const partNo = resolvedPartNo();
    setGenerating(true); setGenError(null); setGenStatus('Generating STEP file…');
    try {
      await api.generateStepFromInferredStack(jobId);
      await api.downloadFile(jobId, 'outputs/model.step', `${partNo}_model.step`, 3, 1500);
      setGenStatus('✅ STEP file downloaded.');
    } catch (err) {
      setGenError(err instanceof Error ? err.message : 'STEP export failed');
      setGenStatus(null);
    } finally { setGenerating(false); }
  };

  const handleDownloadPartSummary = async () => {
    if (!partSummary) return;
    setGenDropOpen(false);
    const partNo = resolvedPartNo();
    setGenerating(true); setGenError(null); setGenStatus('Downloading part summary…');
    try {
      await api.downloadFile(jobId, 'outputs/part_summary.json', `${partNo}_part_summary.json`, 2, 500);
      setGenStatus('✅ Part Summary JSON downloaded.');
    } catch (err) {
      setGenError(err instanceof Error ? err.message : 'Download failed');
      setGenStatus(null);
    } finally { setGenerating(false); }
  };

  const handleDownloadLlm = async () => {
    if (!llmAnalysis) return;
    setGenDropOpen(false);
    const partNo = resolvedPartNo();
    setGenerating(true); setGenError(null); setGenStatus('Downloading LLM analysis…');
    try {
      await api.downloadFile(jobId, 'outputs/llm_analysis.json', `${partNo}_llm_analysis.json`, 2, 500);
      setGenStatus('✅ LLM Analysis JSON downloaded.');
    } catch (err) {
      setGenError(err instanceof Error ? err.message : 'Download failed');
      setGenStatus(null);
    } finally { setGenerating(false); }
  };

  const handleGenerateAll = async () => {
    if (!partSummary) {
      setGenError('Stack inference not complete yet. Run Auto-Detect first.');
      return;
    }
    setGenDropOpen(false);
    const partNo = resolvedPartNo();
    setGenerating(true);
    setGenError(null);
    setGenStatus('Generating Excel…');

    try {
      setGenStatus('Generating Excel…');
      const { blob, filename } = await api.rfqExportXlsx(buildPayload(partNo), 'master');
      downloadBlob(blob, filename);

      setGenStatus('Generating STEP file…');
      await api.generateStepFromInferredStack(jobId);
      await api.downloadFile(jobId, 'outputs/model.step', `${partNo}_model.step`, 3, 1500);

      setGenStatus('Downloading part summary…');
      await api.downloadFile(jobId, 'outputs/part_summary.json', `${partNo}_part_summary.json`, 2, 500);

      if (llmAnalysis) {
        setGenStatus('Downloading LLM analysis…');
        await api.downloadFile(jobId, 'outputs/llm_analysis.json', `${partNo}_llm_analysis.json`, 2, 500);
      }

      setGenStatus('✅ Excel · STEP · JSON downloaded.');
      await loadResults();
    } catch (err) {
      setGenError(err instanceof Error ? err.message : 'Generation failed');
      setGenStatus(null);
    } finally {
      setGenerating(false);
    }
  };

  // ── Derived ───────────────────────────────────────────────────────────────

  const segments: Segment[] = inferredStack?.segments || partSummary?.segments || [];
  const ed = effectiveDims(llmAnalysis, { od: odOvr, maxOd: maxOdOvr, id: idOvr, len: lenOvr });

  const recommendation = llmAnalysis?.validation?.recommendation;
  const overallConf    = llmAnalysis?.validation?.overall_confidence;
  const crossChecks: string[] = llmAnalysis?.validation?.cross_checks || [];

  // Build lathe segments from overrides or raw segments.
  // When coming from raw segments, propagate the LLM bore (id_in) so the 3-D profile
  // is hollow even when inferred_stack.json has id_diameter≈0 for most segments.
  const llmBore = ed.id_in;                    // always from LLM/overrides

  // Features detected by LLM – pre-filter: drop anything below 0.60 confidence or missing a type.
  const llmFeatures = useMemo(() => {
    const raw: any[] = (llmAnalysis?.extracted as any)?.features ?? [];
    return raw.filter((f: any) => typeof f === 'object' && f !== null && ((f.confidence ?? 0) as number) >= 0.60);
  }, [llmAnalysis]);
  const latheSegs: Segment[] =
    odOvr !== null || maxOdOvr !== null || idOvr !== null || lenOvr !== null
      ? [{ z_start: 0, z_end: ed.length_in, od_diameter: ed.max_od_in, id_diameter: ed.id_in }]
      : llmBore > 0.001 && segments.length > 0
        ? segments.map((s: any) => ({ ...s, id_diameter: Math.max((s.id_diameter ?? 0) as number, llmBore) }))
        : segments;

  // Scale-normalise segment diameters against LLM-confirmed od_in.
  // The geometry inference pipeline reads pixels off a 300-DPI image, so its
  // scale can be 2–5× wrong.  When the LLM has a high-confidence od_in, we
  // apply a uniform ratio so the 3-D model matches the real part size.
  const llmOdIn: number = ed.od_in ?? 0;
  const latheSegsNorm: Segment[] = useMemo(() => {
    // If geometry is high-confidence, prefer geometry segments as-is (no LLM scaling)
    const geomHigh = (partSummary?.inference_metadata?.overall_confidence ?? partSummary?.scale_report?.confidence) >= 0.8;
    if (geomHigh) return latheSegs;
    if (!llmOdIn || llmOdIn <= 0 || latheSegs.length === 0) return latheSegs;
    const maxSegOd = Math.max(...latheSegs.map((s) => s.od_diameter));
    if (!maxSegOd || maxSegOd <= 0) return latheSegs;
    const ratio = llmOdIn / maxSegOd;
    if (Math.abs(ratio - 1) < 0.05) return latheSegs; // already within 5% — no change
    return latheSegs.map((s) => ({
      ...s,
      od_diameter: s.od_diameter * ratio,
      id_diameter: (s.id_diameter ?? 0) * ratio,
    }));
  }, [latheSegs, llmOdIn]);

  // Clean small/flagged segments that create spurious shoulders in the 3D view.
  // Merge 'short_segment' or very short span segments into neighbors to reduce visual noise.
  const cleanedSegs = useMemo(() => {
    if (!latheSegsNorm || latheSegsNorm.length === 0) return latheSegsNorm;
    const MIN_SPAN = 0.02; // inches — segments shorter than this are considered for merging
    const out: Segment[] = [];
    for (let i = 0; i < latheSegsNorm.length; i++) {
      const s = { ...latheSegsNorm[i] } as Segment & { flags?: string[] };
      const span = (s.z_end || 0) - (s.z_start || 0);
      const flags = s.flags || [];
      if (span < MIN_SPAN || flags.includes('short_segment') || flags.includes('auto_merged')) {
        // merge into previous if exists, else into next
        if (out.length > 0) {
          const prev = out[out.length - 1];
          const prevSpan = (prev.z_end || 0) - (prev.z_start || 0);
          const total = (prevSpan || 0) + (span || 0) || 1e-6;
          // weighted average diameters
          prev.od_diameter = ((prev.od_diameter || 0) * (prevSpan || 0) + (s.od_diameter || 0) * (span || 0)) / total;
          prev.id_diameter = ((prev.id_diameter || 0) * (prevSpan || 0) + (s.id_diameter || 0) * (span || 0)) / total;
          prev.z_end = s.z_end;
          prev.volume_in3 = (prev.volume_in3 || 0) + (s.volume_in3 || 0);
          prev.flags = Array.from(new Set([...(prev.flags || []), ...(s.flags || [])]));
        } else if (i + 1 < latheSegsNorm.length) {
          // merge into next by adjusting next's z_start and averaging
          const next = { ...latheSegsNorm[i + 1] } as Segment & { flags?: string[] };
          const nextSpan = (next.z_end || 0) - (next.z_start || 0);
          const total = (nextSpan || 0) + (span || 0) || 1e-6;
          next.od_diameter = ((next.od_diameter || 0) * (nextSpan || 0) + (s.od_diameter || 0) * (span || 0)) / total;
          next.id_diameter = ((next.id_diameter || 0) * (nextSpan || 0) + (s.id_diameter || 0) * (span || 0)) / total;
          next.z_start = s.z_start;
          next.volume_in3 = (next.volume_in3 || 0) + (s.volume_in3 || 0);
          next.flags = Array.from(new Set([...(next.flags || []), ...(s.flags || [])]));
          // replace next in iteration
          latheSegsNorm[i + 1] = next;
        } else {
          // lone short segment — just push it
          out.push(s);
        }
      } else {
        out.push(s);
      }
    }
    return out;
  }, [latheSegsNorm]);

  // Derive display dims: prefer LLM+overrides, fall back to stack geometry
  const rawSegs: any[] = inferredStack?.segments || partSummary?.segments || [];
  const zRange: number[] = inferredStack?.z_range || partSummary?.z_range || [0, 0];
  const stackDims = rawSegs.length ? (() => {
    const maxOd = Math.max(...rawSegs.map((s: any) => s.od_diameter as number));
    // Filter out low-quality / transitional segments for the finish-OD estimate.
    // "thin_wall", "low_confidence", and micro features (<5 % of max OD) skew Math.min badly.
    const goodSegs = rawSegs.filter((s: any) => {
      const flags: string[] = s.flags ?? [];
      if (flags.includes('low_confidence') || flags.includes('thin_wall')) return false;
      return (s.od_diameter as number) >= maxOd * 0.05;
    });
    const odSegs = goodSegs.length > 0 ? goodSegs : rawSegs;
    return {
      od_in:     Math.min(...odSegs.map((s: any) => s.od_diameter as number)),
      max_od_in: maxOd,
      id_in:     Math.max(...rawSegs.map((s: any) => (s.id_diameter as number) || 0)),
      length_in: zRange[1] - zRange[0],
    };
  })() : null;
  // Decide which source to use per-dimension (LLM preferred, Geometry as fallback)

  function chooseDim(field: 'od_in' | 'max_od_in' | 'id_in' | 'length_in') {
    const llmVals: any = ed || null;
    const geomVals: any = stackDims || null;
    // Check the RAW LLM extracted value (before effectiveDims clamps null→0.001)
    // so that a missing LLM value correctly falls through to geometry.
    const overrideMap: Record<string, number | null> = {
      od_in: odOvr, max_od_in: maxOdOvr, id_in: idOvr, length_in: lenOvr,
    };
    const hasOverride = overrideMap[field] != null;
    const rawLlmVal   = llmAnalysis?.extracted?.[field] ?? null;
    // "LLM has it" only when there is a manual override OR the LLM actually returned a
    // positive value (not null/0).  effectiveDims.clamp(null→0.001) must NOT qualify.
    const llmHas  = hasOverride || (rawLlmVal != null && typeof rawLlmVal === 'number' && rawLlmVal > 0);
    const geomHas = geomVals && typeof geomVals[field] === 'number' && (geomVals[field] as number) > 0;
    // Always prefer LLM; fall back to geometry only when LLM has no value.
    if (llmHas && llmVals) return { value: llmVals[field] as number, source: 'LLM' };
    if (geomHas) return { value: geomVals[field] as number, source: 'Geometry' };
    return { value: 0, source: 'Unknown' };
  }

  const displayDims = {
    od_in: chooseDim('od_in'),
    max_od_in: chooseDim('max_od_in'),
    id_in: chooseDim('id_in'),
    length_in: chooseDim('length_in'),
  };

  // ── Render ────────────────────────────────────────────────────────────────

  if (loading) {
    return <div className="acr-root"><div className="acr-state">Loading…</div></div>;
  }

  if (error) {
    return <div className="acr-root"><div className="acr-state acr-state--error">Error: {error}</div></div>;
  }

  return (
    <div className="acr-root">
      {/* ── Header ── */}
      <div className="acr-header">
        <input
          className="acr-part-input"
          type="text"
          placeholder="Part No"
          value={rfqPartNo}
          onChange={(e) => setRfqPartNo(e.target.value)}
        />
        <button
          className="acr-detect-btn"
          onClick={handleAutoDetect}
          disabled={detecting || inferring}
        >
          {detecting ? 'Detecting…' : inferring ? 'Inferring…' : 'Auto-Detect Turned View'}
        </button>
      </div>

      {detectionError && (
        <div className="acr-inline-error">{detectionError}</div>
      )}

      {/* ── Body ── */}
      <div className="acr-body">
        {/* Left: PDF page image + 4 key dimensions */}
        <div className="acr-left">
          {pdfPageUrl ? (
            <img className="acr-pdf-img" src={pdfPageUrl} alt="Part drawing page 1" />
          ) : (
            !displayDims && <div className="acr-empty">No drawing preview available</div>
          )}
          {displayDims && (
            <div className="acr-stats-grid">
              <div className="acr-stat-box">
                <span className="acr-stat-label">Finish OD</span>
                <span className="acr-stat-val">
                  {displayDims.od_in.value.toFixed(3)}<span className="acr-stat-unit"> in</span>
                  <small className={`acr-source-badge acr-source-badge--${String(displayDims.od_in.source).toLowerCase()}`}>{displayDims.od_in.source}</small>
                </span>
              </div>
              <div className="acr-stat-box">
                <span className="acr-stat-label">MAX OD</span>
                <span className="acr-stat-val">
                  {displayDims.max_od_in.value.toFixed(3)}<span className="acr-stat-unit"> in</span>
                  <small className={`acr-source-badge acr-source-badge--${String(displayDims.max_od_in.source).toLowerCase()}`}>{displayDims.max_od_in.source}</small>
                </span>
              </div>
              <div className="acr-stat-box">
                <span className="acr-stat-label">Finish ID</span>
                <span className="acr-stat-val">
                  {displayDims.id_in.value.toFixed(3)}<span className="acr-stat-unit"> in</span>
                  <small className={`acr-source-badge acr-source-badge--${String(displayDims.id_in.source).toLowerCase()}`}>{displayDims.id_in.source}</small>
                </span>
              </div>
              <div className="acr-stat-box">
                <span className="acr-stat-label">Length</span>
                <span className="acr-stat-val">
                  {displayDims.length_in.value.toFixed(3)}<span className="acr-stat-unit"> in</span>
                  <small className={`acr-source-badge acr-source-badge--${String(displayDims.length_in.source).toLowerCase()}`}>{displayDims.length_in.source}</small>
                </span>
              </div>
            </div>
          )}
        </div>

        {/* Right: dims card + 3D viewer */}
        <div className="acr-right">
          {llmAnalysis && (
            <div className="acr-dims-card">
              {/* ── LLM pending (background pipeline still running) ── */}
              {llmAnalysis.pending ? (
                <div className="acr-dims-header">
                  <span className="acr-rec-badge acr-rec-badge--pending">⏳ Analyzing…</span>
                  <span className="acr-material" style={{ fontSize: '0.75rem', opacity: 0.7 }}>
                    LLM extraction running in background
                  </span>
                </div>
              ) : llmAnalysis.error ? (
                /* ── LLM error (rate limit or pipeline failure) ── */
                <>
                  <div className="acr-dims-header">
                    <span className="acr-rec-badge acr-rec-badge--review">⚠ LLM Unavailable</span>
                  </div>
                  <ul className="acr-cross-checks">
                    {crossChecks.length > 0
                      ? crossChecks.map((c, i) => <li key={i}>{c}</li>)
                      : <li>{llmAnalysis.error}</li>}
                  </ul>
                </>
              ) : (
                /* ── Normal LLM result ── */
                <>
                  <div className="acr-dims-header">
                    <span
                      className={`acr-rec-badge acr-rec-badge--${(recommendation || 'unknown').toLowerCase()}`}
                    >
                      {recommendation || 'UNKNOWN'} · {overallConf != null ? `${Math.round(overallConf * 100)}%` : '—'}
                    </span>
                    {llmAnalysis.extracted?.material && (
                      <span className="acr-material">{llmAnalysis.extracted.material}</span>
                    )}
                  </div>

                  {crossChecks.length > 0 && (
                    <ul className="acr-cross-checks">
                      {crossChecks.map((c, i) => <li key={i}>{c}</li>)}
                    </ul>
                  )}

                  <div className="acr-dim-rows">
                    <DimRow
                      label="OD (in)"
                      base={llmAnalysis.extracted?.od_in}
                      override={odOvr}
                      onOverride={setOdOvr}
                      min={0.001} max={24} step={0.001}
                    />
                    <DimRow
                      label="MAX OD (in)"
                      base={llmAnalysis.extracted?.max_od_in ?? llmAnalysis.extracted?.od_in}
                      override={maxOdOvr}
                      onOverride={setMaxOdOvr}
                      min={0.001} max={24} step={0.001}
                    />
                    <DimRow
                      label="ID (in)"
                      base={llmAnalysis.extracted?.id_in}
                      override={idOvr}
                      onOverride={setIdOvr}
                      min={0} max={24} step={0.001}
                    />
                    <DimRow
                      label="Length (in)"
                      base={llmAnalysis.extracted?.length_in}
                      override={lenOvr}
                      onOverride={setLenOvr}
                      min={0.001} max={120} step={0.001}
                    />
                  </div>

                  {(odOvr !== null || maxOdOvr !== null || idOvr !== null || lenOvr !== null) && (
                    <button
                      className="acr-reset-btn"
                      onClick={() => { setOdOvr(null); setMaxOdOvr(null); setIdOvr(null); setLenOvr(null); }}
                    >
                      ↺ Reset all overrides
                    </button>
                  )}
                </>
              )}
            </div>
          )}

          <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 8 }}>
            <label style={{ fontSize: 13, color: '#8b949e' }}>
              <input type="checkbox" checked={mergeSegments} onChange={(e) => setMergeSegments(e.target.checked)} />
              <span style={{ marginLeft: 8 }}>Clean short segments (merge)</span>
            </label>
            <div style={{ flex: 1 }} />
          </div>

          <div className="acr-viewer-wrap">
            <LatheViewer
              segments={mergeSegments ? cleanedSegs : latheSegsNorm}
              jobId={jobId}
              boreDiameter={ed.id_in}
              features={llmFeatures}
              finishOd={ed.od_in}
              maxOd={ed.max_od_in}
              lengthIn={ed.length_in}
            />
          </div>
        </div>
      </div>

      {/* ── Footer ── */}
      <div className="acr-footer">
        <div className="acr-gen-split" ref={genDropRef}>
          <button
            className="acr-generate-btn acr-generate-btn--main"
            onClick={handleGenerateAll}
            disabled={!partSummary || generating}
          >
            {generating ? 'Generating…' : 'Download All'}
          </button>
          <button
            className={`acr-generate-btn acr-generate-btn--chevron${genDropOpen ? ' acr-generate-btn--chevron-open' : ''}`}
            onClick={() => setGenDropOpen(v => !v)}
            disabled={!partSummary || generating}
            title="Choose what to download"
          >
            <span className="acr-chevron-icon">&#8964;</span>
          </button>
          {genDropOpen && (
            <div className="acr-gen-dropdown">
              <button className="acr-gen-option" onClick={handleDownloadExcel} disabled={!partSummary}>
                <span className="acr-gen-option-body">
                  <span className="acr-gen-option-label">Excel Spreadsheet</span>
                  <span className="acr-gen-option-desc">RFQ cost estimation workbook</span>
                </span>
                <span className="acr-gen-option-tag">.xlsx</span>
              </button>
              <button className="acr-gen-option" onClick={handleDownloadStep} disabled={!partSummary}>
                <span className="acr-gen-option-body">
                  <span className="acr-gen-option-label">3D Model</span>
                  <span className="acr-gen-option-desc">CAD-ready solid geometry</span>
                </span>
                <span className="acr-gen-option-tag">.step</span>
              </button>
              <button className="acr-gen-option" onClick={handleDownloadPartSummary} disabled={!partSummary}>
                <span className="acr-gen-option-body">
                  <span className="acr-gen-option-label">Part Summary</span>
                  <span className="acr-gen-option-desc">Segment &amp; geometry data</span>
                </span>
                <span className="acr-gen-option-tag">.json</span>
              </button>
              <button className="acr-gen-option" onClick={handleDownloadLlm} disabled={!llmAnalysis || llmAnalysis.pending}>
                <span className="acr-gen-option-body">
                  <span className="acr-gen-option-label">LLM Analysis</span>
                  <span className="acr-gen-option-desc">Extracted dims &amp; validation</span>
                </span>
                <span className="acr-gen-option-tag">.json</span>
              </button>
              <div className="acr-gen-divider" />
              <button className="acr-gen-option acr-gen-option--all" onClick={handleGenerateAll} disabled={!partSummary}>
                <span className="acr-gen-option-body">
                  <span className="acr-gen-option-label">Download All</span>
                  <span className="acr-gen-option-desc">Excel + STEP + both JSON files</span>
                </span>
                <span className="acr-gen-option-tag acr-gen-option-tag--all">4 files</span>
              </button>
            </div>
          )}
        </div>
        {genStatus && <span className="acr-status">{genStatus}</span>}
        {genError  && <span className="acr-inline-error">{genError}</span>}
      </div>
    </div>
  );
}

// ── DimRow sub-component ──────────────────────────────────────────────────

interface DimRowProps {
  label: string;
  base: number | undefined;
  override: number | null;
  onOverride: (v: number | null) => void;
  min: number;
  max: number;
  step: number;
}

function DimRow({ label, base, override, onOverride, min, max, step }: DimRowProps) {
  const displayVal = override ?? base ?? 0;
  const isOverridden = override !== null;

  return (
    <div className="acr-slider-row">
      <span className="acr-slider-label">{label}</span>
      <span className={`acr-slider-val${isOverridden ? ' acr-slider-val--overridden' : ''}`}>
        {displayVal.toFixed(3)}
      </span>
      <input
        type="range"
        className="acr-slider"
        min={min}
        max={max}
        step={step}
        value={displayVal}
        onChange={(e) => onOverride(parseFloat(e.target.value))}
      />
      {isOverridden && (
        <button className="acr-reset-btn acr-reset-btn--inline" onClick={() => onOverride(null)}>
          ↺
        </button>
      )}
    </div>
  );
}

export default AutoConvertResults;
