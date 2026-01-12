import { useState, useEffect } from 'react';
import { api } from '../../services/api';
import ProfileReviewPlot from './ProfileReviewPlot';
import { setSegments, type Segment as SegmentStoreType } from '../../state/segmentStore';
import type { RFQAutofillResponse } from '../../services/types';
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
}

interface AutoConvertResultsProps {
  jobId: string;
  onSwitchToManual?: () => void;
}

function AutoConvertResults({ jobId, onSwitchToManual }: AutoConvertResultsProps) {
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [inferredStack, setInferredStack] = useState<any>(null);
  const [partSummary, setPartSummary] = useState<any>(null);
  const [generatingStep, setGeneratingStep] = useState(false);
  const [stepResult, setStepResult] = useState<any>(null);
  const [overallConfidence, setOverallConfidence] = useState<number | null>(null);
  const [warnings, setWarnings] = useState<string[]>([]);
  const [detecting, setDetecting] = useState(false);
  const [inferring, setInferring] = useState(false);
  const [detectionResult, setDetectionResult] = useState<any>(null);
  const [detectionError, setDetectionError] = useState<string | null>(null);
  const [showStepConfirmation, setShowStepConfirmation] = useState(false);
  const [needsReview, setNeedsReview] = useState(false);
  const [reviewReasons, setReviewReasons] = useState<string[]>([]);
  const [generatingStepFromStack, setGeneratingStepFromStack] = useState(false);
  const [stepFromStackError, setStepFromStackError] = useState<string | null>(null);
  const [stepFromStackStatus, setStepFromStackStatus] = useState<string | null>(null);
  const [occAvailable, setOccAvailable] = useState<boolean | null>(null);
  const [downloadingStep, setDownloadingStep] = useState(false);
  const [occBackend, setOccBackend] = useState<string | null>(null);
  const [occError, setOccError] = useState<string | null>(null);
  const [stepFileExists, setStepFileExists] = useState(false);

  // RFQ AutoFill UI state
  const [rfqPartNo, setRfqPartNo] = useState<string>('');
  const [rmOdAllowanceIn, setRmOdAllowanceIn] = useState<number>(0.10);
  const [rmLenAllowanceIn, setRmLenAllowanceIn] = useState<number>(0.35);
  const [rfqAutofillLoading, setRfqAutofillLoading] = useState(false);
  const [rfqAutofillError, setRfqAutofillError] = useState<string | null>(null);
  const [rfqAutofillResult, setRfqAutofillResult] = useState<RFQAutofillResponse | null>(null);

  const normalizePartNo = (s: string) => {
    const trimmed = (s || '').trim();
    // Strip common single-letter revision suffixes like _C or -C
    return trimmed.replace(/([_-])[a-zA-Z]$/, '');
  };

  useEffect(() => {
    loadResults();
    checkOccAvailability();
  }, [jobId]);

  // Check if STEP file exists in the file list
  useEffect(() => {
    // Only check if we don't already know the file exists
    if (stepFileExists) {
      return; // Already found, no need to keep checking
    }
    
    const checkStepFile = async () => {
      try {
        const files = await api.getJobFiles(jobId);
        const stepFile = files.files.find(f => f.path === 'outputs/model.step');
        const fileExists = !!stepFile;
        setStepFileExists(fileExists);
        console.log('[AutoConvertResults] STEP file check:', { exists: fileExists, files: files.files.map(f => f.path) });
      } catch (err) {
        console.error('[AutoConvertResults] Error checking for STEP file:', err);
        setStepFileExists(false);
      }
    };
    
    // Initial check
    checkStepFile();
    
    // Only set up interval if file doesn't exist yet and we're waiting for generation
    // Check less frequently (every 3 seconds instead of 2) to reduce API calls
    if (!stepFileExists && (stepFromStackStatus === 'OK' || generatingStepFromStack)) {
      const interval = setInterval(checkStepFile, 3000);
      return () => clearInterval(interval);
    }
  }, [jobId, stepFromStackStatus, stepResult, stepFileExists, generatingStepFromStack]);

  const checkOccAvailability = async () => {
    try {
      console.log('[AutoConvertResults] Checking OCC availability...');
      const result = await api.checkOccAvailability();
      console.log('[AutoConvertResults] OCC availability result:', result);
      setOccAvailable(result.occ_available);
      setOccBackend(result.backend);
      setOccError(result.error || null);
      console.log('[AutoConvertResults] OCC available:', result.occ_available, 'backend:', result.backend, 'error:', result.error);
    } catch (err) {
      console.error('[AutoConvertResults] Error checking OCC availability:', err);
      setOccAvailable(false);
      setOccBackend(null);
      setOccError(err instanceof Error ? err.message : 'Failed to check OCC availability');
    }
  };

  const loadResults = async () => {
    console.log('[AutoConvertResults] Loading results for job:', jobId);
    try {
      setLoading(true);
      setError(null);

      // Try to load inferred_stack.json
      console.log('[AutoConvertResults] Fetching job files...');
      const files = await api.getJobFiles(jobId);
      console.log('[AutoConvertResults] Job files:', files);

      // Prefill Part No (best-effort) and only if user hasn't typed one yet:
      // 1) job.name
      // 2) first uploaded PDF filename (without extension)
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
          // Non-fatal: keep going
          console.warn('[AutoConvertResults] Failed to prefill part no:', e);
        }
      }
      
      const inferredStackFile = files.files.find(f => f.path === 'outputs/inferred_stack.json');
      const partSummaryFile = files.files.find(f => f.path === 'outputs/part_summary.json');
      
      console.log('[AutoConvertResults] Inferred stack file:', inferredStackFile);
      console.log('[AutoConvertResults] Part summary file:', partSummaryFile);

      if (inferredStackFile) {
        console.log('[AutoConvertResults] Loading inferred_stack.json...');
        const response = await fetch(api.getPdfUrl(jobId, inferredStackFile.path));
        if (response.ok) {
          const data = await response.json();
          console.log('[AutoConvertResults] Inferred stack data:', data);
          setInferredStack(data);
          setOverallConfidence(data.overall_confidence || null);
          setWarnings(data.warnings || []);
          
          // Populate segment store for 3D viewer hover
          if (data.segments && Array.isArray(data.segments)) {
            const segments: SegmentStoreType[] = data.segments.map((seg: any) => ({
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
            setSegments(jobId, segments);
            console.log('[AutoConvertResults] Populated segment store with', segments.length, 'segments');
          }
        } else {
          console.warn('[AutoConvertResults] Failed to load inferred_stack.json:', response.status);
        }
      } else {
        console.log('[AutoConvertResults] No inferred_stack.json found');
      }

      if (partSummaryFile) {
        console.log('[AutoConvertResults] Loading part_summary.json...');
        const response = await fetch(api.getPdfUrl(jobId, partSummaryFile.path));
        if (response.ok) {
          const data = await response.json();
          console.log('[AutoConvertResults] Part summary data:', data);
          setPartSummary(data);
          
          // Populate segment store for 3D viewer hover (prefer part_summary if available)
          if (data.segments && Array.isArray(data.segments)) {
            const segments: SegmentStoreType[] = data.segments.map((seg: any) => ({
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
            setSegments(jobId, segments);
            console.log('[AutoConvertResults] Populated segment store from part_summary with', segments.length, 'segments');
          }
        } else {
          console.warn('[AutoConvertResults] Failed to load part_summary.json:', response.status);
        }
      } else {
        console.log('[AutoConvertResults] No part_summary.json found');
      }
    } catch (err) {
      console.error('[AutoConvertResults] Error loading results:', err);
      setError(err instanceof Error ? err.message : 'Failed to load results');
    } finally {
      setLoading(false);
      console.log('[AutoConvertResults] Finished loading results');
    }
  };

  const handleGenerateStep = async () => {
    // Check if we need confirmation (auto_detect mode)
    const mode = partSummary?.inference_metadata?.mode || inferredStack?.inference_metadata?.mode;
    if (mode === 'auto_detect' && !showStepConfirmation) {
      // Require user confirmation for auto_detect mode
      setShowStepConfirmation(true);
      return;
    }

    console.log('[AutoConvertResults] Starting STEP generation for job:', jobId);
    setGeneratingStep(true);
    setError(null);
    setShowStepConfirmation(false);

    try {
      console.log('[AutoConvertResults] Calling autoGenerateStep API...');
      const result = await api.autoGenerateStep(jobId);
      console.log('[AutoConvertResults] STEP generation result:', result);
      setStepResult(result);

      if (result.status === 'DONE') {
        console.log('[AutoConvertResults] STEP generation successful, reloading results...');
        // Reload results to get updated part_summary.json
        await loadResults();
        setNeedsReview(false);
        setReviewReasons([]);
      } else if (result.status === 'needs_review') {
        console.log('[AutoConvertResults] STEP generation needs review:', result.reasons);
        setNeedsReview(true);
        setReviewReasons(result.reasons || []);
      } else {
        console.warn('[AutoConvertResults] STEP generation status:', result.status);
      }
    } catch (err) {
      console.error('[AutoConvertResults] Error during STEP generation:', err);
      setError(err instanceof Error ? err.message : 'Failed to generate STEP');
    } finally {
      setGeneratingStep(false);
      console.log('[AutoConvertResults] STEP generation finished');
    }
  };

  const handleGenerateStepFromStack = async () => {
    console.log('[AutoConvertResults] handleGenerateStepFromStack called for job:', jobId);
    try {
      setGeneratingStepFromStack(true);
      setStepFromStackError(null);
      setStepFromStackStatus(null);
      
      console.log('[AutoConvertResults] Calling generateStepFromInferredStack API...');
      const result = await api.generateStepFromInferredStack(jobId);
      console.log('[AutoConvertResults] generateStepFromInferredStack result:', JSON.stringify(result, null, 2));
      
      setStepFromStackStatus(result.status);
      
      if (result.status === 'OK') {
        console.log('[AutoConvertResults] STEP generation successful, waiting for file to appear...');
        // Poll for file to appear in file list (up to 10 seconds)
        let fileFound = false;
        for (let attempt = 0; attempt < 10; attempt++) {
          const files = await api.getJobFiles(jobId);
          const stepFile = files.files.find(f => f.path === 'outputs/model.step');
          
          if (stepFile) {
            console.log(`[AutoConvertResults] STEP file found in file list after ${attempt + 1} attempt(s):`, stepFile);
            setStepFileExists(true);
            setStepResult({ ...result, outputs: (result as any).outputs || ['model.step'] });
            fileFound = true;
            break;
          }
          
          // Wait 1 second before next attempt
          if (attempt < 9) {
            await new Promise(resolve => setTimeout(resolve, 1000));
          }
        }
        
        if (!fileFound) {
          console.warn('[AutoConvertResults] STEP file not found in file list after 10 seconds, but API reported success');
          // Still set stepFileExists to true so download button appears, but it might fail
          setStepFileExists(true);
          setStepResult({ ...result, outputs: (result as any).outputs || ['model.step'] });
        }
        
        // Reload results to get updated files
        await loadResults();
      } else if (result.status === 'UNAVAILABLE') {
        const errorMsg = result.message || 'OCC not installed';
        console.error('[AutoConvertResults] STEP generation unavailable:', errorMsg);
        setStepFromStackError(errorMsg);
      } else {
        const errorMsg = result.message || 'STEP generation failed';
        const debugInfo = result.debug ? JSON.stringify(result.debug, null, 2) : '';
        const fullError = debugInfo ? `${errorMsg}\n\nDebug info:\n${debugInfo}` : errorMsg;
        console.error('[AutoConvertResults] STEP generation failed:', fullError);
        setStepFromStackError(fullError);
      }
    } catch (err) {
      console.error('[AutoConvertResults] Exception during STEP generation:', err);
      let errorMessage = 'Failed to generate STEP';
      if (err instanceof Error) {
        errorMessage = err.message;
        if (err.stack) {
          errorMessage += `\n\nStack trace:\n${err.stack}`;
        }
      } else if (typeof err === 'object' && err !== null) {
        errorMessage = JSON.stringify(err, null, 2);
      }
      setStepFromStackError(errorMessage);
      setStepFromStackStatus('FAILED');
    } finally {
      setGeneratingStepFromStack(false);
      console.log('[AutoConvertResults] STEP generation from stack finished');
    }
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

  const getRFQValueCellClass = (value: number | null | undefined, confidence: number, status: string, reasons: string[]) => {
    if (value === null || value === undefined) return 'rfq-cell-bad';
    if (status === 'REJECTED') return 'rfq-cell-bad';
    if (status === 'NEEDS_REVIEW') return 'rfq-cell-warn';
    // Green only when confidence is strong and scale is not estimated
    if (confidence >= 0.85 && !reasons.includes('SCALE_ESTIMATED')) return 'rfq-cell-good';
    return 'rfq-cell-warn';
  };

  const formatMaybeNumber = (v: number | null | undefined, digits: number = 3) => {
    if (v === null || v === undefined) return '—';
    return Number(v).toFixed(digits);
  };

  if (loading) {
    return <div className="auto-convert-loading">Loading results...</div>;
  }

  if (error && !inferredStack) {
    return <div className="auto-convert-error">Error: {error}</div>;
  }

  const handleAutoDetect = async () => {
    console.log('[AutoConvertResults] Starting auto-detection for job:', jobId);
    setDetecting(true);
    setDetectionError(null);
    try {
      // First, upload PDF if not already done
      console.log('[AutoConvertResults] Checking for PDF file...');
      const files = await api.getJobFiles(jobId);
      console.log('[AutoConvertResults] All files:', files);
      
      const pdfFile = files.files.find(f => f.name.toLowerCase().endsWith('.pdf'));
      console.log('[AutoConvertResults] PDF file found:', pdfFile);
      
      if (!pdfFile) {
        console.error('[AutoConvertResults] No PDF file found');
        setDetectionError('No PDF file found. Please upload a PDF first.');
        return;
      }

      // Check if page images exist, if not upload PDF first
      const pageImages = files.files.filter(f => f.path.startsWith('outputs/pdf_pages/'));
      console.log('[AutoConvertResults] Page images found:', pageImages.length);
      
      if (pageImages.length === 0) {
        console.log('[AutoConvertResults] No page images found, uploading PDF...');
        // Need to upload PDF first
        const pdfUrl = api.getPdfUrl(jobId, pdfFile.path);
        console.log('[AutoConvertResults] Fetching PDF from:', pdfUrl);
        const pdfResponse = await fetch(pdfUrl);
        const pdfBlob = await pdfResponse.blob();
        const pdfFileObj = new File([pdfBlob], pdfFile.name, { type: 'application/pdf' });
        console.log('[AutoConvertResults] Uploading PDF...');
        const uploadResult = await api.uploadPdf(jobId, pdfFileObj);
        console.log('[AutoConvertResults] PDF upload result:', uploadResult);
      }

      // Detect views
      console.log('[AutoConvertResults] Detecting views...');
      const viewsResult = await api.detectViews(jobId);
      console.log('[AutoConvertResults] Views detection result:', viewsResult);

      // Run auto-detection
      console.log('[AutoConvertResults] Running auto-detection...');
      const result = await api.autoDetectTurnedView(jobId);
      console.log('[AutoConvertResults] Auto-detection result:', result);
      setDetectionResult(result);
      
      // If a view was detected with sufficient confidence, automatically run inference
      if (result.best_view && result.best_view.confidence >= 0.65) {
        console.log('[AutoConvertResults] High confidence view detected, auto-running inference...');
        await handleInferStack(result.best_view.page, result.best_view.view_index);
      } else {
        console.log('[AutoConvertResults] Low confidence or no view detected, showing ranked views for manual selection');
        if (result.ranked_views && result.ranked_views.length > 0) {
          console.log('[AutoConvertResults] Ranked views:', result.ranked_views.map((v: any) => ({
            page: v.page,
            view_index: v.view_index,
            confidence: v.confidence
          })));
        }
      }
    } catch (err) {
      console.error('[AutoConvertResults] Error during auto-detection:', err);
      setDetectionError(err instanceof Error ? err.message : 'Failed to detect turned view');
    } finally {
      setDetecting(false);
      console.log('[AutoConvertResults] Auto-detection finished');
    }
  };

  const handleInferStack = async (page?: number, viewIndex?: number) => {
    console.log('[AutoConvertResults] Starting stack inference for job:', jobId, 'page:', page, 'viewIndex:', viewIndex);
    setInferring(true);
    setDetectionError(null);
    try {
      console.log('[AutoConvertResults] Calling inferStackFromView API...');
      const result = await api.inferStackFromView(jobId, page, viewIndex);
      console.log('[AutoConvertResults] Stack inference result:', result);
      
      // Check for validation failure
      if (result.status === 'VALIDATION_FAILED') {
        console.error('[AutoConvertResults] Validation failed:', result.validation_errors);
        const errorMsg = result.message || 'Auto-detect validation failed. Please use Assisted Manual mode.';
        setDetectionError(errorMsg);
        // Show detailed validation errors
        if (result.validation_errors && result.validation_errors.length > 0) {
          const detailedMsg = `${errorMsg}\n\nValidation Errors:\n${result.validation_errors.map(e => `  • ${e}`).join('\n')}`;
          if (result.derived_values) {
            const derivedMsg = `\n\nDerived Values:\n  • Total Length: ${result.derived_values.total_length_inches.toFixed(3)} in\n  • Max OD: ${result.derived_values.max_od_inches.toFixed(3)} in`;
            setDetectionError(detailedMsg + derivedMsg);
          } else {
            setDetectionError(detailedMsg);
          }
        } else {
          setDetectionError(errorMsg);
        }
        return; // Don't reload results if validation failed
      }
      
      // Reload results after successful inference
      console.log('[AutoConvertResults] Reloading results after inference...');
      await loadResults();
      console.log('[AutoConvertResults] Results reloaded successfully');
    } catch (err) {
      console.error('[AutoConvertResults] Error during stack inference:', err);
      setDetectionError(err instanceof Error ? err.message : 'Failed to infer stack');
    } finally {
      setInferring(false);
      console.log('[AutoConvertResults] Stack inference finished');
    }
  };

  if (!inferredStack) {
    return (
      <div className="auto-convert-empty">
        <h2>Auto Convert Mode</h2>
        <div style={{ 
          padding: '1.5rem', 
          background: '#f8f9fa', 
          border: '2px solid #007bff', 
          borderRadius: '8px',
          marginBottom: '1.5rem'
        }}>
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
              transition: 'all 0.2s'
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

        {/* RFQ AutoFill (visible even before inference; disabled until part_summary exists) */}
        <div className="rfq-autofill-section">
          <h3>RFQ AutoFill</h3>
          <p className="rfq-autofill-description">
            Enter a Part No and run Auto-Detect/Infer first to generate <code>part_summary.json</code>, then you can auto-fill RFQ fields.
          </p>

          <div className="rfq-autofill-controls">
            <div className="rfq-autofill-control">
              <label>Part No</label>
              <input
                type="text"
                value={rfqPartNo}
                onChange={(e) => setRfqPartNo(e.target.value)}
                placeholder="Enter part number (if not detected)"
              />
            </div>

            <div className="rfq-autofill-control">
              <label>RM OD Allowance (in)</label>
              <input
                type="number"
                step="0.01"
                value={rmOdAllowanceIn}
                onChange={(e) => setRmOdAllowanceIn(Number(e.target.value))}
              />
            </div>

            <div className="rfq-autofill-control">
              <label>RM Len Allowance (in)</label>
              <input
                type="number"
                step="0.01"
                value={rmLenAllowanceIn}
                onChange={(e) => setRmLenAllowanceIn(Number(e.target.value))}
              />
            </div>

            <div className="rfq-autofill-actions">
              <button
                className="rfq-autofill-btn"
                disabled={rfqAutofillLoading || !partSummary || !rfqPartNo.trim()}
                title={
                  !partSummary
                    ? "Run inference first to generate part_summary.json"
                    : !rfqPartNo.trim()
                      ? "Enter Part No to enable"
                      : undefined
                }
                onClick={async () => {
                  if (!partSummary) return;
                  if (!rfqPartNo.trim()) {
                    setRfqAutofillError('Part No is required.');
                    return;
                  }

                  try {
                    setRfqAutofillLoading(true);
                    setRfqAutofillError(null);

                    const mergedPartSummary = {
                      ...(partSummary || {}),
                      scale_report: inferredStack?.scale_report || (partSummary as any)?.scale_report,
                    };

                    const result = await api.rfqAutofill({
                      rfq_id: jobId,
                      part_no: normalizePartNo(rfqPartNo),
                      source: {
                        part_summary: mergedPartSummary,
                        step_metrics: null,
                      },
                      tolerances: {
                        rm_od_allowance_in: rmOdAllowanceIn,
                        rm_len_allowance_in: rmLenAllowanceIn,
                      },
                    });

                    setRfqAutofillResult(result);
                  } catch (err) {
                    console.error('[AutoConvertResults] RFQ autofill error:', err);
                    setRfqAutofillError(err instanceof Error ? err.message : 'Failed to auto-fill RFQ');
                  } finally {
                    setRfqAutofillLoading(false);
                  }
                }}
              >
                Auto-fill RFQ
              </button>
            </div>
          </div>
        </div>
        
        {detectionError && (
          <div className="auto-convert-error" style={{ 
            marginTop: '1rem', 
            padding: '1rem', 
            background: '#fee', 
            border: '1px solid #fcc', 
            borderRadius: '4px',
            whiteSpace: 'pre-line'
          }}>
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
                    fontSize: '0.95rem'
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
                  // Handle nested scores structure from backend
                  const scores = view.scores || view;
                  const viewConf = scores.view_conf || scores.confidence || 0;
                  const axisConf = scores.axis_conf || scores.axis_conf || 0;
                  const symConf = scores.sym_conf || scores.sym_conf || 0;
                  
                  return (
                    <tr key={idx} style={{ borderBottom: '1px solid #f0f0f0' }}>
                      <td style={{ padding: '0.5rem' }}>{view.page}</td>
                      <td style={{ padding: '0.5rem' }}>{view.view_index}</td>
                      <td style={{ padding: '0.5rem' }}>
                        <span style={{
                          padding: '0.25rem 0.5rem',
                          borderRadius: '4px',
                          background: viewConf >= 0.65 ? '#d4edda' : viewConf >= 0.5 ? '#fff3cd' : '#f8d7da',
                          color: viewConf >= 0.65 ? '#155724' : viewConf >= 0.5 ? '#856404' : '#721c24'
                        }}>
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
                            cursor: inferring ? 'not-allowed' : 'pointer'
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
                <p style={{ margin: 0 }}><strong>⚠️ No view met the confidence threshold (≥65%).</strong></p>
                <p style={{ margin: '0.5rem 0 0 0' }}>You can still try inference on any view above, but results may be less accurate. Consider using Assisted Manual mode for better results.</p>
              </div>
            )}
          </div>
        )}

        {detectionResult && detectionResult.best_view && (
          <div className="detection-success" style={{ marginTop: '1rem', padding: '1rem', background: '#d4edda', border: '1px solid #28a745', borderRadius: '4px' }}>
            <p><strong>✓ High Confidence View Detected!</strong></p>
            <p>Page {detectionResult.best_view.page}, View {detectionResult.best_view.view_index}</p>
            <p>Confidence: {(detectionResult.best_view.confidence * 100).toFixed(1)}%</p>
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

  const segments: Segment[] = inferredStack.segments || [];
  const hasLowConfidence = overallConfidence !== null && overallConfidence < 0.65;
  
  // Check if STEP file actually exists - prioritize actual file existence
  // Only set to true if the file actually exists, not just based on metadata
  // Ensure it's always a boolean (not undefined)
  const hasStepFile = Boolean(stepFileExists || stepResult?.outputs?.includes('model.step'));
  
  // Check step_status from inferred_stack.json
  const stepStatus = inferredStack?.step_status || 'UNKNOWN';
  const stepReason = inferredStack?.step_reason || null;
  
  // Debug logging for button visibility (after hasStepFile is declared)
  console.log('[AutoConvertResults] Button visibility check:', {
    hasStepFile,
    stepFileExists,
    inferredStack: !!inferredStack,
    occAvailable,
    occBackend,
    stepStatus,
    stepReason,
    shouldShowButton: !hasStepFile && !!inferredStack && (occAvailable === true || occAvailable === null),
    buttonShouldBeEnabled: occAvailable === true && !generatingStepFromStack
  });

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
          <p>
            The auto-detection confidence is below the recommended threshold. 
            For more accurate results, consider switching to Assisted Manual mode.
          </p>
          <button
            className="switch-to-manual-btn"
            onClick={() => {
              if (onSwitchToManual) {
                onSwitchToManual();
              }
            }}
          >
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
                    {seg.wall_thickness !== undefined
                      ? seg.wall_thickness.toFixed(3)
                      : ((seg.od_diameter - seg.id_diameter) / 2).toFixed(3)}
                  </td>
                  <td>
                    {seg.volume_in3 !== undefined
                      ? seg.volume_in3.toFixed(6)
                      : '—'}
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

      {partSummary && partSummary.totals && (
        <div className="totals-section">
          <h3>Totals</h3>
          <div className="totals-grid">
            <div className="total-card">
              <div className="total-label">Total Volume</div>
              <div className="total-value">
                {partSummary.totals.volume_in3.toFixed(6)} in³
              </div>
            </div>
            <div className="total-card">
              <div className="total-label">Total Surface Area</div>
              <div className="total-value">
                {partSummary.totals.total_surface_area_in2.toFixed(6)} in²
              </div>
            </div>
            <div className="total-card">
              <div className="total-label">OD Surface Area</div>
              <div className="total-value">
                {partSummary.totals.od_area_in2.toFixed(6)} in²
              </div>
            </div>
            <div className="total-card">
              <div className="total-label">ID Surface Area</div>
              <div className="total-value">
                {partSummary.totals.id_area_in2.toFixed(6)} in²
              </div>
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
            <input
              type="text"
              value={rfqPartNo}
              onChange={(e) => setRfqPartNo(e.target.value)}
              placeholder="Enter part number (if not detected)"
            />
          </div>

          <div className="rfq-autofill-control">
            <label>RM OD Allowance (in)</label>
            <input
              type="number"
              step="0.01"
              value={rmOdAllowanceIn}
              onChange={(e) => setRmOdAllowanceIn(Number(e.target.value))}
            />
          </div>

          <div className="rfq-autofill-control">
            <label>RM Len Allowance (in)</label>
            <input
              type="number"
              step="0.01"
              value={rmLenAllowanceIn}
              onChange={(e) => setRmLenAllowanceIn(Number(e.target.value))}
            />
          </div>

          <div className="rfq-autofill-actions">
            <button
              className="rfq-autofill-btn"
              disabled={rfqAutofillLoading || !partSummary || !rfqPartNo.trim()}
              title={
                !partSummary
                  ? "part_summary.json not loaded yet. Run inference first."
                  : !rfqPartNo.trim()
                    ? "Enter Part No to enable"
                    : undefined
              }
              onClick={async () => {
                if (!partSummary) return;
                if (!rfqPartNo.trim()) {
                  setRfqAutofillError('Part No is required.');
                  return;
                }

                try {
                  setRfqAutofillLoading(true);
                  setRfqAutofillError(null);

                  // Prefer server-side job_id loading to avoid sending the full part_summary payload.
                  const result = await api.rfqAutofillForJob({
                    rfq_id: jobId,
                    job_id: jobId,
                    part_no: normalizePartNo(rfqPartNo),
                    tolerances: {
                      rm_od_allowance_in: rmOdAllowanceIn,
                      rm_len_allowance_in: rmLenAllowanceIn,
                    },
                    step_metrics: null,
                  });

                  setRfqAutofillResult(result);
                } catch (err) {
                  console.error('[AutoConvertResults] RFQ autofill error:', err);
                  setRfqAutofillError(err instanceof Error ? err.message : 'Failed to auto-fill RFQ');
                } finally {
                  setRfqAutofillLoading(false);
                }
              }}
            >
              {rfqAutofillLoading ? 'Auto-filling…' : 'Auto-fill RFQ'}
            </button>
          </div>
        </div>

        {rfqAutofillError && <div className="rfq-autofill-error">Error: {rfqAutofillError}</div>}

        {rfqAutofillResult && (
          <div className="rfq-autofill-results">
            <div className="rfq-autofill-summary">
              <div>
                <strong>Status:</strong> {rfqAutofillResult.status}
              </div>
              {rfqAutofillResult.reasons?.length > 0 && (
                <div>
                  <strong>Reasons:</strong> {rfqAutofillResult.reasons.join(', ')}
                </div>
              )}
            </div>

            <div className="rfq-autofill-table-container">
              <table className="rfq-autofill-table">
                <thead>
                  <tr>
                    <th>Field</th>
                    <th>Value</th>
                    <th>Confidence</th>
                    <th>Source</th>
                  </tr>
                </thead>
                <tbody>
                  {(
                    [
                      ['finish_od_in', 'Finish OD (in)'],
                      ['finish_len_in', 'Finish Len (in)'],
                      ['finish_id_in', 'Finish ID (in)'],
                      ['rm_od_in', 'RM OD (in)'],
                      ['rm_len_in', 'RM Len (in)'],
                    ] as const
                  ).map(([key, label]) => {
                    const fv = (rfqAutofillResult.fields as any)[key] as {
                      value: number | null;
                      confidence: number;
                      source: string;
                    };
                    return (
                      <tr key={key}>
                        <td>{label}</td>
                        <td
                          className={`rfq-autofill-mono ${getRFQValueCellClass(
                            fv?.value,
                            fv?.confidence ?? 0,
                            rfqAutofillResult.status,
                            rfqAutofillResult.reasons || []
                          )}`}
                        >
                          {formatMaybeNumber(fv?.value, 3)}
                        </td>
                        <td>
                          <span className={`confidence-badge ${getRFQConfidenceBadgeClass(fv?.confidence ?? 0)}`}>
                            {((fv?.confidence ?? 0) * 100).toFixed(0)}%
                          </span>
                        </td>
                        <td className="rfq-autofill-mono">{fv?.source || '—'}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>

            <details className="rfq-autofill-details">
              <summary>Reasons & debug</summary>
              <div className="rfq-autofill-details-body">
                <div>
                  <strong>Reasons:</strong> {(rfqAutofillResult.reasons || []).length ? rfqAutofillResult.reasons.join(', ') : '—'}
                </div>
                <div className="rfq-autofill-debug">
                  <strong>Debug:</strong>{' '}
                  <span className="rfq-autofill-mono">
                    max_od_in={rfqAutofillResult.debug.max_od_in.toFixed(3)}, overall_len_in=
                    {rfqAutofillResult.debug.overall_len_in.toFixed(3)}, scale_method=
                    {rfqAutofillResult.debug.scale_method}, overall_confidence=
                    {(rfqAutofillResult.debug.overall_confidence * 100).toFixed(0)}%, min_len_gate_in=
                    {rfqAutofillResult.debug.min_len_gate_in.toFixed(4)}
                  </span>
                </div>
              </div>
            </details>
          </div>
        )}
      </div>

      {/* Profile Review Plot - shown when there are segments */}
      {segments.length > 0 && (
        <div className="profile-review-section">
          <ProfileReviewPlot
            segments={segments.map(seg => ({
              z_start: seg.z_start,
              z_end: seg.z_end,
              od_diameter: seg.od_diameter,
              id_diameter: seg.id_diameter,
              confidence: seg.confidence,
              flags: (seg as any).flags || []
            }))}
            zRange={partSummary?.z_range}
            units={partSummary?.units?.length || 'in'}
          />
        </div>
      )}

      <div className="step-generation-section">
        <h3>3D Model Generation</h3>
        
        {/* Button-triggered STEP generation from inferred stack - Show when OCC is available or still checking */}
        {!hasStepFile && inferredStack && (occAvailable === true || occAvailable === null) && (
          <div style={{ marginBottom: '1.5rem', padding: '1rem', background: '#2a2a2a', borderRadius: '8px' }}>
            <p className="step-description">
              Generate a STEP file (3D CAD model) from the inferred stack. This will create a solid model
              by converting the stack segments to a Profile2D and revolving it.
              {occAvailable === null && (
                <span style={{ fontSize: '0.85rem', color: '#888', marginLeft: '0.5rem' }}>
                  (Checking OCC availability...)
                </span>
              )}
              {occAvailable === true && occBackend && (
                <span style={{ fontSize: '0.85rem', color: '#888', marginLeft: '0.5rem' }}>
                  (Using {occBackend} backend)
                </span>
              )}
            </p>
            <button
              onClick={() => {
                console.log('[AutoConvertResults] Button clicked! State:', { occAvailable, generatingStepFromStack, occBackend });
                handleGenerateStepFromStack();
              }}
              disabled={generatingStepFromStack || occAvailable !== true}
              className="generate-step-btn"
              style={{
                opacity: (generatingStepFromStack || occAvailable !== true) ? 0.6 : 1,
                cursor: (generatingStepFromStack || occAvailable !== true) ? 'not-allowed' : 'pointer'
              }}
            >
              {generatingStepFromStack ? 'Generating STEP...' : occAvailable === null ? 'Checking OCC...' : 'Generate STEP from inferred stack'}
            </button>
            {stepFromStackError && (
              <div className="step-error" style={{ 
                marginTop: '1rem', 
                padding: '1rem', 
                background: '#3a1a1a', 
                border: '1px solid #ff4444',
                borderRadius: '4px',
                whiteSpace: 'pre-wrap',
                fontFamily: 'monospace',
                fontSize: '0.9rem'
              }}>
                <strong style={{ color: '#ff6666' }}>Error:</strong>
                <div style={{ marginTop: '0.5rem', color: '#ffaaaa' }}>
                  {stepFromStackError}
                </div>
                {stepFromStackStatus === 'UNAVAILABLE' && (
                  <p style={{ marginTop: '0.5rem', fontSize: '0.9rem', color: '#ffaa00' }}>
                    OCC (OpenCASCADE) is not installed. Install pythonocc-core to enable STEP generation.
                  </p>
                )}
                {stepFromStackStatus === 'FAILED' && onSwitchToManual && (
                  <button
                    onClick={onSwitchToManual}
                    className="switch-to-manual-btn"
                    style={{ marginTop: '1rem' }}
                  >
                    Switch to Assisted Manual
                  </button>
                )}
              </div>
            )}
            {stepFromStackStatus === 'OK' && (
              <div className="step-success" style={{ marginTop: '1rem' }}>
                <p>✓ STEP file generated successfully!</p>
                <p style={{ marginTop: '0.5rem', fontSize: '0.9rem' }}>
                  The STEP file should appear in the Downloads section below.
                </p>
              </div>
            )}
          </div>
        )}
        
        {/* Show OCC unavailable message only if OCC is not available */}
        {!hasStepFile && occAvailable === false && (
          <div className="step-requires-occ-warning">
            <h4>⚠️ STEP Disabled: OCC Not Installed or Not Properly Configured</h4>
            <p>
              OCC (OpenCASCADE) is not available in the backend environment. 
              STEP file generation requires OCC to build 3D geometry.
            </p>
            {occError && (
              <div style={{ 
                marginTop: '0.5rem', 
                padding: '0.75rem', 
                background: '#2a1a1a', 
                border: '1px solid #ff4444',
                borderRadius: '4px',
                fontFamily: 'monospace',
                fontSize: '0.85rem',
                color: '#ffaaaa'
              }}>
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
              <li>For pythonocc-core: <code style={{ background: '#1a1a1a', padding: '0.2rem 0.4rem', borderRadius: '3px' }}>pip install pythonocc-core</code></li>
              <li>For OCP (CadQuery): <code style={{ background: '#1a1a1a', padding: '0.2rem 0.4rem', borderRadius: '3px' }}>pip install cadquery</code></li>
            </ul>
          </div>
        )}
        
        {/* Only show stepStatus warnings if OCC is not available - if OCC is available, the button above handles it */}
        {/* Only show these warnings if the button above is NOT showing */}
        {!(!hasStepFile && inferredStack && (occAvailable === true || occAvailable === null)) && (
          <>
            {stepStatus === 'UNAVAILABLE' && !hasStepFile && occAvailable === false ? (
              <div className="step-requires-occ-warning">
                <h4>⚠️ STEP Generation Unavailable</h4>
                <p>
                  OCC (OpenCASCADE) is not installed. STEP file generation requires OCC to build 3D geometry.
                </p>
                {stepReason && (
                  <p><strong>Reason:</strong> {stepReason}</p>
                )}
                <p>
                  <strong>Solution:</strong> Install OCC (pythonocc-core) to enable automatic STEP generation, 
                  or switch to Assisted Manual mode.
                </p>
                {onSwitchToManual && (
                  <button
                    onClick={onSwitchToManual}
                    className="switch-to-manual-btn"
                    style={{ marginTop: '1rem' }}
                  >
                    Switch to Assisted Manual
                  </button>
                )}
              </div>
            ) : stepStatus === 'FAILED' && !hasStepFile && occAvailable === false ? (
              <div className="step-error">
                <h4>❌ STEP Generation Failed</h4>
                <p>
                  Automatic STEP generation failed during auto-detect.
                </p>
                {stepReason && (
                  <p><strong>Reason:</strong> {stepReason}</p>
                )}
                <p>
                  Check <a href={api.getPdfUrl(jobId, 'outputs/scale_report.json')} target="_blank" rel="noopener noreferrer">scale_report.json</a> for details.
                </p>
                {onSwitchToManual && (
                  <button
                    onClick={onSwitchToManual}
                    className="switch-to-manual-btn"
                    style={{ marginTop: '1rem' }}
                  >
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
                        // Retry STEP generation
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
                    <button
                      onClick={onSwitchToManual}
                      className="edit-manually-btn"
                    >
                      Edit Stack Manually
                    </button>
                  )}
                </div>
              </div>
            ) : (hasStepFile || stepFromStackStatus === 'OK') ? (
              <div className="step-success">
                <p>✓ STEP file generated successfully!</p>
                <div className="step-downloads" style={{ 
                  display: 'flex', 
                  gap: '1rem', 
                  marginTop: '1rem',
                  flexWrap: 'wrap'
                }}>
                  <button
                    type="button"
                    className="download-link step-download-btn"
                    onClick={async () => {
                      setDownloadingStep(true);
                      try {
                        // Verify file exists in file list before attempting download
                        const files = await api.getJobFiles(jobId);
                        const stepFile = files.files.find(f => f.path === 'outputs/model.step');
                        
                        if (!stepFile) {
                          // File not in list yet, wait and retry
                          console.log('[AutoConvertResults] STEP file not in file list, waiting...');
                          for (let i = 0; i < 5; i++) {
                            await new Promise(resolve => setTimeout(resolve, 1000));
                            const retryFiles = await api.getJobFiles(jobId);
                            const retryStepFile = retryFiles.files.find(f => f.path === 'outputs/model.step');
                            if (retryStepFile) {
                              console.log(`[AutoConvertResults] STEP file found after ${i + 1} second(s)`);
                              break;
                            }
                          }
                        }
                        
                        await api.downloadFile(jobId, 'outputs/model.step', 'model.step', 5, 2000); // 5 retries, 2 second delays
                      } catch (error) {
                        console.error('Failed to download STEP file:', error);
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
                      opacity: downloadingStep ? 0.8 : 1
                    }}
                    onMouseEnter={(e) => {
                      if (!downloadingStep) {
                        e.currentTarget.style.backgroundColor = '#388e3c';
                      }
                    }}
                    onMouseLeave={(e) => {
                      if (!downloadingStep) {
                        e.currentTarget.style.backgroundColor = '#4caf50';
                      }
                    }}
                  >
                    <span>📥</span>
                    <span>{downloadingStep ? 'Downloading...' : 'Download STEP File'}</span>
                  </button>
                  {stepResult?.outputs?.includes('model.glb') && (
                    <button
                      type="button"
                      className="download-link"
                      onClick={async () => {
                        try {
                          await api.downloadFile(jobId, 'outputs/model.glb', 'model.glb');
                        } catch (error) {
                          console.error('Failed to download GLB file:', error);
                          alert('Failed to download GLB file. Please try again.');
                        }
                      }}
                      style={{
                        display: 'inline-flex',
                        alignItems: 'center',
                        gap: '0.5rem',
                        padding: '0.75rem 1.5rem',
                        backgroundColor: '#646cff',
                        color: 'white',
                        textDecoration: 'none',
                        borderRadius: '4px',
                        fontWeight: '600',
                        transition: 'background-color 0.2s',
                        cursor: 'pointer',
                        border: 'none',
                        outline: 'none'
                      }}
                    >
                      <span>📦</span>
                      <span>Download GLB</span>
                    </button>
                  )}
                </div>
                <p style={{ 
                  marginTop: '1rem', 
                  fontSize: '0.9rem', 
                  color: '#aaa',
                  fontStyle: 'italic'
                }}>
                  The STEP file can be opened in CAD software like SolidWorks, Fusion 360, FreeCAD, or any STEP-compatible viewer.
                </p>
              </div>
            ) : (
              <>
                <p className="step-description">
                  Generate a STEP file (3D CAD model) from the inferred stack. This will create a solid model
                  and update the part summary with feature counts.
                </p>
                {error && <div className="step-error">{error}</div>}
                {showStepConfirmation ? (
                  <div className="step-confirmation">
                    <p><strong>Confirm STEP Generation</strong></p>
                    <p>This will generate a 3D STEP model from the auto-detected stack. Please review the inferred segments and confidence scores before proceeding.</p>
                    <div className="confirmation-buttons">
                      <button
                        onClick={handleGenerateStep}
                        className="confirm-btn"
                      >
                        Confirm & Generate STEP
                      </button>
                      <button
                        onClick={() => setShowStepConfirmation(false)}
                        className="cancel-btn"
                      >
                        Cancel
                      </button>
                    </div>
                  </div>
                ) : (
                  <button
                    onClick={handleGenerateStep}
                    disabled={generatingStep || !inferredStack}
                    className="generate-step-btn"
                  >
                    {generatingStep ? 'Generating STEP...' : 'Generate STEP (3D model)'}
                  </button>
                )}
              </>
            )}
          </>
        )}
      </div>
    </div>
  );
}

export default AutoConvertResults;

