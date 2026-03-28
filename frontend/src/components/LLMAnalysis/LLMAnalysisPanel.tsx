import { useState, useCallback, useEffect } from 'react';
import { api } from '../../services/api';
import type { LLMAnalysisResult, LLMExtractedSpecs } from '../../services/types';
import './LLMAnalysisPanel.css';

interface Props {
  jobId: string;
}

const SPEC_LABELS: { key: keyof LLMExtractedSpecs; label: string; unit?: string; group?: string }[] = [
  { key: 'part_number',      label: 'Part Number' },
  { key: 'part_name',        label: 'Part Name' },
  { key: 'material',         label: 'Material' },
  { key: 'quantity',         label: 'Quantity' },
  // OD group
  { key: 'od_in',            label: 'Finish OD',  unit: 'in', group: 'OD' },
  { key: 'max_od_in',        label: 'MAX OD',     unit: 'in', group: 'OD' },
  // ID group
  { key: 'id_in',            label: 'Finish ID',  unit: 'in', group: 'ID' },
  { key: 'max_id_in',        label: 'MAX ID',     unit: 'in', group: 'ID' },
  // Length group
  { key: 'length_in',        label: 'Finish Length', unit: 'in', group: 'Length' },
  { key: 'max_length_in',    label: 'MAX Length', unit: 'in', group: 'Length' },
  // Tolerances / meta
  { key: 'tolerance_od',     label: 'Tolerance OD' },
  { key: 'tolerance_id',     label: 'Tolerance ID' },
  { key: 'tolerance_length', label: 'Tolerance Length' },
  { key: 'finish',           label: 'Finish' },
  { key: 'revision',         label: 'Revision' },
];

function confidenceColor(conf: number | undefined): string {
  if (conf === undefined) return '';
  if (conf >= 0.85) return 'conf-high';
  if (conf >= 0.6) return 'conf-med';
  return 'conf-low';
}

export default function LLMAnalysisPanel({ jobId }: Props) {
  const [result, setResult] = useState<LLMAnalysisResult | null>(null);
  const [partSummary, setPartSummary] = useState<any | null>(null);
  const [running, setRunning] = useState(false);
  const [exporting, setExporting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [autoLoaded, setAutoLoaded] = useState(false);

  // Auto-load cached analysis produced by the infer-stack pipeline
  useEffect(() => {
    let cancelled = false;
    api.getLlmAnalysis(jobId)
      .then(data => {
        if (!cancelled && data !== null) {
          setResult(data);
          setAutoLoaded(true);
        }
      })
      .catch(() => { /* network error — ignore on mount */ });
    // Also load part_summary (geometry-derived values) so we can prefer them when reliable
    api.getPartSummary(jobId).then(ps => setPartSummary(ps)).catch(() => {});
    return () => { cancelled = true; };
  }, [jobId]);

  const runAnalysis = useCallback(async () => {
    setRunning(true);
    setError(null);
    setAutoLoaded(false);
    try {
      const data = await api.llmAnalyzeJob(jobId);
      setResult(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Analysis failed');
    } finally {
      setRunning(false);
    }
  }, [jobId]);

  const loadCached = useCallback(async () => {
    setError(null);
    setAutoLoaded(false);
    try {
      const data = await api.getLlmAnalysis(jobId);
      if (data === null) {
        setError('No analysis found yet — click \'▶ Run LLM Analysis\' to generate one.');
      } else {
        setResult(data);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load result');
    }
  }, [jobId]);

  const downloadExcel = useCallback(async () => {
    setExporting(true);
    setError(null);
    try {
      const { blob, filename } = await api.llmAnalysisExportExcel(jobId);
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = filename;
      a.click();
      URL.revokeObjectURL(url);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Excel export failed');
    } finally {
      setExporting(false);
    }
  }, [jobId]);

  const rec = result?.validation?.recommendation;
  const recClass =
    rec === 'ACCEPT' ? 'rec-accept' : rec === 'REJECT' ? 'rec-reject' : 'rec-review';

  return (
    <div className="llm-panel">
      {/* ── Header ── */}
      <div className="llm-panel-header">
        <div>
          <h2 className="llm-panel-title">LLM PDF Analysis</h2>
          <p className="llm-panel-subtitle">
            Two-agent pipeline: Agent 1 extracts specs, Agent 2 validates them.
          </p>
        </div>
        <div className="llm-panel-actions">
          {autoLoaded && !running && (
            <span className="badge badge-auto">⚡ Auto-analyzed</span>
          )}
          <button className="btn btn-secondary" onClick={loadCached} disabled={running}>
            ↻ Refresh
          </button>
          <button className="btn btn-primary" onClick={runAnalysis} disabled={running}>
            {running ? '⏳ Analysing…' : '▶ Run LLM Analysis'}
          </button>
          {result && (
            <button className="btn btn-excel" onClick={downloadExcel} disabled={exporting}>
              {exporting ? 'Exporting…' : '⬇ Export to Excel'}
            </button>
          )}
        </div>
      </div>

      {error && <div className="llm-error">⚠ {error}</div>}

      {running && (
        <div className="llm-running">
          <div className="spinner" />
          <span>Running two-agent LLM pipeline — this may take 10–30 s…</span>
        </div>
      )}

      {!result && !running && !error && (
        <div className="llm-empty-state">
          <div className="llm-empty-icon">🤖</div>
          <p>No analysis yet for this job.</p>
          <p className="llm-empty-hint">
            Click <strong>▶ Run LLM Analysis</strong> to extract part specs from the uploaded PDF.
            Analysis also runs automatically after infer-stack completes.
          </p>
        </div>
      )}

      {result && !running && (
        <>
          {/* ── Summary badges ── */}
          <div className="llm-summary-row">
            <div className={`llm-badge ${result.valid ? 'badge-ok' : 'badge-fail'}`}>
              {result.valid ? '✅ VALID' : '❌ INVALID'}
            </div>
            <div className={`llm-badge ${recClass}`}>
              Recommendation: {rec ?? '—'}
            </div>
            <div className="llm-badge badge-neutral">
              Confidence:{' '}
              {result.validation?.overall_confidence !== undefined
                ? `${Math.round(result.validation.overall_confidence * 100)}%`
                : '—'}
            </div>
            <div className="llm-badge badge-neutral">
              PDF text: {result.pdf_text_length?.toLocaleString()} chars
            </div>
          </div>

          {/* ── Extracted Specs table ── */}
          <section className="llm-section">
            <h3 className="llm-section-title">Agent 1 — Extracted Specifications</h3>
            <table className="llm-table">
              <thead>
                <tr>
                  <th>Field</th>
                  <th>Value</th>
                  <th>Confidence</th>
                  <th>Issue</th>
                </tr>
              </thead>
              <tbody>
                {SPEC_LABELS.map(({ key, label, unit, group }, idx) => {
                  // Determine displayed value: prefer geometry-derived length when available and reliable
                  const llmVal = result.extracted?.[key];
                  const vinfo = result.validation?.fields?.[key];
                  const conf = vinfo?.confidence;
                  const issue = vinfo?.issue;
                  let displayVal: any = llmVal;
                  let displaySource = 'LLM';
                  if (key === 'length_in' && partSummary) {
                    // geometry confidence from inference_metadata or scale_report
                    const geomConf = partSummary.inference_metadata?.overall_confidence ?? partSummary.scale_report?.confidence ?? 0;
                    const scaleValid = partSummary.scale_report?.validation_passed ?? false;
                    if (geomConf >= 0.8 || scaleValid) {
                      displayVal = partSummary.totals?.total_length_in ?? displayVal;
                      displaySource = 'Geometry';
                    }
                  }
                  // group separator row
                  const prevGroup = idx > 0 ? SPEC_LABELS[idx - 1].group : null;
                  const showGroupHeader = group && group !== prevGroup;
                  return (
                    <>
                      {showGroupHeader && (
                        <tr key={`grp-${group}`} className="group-header-row">
                          <td colSpan={4} className="group-header-cell">{group} Dimensions</td>
                        </tr>
                      )}
                      <tr key={key} className={issue ? 'row-issue' : ''}>
                        <td className="field-label">{label}</td>
                        <td>
                          {displayVal !== null && displayVal !== undefined
                            ? `${displayVal}${unit ? ' ' + unit : ''} `
                            : <span className="null-val">—</span>}
                          {displaySource && <span className="source-badge">{displaySource}</span>}
                        </td>
                        <td className={confidenceColor(conf)}>
                          {conf !== undefined ? `${Math.round(conf * 100)}%` : '—'}
                        </td>
                        <td className="issue-cell">{issue ?? ''}</td>
                      </tr>
                    </>
                  );
                })}
              </tbody>
            </table>
          </section>

          {/* ── Code-level issues ── */}
          {result.code_issues?.length > 0 && (
            <section className="llm-section">
              <h3 className="llm-section-title llm-section-title--warn">
                ⚠ Code-Level Rule Violations
              </h3>
              <ul className="llm-issues-list">
                {result.code_issues.map((issue, i) => (
                  <li key={i}>{issue}</li>
                ))}
              </ul>
            </section>
          )}

          {/* ── Agent 2 cross-checks ── */}
          {(result.validation?.cross_checks?.length ?? 0) > 0 && (
            <section className="llm-section">
              <h3 className="llm-section-title llm-section-title--warn">
                Agent 2 — Cross-Check Findings
              </h3>
              <ul className="llm-issues-list">
                {result.validation.cross_checks.map((c, i) => (
                  <li key={i}>{c}</li>
                ))}
              </ul>
            </section>
          )}

          {/* ── No issues banner ── */}
          {result.valid &&
            result.code_issues.length === 0 &&
            (result.validation?.cross_checks?.length ?? 0) === 0 && (
              <div className="llm-all-ok">
                ✅ All checks passed — extraction looks clean.
              </div>
            )}
        </>
      )}
    </div>
  );
}
