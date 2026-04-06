import { useState, useEffect } from 'react';
import './ResultsView.css';
import ProfilePlot from './ProfilePlot';
import ThreeJSViewer from './ThreeJSViewer';
import SegmentTable from './SegmentTable';
import TotalsCards from './TotalsCards';
import { api } from '../../services/api';
import { PartSummary } from '../../services/types';
import { setSegments, type Segment } from '../../state/segmentStore';

interface ResultsViewProps {
  jobId: string;
  job: any;
  onSwitchMode?: (mode: string) => void;
}

function ResultsView({ jobId, job, onSwitchMode }: ResultsViewProps) {
  const [summary, setSummary] = useState<PartSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [highlightedSegment, setHighlightedSegment] = useState<number | undefined>(undefined);
  const [overallConfidence, setOverallConfidence] = useState<number | null>(null);

  const [hasStepFile, setHasStepFile] = useState(false);
  const [stepDownloadUrl, setStepDownloadUrl] = useState<string | null>(null);

  // STEP generation (from inferred stack)
  const [inferredStackExists, setInferredStackExists] = useState(false);
  const [occAvailable, setOccAvailable] = useState<boolean | null>(null);
  const [occBackend, setOccBackend] = useState<string | null>(null);
  const [occError, setOccError] = useState<string | null>(null);

  const [generatingStepFromStack, setGeneratingStepFromStack] = useState(false);
  const [stepFromStackError, setStepFromStackError] = useState<string | null>(null);
  const [stepFromStackStatus, setStepFromStackStatus] = useState<string | null>(null);
  const [downloadingStep, setDownloadingStep] = useState(false);

  // Feature visualization toggles
  const [showHoles, setShowHoles] = useState(true);
  const [showSlots, setShowSlots] = useState(true);
  const [showChamfers, setShowChamfers] = useState(false);
  const [showFillets, setShowFillets] = useState(false);

  useEffect(() => {
    const loadSummary = async () => {
      try {
        setLoading(true);
        setError(null);

        // OCC availability drives whether we can generate STEP on-demand
        try {
          console.log('[ResultsView] Checking OCC availability...');
          const occ = await api.checkOccAvailability();
          console.log('[ResultsView] OCC availability result:', occ);
          setOccAvailable(occ?.occ_available ?? false);
          setOccBackend(occ?.backend ?? null);
          setOccError(occ?.error ?? null);
        } catch (e) {
          console.warn('[ResultsView] OCC availability check failed:', e);
          setOccAvailable(false);
          setOccBackend(null);
          setOccError(e instanceof Error ? e.message : 'OCC check failed');
        }

        // Load part_summary.json
        const summaryData = await api.getPartSummary(jobId);
        setSummary(summaryData);
        
        // Populate segment store for 3D viewer hover
        if (summaryData?.segments && Array.isArray(summaryData.segments)) {
          const segments: Segment[] = summaryData.segments.map((seg: any) => ({
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
          console.log('[ResultsView] Populated segment store with', segments.length, 'segments');
        }

        if (summaryData?.inference_metadata?.overall_confidence !== undefined && summaryData?.inference_metadata?.overall_confidence !== null) {
          setOverallConfidence(summaryData.inference_metadata.overall_confidence);
        } else if (job?.inference_results?.overall_confidence !== undefined) {
          setOverallConfidence(job.inference_results.overall_confidence);
        }

        // Check output files (STEP + inferred_stack)
        let stepExists = false;
        let stepUrl: string | null = null;

        try {
          const files = await api.getJobFiles(jobId);
          console.log('[ResultsView] Job files:', files);

          const stepFile = files.files.find((f: any) => f.path === 'outputs/model.step');
          const inferredStackFile = files.files.find((f: any) => f.path === 'outputs/inferred_stack.json');

          setInferredStackExists(!!inferredStackFile);

          // Prefer actual file presence
          stepExists = !!stepFile;
          stepUrl = stepFile?.url || (stepFile ? api.getPdfUrl(jobId, stepFile.path) : null);

          // Optional: if backend exposes outputs_info.step_model, allow it as secondary
          if (!stepExists && job?.outputs_info?.step_model) {
            const stepInfo = job.outputs_info.step_model;
            if (stepInfo?.exists && stepInfo?.download_url) {
              stepExists = true;
              stepUrl = stepInfo.download_url;
            }
          }
        } catch (fileErr) {
          console.warn('[ResultsView] Could not check job files:', fileErr);
        }

        setHasStepFile(stepExists);
        setStepDownloadUrl(stepUrl);
      } catch (err) {
        console.error('Error loading results summary:', err);
        setError(err instanceof Error ? err.message : 'Failed to load results');
      } finally {
        setLoading(false);
      }
    };

    loadSummary();
  }, [jobId, job]);

  // Separate effect to periodically check for STEP file if it doesn't exist yet
  useEffect(() => {
    // Only check if file doesn't exist and we're not currently generating
    if (hasStepFile || generatingStepFromStack) {
      return;
    }

    const interval = setInterval(async () => {
      try {
        const files = await api.getJobFiles(jobId);
        const stepFile = files.files.find((f: any) => f.path === 'outputs/model.step');
        if (stepFile) {
          setHasStepFile(true);
          setStepDownloadUrl(stepFile.url || api.getPdfUrl(jobId, stepFile.path));
        }
      } catch (err) {
        console.warn('[ResultsView] Error checking for STEP file:', err);
      }
    }, 3000); // Check every 3 seconds
    
    return () => clearInterval(interval);
  }, [jobId, hasStepFile, generatingStepFromStack]);

  const handleGenerateStepFromStack = async () => {
    if (generatingStepFromStack) return;

    setStepFromStackError(null);
    setStepFromStackStatus(null);
    setGeneratingStepFromStack(true);

    try {
      console.log('[ResultsView] Calling generateStepFromInferredStack for job:', jobId);
      const result = await api.generateStepFromInferredStack(jobId);
      console.log('[ResultsView] generateStepFromInferredStack result:', result);

      setStepFromStackStatus(result?.status ?? null);

      if (result?.status === 'OK') {
        // Poll for file to appear in file list (up to 10 seconds)
        console.log('[ResultsView] STEP generation successful, waiting for file to appear...');
        let fileFound = false;
        for (let attempt = 0; attempt < 10; attempt++) {
          const files = await api.getJobFiles(jobId);
          const stepFile = files.files.find((f: any) => f.path === 'outputs/model.step');
          
          if (stepFile) {
            console.log(`[ResultsView] STEP file found in file list after ${attempt + 1} attempt(s)`);
            setHasStepFile(true);
            setStepDownloadUrl(stepFile.url || api.getPdfUrl(jobId, stepFile.path));
            fileFound = true;
            break;
          }
          
          // Wait 1 second before next attempt
          if (attempt < 9) {
            await new Promise(resolve => setTimeout(resolve, 1000));
          }
        }
        
        if (!fileFound) {
          console.warn('[ResultsView] STEP file not found in file list after 10 seconds, but API reported success');
          // Still set hasStepFile to true so download button appears, but it might fail
          setHasStepFile(true);
          setStepDownloadUrl(api.getPdfUrl(jobId, 'outputs/model.step'));
        }
      } else if (result?.status === 'UNAVAILABLE') {
        setStepFromStackError(result?.message || 'OCC not available (backend reported UNAVAILABLE).');
      } else {
        setStepFromStackError(result?.message || 'STEP generation failed.');
      }
    } catch (err) {
      console.error('[ResultsView] STEP generation error:', err);
      setStepFromStackStatus('FAILED');
      setStepFromStackError(err instanceof Error ? err.message : 'Failed to generate STEP');
    } finally {
      setGeneratingStepFromStack(false);
    }
  };

  if (loading) {
    return <div className="loading">Loading results...</div>;
  }

  if (error || !summary) {
    return (
      <div className="error">
        Error loading results: {error || 'No results found'}
        {summary === null && (
          <div className="error-hint">
            Make sure you have run Auto Convert or Manual/Profile2D mode first.
          </div>
        )}
      </div>
    );
  }

  const isAutoMode = summary.inference_metadata?.mode === 'auto_detect';
  const isStepBacked = (summary.inference_metadata?.source || '').startsWith('uploaded_step');
  // Warnings may come from the summary or be empty
  const warnings: string[] = (summary as any).warnings || [];

  return (
    <div className="results-view">
      <div className="results-header">
        <h2>Results</h2>

        {overallConfidence !== null && (
          <div className={`overall-confidence-badge ${getConfidenceBadgeClass(overallConfidence)}`}>
            Overall Confidence: {getConfidenceLabel(overallConfidence)} ({(overallConfidence * 100).toFixed(0)}%)
          </div>
        )}

        {onSwitchMode && (
          <div className="mode-switch-section">
            <button
              className="switch-mode-btn"
              onClick={() => onSwitchMode(isAutoMode ? 'assisted_manual' : 'auto_convert')}
            >
              Switch to {isAutoMode ? 'Manual' : 'Auto Convert'} Mode
            </button>
          </div>
        )}
      </div>

      {warnings.length > 0 && (
        <div className="results-warnings">
          <h3>Warnings</h3>
          <ul>
            {warnings.map((warning: string, idx: number) => (
              <li key={idx}>{warning}</li>
            ))}
          </ul>
        </div>
      )}

      <div className="downloads-section">
        <h3>Downloads</h3>
        <div className="download-links">
          <button
            type="button"
            className="download-link"
            onClick={() => api.downloadFile(jobId, 'outputs/part_summary.json', 'part_summary.json')}
          >
            Download part_summary.json
          </button>

          {(hasStepFile && stepDownloadUrl) || stepFromStackStatus === 'OK' ? (
            <button
              type="button"
              className="download-link step-download"
              onClick={async () => {
                setDownloadingStep(true);
                try {
                  // Verify file exists in file list before attempting download
                  const files = await api.getJobFiles(jobId);
                  const stepFile = files.files.find((f: any) => f.path === 'outputs/model.step');
                  
                  if (!stepFile) {
                    // File not in list yet, wait and retry
                    console.log('[ResultsView] STEP file not in file list, waiting...');
                    for (let i = 0; i < 5; i++) {
                      await new Promise(resolve => setTimeout(resolve, 1000));
                      const retryFiles = await api.getJobFiles(jobId);
                      const retryStepFile = retryFiles.files.find((f: any) => f.path === 'outputs/model.step');
                      if (retryStepFile) {
                        console.log(`[ResultsView] STEP file found after ${i + 1} second(s)`);
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
                border: 'none',
                outline: 'none',
                cursor: downloadingStep ? 'wait' : 'pointer',
                opacity: downloadingStep ? 0.7 : 1
              }}
            >
              {downloadingStep ? 'Downloading...' : 'Download model.step'}
            </button>
          ) : (
            <>
              {!isStepBacked && inferredStackExists && occAvailable === true ? (
                <button
                  type="button"
                  className="download-link step-download"
                  onClick={handleGenerateStepFromStack}
                  disabled={generatingStepFromStack}
                  title={occBackend ? `Using ${occBackend}` : 'Generate STEP from inferred stack'}
                  style={{
                    border: 'none',
                    outline: 'none',
                    opacity: generatingStepFromStack ? 0.7 : 1,
                    cursor: generatingStepFromStack ? 'not-allowed' : 'pointer'
                  }}
                >
                  {generatingStepFromStack ? 'Generating STEP...' : 'Generate model.step'}
                </button>
              ) : isStepBacked ? (
                <div
                  className="download-link disabled"
                  title="STEP-uploaded jobs use the uploaded STEP file as the 3D source of truth."
                  style={{
                    display: 'inline-flex',
                    alignItems: 'center',
                    gap: '0.5rem'
                  }}
                >
                  <span>✓</span>
                  <span>STEP-backed 3D source</span>
                </div>
              ) : (
                <div
                  className="download-link disabled"
                  title={
                    !inferredStackExists
                      ? 'Run Auto Convert → Infer Stack first (creates outputs/inferred_stack.json).'
                      : occAvailable === false
                        ? `OCC not available in backend.${occError ? ` Error: ${occError}` : ''}`
                        : 'Checking OCC availability...'
                  }
                  style={{
                    display: 'inline-flex',
                    alignItems: 'center',
                    gap: '0.5rem'
                  }}
                >
                  <span>⚠</span>
                  <span>STEP not ready</span>
                </div>
              )}
            </>
          )}
        </div>

        {!hasStepFile && stepFromStackStatus !== 'OK' && (
          <p className="download-hint" style={{ marginTop: '1rem', whiteSpace: 'pre-line' }}>
            {isStepBacked
              ? '💡 STEP-uploaded jobs use the uploaded STEP file directly for 3D preview and downloads.'
              : inferredStackExists && occAvailable === true
              ? '💡 You can generate a STEP directly from the inferred stack (button above).'
              : !inferredStackExists
                ? '💡 Run Auto Convert → Infer Stack first (this creates outputs/inferred_stack.json).'
                : occAvailable === false
                  ? '💡 STEP generation is disabled because OCC (OpenCASCADE) is not available in the backend.'
                  : '💡 Checking STEP capability...'}
            {stepFromStackError ? `\n\nError: ${stepFromStackError}` : ''}
          </p>
        )}
        {stepFromStackStatus === 'OK' && (
          <p className="download-hint" style={{ marginTop: '1rem', color: '#28a745', fontWeight: '500' }}>
            ✓ STEP generated successfully. Click "Download model.step" above to download.
          </p>
        )}
      </div>

      <ProfilePlot
        summary={summary}
        highlightedSegment={highlightedSegment}
        onSegmentHover={(index) => setHighlightedSegment(index ?? undefined)}
        onSegmentClick={setHighlightedSegment}
      />
      {/* Feature toggles for 3D viewer */}
      {summary?.features && (
        <div className="feature-toggles" style={{ 
          display: 'flex', 
          gap: '1rem', 
          padding: '0.5rem 1rem',
          background: '#1a1a2e',
          borderRadius: '6px',
          marginBottom: '1rem',
          flexWrap: 'wrap',
          alignItems: 'center'
        }}>
          <span style={{ color: '#888', fontSize: '0.9rem' }}>3D Features:</span>
          <label style={{ display: 'flex', alignItems: 'center', gap: '0.3rem', cursor: 'pointer' }}>
            <input 
              type="checkbox" 
              checked={showHoles} 
              onChange={(e) => setShowHoles(e.target.checked)} 
            />
            <span style={{ color: '#ff4444' }}>Holes ({summary.features.holes?.length || 0})</span>
          </label>
          <label style={{ display: 'flex', alignItems: 'center', gap: '0.3rem', cursor: 'pointer' }}>
            <input 
              type="checkbox" 
              checked={showSlots} 
              onChange={(e) => setShowSlots(e.target.checked)} 
            />
            <span style={{ color: '#44ff44' }}>Slots ({summary.features.slots?.length || 0})</span>
          </label>
          <label style={{ display: 'flex', alignItems: 'center', gap: '0.3rem', cursor: 'pointer' }}>
            <input 
              type="checkbox" 
              checked={showChamfers} 
              onChange={(e) => setShowChamfers(e.target.checked)} 
            />
            <span style={{ color: '#ffff44' }}>Chamfers ({summary.features.chamfers?.length || 0})</span>
          </label>
          <label style={{ display: 'flex', alignItems: 'center', gap: '0.3rem', cursor: 'pointer' }}>
            <input 
              type="checkbox" 
              checked={showFillets} 
              onChange={(e) => setShowFillets(e.target.checked)} 
            />
            <span style={{ color: '#ff44ff' }}>Fillets ({summary.features.fillets?.length || 0})</span>
          </label>
        </div>
      )}
      <ThreeJSViewer 
        summary={summary} 
        jobId={jobId}
        onHoveredSegmentChange={(index) => {
          // Sync table highlight with 3D viewer hover
          setHighlightedSegment(index ?? undefined);
        }}
        showHoles={showHoles}
        showSlots={showSlots}
        showChamfers={showChamfers}
        showFillets={showFillets}
      />
      <SegmentTable
        summary={summary}
        highlightedSegment={highlightedSegment}
        onSegmentClick={setHighlightedSegment}
        showConfidence={isAutoMode}
      />
      <TotalsCards summary={summary} />
    </div>
  );
}

function getConfidenceBadgeClass(confidence: number): string {
  if (confidence >= 0.8) return 'confidence-high';
  if (confidence >= 0.6) return 'confidence-medium';
  return 'confidence-low';
}

function getConfidenceLabel(confidence: number): string {
  if (confidence >= 0.8) return 'High';
  if (confidence >= 0.6) return 'Medium';
  return 'Low';
}

export default ResultsView;
