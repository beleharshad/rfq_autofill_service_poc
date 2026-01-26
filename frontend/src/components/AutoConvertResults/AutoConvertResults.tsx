// AutoConvertResults.tsx
// Refactor goals (done):
// ✅ No truncation of functionality (kept all major flows)
// ✅ Remove duplicated/legacy RFQ pipelines (single “rfqState” becomes source of truth)
// ✅ Fix broken/orphaned code block (there was a stray `rows.push(...)` section outside any function)
// ✅ Add proper “wait for pdf_pages” gate to prevent: "PDF pages not found..."
// ✅ Stronger request-cancellation protection using useRef (no stale state closure bugs)
// ✅ Restructure into clear sections: helpers → hooks/state → loaders → handlers → render

import { useEffect, useMemo, useRef, useState } from 'react';
import { api } from '../../services/api';
import ProfileReviewPlot from './ProfileReviewPlot';
import { setSegments, type Segment as SegmentStoreType } from '../../state/segmentStore';
import type {
  RFQAutofillRequest,
  RFQAutofillResponse,
  RFQExportFileInfo,
  RFQEnvelopeResponse,
} from '../../services/types';
import './AutoConvertResults.css';

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
}

/** -----------------------------
 * Helpers (pure)
 * ---------------------------- */

const normalizePartNo = (s: string) => {
  const trimmed = (s || '').trim();
  // Strip common single-letter revision suffixes like _C or -C
  return trimmed.replace(/([_-])[a-zA-Z]$/, '');
};

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

const calcVolumeIn3 = (seg: Segment): number | null => {
  const length = seg.z_end - seg.z_start;
  const od = seg.od_diameter;
  const id = seg.id_diameter ?? 0;
  if (!isFinite(length) || !isFinite(od) || !isFinite(id) || length <= 0 || od <= 0) {
    return null;
  }
  const odRadius = od / 2;
  const idRadius = Math.max(0, id / 2);
  const area = Math.PI * (odRadius * odRadius - idRadius * idRadius);
  const vol = area * length;
  return vol > 0 ? vol : null;
};

const getMismatchClass = (pdfValue: string | null | undefined, geometryValue: number | null | undefined): string => {
  if (!pdfValue || geometryValue === null || geometryValue === undefined) return '';
  const pdfNum = parseFloat(pdfValue);
  if (isNaN(pdfNum)) return '';
  const diff = Math.abs(pdfNum - geometryValue);
  if (diff > 0.10) return 'rfq-mismatch-red';
  if (diff > 0.03) return 'rfq-mismatch-yellow';
  return '';
};

const getConfidenceClass = (confidence: number | null | undefined): string => {
  if (!confidence) return 'rfq-confidence-unknown';
  if (confidence >= 0.85) return 'rfq-confidence-high';
  if (confidence >= 0.65) return 'rfq-confidence-medium';
  return 'rfq-confidence-low';
};

const getConfidenceBadgeClass = (confidence: number | undefined) => {
  if (confidence === undefined) return 'confidence-unknown';
  if (confidence >= 0.8) return 'confidence-high';
  if (confidence >= 0.6) return 'confidence-medium';
  return 'confidence-low';
};

const getConfidenceLabel = (confidence: number | undefined) => {
  if (confidence === undefined) return 'Unknown';
  if (confidence >= 0.8) return 'High';
  if (confidence >= 0.6) return 'Medium';
  return 'Low';
};

const getRFQConfidenceBadgeClass = (confidence: number) => {
  if (confidence >= 0.85) return 'confidence-high';
  if (confidence >= 0.65) return 'confidence-medium';
  return 'confidence-low';
};

const getRFQValueCellClass = (
  value: number | null | undefined,
  confidence: number,
  status: string,
  reasons: string[]
) => {
  if (value === null || value === undefined) return 'rfq-cell-bad';
  if (status === 'REJECTED') return 'rfq-cell-bad';
  if (status === 'NEEDS_REVIEW') return 'rfq-cell-warn';
  if (confidence >= 0.85 && !reasons.includes('SCALE_ESTIMATED')) return 'rfq-cell-good';
  return 'rfq-cell-warn';
};

const getRFQFieldBorderClass = (fieldKey: string, reasons: string[]) => {
  if (fieldKey === 'finish_od_in' || fieldKey === 'finish_len_in') {
    if (reasons.includes('SCALE_ESTIMATED')) return 'rfq-border-bad';
  }
  if (fieldKey === 'finish_id_in') {
    if (reasons.includes('LOW_CONF_FINISH_ID')) return 'rfq-border-bad';
  }
  return '';
};

const formatMaybeNumber = (v: number | null | undefined, digits: number = 3) => {
  if (v === null || v === undefined) return '—';
  return Number(v).toFixed(digits);
};

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

const formatFileSize = (bytes: number) => {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
};

const formatDateTime = (isoString: string) => new Date(isoString).toLocaleString();

/** -----------------------------
 * RFQ ViewModel builder (single source)
 * ---------------------------- */
type RFQUIFlags = {
  useEnvelope: boolean;
  vendorQuoteMode: boolean;
  mode: 'quick_quote' | 'detailed';
  applyThreshold: 0.6 | 0.7 | 0.85;
  includeEstimateBlock: boolean;
};

type RFQViewRow = {
  key: string;
  label: string;
  value: number | null;
  conf: number | null;
  source: string | null;
  kind: 'finish' | 'raw' | 'rm';
};

const buildRfqViewModel = (
  autofill: RFQAutofillResponse | null,
  envelope: RFQEnvelopeResponse | null,
  flags: RFQUIFlags
) => {
  if (!autofill) return { rows: [] as RFQViewRow[], status: '', reasons: [] as string[], debugLine: '', rawStock: {} as any };

  const rows: RFQViewRow[] = [];

  // Finish dims
  if (autofill.fields.finish_od_in?.value !== null) {
    rows.push({
      key: 'finish_od_in',
      label: 'Finish OD (in)',
      value: autofill.fields.finish_od_in.value,
      conf: autofill.fields.finish_od_in.confidence,
      source: autofill.fields.finish_od_in.source,
      kind: 'finish',
    });
  }
  if (autofill.fields.finish_len_in?.value !== null) {
    rows.push({
      key: 'finish_len_in',
      label: 'Finish Length (in)',
      value: autofill.fields.finish_len_in.value,
      conf: autofill.fields.finish_len_in.confidence,
      source: autofill.fields.finish_len_in.source,
      kind: 'finish',
    });
  }
  if (autofill.fields.finish_id_in?.value !== null) {
    rows.push({
      key: 'finish_id_in',
      label: 'Finish ID (in)',
      value: autofill.fields.finish_id_in.value,
      conf: autofill.fields.finish_id_in.confidence,
      source: autofill.fields.finish_id_in.source,
      kind: 'finish',
    });
  }

  // RAW/Stock dims (prefer envelope when present)
  const rawOdValue = flags.useEnvelope ? (envelope?.fields?.raw_max_od_in?.value ?? autofill.fields.rm_od_in.value) : autofill.fields.rm_od_in.value;
  const rawLenValue = flags.useEnvelope ? (envelope?.fields?.raw_len_in?.value ?? autofill.fields.rm_len_in.value) : autofill.fields.rm_len_in.value;

  const rawOdConf = flags.useEnvelope
    ? (envelope?.fields?.raw_max_od_in?.confidence ?? autofill.fields.rm_od_in.confidence)
    : autofill.fields.rm_od_in.confidence;

  const rawLenConf = flags.useEnvelope
    ? (envelope?.fields?.raw_len_in?.confidence ?? autofill.fields.rm_len_in.confidence)
    : autofill.fields.rm_len_in.confidence;

  if (rawOdValue !== null) {
    rows.push({
      key: 'raw_od_in',
      label: 'RAW OD (in) [stock]',
      value: rawOdValue,
      conf: rawOdConf,
      source: (flags.useEnvelope ? envelope?.fields?.raw_max_od_in?.source : null) || autofill.fields.rm_od_in.source,
      kind: 'raw',
    });
  }
  if (rawLenValue !== null) {
    rows.push({
      key: 'raw_len_in',
      label: 'RAW Length (in) [cut]',
      value: rawLenValue,
      conf: rawLenConf,
      source: (flags.useEnvelope ? envelope?.fields?.raw_len_in?.source : null) || autofill.fields.rm_len_in.source,
      kind: 'raw',
    });
  }

  // RM dims (final)
  if (autofill.fields.rm_od_in?.value !== null) {
    rows.push({
      key: 'rm_od_in',
      label: 'RM OD (in)',
      value: autofill.fields.rm_od_in.value,
      conf: autofill.fields.rm_od_in.confidence,
      source: autofill.fields.rm_od_in.source,
      kind: 'rm',
    });
  }
  if (autofill.fields.rm_len_in?.value !== null) {
    rows.push({
      key: 'rm_len_in',
      label: 'RM Length (in)',
      value: autofill.fields.rm_len_in.value,
      conf: autofill.fields.rm_len_in.confidence,
      source: autofill.fields.rm_len_in.source,
      kind: 'rm',
    });
  }

  // Merge reasons
  const reasons = [...(autofill.reasons || [])];
  (envelope?.reasons || []).forEach((r) => {
    if (!reasons.includes(r)) reasons.push(r);
  });

  const debug = autofill.debug;
  const debugLine = debug
    ? `max_od=${debug.max_od_in.toFixed(3)}, overall_len=${debug.overall_len_in.toFixed(
        3
      )}, scale_method=${debug.scale_method}, overall_conf=${(debug.overall_confidence * 100).toFixed(
        0
      )}%, min_len_gate=${debug.min_len_gate_in.toFixed(4)}`
    : '';

  const rawStock = { od: rawOdValue || undefined, len: rawLenValue || undefined };

  return { rows, status: autofill.status, reasons, debugLine, rawStock };
};

/** -----------------------------
 * Component
 * ---------------------------- */
function AutoConvertResults({ jobId, onSwitchToManual }: AutoConvertResultsProps) {
  /** -----------------------------
   * Core state
   * ---------------------------- */
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [inferredStack, setInferredStack] = useState<any>(null);
  const [partSummary, setPartSummary] = useState<any>(null);

  const [overallConfidence, setOverallConfidence] = useState<number | null>(null);
  const [warnings, setWarnings] = useState<string[]>([]);

  /** -----------------------------
   * Auto-detect / inference state
   * ---------------------------- */
  const [detecting, setDetecting] = useState(false);
  const [inferring, setInferring] = useState(false);
  const [detectionResult, setDetectionResult] = useState<any>(null);
  const [detectionError, setDetectionError] = useState<string | null>(null);

  /** -----------------------------
   * STEP / OCC state
   * ---------------------------- */
  const [generatingStep, setGeneratingStep] = useState(false);
  const [stepResult, setStepResult] = useState<any>(null);
  const [showStepConfirmation, setShowStepConfirmation] = useState(false);
  const [needsReview, setNeedsReview] = useState(false);
  const [reviewReasons, setReviewReasons] = useState<string[]>([]);

  const [generatingStepFromStack, setGeneratingStepFromStack] = useState(false);
  const [stepFromStackError, setStepFromStackError] = useState<string | null>(null);
  const [stepFromStackStatus, setStepFromStackStatus] = useState<string | null>(null);

  const [occAvailable, setOccAvailable] = useState<boolean | null>(null);
  const [occBackend, setOccBackend] = useState<string | null>(null);
  const [occError, setOccError] = useState<string | null>(null);

  const [downloadingStep, setDownloadingStep] = useState(false);
  const [stepFileExists, setStepFileExists] = useState(false);

  /** -----------------------------
   * RFQ UI state (single pipeline)
   * ---------------------------- */
  const [rfqPartNo, setRfqPartNo] = useState<string>('');
  const [rmOdAllowanceIn, setRmOdAllowanceIn] = useState<number>(0.10);
  const [rmLenAllowanceIn, setRmLenAllowanceIn] = useState<number>(0.35);

  const [rfqMode, setRfqMode] = useState<'ENVELOPE' | 'GEOMETRY'>('ENVELOPE');
  const [rfqCostInputsOpen, setRfqCostInputsOpen] = useState<boolean>(false);

  const [rfqIncludeEstimate, setRfqIncludeEstimate] = useState<boolean>(true);

  const [rfqCostInputs, setRfqCostInputs] = useState<NonNullable<RFQAutofillRequest['cost_inputs']>>({
    rm_rate_per_kg: 100,
    turning_rate_per_min: 4.0,
    roughing_cost: 162,
    inspection_cost: 10,
    material_density_kg_m3: 7850,
    special_process_cost: null,
  });

  // Editable RFQ row cells (only the 5 v1 columns)
  const [rfqRowValues, setRfqRowValues] = useState<Record<string, string>>({
    finish_od_in: '',
    finish_len_in: '',
    finish_id_in: '',
    rm_od_in: '',
    rm_len_in: '',
  });
  const [rfqApplied, setRfqApplied] = useState<Record<string, boolean>>({});

  // Apply selections (per-row checkboxes)
  const [rfqApplySelections, setRfqApplySelections] = useState<Record<string, boolean>>({});

  // Consolidated RFQ state (source of truth)
  const [rfqState, setRfqState] = useState<{
    loading: boolean;
    hardError: string | null;
    warnings: string[];
    uiFlags: RFQUIFlags;
    requests: {
      autofillPayload: RFQAutofillRequest | null;
      envelopePayload: any | null;
    };
    responses: {
      autofill: RFQAutofillResponse | null;
      envelope: RFQEnvelopeResponse | null;
    };
  }>({
    loading: false,
    hardError: null,
    warnings: [],
    uiFlags: {
      useEnvelope: true,
      vendorQuoteMode: true,
      mode: 'quick_quote',
      applyThreshold: 0.7,
      includeEstimateBlock: true,
    },
    requests: {
      autofillPayload: null,
      envelopePayload: null,
    },
    responses: {
      autofill: null,
      envelope: null,
    },
  });

  // Per-field "needs review" flags derived from autofill reasons
  // (used for the inline messaging in the editable RFQ cells)
  const rfqNeedsReview = useMemo<Record<string, boolean>>(() => {
    const reasons = rfqState.responses.autofill?.reasons || [];
    const keys = Object.keys(rfqRowValues);
    const out: Record<string, boolean> = {};
    keys.forEach((k) => {
      out[k] = getRFQFieldBorderClass(k, reasons) === 'rfq-border-bad';
    });
    return out;
  }, [rfqRowValues, rfqState.responses.autofill?.reasons]);

  // RFQ request cancellation protection (useRef avoids stale closure)
  const rfqRequestIdRef = useRef(0);

  /** -----------------------------
   * RFQ Export state
   * ---------------------------- */
  const [rfqExportLoading, setRfqExportLoading] = useState(false);
  const [rfqExportError, setRfqExportError] = useState<string | null>(null);
  const [rfqExportsLoading, setRfqExportsLoading] = useState(false);
  const [rfqExports, setRfqExports] = useState<RFQExportFileInfo[]>([]);

  // Toggle panels
  const [envelopeDetailsOpen, setEnvelopeDetailsOpen] = useState<boolean>(false);
  const [pdfHintsOpen, setPdfHintsOpen] = useState<boolean>(false);

  // Feature viewer toggles with localStorage persistence
  const [showHoles, setShowHoles] = useState<boolean>(true);
  const [showSlots, setShowSlots] = useState<boolean>(true);
  const [showChamfers, setShowChamfers] = useState<boolean>(false);
  const [showFillets, setShowFillets] = useState<boolean>(false);

  // (Kept as placeholders—your code referenced vendorExtract but didn’t provide how it’s set)
  const [rfqVendorExtract] = useState<any>(null);

  /** -----------------------------
   * Derived memo
   * ---------------------------- */
  const segments: Segment[] = inferredStack?.segments || [];
  const hasLowConfidence = overallConfidence !== null && overallConfidence < 0.65;

  // hasStepFile should prefer actual file presence
  const hasStepFile = Boolean(stepFileExists || stepResult?.outputs?.includes('model.step'));

  const stepStatus = inferredStack?.step_status || 'UNKNOWN';
  const stepReason = inferredStack?.step_reason || null;

  const rfqViewModel = useMemo(
    () => buildRfqViewModel(rfqState.responses.autofill, rfqState.responses.envelope, rfqState.uiFlags),
    [rfqState.responses.autofill, rfqState.responses.envelope, rfqState.uiFlags]
  );

  // Normalize totals field names (backend may return different keys depending on pipeline).
  const totalsDisplay = useMemo(() => {
    const t: any = partSummary?.totals;
    if (!t) return null;

    const totalVolumeIn3: number | null = t.total_volume_in3 ?? t.volume_in3 ?? null;
    const odAreaIn2: number | null = t.total_od_area_in2 ?? t.od_area_in2 ?? null;
    const idAreaIn2: number | null = t.total_id_area_in2 ?? t.id_area_in2 ?? null;
    const totalSurfaceAreaIn2: number | null =
      t.total_surface_area_in2 ??
      (typeof odAreaIn2 === 'number' && typeof idAreaIn2 === 'number' ? odAreaIn2 + idAreaIn2 : null);

    return { totalVolumeIn3, totalSurfaceAreaIn2, odAreaIn2, idAreaIn2 };
  }, [partSummary]);

  const detectedFeatures = partSummary?.features;
  const featureWarnings: string[] = detectedFeatures?.meta?.warnings || [];
  const holes = detectedFeatures?.holes || [];
  const slots = detectedFeatures?.slots || [];
  const chamfers = detectedFeatures?.chamfers || [];
  const fillets = detectedFeatures?.fillets || [];
  const threads = detectedFeatures?.threads || [];

  const holesByPattern = useMemo(() => {
    const groups: Record<string, typeof holes> = {};
    holes.forEach((hole: any) => {
      const key = hole?.pattern || 'single';
      if (!groups[key]) groups[key] = [];
      groups[key].push(hole);
    });
    return groups;
  }, [holes]);

  /** -----------------------------
   * Load / init effects
   * ---------------------------- */

  // Persist RFQ UI prefs locally
  useEffect(() => {
    try {
      const raw = localStorage.getItem('rfq.autofill.quickQuotePrefs');
      if (!raw) return;
      const parsed = JSON.parse(raw) as any;
      if (parsed?.mode === 'ENVELOPE' || parsed?.mode === 'GEOMETRY') setRfqMode(parsed.mode);
      if (typeof parsed?.vendorQuoteMode === 'boolean') {
        setRfqState((prev) => ({ ...prev, uiFlags: { ...prev.uiFlags, vendorQuoteMode: parsed.vendorQuoteMode } }));
      }
      if (typeof parsed?.confidenceThreshold === 'number') {
        const v = parsed.confidenceThreshold;
        if (v === 0.6 || v === 0.7 || v === 0.85) {
          setRfqState((prev) => ({ ...prev, uiFlags: { ...prev.uiFlags, applyThreshold: v } }));
        }
      }
      if (typeof parsed?.includeEstimate === 'boolean') setRfqIncludeEstimate(parsed.includeEstimate);
      if (parsed?.costInputs && typeof parsed.costInputs === 'object') {
        setRfqCostInputs((prev) => ({ ...prev, ...parsed.costInputs }));
      }
    } catch {
      // ignore
    }
  }, []);

  useEffect(() => {
    try {
      localStorage.setItem(
        'rfq.autofill.quickQuotePrefs',
        JSON.stringify({
          mode: rfqMode,
          vendorQuoteMode: rfqState.uiFlags.vendorQuoteMode,
          confidenceThreshold: rfqState.uiFlags.applyThreshold,
          includeEstimate: rfqIncludeEstimate,
          costInputs: rfqCostInputs,
        })
      );
    } catch {
      // ignore
    }
  }, [rfqMode, rfqState.uiFlags.vendorQuoteMode, rfqState.uiFlags.applyThreshold, rfqIncludeEstimate, rfqCostInputs]);

  // Load feature viewer toggles from localStorage
  useEffect(() => {
    try {
      const raw = localStorage.getItem('viewer.featureToggles');
      if (!raw) return;
      const parsed = JSON.parse(raw) as any;
      if (typeof parsed?.showHoles === 'boolean') setShowHoles(parsed.showHoles);
      if (typeof parsed?.showSlots === 'boolean') setShowSlots(parsed.showSlots);
      if (typeof parsed?.showChamfers === 'boolean') setShowChamfers(parsed.showChamfers);
      if (typeof parsed?.showFillets === 'boolean') setShowFillets(parsed.showFillets);
    } catch {
      // ignore
    }
  }, []);

  // Persist feature viewer toggles to localStorage
  useEffect(() => {
    try {
      localStorage.setItem(
        'viewer.featureToggles',
        JSON.stringify({
          showHoles,
          showSlots,
          showChamfers,
          showFillets,
        })
      );
    } catch {
      // ignore
    }
  }, [showHoles, showSlots, showChamfers, showFillets]);

  // Helper function for confidence level styling
  const getConfidenceLevel = (confidence: number): string => {
    if (confidence >= 0.85) return 'high';
    if (confidence >= 0.65) return 'medium';
    return 'low';
  };

  useEffect(() => {
    loadResults();
    checkOccAvailability();
    refreshRfqExports();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [jobId]);

  // STEP file watcher
  useEffect(() => {
    if (stepFileExists) return;

    const checkStepFile = async () => {
      try {
        const files = await api.getJobFiles(jobId);
        const stepFile = files.files.find((f) => f.path === 'outputs/model.step');
        const fileExists = !!stepFile;
        setStepFileExists(fileExists);
        console.log('[AutoConvertResults] STEP file check:', { exists: fileExists, files: files.files.map((f) => f.path) });
      } catch (err) {
        console.error('[AutoConvertResults] Error checking for STEP file:', err);
        setStepFileExists(false);
      }
    };

    checkStepFile();

    if (!stepFileExists && (stepFromStackStatus === 'OK' || generatingStepFromStack)) {
      const interval = setInterval(checkStepFile, 3000);
      return () => clearInterval(interval);
    }
  }, [jobId, stepFromStackStatus, stepResult, stepFileExists, generatingStepFromStack]);

  // Init apply selections when RFQ results change
  useEffect(() => {
    if (!rfqState.responses.autofill) return;
    const vm = buildRfqViewModel(rfqState.responses.autofill, rfqState.responses.envelope, rfqState.uiFlags);
    const next: Record<string, boolean> = {};
    vm.rows.forEach((row) => {
      next[row.key] = row.value !== null && row.conf !== null && row.conf >= rfqState.uiFlags.applyThreshold;
    });
    setRfqApplySelections(next);
  }, [rfqState.responses.autofill, rfqState.responses.envelope, rfqState.uiFlags.applyThreshold]);

  /** -----------------------------
   * Loaders
   * ---------------------------- */
  const loadResults = async () => {
    console.log('[AutoConvertResults] Loading results for job:', jobId);
    try {
      setLoading(true);
      setError(null);

      const files = await api.getJobFiles(jobId);
      console.log('[AutoConvertResults] Job files:', files);

      // Prefill Part No if empty:
      if (!rfqPartNo.trim()) {
        try {
          const job = await api.getJob(jobId);
          const jobName = (job?.name || '').trim();
          if (jobName) {
            setRfqPartNo(normalizePartNo(jobName));
          } else {
            const pdf = files.files.find((f) => f.name.toLowerCase().endsWith('.pdf'));
            if (pdf?.name) {
              const base = pdf.name.replace(/\.pdf$/i, '').trim();
              if (base) setRfqPartNo(normalizePartNo(base));
            }
          }
        } catch (e) {
          console.warn('[AutoConvertResults] Failed to prefill part no:', e);
        }
      }

      const inferredStackFile = files.files.find((f) => f.path === 'outputs/inferred_stack.json');
      const partSummaryFile = files.files.find((f) => f.path === 'outputs/part_summary.json');

      if (inferredStackFile) {
        const response = await fetch(api.getPdfUrl(jobId, inferredStackFile.path));
        if (response.ok) {
          const data = await response.json();
          setInferredStack(data);
          setOverallConfidence(data.overall_confidence || null);
          setWarnings(data.warnings || []);

          if (data.segments && Array.isArray(data.segments)) {
            const segmentsForStore: SegmentStoreType[] = data.segments.map((seg: any) => ({
              z_start: seg.z_start,
              z_end: seg.z_end,
              od_diameter: seg.od_diameter,
              id_diameter: seg.id_diameter,
              wall_thickness: seg.wall_thickness,
              confidence: seg.confidence,
              flags: seg.flags,
              volume_in3: seg.volume_in3,
              od_area_in2: seg.od_area_in2,
              id_area_in2: seg.id_area_in2,
            }));
            setSegments(jobId, segmentsForStore);
          }
        }
      } else {
        setInferredStack(null);
      }

      if (partSummaryFile) {
        const response = await fetch(api.getPdfUrl(jobId, partSummaryFile.path));
        if (response.ok) {
          const data = await response.json();
          setPartSummary(data);

          if (data.segments && Array.isArray(data.segments)) {
            const segmentsForStore: SegmentStoreType[] = data.segments.map((seg: any) => ({
              z_start: seg.z_start,
              z_end: seg.z_end,
              od_diameter: seg.od_diameter,
              id_diameter: seg.id_diameter,
              wall_thickness: seg.wall_thickness,
              confidence: seg.confidence,
              flags: seg.flags,
              volume_in3: seg.volume_in3,
              od_area_in2: seg.od_area_in2,
              id_area_in2: seg.id_area_in2,
            }));
            setSegments(jobId, segmentsForStore);
          }
        }
      } else {
        setPartSummary(null);
      }
    } catch (err) {
      console.error('[AutoConvertResults] Error loading results:', err);
      setError(err instanceof Error ? err.message : 'Failed to load results');
    } finally {
      setLoading(false);
    }
  };

  const checkOccAvailability = async () => {
    try {
      console.log('[AutoConvertResults] Checking OCC availability...');
      const result = await api.checkOccAvailability();
      setOccAvailable(result.occ_available);
      setOccBackend(result.backend);
      setOccError(result.error || null);
    } catch (err) {
      console.error('[AutoConvertResults] Error checking OCC availability:', err);
      setOccAvailable(false);
      setOccBackend(null);
      setOccError(err instanceof Error ? err.message : 'Failed to check OCC availability');
    }
  };

  /** -----------------------------
   * PDF pages readiness gate (fixes your 404)
   * ---------------------------- */
  const ensurePdfPagesReady = async () => {
    const files = await api.getJobFiles(jobId);
    const pageImages = files.files.filter((f) => f.path.startsWith('outputs/pdf_pages/'));
    if (pageImages.length > 0) return;

    const pdfFile = files.files.find((f) => f.name.toLowerCase().endsWith('.pdf'));
    if (!pdfFile) throw new Error('No PDF file found. Please upload a PDF first.');

    const pdfUrl = api.getPdfUrl(jobId, pdfFile.path);
    const pdfResponse = await fetch(pdfUrl);
    const pdfBlob = await pdfResponse.blob();
    const pdfFileObj = new File([pdfBlob], pdfFile.name, { type: 'application/pdf' });

    await api.uploadPdf(jobId, pdfFileObj);

    // Poll until pages exist (prevents: "PDF pages not found...")
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

  /** -----------------------------
   * Handlers: Auto-detect & infer
   * ---------------------------- */
  const handleAutoDetect = async () => {
    console.log('[AutoConvertResults] Starting auto-detection for job:', jobId);
    setDetecting(true);
    setDetectionError(null);

    try {
      await ensurePdfPagesReady();

      console.log('[AutoConvertResults] Detecting views...');
      const viewsResult = await api.detectViews(jobId);
      console.log('[AutoConvertResults] Views detection result:', viewsResult);

      console.log('[AutoConvertResults] Running auto-detection...');
      const result = await api.autoDetectTurnedView(jobId);
      console.log('[AutoConvertResults] Auto-detection result:', result);

      setDetectionResult(result);

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
    console.log('[AutoConvertResults] Starting stack inference for job:', jobId, 'page:', page, 'viewIndex:', viewIndex);
    setInferring(true);
    setDetectionError(null);

    try {
      const result = await api.inferStackFromView(jobId, page, viewIndex);
      console.log('[AutoConvertResults] Stack inference result:', result);

      if (result.status === 'VALIDATION_FAILED') {
        const errorMsg = result.message || 'Auto-detect validation failed. Please use Assisted Manual mode.';
        if (result.validation_errors?.length) {
          const detailed = `${errorMsg}\n\nValidation Errors:\n${result.validation_errors.map((e: any) => `  • ${e}`).join('\n')}`;
          if (result.derived_values) {
            const derived = `\n\nDerived Values:\n  • Total Length: ${result.derived_values.total_length_inches.toFixed(
              3
            )} in\n  • Max OD: ${result.derived_values.max_od_inches.toFixed(3)} in`;
            setDetectionError(detailed + derived);
          } else {
            setDetectionError(detailed);
          }
        } else {
          setDetectionError(errorMsg);
        }
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

  /** -----------------------------
   * Handlers: STEP generation
   * ---------------------------- */
  const handleGenerateStep = async () => {
    const mode = partSummary?.inference_metadata?.mode || inferredStack?.inference_metadata?.mode;
    if (mode === 'auto_detect' && !showStepConfirmation) {
      setShowStepConfirmation(true);
      return;
    }

    setGeneratingStep(true);
    setError(null);
    setShowStepConfirmation(false);

    try {
      const result = await api.autoGenerateStep(jobId);
      setStepResult(result);

      if (result.status === 'DONE') {
        await loadResults();
        setNeedsReview(false);
        setReviewReasons([]);
      } else if (result.status === 'needs_review') {
        setNeedsReview(true);
        setReviewReasons(result.reasons || []);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to generate STEP');
    } finally {
      setGeneratingStep(false);
    }
  };

  const handleGenerateStepFromStack = async () => {
    try {
      setGeneratingStepFromStack(true);
      setStepFromStackError(null);
      setStepFromStackStatus(null);

      const result = await api.generateStepFromInferredStack(jobId);
      setStepFromStackStatus(result.status);

      if (result.status === 'OK') {
        // poll for outputs/model.step to appear
        let fileFound = false;
        for (let attempt = 0; attempt < 10; attempt++) {
          const files = await api.getJobFiles(jobId);
          const stepFile = files.files.find((f) => f.path === 'outputs/model.step');
          if (stepFile) {
            setStepFileExists(true);
            setStepResult({ ...result, outputs: (result as any).outputs || ['model.step'] });
            fileFound = true;
            break;
          }
          if (attempt < 9) await sleep(1000);
        }

        if (!fileFound) {
          console.warn('[AutoConvertResults] STEP file not found after 10s, but API reported OK');
          setStepFileExists(true);
          setStepResult({ ...result, outputs: (result as any).outputs || ['model.step'] });
        }

        await loadResults();
      } else if (result.status === 'UNAVAILABLE') {
        setStepFromStackError(result.message || 'OCC not installed');
      } else {
        const debugInfo = result.debug ? JSON.stringify(result.debug, null, 2) : '';
        const msg = result.message || 'STEP generation failed';
        setStepFromStackError(debugInfo ? `${msg}\n\nDebug info:\n${debugInfo}` : msg);
      }
    } catch (err) {
      let errorMessage = 'Failed to generate STEP';
      if (err instanceof Error) {
        errorMessage = err.message;
        if (err.stack) errorMessage += `\n\nStack trace:\n${err.stack}`;
      } else if (typeof err === 'object' && err !== null) {
        errorMessage = JSON.stringify(err, null, 2);
      }
      setStepFromStackError(errorMessage);
      setStepFromStackStatus('FAILED');
    } finally {
      setGeneratingStepFromStack(false);
    }
  };

  /** -----------------------------
   * RFQ: apply suggestions
   * ---------------------------- */
  const handleApplySuggestions = () => {
    if (!rfqState.responses.autofill) return;
    const vm = buildRfqViewModel(rfqState.responses.autofill, rfqState.responses.envelope, rfqState.uiFlags);

    let appliedCount = 0;
    vm.rows.forEach((row) => {
      if (!rfqApplySelections[row.key] || row.value === null) return;
      if (row.key in rfqRowValues) {
        setRfqRowValues((prev) => ({ ...prev, [row.key]: row.value!.toString() }));
        setRfqApplied((prev) => ({ ...prev, [row.key]: true }));
        appliedCount++;
      }
    });

    alert(`Applied ${appliedCount}/${vm.rows.length} suggested fields`);
  };

  const copyDebugJson = () => {
    const vm = buildRfqViewModel(rfqState.responses.autofill, rfqState.responses.envelope, rfqState.uiFlags);
    const debugData = {
      uiFlags: rfqState.uiFlags,
      requests: rfqState.requests,
      responses: rfqState.responses,
      merged: vm,
      current_row_values: rfqRowValues,
      applied_status: rfqApplied,
      needs_review_status: rfqNeedsReview,
    };

    navigator.clipboard
      .writeText(JSON.stringify(debugData, null, 2))
      .then(() => alert('Debug JSON copied to clipboard'))
      .catch(() => alert('Failed to copy debug JSON'));
  };

  /** -----------------------------
   * RFQ: run autofill + envelope (single flow)
   * ---------------------------- */
  const handleRunRfqAutofill = async () => {
    if (!partSummary) {
      setRfqState((p) => ({ ...p, hardError: 'Run inference first to generate part_summary.json' }));
      return;
    }
    if (!rfqPartNo.trim()) {
      setRfqState((p) => ({ ...p, hardError: 'Part No is required.' }));
      return;
    }

    const requestId = ++rfqRequestIdRef.current;

    setRfqState((prev) => ({
      ...prev,
      loading: true,
      hardError: null,
      warnings: [],
      responses: { autofill: null, envelope: null },
    }));

    try {
      const autofillPayload: RFQAutofillRequest = {
        rfq_id: jobId,
        part_no: normalizePartNo(rfqPartNo),
        mode: rfqState.uiFlags.mode === 'quick_quote' ? 'ENVELOPE' : 'GEOMETRY',
        vendor_quote_mode: rfqState.uiFlags.vendorQuoteMode,
        // Backend expects the full part_summary object for dimension extraction.
        source: { job_id: jobId, part_summary: partSummary as any, step_metrics: null },
        tolerances: { rm_od_allowance_in: rmOdAllowanceIn, rm_len_allowance_in: rmLenAllowanceIn },
        cost_inputs: rfqState.uiFlags.includeEstimateBlock ? rfqCostInputs : null,
      };

      const envelopePayload = {
        rfq_id: jobId,
        part_no: normalizePartNo(rfqPartNo),
        source: { job_id: jobId },
        allowances: { od_in: rmOdAllowanceIn, len_in: rmLenAllowanceIn },
        rounding: { od_step: 0.05, len_step: 0.10 },
      };

      setRfqState((prev) => ({ ...prev, requests: { autofillPayload, envelopePayload } }));

      // Step 1: autofill
      const autofillResult = await api.rfqAutofill(autofillPayload);
      if (requestId !== rfqRequestIdRef.current) return;

      // Step 2: envelope (optional, non-blocking)
      let envelopeResult: RFQEnvelopeResponse | null = null;
      if (rfqState.uiFlags.useEnvelope) {
        try {
          envelopeResult = await api.rfqEnvelope(envelopePayload);
        } catch (e) {
          console.warn('[AutoConvertResults] Envelope API failed:', e);
          setRfqState((prev) => ({ ...prev, warnings: ['Envelope unavailable — showing RM derived from autofill only.'] }));
        }
      }
      if (requestId !== rfqRequestIdRef.current) return;

      setRfqState((prev) => ({
        ...prev,
        responses: { autofill: autofillResult, envelope: envelopeResult },
      }));
    } catch (err) {
      if (requestId === rfqRequestIdRef.current) {
        setRfqState((prev) => ({ ...prev, hardError: err instanceof Error ? err.message : 'Failed to auto-fill RFQ' }));
      }
    } finally {
      if (requestId === rfqRequestIdRef.current) {
        setRfqState((prev) => ({ ...prev, loading: false }));
      }
    }
  };

  /** -----------------------------
   * RFQ Export
   * ---------------------------- */
  const refreshRfqExports = async () => {
    try {
      setRfqExportsLoading(true);
      setRfqExportError(null);
      const resp = await api.rfqListExports(jobId);
      setRfqExports(resp.files || []);
    } catch (err) {
      setRfqExportError(err instanceof Error ? err.message : 'Failed to load exports');
    } finally {
      setRfqExportsLoading(false);
    }
  };

  const handleExportRfq = async () => {
    if (!rfqState.responses.autofill || !rfqPartNo.trim()) {
      setRfqExportError('Auto-fill RFQ first and ensure Part No is entered.');
      return;
    }

    setRfqExportLoading(true);
    setRfqExportError(null);

    try {
      const requestBody: RFQAutofillRequest = {
        rfq_id: jobId,
        part_no: normalizePartNo(rfqPartNo),
        mode: rfqMode,
        vendor_quote_mode: rfqState.uiFlags.vendorQuoteMode,
        // Backend expects the full part_summary object for dimension extraction.
        source: { job_id: jobId, part_summary: partSummary as any, step_metrics: null },
        tolerances: { rm_od_allowance_in: rmOdAllowanceIn, rm_len_allowance_in: rmLenAllowanceIn },
        cost_inputs: rfqMode === 'ENVELOPE' && rfqIncludeEstimate ? rfqCostInputs : null,
      };

      const { blob, filename } = await api.rfqExportXlsx(requestBody);
      downloadBlob(blob, filename);
      await refreshRfqExports();
    } catch (err) {
      console.error('[AutoConvertResults] RFQ export error:', err);
      setRfqExportError(err instanceof Error ? err.message : 'Failed to export RFQ to Excel');
    } finally {
      setRfqExportLoading(false);
    }
  };

  const handleDownloadExport = async (filename: string) => {
    setRfqExportLoading(true);
    setRfqExportError(null);
    try {
      const { blob, filename: downloadedFilename } = await api.rfqDownloadExport(jobId, filename);
      downloadBlob(blob, downloadedFilename);
    } catch (err) {
      console.error('[AutoConvertResults] Download export error:', err);
      setRfqExportError(err instanceof Error ? err.message : 'Failed to download Excel file');
    } finally {
      setRfqExportLoading(false);
    }
  };

  /** -----------------------------
   * Render: Early states
   * ---------------------------- */
  if (loading) {
    return (
      <div className="auto-convert-results">
        <div className="auto-convert-loading">Loading results...</div>
      </div>
    );
  }

  if (error && !inferredStack) {
    return (
      <div className="auto-convert-results">
        <div className="auto-convert-error">Error: {error}</div>
      </div>
    );
  }

  /** -----------------------------
   * Render: Empty (no inferred stack)
   * ---------------------------- */
  if (!inferredStack) {
    return (
      <div className="auto-convert-empty">
        <h2>Auto Convert Mode</h2>

        <div
          style={{
            padding: '1.5rem',
            background: '#f8f9fa',
            border: '2px solid #007bff',
            borderRadius: '8px',
            marginBottom: '1.5rem',
          }}
        >
          <p style={{ fontSize: '1.1rem', marginBottom: '1rem', fontWeight: '500' }}>
            No inferred stack found. Run auto-detection and inference to extract dimensions from the PDF.
          </p>
          <p style={{ color: '#666', marginBottom: '1.5rem' }}>
            Make sure you have uploaded a PDF file first. Then click the button below to start the auto-detection process.
          </p>

          <button
            onClick={handleAutoDetect}
            disabled={detecting || inferring}
            style={{
              padding: '1rem 2rem',
              fontSize: '1.1rem',
              fontWeight: '600',
              background: detecting ? '#ccc' : '#007bff',
              color: 'white',
              border: 'none',
              borderRadius: '6px',
              cursor: detecting ? 'not-allowed' : 'pointer',
              boxShadow: detecting ? 'none' : '0 2px 4px rgba(0,0,0,0.2)',
              transition: 'all 0.2s',
            }}
            onMouseEnter={(e) => {
              if (!detecting && !inferring) {
                e.currentTarget.style.background = '#0056b3';
                e.currentTarget.style.transform = 'translateY(-1px)';
                e.currentTarget.style.boxShadow = '0 4px 8px rgba(0,0,0,0.3)';
              }
            }}
            onMouseLeave={(e) => {
              if (!detecting && !inferring) {
                e.currentTarget.style.background = '#007bff';
                e.currentTarget.style.transform = 'translateY(0)';
                e.currentTarget.style.boxShadow = '0 2px 4px rgba(0,0,0,0.2)';
              }
            }}
          >
            {detecting ? '🔄 Detecting Turned View...' : inferring ? '🔄 Inferring Stack...' : '🚀 1. Auto-Detect Turned View'}
          </button>
        </div>

        {/* RFQ AutoFill (visible even before inference; disabled until partSummary exists) */}
        <div className="rfq-autofill-section">
          <h3>RFQ AutoFill</h3>
          <p className="rfq-autofill-description">
            Enter a Part No and run Auto-Detect/Infer first to generate <code>part_summary.json</code>, then you can auto-fill RFQ fields.
          </p>

          <div className="rfq-autofill-controls">
            <div className="rfq-autofill-control">
              <label>Part No</label>
              <input type="text" value={rfqPartNo} onChange={(e) => setRfqPartNo(e.target.value)} placeholder="Enter part number" />
            </div>

            <div className="rfq-autofill-control">
              <label>RM OD Allowance (in)</label>
              <input type="number" step="0.01" value={rmOdAllowanceIn} onChange={(e) => setRmOdAllowanceIn(Number(e.target.value))} />
            </div>

            <div className="rfq-autofill-control">
              <label>RM Len Allowance (in)</label>
              <input type="number" step="0.01" value={rmLenAllowanceIn} onChange={(e) => setRmLenAllowanceIn(Number(e.target.value))} />
            </div>

            <div className="rfq-autofill-actions">
              <div className="rfq-mode-toggle">
                <label className="rfq-mode-option">
                  <input type="radio" name="rfq-mode-noinf" checked={rfqMode === 'ENVELOPE'} onChange={() => setRfqMode('ENVELOPE')} />
                  Quick Quote (Envelope)
                </label>
                <label className="rfq-mode-option rfq-mode-disabled" title="Detailed mode is beta">
                  <input type="radio" name="rfq-mode-noinf" checked={rfqMode === 'GEOMETRY'} onChange={() => setRfqMode('GEOMETRY')} />
                  Detailed (Geometry) <span className="rfq-beta-tag">beta</span>
                </label>
              </div>

              <label
                className="rfq-inline-check"
                style={{ marginTop: '0.5rem', fontSize: '0.9rem' }}
                title="Vendor Quote Mode: solid cylinder, fine rounding (matches Excel)"
              >
                <input
                  type="checkbox"
                  checked={rfqState.uiFlags.vendorQuoteMode}
                  onChange={(e) => setRfqState((prev) => ({ ...prev, uiFlags: { ...prev.uiFlags, vendorQuoteMode: e.target.checked } }))}
                />
                <span>📋 Vendor Quote Mode (Excel-exact)</span>
              </label>

              <button className="rfq-autofill-btn" disabled title="Run inference first to enable RFQ auto-fill">
                Auto-fill RFQ (run inference first)
              </button>
            </div>
          </div>

          {rfqMode === 'ENVELOPE' && (
            <div className="rfq-cost-panel">
              <div className="rfq-cost-panel-header">
                <button type="button" className="rfq-cost-panel-toggle" onClick={() => setRfqCostInputsOpen((v) => !v)}>
                  {rfqCostInputsOpen ? 'Hide cost inputs' : 'Show cost inputs'}
                </button>
                <label className="rfq-inline-check">
                  <input type="checkbox" checked={rfqIncludeEstimate} onChange={(e) => setRfqIncludeEstimate(e.target.checked)} />
                  Include estimate block
                </label>
              </div>

              {rfqCostInputsOpen && (
                <div className="rfq-cost-grid">
                  <div className="rfq-autofill-control">
                    <label>RM rate per kg</label>
                    <input
                      type="number"
                      step="0.01"
                      value={rfqCostInputs.rm_rate_per_kg}
                      onChange={(e) => setRfqCostInputs((p) => ({ ...p, rm_rate_per_kg: Number(e.target.value) }))}
                    />
                  </div>
                  <div className="rfq-autofill-control">
                    <label>Turning rate per min</label>
                    <input
                      type="number"
                      step="0.01"
                      value={rfqCostInputs.turning_rate_per_min}
                      onChange={(e) => setRfqCostInputs((p) => ({ ...p, turning_rate_per_min: Number(e.target.value) }))}
                    />
                  </div>
                  <div className="rfq-autofill-control">
                    <label>Roughing cost</label>
                    <input
                      type="number"
                      step="0.01"
                      value={rfqCostInputs.roughing_cost ?? 0}
                      onChange={(e) => setRfqCostInputs((p) => ({ ...p, roughing_cost: Number(e.target.value) }))}
                    />
                  </div>
                  <div className="rfq-autofill-control">
                    <label>Inspection cost</label>
                    <input
                      type="number"
                      step="0.01"
                      value={rfqCostInputs.inspection_cost ?? 0}
                      onChange={(e) => setRfqCostInputs((p) => ({ ...p, inspection_cost: Number(e.target.value) }))}
                    />
                  </div>
                  <div className="rfq-autofill-control">
                    <label>Material density (kg/m³)</label>
                    <input
                      type="number"
                      step="1"
                      value={rfqCostInputs.material_density_kg_m3 ?? 7850}
                      onChange={(e) => setRfqCostInputs((p) => ({ ...p, material_density_kg_m3: Number(e.target.value) }))}
                    />
                  </div>
                </div>
              )}
            </div>
          )}
        </div>

        {detectionError && (
          <div
            className="auto-convert-error"
            style={{
              marginTop: '1rem',
              padding: '1rem',
              background: '#fee',
              border: '1px solid #fcc',
              borderRadius: '4px',
              whiteSpace: 'pre-line',
            }}
          >
            <strong>⚠️ Auto-Detect Failed:</strong>
            <div style={{ marginTop: '0.5rem' }}>{detectionError}</div>
            {onSwitchToManual && (
              <div style={{ marginTop: '1rem' }}>
                <button
                  onClick={onSwitchToManual}
                  style={{
                    padding: '0.5rem 1rem',
                    background: '#007bff',
                    color: 'white',
                    border: 'none',
                    borderRadius: '4px',
                    cursor: 'pointer',
                    fontSize: '0.95rem',
                  }}
                >
                  Switch to Assisted Manual Mode
                </button>
              </div>
            )}
          </div>
        )}

        {detectionResult && detectionResult.ranked_views && detectionResult.ranked_views.length > 0 && (
          <div style={{ marginTop: '1rem', padding: '1rem', background: '#f8f9fa', border: '1px solid #dee2e6', borderRadius: '4px' }}>
            <h4>Detected Views (Ranked by Confidence):</h4>
            <table style={{ width: '100%', marginTop: '0.5rem', borderCollapse: 'collapse' }}>
              <thead>
                <tr style={{ borderBottom: '1px solid #dee2e6' }}>
                  <th style={{ textAlign: 'left', padding: '0.5rem' }}>Page</th>
                  <th style={{ textAlign: 'left', padding: '0.5rem' }}>View</th>
                  <th style={{ textAlign: 'left', padding: '0.5rem' }}>Confidence</th>
                  <th style={{ textAlign: 'left', padding: '0.5rem' }}>Axis</th>
                  <th style={{ textAlign: 'left', padding: '0.5rem' }}>Symmetry</th>
                  <th style={{ textAlign: 'left', padding: '0.5rem' }}>Action</th>
                </tr>
              </thead>
              <tbody>
                {detectionResult.ranked_views.map((view: any, idx: number) => {
                  const scores = view.scores || view;
                  const viewConf = scores.view_conf || scores.confidence || 0;
                  const axisConf = scores.axis_conf || 0;
                  const symConf = scores.sym_conf || 0;

                  return (
                    <tr key={idx} style={{ borderBottom: '1px solid #f0f0f0' }}>
                      <td style={{ padding: '0.5rem' }}>{view.page}</td>
                      <td style={{ padding: '0.5rem' }}>{view.view_index}</td>
                      <td style={{ padding: '0.5rem' }}>
                        <span
                          style={{
                            padding: '0.25rem 0.5rem',
                            borderRadius: '4px',
                            background: viewConf >= 0.65 ? '#d4edda' : viewConf >= 0.5 ? '#fff3cd' : '#f8d7da',
                            color: viewConf >= 0.65 ? '#155724' : viewConf >= 0.5 ? '#856404' : '#721c24',
                          }}
                        >
                          {(viewConf * 100).toFixed(1)}%
                        </span>
                      </td>
                      <td style={{ padding: '0.5rem' }}>{(axisConf * 100).toFixed(1)}%</td>
                      <td style={{ padding: '0.5rem' }}>{(symConf * 100).toFixed(1)}%</td>
                      <td style={{ padding: '0.5rem' }}>
                        <button
                          onClick={() => handleInferStack(view.page, view.view_index)}
                          disabled={inferring}
                          style={{
                            padding: '0.25rem 0.75rem',
                            fontSize: '0.875rem',
                            background: inferring ? '#ccc' : '#007bff',
                            color: 'white',
                            border: 'none',
                            borderRadius: '4px',
                            cursor: inferring ? 'not-allowed' : 'pointer',
                          }}
                        >
                          {inferring ? 'Inferring...' : 'Infer Stack'}
                        </button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>

            {(!detectionResult.best_view || detectionResult.best_view === null) && (
              <div style={{ marginTop: '1rem', padding: '0.75rem', background: '#fff3cd', border: '1px solid #ffc107', borderRadius: '4px' }}>
                <p style={{ margin: 0 }}>
                  <strong>⚠️ No view met the confidence threshold (≥65%).</strong>
                </p>
                <p style={{ margin: '0.5rem 0 0 0' }}>
                  You can still try inference on any view above, but results may be less accurate. Consider using Assisted Manual mode for better
                  results.
                </p>
              </div>
            )}
          </div>
        )}

        {detectionResult && detectionResult.best_view && (
          <div className="detection-success" style={{ marginTop: '1rem', padding: '1rem', background: '#d4edda', border: '1px solid #28a745', borderRadius: '4px' }}>
            <p>
              <strong>✓ High Confidence View Detected!</strong>
            </p>
            <p>
              Page {detectionResult.best_view.page}, View {detectionResult.best_view.view_index}
            </p>
            {(() => {
              const conf =
                (detectionResult as any)?.best_view?.scores?.view_conf ??
                (detectionResult as any)?.best_view?.confidence ??
                null;
              return <p>Confidence: {conf != null ? `${(conf * 100).toFixed(1)}%` : '—'}</p>;
            })()}
            {!inferredStack && (
              <button
                onClick={() => handleInferStack(detectionResult.best_view.page, detectionResult.best_view.view_index)}
                disabled={inferring}
                style={{ marginTop: '0.5rem', padding: '0.5rem 1rem', background: '#28a745', color: 'white', border: 'none', borderRadius: '4px', cursor: 'pointer' }}
              >
                {inferring ? 'Inferring Stack...' : 'Infer Stack from Detected View'}
              </button>
            )}
          </div>
        )}

        <div style={{ marginTop: '1rem', padding: '1rem', background: '#f8f9fa', borderRadius: '4px' }}>
          <h3>How it works:</h3>
          <ol style={{ paddingLeft: '1.5rem' }}>
            <li>Click "Auto-Detect Turned View" to analyze the PDF and find the best section view</li>
            <li>If a view is detected, click "Infer Stack" to extract dimensions</li>
            <li>Review the inferred segments and confidence scores</li>
            <li>Generate STEP file if needed, or switch to Assisted Manual for corrections</li>
          </ol>
        </div>
      </div>
    );
  }

  /** -----------------------------
   * Render: Main results
   * ---------------------------- */
  return (
    <div className="auto-convert-results">
      <div className="auto-convert-header">
        <h2>Auto Convert Results</h2>
        {overallConfidence !== null && (
          <div className={`overall-confidence-badge ${getConfidenceBadgeClass(overallConfidence)}`}>
            Overall Confidence: {getConfidenceLabel(overallConfidence)} ({(overallConfidence * 100).toFixed(0)}%)
          </div>
        )}
      </div>

      {hasLowConfidence && (
        <div className="low-confidence-warning">
          <h3>⚠️ Low Confidence Detection</h3>
          <p>The auto-detection confidence is below the recommended threshold. For more accurate results, consider switching to Assisted Manual mode.</p>
          <button className="switch-to-manual-btn" onClick={() => onSwitchToManual?.()}>
            Switch to Assisted Manual
          </button>
        </div>
      )}

      {warnings.length > 0 && (
        <div className="auto-convert-warnings">
          <h3>Warnings</h3>
          <ul>
            {warnings.map((warning, i) => (
              <li key={i}>{warning}</li>
            ))}
          </ul>
        </div>
      )}

      <div className="segments-section">
        <h3>Inferred Segments</h3>
        <div className="segments-table-container">
          <table className="segments-table">
            <thead>
              <tr>
                <th>Segment</th>
                <th>Z Start (in)</th>
                <th>Z End (in)</th>
                <th>OD (in)</th>
                <th>ID (in)</th>
                <th>Wall (in)</th>
                <th>Volume (in³)</th>
                <th>Confidence</th>
              </tr>
            </thead>
            <tbody>
              {segments.map((seg, idx) => (
                <tr key={idx}>
                  <td>{idx + 1}</td>
                  <td>{seg.z_start.toFixed(3)}</td>
                  <td>{seg.z_end.toFixed(3)}</td>
                  <td>{seg.od_diameter.toFixed(3)}</td>
                  <td>{seg.id_diameter.toFixed(3)}</td>
                  <td>
                    {seg.wall_thickness !== undefined ? seg.wall_thickness.toFixed(3) : ((seg.od_diameter - seg.id_diameter) / 2).toFixed(3)}
                  </td>
                  <td>
                    {(() => {
                      const volume = typeof seg.volume_in3 === 'number' ? seg.volume_in3 : calcVolumeIn3(seg);
                      return volume !== null ? volume.toFixed(6) : '—';
                    })()}
                  </td>
                  <td>
                    {seg.confidence !== undefined ? (
                      <span className={`confidence-badge ${getConfidenceBadgeClass(seg.confidence)}`}>
                        {getConfidenceLabel(seg.confidence)} ({(seg.confidence * 100).toFixed(0)}%)
                      </span>
                    ) : (
                      <span className="confidence-badge confidence-unknown">—</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {totalsDisplay && (
        <div className="totals-section">
          <h3>Totals</h3>
          <div className="totals-grid">
            <div className="total-card">
              <div className="total-label">Total Volume</div>
              <div className="total-value">{formatMaybeNumber(totalsDisplay.totalVolumeIn3, 6)} in³</div>
            </div>
            <div className="total-card">
              <div className="total-label">Total Surface Area</div>
              <div className="total-value">{formatMaybeNumber(totalsDisplay.totalSurfaceAreaIn2, 6)} in²</div>
            </div>
            <div className="total-card">
              <div className="total-label">OD Surface Area</div>
              <div className="total-value">{formatMaybeNumber(totalsDisplay.odAreaIn2, 6)} in²</div>
            </div>
            <div className="total-card">
              <div className="total-label">ID Surface Area</div>
              <div className="total-value">{formatMaybeNumber(totalsDisplay.idAreaIn2, 6)} in²</div>
            </div>
          </div>
        </div>
      )}

      {/* RFQ AutoFill */}
      <div className="rfq-autofill-section">
        <h3>RFQ AutoFill</h3>
        <p className="rfq-autofill-description">
          Compute suggested RFQ fields from the inferred <code>part_summary.json</code>.
        </p>

        <div className="rfq-autofill-controls">
          <div className="rfq-autofill-control">
            <label>Part No</label>
            <input type="text" value={rfqPartNo} onChange={(e) => setRfqPartNo(e.target.value)} placeholder="Enter part number (if not detected)" />
          </div>

          <div className="rfq-autofill-control">
            <label>RM OD Allowance (in)</label>
            <input type="number" step="0.01" value={rmOdAllowanceIn} onChange={(e) => setRmOdAllowanceIn(Number(e.target.value))} />
          </div>

          <div className="rfq-autofill-control">
            <label>RM Len Allowance (in)</label>
            <input type="number" step="0.01" value={rmLenAllowanceIn} onChange={(e) => setRmLenAllowanceIn(Number(e.target.value))} />
          </div>

          <div className="rfq-autofill-actions">
            <div className="rfq-mode-toggle">
              <label className="rfq-mode-option">
                <input type="radio" name="rfq-mode" checked={rfqMode === 'ENVELOPE'} onChange={() => setRfqMode('ENVELOPE')} />
                Quick Quote (Envelope)
              </label>
              <label className="rfq-mode-option rfq-mode-disabled" title="Detailed mode is beta">
                <input type="radio" name="rfq-mode" checked={rfqMode === 'GEOMETRY'} onChange={() => setRfqMode('GEOMETRY')} />
                Detailed (Geometry) <span className="rfq-beta-tag">beta</span>
              </label>
            </div>

            <label className="rfq-inline-check" style={{ marginTop: '0.5rem', fontSize: '0.9rem' }}>
              <input
                type="checkbox"
                checked={rfqState.uiFlags.useEnvelope}
                onChange={(e) => setRfqState((prev) => ({ ...prev, uiFlags: { ...prev.uiFlags, useEnvelope: e.target.checked } }))}
              />
              <span>🎯 Use Geometry Envelope (RAW stock sizes)</span>
            </label>

            {!rfqState.uiFlags.useEnvelope && <div className="rfq-warning-banner">⚠️ Legacy mode: dimensions may be inaccurate; geometry envelope is recommended.</div>}

            <label className="rfq-inline-check" style={{ marginTop: '0.5rem', fontSize: '0.9rem' }} title="Vendor Quote Mode: solid cylinder, fine rounding (matches Excel)">
              <input
                type="checkbox"
                checked={rfqState.uiFlags.vendorQuoteMode}
                onChange={(e) => setRfqState((prev) => ({ ...prev, uiFlags: { ...prev.uiFlags, vendorQuoteMode: e.target.checked } }))}
              />
              <span>📋 Vendor Quote Mode (Excel-exact)</span>
            </label>

            <button className="rfq-autofill-btn" disabled={rfqState.loading || !partSummary || !rfqPartNo.trim()} onClick={handleRunRfqAutofill}>
              {rfqState.loading ? 'Auto-filling…' : 'Auto-fill RFQ'}
            </button>
          </div>
        </div>

        {rfqMode === 'ENVELOPE' && (
          <div className="rfq-cost-panel">
            <div className="rfq-cost-panel-header">
              <button type="button" className="rfq-cost-panel-toggle" onClick={() => setRfqCostInputsOpen((v) => !v)}>
                {rfqCostInputsOpen ? 'Hide cost inputs' : 'Show cost inputs'}
              </button>
              <label className="rfq-inline-check">
                <input type="checkbox" checked={rfqIncludeEstimate} onChange={(e) => setRfqIncludeEstimate(e.target.checked)} />
                Include estimate block
              </label>
            </div>
            {rfqCostInputsOpen && (
              <div className="rfq-cost-grid">
                <div className="rfq-autofill-control">
                  <label>RM rate per kg</label>
                  <input
                    type="number"
                    step="0.01"
                    value={rfqCostInputs.rm_rate_per_kg}
                    onChange={(e) => setRfqCostInputs((p) => ({ ...p, rm_rate_per_kg: Number(e.target.value) }))}
                  />
                </div>
                <div className="rfq-autofill-control">
                  <label>Turning rate per min</label>
                  <input
                    type="number"
                    step="0.01"
                    value={rfqCostInputs.turning_rate_per_min}
                    onChange={(e) => setRfqCostInputs((p) => ({ ...p, turning_rate_per_min: Number(e.target.value) }))}
                  />
                </div>
                <div className="rfq-autofill-control">
                  <label>Roughing cost</label>
                  <input
                    type="number"
                    step="0.01"
                    value={rfqCostInputs.roughing_cost ?? 0}
                    onChange={(e) => setRfqCostInputs((p) => ({ ...p, roughing_cost: Number(e.target.value) }))}
                  />
                </div>
                <div className="rfq-autofill-control">
                  <label>Inspection cost</label>
                  <input
                    type="number"
                    step="0.01"
                    value={rfqCostInputs.inspection_cost ?? 0}
                    onChange={(e) => setRfqCostInputs((p) => ({ ...p, inspection_cost: Number(e.target.value) }))}
                  />
                </div>
                <div className="rfq-autofill-control">
                  <label>Material density (kg/m³)</label>
                  <input
                    type="number"
                    step="1"
                    value={rfqCostInputs.material_density_kg_m3 ?? 7850}
                    onChange={(e) => setRfqCostInputs((p) => ({ ...p, material_density_kg_m3: Number(e.target.value) }))}
                  />
                </div>
              </div>
            )}
          </div>
        )}

        {rfqState.hardError && <div className="rfq-autofill-error">Error: {rfqState.hardError}</div>}

        {rfqState.uiFlags.useEnvelope && rfqState.responses.envelope && (
          <div className={`rfq-envelope-banner rfq-envelope-${(rfqState.responses.envelope.status || 'unknown').toLowerCase()}`}>
            {rfqState.responses.envelope.status === 'AUTO_FILLED' && '🎯 Auto-filled from 3D geometry'}
            {rfqState.responses.envelope.status === 'NEEDS_REVIEW' && '⚠️ Needs review'}
            {rfqState.responses.envelope.status === 'REJECTED' && `❌ Rejected: ${(rfqState.responses.envelope.reasons || []).join(', ')}`}
          </div>
        )}

        {rfqState.uiFlags.useEnvelope && rfqState.warnings.length > 0 && (
          <div className="rfq-envelope-banner rfq-envelope-unavailable">⚠️ {rfqState.warnings.join(', ')}</div>
        )}

        {rfqState.uiFlags.useEnvelope && rfqState.responses.envelope && rfqState.responses.envelope.status !== 'REJECTED' && (
          <details className="rfq-envelope-details" open={envelopeDetailsOpen}>
            <summary onClick={() => setEnvelopeDetailsOpen(!envelopeDetailsOpen)}>📊 Envelope details</summary>
            <div className="rfq-envelope-details-body">
              <div className="rfq-envelope-grid">
                {[
                  { label: 'Finish Max OD (in)', field: rfqState.responses.envelope.fields.finish_max_od_in, showReasons: true },
                  { label: 'Finish Length (in)', field: rfqState.responses.envelope.fields.finish_len_in, showReasons: true },
                  { label: 'RAW Max OD (in) [stock]', field: rfqState.responses.envelope.fields.raw_max_od_in, showReasons: true },
                  { label: 'RAW Max Length (in) [cut]', field: rfqState.responses.envelope.fields.raw_len_in, showReasons: true },
                  { label: 'RM OD (in)', field: rfqState.responses.autofill?.fields.rm_od_in, showReasons: false },
                  { label: 'RM Length (in)', field: rfqState.responses.autofill?.fields.rm_len_in, showReasons: false },
                ].map(({ label, field, showReasons }) => (
                  <div key={label} className="rfq-envelope-row">
                    <div className="rfq-envelope-label">{label}</div>
                    <div className="rfq-envelope-value">{field?.value ? field.value.toFixed(4) : '—'}</div>
                    <div className="rfq-envelope-confidence">{field?.confidence ? `${(field.confidence * 100).toFixed(0)}%` : '—'}</div>
                    <div className="rfq-envelope-reasons">
                      {showReasons &&
                        (rfqState.responses.envelope?.reasons || []).map((reason) => (
                          <span key={reason} className={`rfq-reason-chip rfq-reason-${reason.toLowerCase()}`}>
                            {reason}
                          </span>
                        ))}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          </details>
        )}

        {rfqVendorExtract && (
          <details className="rfq-pdf-hints" open={pdfHintsOpen}>
            <summary onClick={() => setPdfHintsOpen(!pdfHintsOpen)}>📄 Show PDF hints</summary>
            <div className="rfq-pdf-hints-body">
              {rfqVendorExtract.pdf_hint?.finish_od_in && (
                <div
                  className={`rfq-pdf-hint-row ${getMismatchClass(
                    rfqVendorExtract.pdf_hint.finish_od_in.value,
                    rfqState.responses.envelope?.fields.finish_max_od_in.value
                  )}`}
                >
                  <span>PDF OD hint:</span>
                  <span>{rfqVendorExtract.pdf_hint.finish_od_in.value}</span>
                  <span>
                    {rfqVendorExtract.pdf_hint.finish_od_in.confidence ? (rfqVendorExtract.pdf_hint.finish_od_in.confidence * 100).toFixed(0) : '?'}%
                  </span>
                </div>
              )}
              {rfqVendorExtract.pdf_hint?.finish_len_in && (
                <div
                  className={`rfq-pdf-hint-row ${getMismatchClass(
                    rfqVendorExtract.pdf_hint.finish_len_in.value,
                    rfqState.responses.envelope?.fields.finish_len_in.value
                  )}`}
                >
                  <span>PDF Length hint:</span>
                  <span>{rfqVendorExtract.pdf_hint.finish_len_in.value}</span>
                  <span>
                    {rfqVendorExtract.pdf_hint.finish_len_in.confidence ? (rfqVendorExtract.pdf_hint.finish_len_in.confidence * 100).toFixed(0) : '?'}%
                  </span>
                </div>
              )}
              <div className="rfq-pdf-hint-note">ℹ️ PDF hints are for reference only and do not override 3D geometry.</div>
            </div>
          </details>
        )}

        {rfqState.responses.autofill && (
          <div className="rfq-autofill-results">
            <div className="rfq-autofill-summary">
              <div className="rfq-status">
                <strong>Status:</strong>{' '}
                <span className={`rfq-status-badge rfq-status-${(rfqState.responses.autofill?.status || 'unknown').toLowerCase()}`}>
                  {rfqState.responses.autofill?.status || 'Unknown'}
                </span>
              </div>

              <div className="rfq-reasons">
                <strong>Reasons:</strong>{' '}
                {rfqState.responses.autofill?.reasons && rfqState.responses.autofill.reasons.length ? (
                  <span className="rfq-reason-chips">
                    {rfqState.responses.autofill.reasons.map((r) => (
                      <span key={r} className="rfq-reason-chip">
                        {r}
                      </span>
                    ))}
                  </span>
                ) : (
                  '—'
                )}
              </div>

              {rfqState.uiFlags.vendorQuoteMode && (
                <div className="rfq-ui-flags">
                  <span className="rfq-ui-flag">VENDOR_QUOTE_MODE</span>
                </div>
              )}
              {rfqState.uiFlags.useEnvelope && (
                <div className="rfq-ui-flags">
                  <span className="rfq-ui-flag">ENVELOPE_MODE</span>
                </div>
              )}
            </div>

            {/* Envelope rows */}
            <div className="rfq-envelope-subcard">
              <h4>📐 Envelope Details</h4>
              <div className="rfq-envelope-rows">
                {rfqViewModel.rows.map((row) => (
                  <div key={row.key} className={`rfq-envelope-row rfq-category-${row.kind}`}>
                    <div className="rfq-field-info">
                      <div className="rfq-field-label">
                        {row.label}
                        {row.kind === 'raw' && <span className="rfq-raw-badge">RAW/Stock</span>}
                      </div>
                      <div className="rfq-field-value">
                        <span className="rfq-value-text">{row.value !== null ? row.value.toFixed(3) : '—'}</span>
                        <span className={`rfq-confidence-indicator ${getConfidenceClass(row.conf)}`}>
                          {row.conf !== null ? `${(row.conf * 100).toFixed(0)}%` : '—'}
                        </span>
                      </div>
                      <div className="rfq-field-source">{row.source || '—'}</div>
                    </div>
                  </div>
                ))}
              </div>
            </div>

            {/* Apply suggestions */}
            <div className="rfq-apply-section" style={{ marginTop: '1.5rem', padding: '1rem', background: '#f8f9fa', borderRadius: '8px' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: '1rem', marginBottom: '1rem' }}>
                <button
                  type="button"
                  className="rfq-autofill-btn"
                  onClick={handleApplySuggestions}
                  disabled={!rfqState.responses.autofill}
                  style={{ fontSize: '0.9rem', padding: '0.5rem 1rem' }}
                >
                  ✓ Apply Suggestions
                </button>
                <div style={{ fontSize: '0.9rem', color: '#666' }}>Threshold: ≥{(rfqState.uiFlags.applyThreshold * 100).toFixed(0)}% confidence</div>
              </div>

              <div className="rfq-apply-grid">
                {rfqViewModel.rows.map((row) => (
                  <label key={row.key} className="rfq-apply-item">
                    <input
                      type="checkbox"
                      checked={rfqApplySelections[row.key] || false}
                      onChange={(e) => setRfqApplySelections((prev) => ({ ...prev, [row.key]: e.target.checked }))}
                      disabled={row.value === null || row.conf === null || row.conf < rfqState.uiFlags.applyThreshold}
                    />
                    <span className="rfq-apply-label">
                      {row.label}: {row.value !== null ? row.value.toFixed(4) : '—'}
                      {row.conf !== null && (
                        <span className={`rfq-confidence-badge rfq-confidence-${getConfidenceClass(row.conf)}`}>{(row.conf * 100).toFixed(0)}%</span>
                      )}
                    </span>
                  </label>
                ))}
              </div>
            </div>

            {/* Editable cells panel (kept) */}
            <div className="rfq-apply-panel">
              <div className="rfq-edit-grid">
                {(
                  [
                    ['finish_od_in', 'Finish OD (Inch)'],
                    ['finish_len_in', 'Finish Length (Inch)'],
                    ['finish_id_in', 'Finish ID (Inch)'],
                    ['rm_od_in', 'RM OD (Inch)'],
                    ['rm_len_in', 'RM Length (Inch)'],
                  ] as const
                ).map(([k, label]) => {
                  const fv = (rfqState.responses.autofill?.fields as any)?.[k] as { value: number | null; confidence: number; source: string };
                  const border = getRFQFieldBorderClass(k, rfqState.responses.autofill?.reasons || []);
                  const confClass = getRFQConfidenceBadgeClass(fv?.confidence ?? 0);

                  return (
                    <div key={k} className={`rfq-edit-cell ${border}`}>
                      <div className="rfq-edit-label">{label}</div>
                      <input
                        className="rfq-edit-input"
                        type="number"
                        step="0.001"
                        value={rfqRowValues[k] ?? ''}
                        onChange={(e) => setRfqRowValues((p) => ({ ...p, [k]: e.target.value }))}
                      />
                      <div className="rfq-edit-meta">
                        <span className={`confidence-badge ${confClass}`}>{((fv?.confidence ?? 0) * 100).toFixed(0)}%</span>
                        {rfqApplied[k] ? (
                          <span className="rfq-edit-applied">Applied ✓</span>
                        ) : rfqNeedsReview[k] ? (
                          <span className="rfq-edit-review">Needs review — Suggested: {formatMaybeNumber(fv?.value, 3)}</span>
                        ) : (
                          <span className="rfq-edit-suggested">Suggested: {formatMaybeNumber(fv?.value, 3)}</span>
                        )}
                      </div>
                    </div>
                  );
                })}
              </div>

              {rfqState.responses.autofill?.estimate && (
                <div className="rfq-estimate-block">
                  <h4>Quick Quote Estimate</h4>
                  <div className="rfq-autofill-table-container">
                    <table className="rfq-autofill-table">
                      <thead>
                        <tr>
                          <th>Item</th>
                          <th>Value</th>
                          <th>Confidence</th>
                          <th>Source</th>
                        </tr>
                      </thead>
                      <tbody>
                        {(
                          [
                            ['rm_weight_kg', 'RM Weight (kg)'],
                            ['material_cost', 'Material Cost'],
                            ['turning_minutes', 'Turning Minutes'],
                            ['turning_cost', 'Turning Cost'],
                            ['subtotal', 'Subtotal'],
                          ] as const
                        ).map(([ek, label]) => {
                          const ev = (rfqState.responses.autofill?.estimate as any)?.[ek] as { value: number | null; confidence: number; source: string };
                          return (
                            <tr key={ek}>
                              <td>{label}</td>
                              <td className={`rfq-autofill-mono ${getRFQValueCellClass(ev?.value, ev?.confidence ?? 0, rfqState.responses.autofill?.status || 'UNKNOWN', rfqState.responses.autofill?.reasons || [])}`}>
                                {formatMaybeNumber(ev?.value, 3)}
                              </td>
                              <td>
                                <span className={`confidence-badge ${getRFQConfidenceBadgeClass(ev?.confidence ?? 0)}`}>{((ev?.confidence ?? 0) * 100).toFixed(0)}%</span>
                              </td>
                              <td className="rfq-autofill-mono">{ev?.source || '—'}</td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                  </div>
                </div>
              )}
            </div>

            <details className="rfq-autofill-details" open={rfqState.responses.autofill?.status === 'REJECTED'}>
              <summary>Reasons & debug</summary>
              <div className="rfq-autofill-details-body">
                <div className="rfq-debug-actions">
                  <button type="button" className="rfq-debug-btn" onClick={copyDebugJson}>
                    📋 Copy debug JSON
                  </button>
                </div>
                <div>
                  <strong>Reasons:</strong> {(rfqState.responses.autofill?.reasons || []).length ? rfqState.responses.autofill!.reasons.join(', ') : '—'}
                </div>
                <div className="rfq-autofill-debug">
                  <strong>Debug:</strong> <span className="rfq-autofill-mono">{rfqViewModel.debugLine || '—'}</span>
                </div>
              </div>
            </details>

            {/* Export */}
            <div className="rfq-export-section">
              <h4>Export to Excel</h4>
              <div style={{ display: 'flex', gap: '1rem', alignItems: 'center', marginBottom: '1rem' }}>
                <button
                  type="button"
                  className="rfq-autofill-btn"
                  disabled={rfqExportLoading || !rfqState.responses.autofill || !rfqPartNo.trim()}
                  onClick={handleExportRfq}
                  title={!rfqState.responses.autofill ? 'Auto-fill RFQ first' : !rfqPartNo.trim() ? 'Enter Part No' : 'Export to Excel'}
                >
                  {rfqExportLoading ? 'Exporting...' : '📊 Export to Excel'}
                </button>
                <button type="button" className="rfq-autofill-btn" disabled={rfqExportsLoading} onClick={refreshRfqExports} style={{ backgroundColor: '#6c757d' }}>
                  {rfqExportsLoading ? 'Refreshing...' : '🔄 Refresh List'}
                </button>
              </div>

              {rfqExportError && <div className="rfq-autofill-error" style={{ marginBottom: '1rem', marginTop: '0' }}>Export Error: {rfqExportError}</div>}

              {rfqExports.length > 0 && (
                <div>
                  <h5>Recent Exports (newest first):</h5>
                  <div style={{ maxHeight: '200px', overflowY: 'auto', border: '1px solid #dee2e6', borderRadius: '4px' }}>
                    <table style={{ width: '100%', fontSize: '0.9rem' }}>
                      <thead style={{ backgroundColor: '#e9ecef', position: 'sticky', top: 0 }}>
                        <tr>
                          <th style={{ padding: '0.5rem', textAlign: 'left' }}>File</th>
                          <th style={{ padding: '0.5rem', textAlign: 'left' }}>Size</th>
                          <th style={{ padding: '0.5rem', textAlign: 'left' }}>Modified</th>
                          <th style={{ padding: '0.5rem', textAlign: 'center' }}>Action</th>
                        </tr>
                      </thead>
                      <tbody>
                        {rfqExports.map((file, index) => (
                          <tr key={file.filename} style={{ borderBottom: index < rfqExports.length - 1 ? '1px solid #dee2e6' : 'none' }}>
                            <td style={{ padding: '0.5rem', fontFamily: 'monospace', fontSize: '0.85rem' }}>{file.filename}</td>
                            <td style={{ padding: '0.5rem' }}>{formatFileSize(file.size_bytes)}</td>
                            <td style={{ padding: '0.5rem', fontSize: '0.8rem' }}>{formatDateTime(file.mtime_utc)}</td>
                            <td style={{ padding: '0.5rem', textAlign: 'center' }}>
                              <button
                                type="button"
                                className="download-link"
                                onClick={() => handleDownloadExport(file.filename)}
                                disabled={rfqExportLoading}
                                style={{
                                  padding: '0.25rem 0.5rem',
                                  fontSize: '0.8rem',
                                  backgroundColor: '#007bff',
                                  color: 'white',
                                  border: 'none',
                                  borderRadius: '3px',
                                  cursor: rfqExportLoading ? 'not-allowed' : 'pointer',
                                  opacity: rfqExportLoading ? 0.6 : 1,
                                }}
                              >
                                {rfqExportLoading ? '...' : '⬇️'}
                              </button>
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              )}

              {rfqExports.length === 0 && !rfqExportsLoading && <p style={{ color: '#666', fontStyle: 'italic' }}>No exports yet. Click "Export to Excel" to create one.</p>}
            </div>
          </div>
        )}
      </div>

      {/* Detected Features */}
      <div className="detected-features-section" style={{ marginTop: '2rem' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '1rem' }}>
          <h3 style={{ margin: 0 }}>Detected Features</h3>
          <button
            onClick={async () => {
              try {
                const response = await fetch(api.getPdfUrl(jobId, 'outputs/part_summary.json'));
                if (response.ok) {
                  const data = await response.json();
                  setPartSummary(data);
                  console.log('[AutoConvertResults] Features refreshed:', data.features ? 'Found' : 'None');
                }
              } catch (err) {
                console.error('[AutoConvertResults] Error refreshing features:', err);
              }
            }}
            style={{
              padding: '0.4rem 0.8rem',
              fontSize: '0.85rem',
              background: '#2a4a7a',
              color: '#fff',
              border: 'none',
              borderRadius: '4px',
              cursor: 'pointer',
            }}
          >
            Refresh Features
          </button>
        </div>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: '1rem', marginBottom: '1rem' }}>
          <label style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
            <input type="checkbox" checked={showHoles} onChange={(e) => setShowHoles(e.target.checked)} />
            Show holes
          </label>
          <label style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
            <input type="checkbox" checked={showSlots} onChange={(e) => setShowSlots(e.target.checked)} />
            Show slots
          </label>
          <label style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
            <input type="checkbox" checked={showChamfers} onChange={(e) => setShowChamfers(e.target.checked)} />
            Show chamfers
          </label>
          <label style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
            <input type="checkbox" checked={showFillets} onChange={(e) => setShowFillets(e.target.checked)} />
            Show fillets (visual)
          </label>
        </div>

        {featureWarnings.length > 0 && (
          <div style={{ marginBottom: '1rem', display: 'flex', flexWrap: 'wrap', gap: '0.5rem' }}>
            {featureWarnings.map((warning: string, idx: number) => (
              <span
                key={`${warning}-${idx}`}
                style={{
                  background: '#4a3b14',
                  color: '#f4d17b',
                  border: '1px solid #7a5a1a',
                  borderRadius: '12px',
                  padding: '0.2rem 0.6rem',
                  fontSize: '0.85rem',
                }}
              >
                {warning}
              </span>
            ))}
          </div>
        )}

        {!detectedFeatures && (
          <p style={{ color: '#888', fontStyle: 'italic' }}>No features detected yet.</p>
        )}

        {detectedFeatures && (
          <div style={{ display: 'grid', gap: '1.5rem' }}>
            <div className="feature-panel">
              <h4>Holes ({holes.length})</h4>
              {holes.length === 0 && <p style={{ color: '#888', fontStyle: 'italic' }}>No holes detected.</p>}
              {Object.entries(holesByPattern).map(([pattern, list]) => (
                <div key={pattern} style={{ marginBottom: '0.75rem' }}>
                  <div style={{ fontWeight: 600, marginBottom: '0.5rem' }}>
                    Pattern: {pattern} ({list.length})
                  </div>
                  <div style={{ display: 'grid', gap: '0.5rem' }}>
                    {list.map((hole: any, idx: number) => (
                      <div key={`${pattern}-${idx}`} style={{ padding: '0.6rem', border: '1px solid #333', borderRadius: '6px' }}>
                        <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                          <div>
                            Ø {hole.diameter?.toFixed?.(4) ?? hole.diameter} in
                            {hole.depth ? `, depth ${hole.depth?.toFixed?.(4) ?? hole.depth} in` : ' (THRU)'}
                            {hole.kind ? `, ${hole.kind}` : ''}
                          </div>
                          <span className={`confidence-badge confidence-${getConfidenceLevel(hole.confidence || 0)}`}>
                            {((hole.confidence || 0) * 100).toFixed(0)}%
                          </span>
                        </div>
                        <div style={{ marginTop: '0.3rem', color: '#aaa', fontSize: '0.9rem' }}>
                          Page {hole.source_page ?? '—'} · View {hole.source_view_index ?? '—'}
                        </div>
                        {hole.notes && <div style={{ marginTop: '0.3rem', color: '#888' }}>{hole.notes}</div>}
                      </div>
                    ))}
                  </div>
                </div>
              ))}
            </div>

            <div className="feature-panel">
              <h4>Slots ({slots.length})</h4>
              {slots.length === 0 && <p style={{ color: '#888', fontStyle: 'italic' }}>No slots detected.</p>}
              {slots.map((slot: any, idx: number) => (
                <div key={`slot-${idx}`} style={{ padding: '0.6rem', border: '1px solid #333', borderRadius: '6px', marginBottom: '0.5rem' }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                    <div>
                      {slot.width?.toFixed?.(4) ?? slot.width} in × {slot.length?.toFixed?.(4) ?? slot.length} in
                      {slot.depth ? `, depth ${slot.depth?.toFixed?.(4) ?? slot.depth} in` : ''}
                      {slot.orientation ? `, ${slot.orientation}` : ''}
                      {slot.count ? `, ${slot.count}X` : ''}
                    </div>
                    <span className={`confidence-badge confidence-${getConfidenceLevel(slot.confidence || 0)}`}>
                      {((slot.confidence || 0) * 100).toFixed(0)}%
                    </span>
                  </div>
                  <div style={{ marginTop: '0.3rem', color: '#aaa', fontSize: '0.9rem' }}>
                    Page {slot.source_page ?? '—'} · View {slot.source_view_index ?? '—'}
                  </div>
                  {slot.notes && <div style={{ marginTop: '0.3rem', color: '#888' }}>{slot.notes}</div>}
                </div>
              ))}
            </div>

            <div className="feature-panel">
              <h4>Chamfers ({chamfers.length})</h4>
              {chamfers.length === 0 && <p style={{ color: '#888', fontStyle: 'italic' }}>No chamfers detected.</p>}
              {chamfers.map((chamfer: any, idx: number) => (
                <div key={`chamfer-${idx}`} style={{ padding: '0.6rem', border: '1px solid #333', borderRadius: '6px', marginBottom: '0.5rem' }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                    <div>
                      C{chamfer.size?.toFixed?.(4) ?? chamfer.size} × {chamfer.angle ?? 45}°
                      {chamfer.edge_location ? `, ${chamfer.edge_location}` : ''}
                    </div>
                    <span className={`confidence-badge confidence-${getConfidenceLevel(chamfer.confidence || 0)}`}>
                      {((chamfer.confidence || 0) * 100).toFixed(0)}%
                    </span>
                  </div>
                  <div style={{ marginTop: '0.3rem', color: '#aaa', fontSize: '0.9rem' }}>
                    Page {chamfer.source_page ?? '—'} · View {chamfer.source_view_index ?? '—'}
                  </div>
                  {chamfer.notes && <div style={{ marginTop: '0.3rem', color: '#888' }}>{chamfer.notes}</div>}
                </div>
              ))}
            </div>

            <div className="feature-panel">
              <h4>Fillets ({fillets.length})</h4>
              {fillets.length === 0 && <p style={{ color: '#888', fontStyle: 'italic' }}>No fillets detected.</p>}
              {fillets.map((fillet: any, idx: number) => (
                <div key={`fillet-${idx}`} style={{ padding: '0.6rem', border: '1px solid #333', borderRadius: '6px', marginBottom: '0.5rem' }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                    <div>
                      R{fillet.radius?.toFixed?.(4) ?? fillet.radius}
                      {fillet.edge_location ? `, ${fillet.edge_location}` : ''}
                    </div>
                    <span className={`confidence-badge confidence-${getConfidenceLevel(fillet.confidence || 0)}`}>
                      {((fillet.confidence || 0) * 100).toFixed(0)}%
                    </span>
                  </div>
                  <div style={{ marginTop: '0.3rem', color: '#aaa', fontSize: '0.9rem' }}>
                    Page {fillet.source_page ?? '—'} · View {fillet.source_view_index ?? '—'}
                  </div>
                  {fillet.notes && <div style={{ marginTop: '0.3rem', color: '#888' }}>{fillet.notes}</div>}
                </div>
              ))}
            </div>

            <div className="feature-panel">
              <h4>Threads ({threads.length})</h4>
              {threads.length === 0 && <p style={{ color: '#888', fontStyle: 'italic' }}>No threads detected.</p>}
              {threads.map((thread: any, idx: number) => (
                <div key={`thread-${idx}`} style={{ padding: '0.6rem', border: '1px solid #333', borderRadius: '6px', marginBottom: '0.5rem' }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                    <div>
                      {thread.designation}
                      {thread.length ? `, length ${thread.length?.toFixed?.(4) ?? thread.length} in` : ''}
                      {thread.kind ? `, ${thread.kind}` : ''}
                    </div>
                    <span className={`confidence-badge confidence-${getConfidenceLevel(thread.confidence || 0)}`}>
                      {((thread.confidence || 0) * 100).toFixed(0)}%
                    </span>
                  </div>
                  <div style={{ marginTop: '0.3rem', color: '#aaa', fontSize: '0.9rem' }}>
                    Page {thread.source_page ?? '—'} · View {thread.source_view_index ?? '—'}
                  </div>
                  {thread.notes && <div style={{ marginTop: '0.3rem', color: '#888' }}>{thread.notes}</div>}
                </div>
              ))}
            </div>
          </div>
        )}
      </div>

      {/* Profile Review Plot */}
      {segments.length > 0 && (
        <div className="profile-review-section">
          <ProfileReviewPlot
            segments={segments.map((seg) => ({
              z_start: seg.z_start,
              z_end: seg.z_end,
              od_diameter: seg.od_diameter,
              id_diameter: seg.id_diameter,
              confidence: seg.confidence,
              flags: (seg as any).flags || [],
            }))}
            zRange={partSummary?.z_range}
            units={partSummary?.units?.length || 'in'}
            features={partSummary?.features}
            showHoles={showHoles}
            showSlots={showSlots}
            showChamfers={showChamfers}
            showFillets={showFillets}
          />
        </div>
      )}

      {/* STEP generation */}
      <div className="step-generation-section">
        <h3>3D Model Generation</h3>

        {!hasStepFile && inferredStack && (occAvailable === true || occAvailable === null) && (
          <div style={{ marginBottom: '1.5rem', padding: '1rem', background: '#2a2a2a', borderRadius: '8px' }}>
            <p className="step-description">
              Generate a STEP file (3D CAD model) from the inferred stack. This will create a solid model by converting the stack segments to a
              Profile2D and revolving it.
              {occAvailable === null && <span style={{ fontSize: '0.85rem', color: '#888', marginLeft: '0.5rem' }}>(Checking OCC availability...)</span>}
              {occAvailable === true && occBackend && <span style={{ fontSize: '0.85rem', color: '#888', marginLeft: '0.5rem' }}>(Using {occBackend} backend)</span>}
            </p>

            <button
              onClick={handleGenerateStepFromStack}
              disabled={generatingStepFromStack || occAvailable !== true}
              className="generate-step-btn"
              style={{ opacity: generatingStepFromStack || occAvailable !== true ? 0.6 : 1, cursor: generatingStepFromStack || occAvailable !== true ? 'not-allowed' : 'pointer' }}
            >
              {generatingStepFromStack ? 'Generating STEP...' : occAvailable === null ? 'Checking OCC...' : 'Generate STEP from inferred stack'}
            </button>

            {stepFromStackError && (
              <div
                className="step-error"
                style={{
                  marginTop: '1rem',
                  padding: '1rem',
                  background: '#3a1a1a',
                  border: '1px solid #ff4444',
                  borderRadius: '4px',
                  whiteSpace: 'pre-wrap',
                  fontFamily: 'monospace',
                  fontSize: '0.9rem',
                }}
              >
                <strong style={{ color: '#ff6666' }}>Error:</strong>
                <div style={{ marginTop: '0.5rem', color: '#ffaaaa' }}>{stepFromStackError}</div>
                {stepFromStackStatus === 'UNAVAILABLE' && (
                  <p style={{ marginTop: '0.5rem', fontSize: '0.9rem', color: '#ffaa00' }}>
                    OCC (OpenCASCADE) is not installed. Install pythonocc-core to enable STEP generation.
                  </p>
                )}
                {stepFromStackStatus === 'FAILED' && onSwitchToManual && (
                  <button onClick={onSwitchToManual} className="switch-to-manual-btn" style={{ marginTop: '1rem' }}>
                    Switch to Assisted Manual
                  </button>
                )}
              </div>
            )}

            {stepFromStackStatus === 'OK' && (
              <div className="step-success" style={{ marginTop: '1rem' }}>
                <p>✓ STEP file generated successfully!</p>
                <p style={{ marginTop: '0.5rem', fontSize: '0.9rem' }}>The STEP file should appear in the Downloads section below.</p>
              </div>
            )}
          </div>
        )}

        {!hasStepFile && occAvailable === false && (
          <div className="step-requires-occ-warning">
            <h4>⚠️ STEP Disabled: OCC Not Installed or Not Properly Configured</h4>
            <p>OCC (OpenCASCADE) is not available in the backend environment. STEP file generation requires OCC to build 3D geometry.</p>
            {occError && (
              <div style={{ marginTop: '0.5rem', padding: '0.75rem', background: '#2a1a1a', border: '1px solid #ff4444', borderRadius: '4px', fontFamily: 'monospace', fontSize: '0.85rem', color: '#ffaaaa' }}>
                <strong>Error details:</strong>
                <pre style={{ marginTop: '0.5rem', whiteSpace: 'pre-wrap' }}>{occError}</pre>
              </div>
            )}
            <p style={{ marginTop: '1rem' }}>
              <strong>Solution:</strong> Install pythonocc-core or OCP (CadQuery backend) in the backend environment.
            </p>
            <p style={{ marginTop: '0.5rem', fontSize: '0.9rem', color: '#aaa' }}>
              <strong>Installation commands:</strong>
            </p>
            <ul style={{ marginTop: '0.5rem', paddingLeft: '1.5rem', fontSize: '0.9rem', color: '#aaa' }}>
              <li>
                For pythonocc-core:{' '}
                <code style={{ background: '#1a1a1a', padding: '0.2rem 0.4rem', borderRadius: '3px' }}>pip install pythonocc-core</code>
              </li>
              <li>
                For OCP (CadQuery): <code style={{ background: '#1a1a1a', padding: '0.2rem 0.4rem', borderRadius: '3px' }}>pip install cadquery</code>
              </li>
            </ul>
          </div>
        )}

        {/* Fallback warnings (kept) */}
        {stepStatus === 'UNAVAILABLE' && !hasStepFile && occAvailable === false ? (
          <div className="step-requires-occ-warning">
            <h4>⚠️ STEP Generation Unavailable</h4>
            <p>OCC (OpenCASCADE) is not installed. STEP file generation requires OCC to build 3D geometry.</p>
            {stepReason && <p><strong>Reason:</strong> {stepReason}</p>}
            <p><strong>Solution:</strong> Install OCC (pythonocc-core) to enable automatic STEP generation, or switch to Assisted Manual mode.</p>
            {onSwitchToManual && (
              <button onClick={onSwitchToManual} className="switch-to-manual-btn" style={{ marginTop: '1rem' }}>
                Switch to Assisted Manual
              </button>
            )}
          </div>
        ) : stepStatus === 'FAILED' && !hasStepFile && occAvailable === false ? (
          <div className="step-error">
            <h4>❌ STEP Generation Failed</h4>
            <p>Automatic STEP generation failed during auto-detect.</p>
            {stepReason && <p><strong>Reason:</strong> {stepReason}</p>}
            <p>
              Check{' '}
              <a href={api.getPdfUrl(jobId, 'outputs/scale_report.json')} target="_blank" rel="noopener noreferrer">
                scale_report.json
              </a>{' '}
              for details.
            </p>
            {onSwitchToManual && (
              <button onClick={onSwitchToManual} className="switch-to-manual-btn" style={{ marginTop: '1rem' }}>
                Switch to Assisted Manual
              </button>
            )}
          </div>
        ) : needsReview ? (
          <div className="needs-review-warning">
            <h4>⚠️ STEP Generation Requires Review</h4>
            <p>The inferred stack did not pass the safety gate checks. Please review the following issues:</p>
            <ul>
              {reviewReasons.map((reason, i) => (
                <li key={i}>{reason}</li>
              ))}
            </ul>
            <p>You can either:</p>
            <ol>
              <li>Edit the stack manually to fix the issues</li>
              <li>Approve the stack anyway (use with caution)</li>
            </ol>
            <div className="review-actions">
              <button
                onClick={async () => {
                  try {
                    await api.approveStep(jobId);
                    setNeedsReview(false);
                    setReviewReasons([]);
                    await handleGenerateStep();
                  } catch (err) {
                    setError(err instanceof Error ? err.message : 'Failed to approve stack');
                  }
                }}
                className="approve-btn"
              >
                Approve & Generate STEP Anyway
              </button>
              {onSwitchToManual && (
                <button onClick={onSwitchToManual} className="edit-manually-btn">
                  Edit Stack Manually
                </button>
              )}
            </div>
          </div>
        ) : hasStepFile || stepFromStackStatus === 'OK' ? (
          <div className="step-success">
            <p>✓ STEP file generated successfully!</p>

            <div className="step-downloads" style={{ display: 'flex', gap: '1rem', marginTop: '1rem', flexWrap: 'wrap' }}>
              <button
                type="button"
                className="download-link step-download-btn"
                onClick={async () => {
                  setDownloadingStep(true);
                  try {
                    const files = await api.getJobFiles(jobId);
                    const stepFile = files.files.find((f) => f.path === 'outputs/model.step');

                    if (!stepFile) {
                      for (let i = 0; i < 5; i++) {
                        await sleep(1000);
                        const retryFiles = await api.getJobFiles(jobId);
                        const retryStepFile = retryFiles.files.find((f) => f.path === 'outputs/model.step');
                        if (retryStepFile) break;
                      }
                    }

                    await api.downloadFile(jobId, 'outputs/model.step', 'model.step', 5, 2000);
                  } catch (e) {
                    console.error('Failed to download STEP file:', e);
                    alert('Failed to download STEP file. The file may still be writing to disk. Please wait a moment and try again, or refresh the page.');
                  } finally {
                    setDownloadingStep(false);
                  }
                }}
                disabled={downloadingStep}
                style={{
                  display: 'inline-flex',
                  alignItems: 'center',
                  gap: '0.5rem',
                  padding: '0.75rem 1.5rem',
                  backgroundColor: downloadingStep ? '#66bb6a' : '#4caf50',
                  color: 'white',
                  textDecoration: 'none',
                  borderRadius: '4px',
                  fontWeight: '600',
                  transition: 'background-color 0.2s',
                  cursor: downloadingStep ? 'wait' : 'pointer',
                  border: 'none',
                  outline: 'none',
                  opacity: downloadingStep ? 0.8 : 1,
                }}
                onMouseEnter={(e) => {
                  if (!downloadingStep) e.currentTarget.style.backgroundColor = '#388e3c';
                }}
                onMouseLeave={(e) => {
                  if (!downloadingStep) e.currentTarget.style.backgroundColor = '#4caf50';
                }}
              >
                <span>📥</span>
                <span>{downloadingStep ? 'Downloading...' : 'Download STEP File'}</span>
              </button>
            </div>

            <p style={{ marginTop: '1rem', fontSize: '0.9rem', color: '#aaa', fontStyle: 'italic' }}>
              The STEP file can be opened in CAD software like SolidWorks, Fusion 360, FreeCAD, or any STEP-compatible viewer.
            </p>
          </div>
        ) : (
          <>
            <p className="step-description">
              Generate a STEP file (3D CAD model) from the inferred stack. This will create a solid model and update the part summary with feature counts.
            </p>

            {error && <div className="step-error">{error}</div>}

            {showStepConfirmation ? (
              <div className="step-confirmation">
                <p>
                  <strong>Confirm STEP Generation</strong>
                </p>
                <p>This will generate a 3D STEP model from the auto-detected stack. Please review the inferred segments and confidence scores before proceeding.</p>
                <div className="confirmation-buttons">
                  <button onClick={handleGenerateStep} className="confirm-btn">
                    Confirm & Generate STEP
                  </button>
                  <button onClick={() => setShowStepConfirmation(false)} className="cancel-btn">
                    Cancel
                  </button>
                </div>
              </div>
            ) : (
              <button onClick={handleGenerateStep} disabled={generatingStep || !inferredStack} className="generate-step-btn">
                {generatingStep ? 'Generating STEP...' : 'Generate STEP (3D model)'}
              </button>
            )}
          </>
        )}
      </div>
    </div>
  );
}

export default AutoConvertResults;
