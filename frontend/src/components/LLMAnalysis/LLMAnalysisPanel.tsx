import { useState, useCallback, useEffect, useRef } from 'react';
import { api } from '../../services/api';
import type { LLMAnalysisResult, LLMExtractedSpecs, CorrectionsMap } from '../../services/types';
import './LLMAnalysisPanel.css';

interface Props {
  jobId: string;
  /** Called whenever a correction is saved, so parent components can refresh. */
  onCorrectionsChange?: (corrections: CorrectionsMap) => void;
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

/** Fields that support numeric inline editing (pencil icon). */
const EDITABLE_NUMERIC_KEYS = new Set([
  'od_in', 'max_od_in', 'id_in', 'max_id_in', 'length_in', 'max_length_in', 'quantity',
]);

export default function LLMAnalysisPanel({ jobId, onCorrectionsChange }: Props) {
  const [result, setResult] = useState<LLMAnalysisResult | null>(null);
  const [running, setRunning] = useState(false);
  const [exporting, setExporting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [autoLoaded, setAutoLoaded] = useState(false);

  // Correction state
  const [corrections, setCorrections] = useState<CorrectionsMap>({});
  const [editingKey, setEditingKey] = useState<string | null>(null);
  const [editValue, setEditValue] = useState<string>('');
  const editInputRef = useRef<HTMLInputElement>(null);

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
    // Load any previously-saved corrections
    api.getCorrections(jobId).then(corrs => {
      if (!cancelled) setCorrections(corrs);
    }).catch(() => {});
    return () => { cancelled = true; };
  }, [jobId]);

  // Focus the inline edit input whenever it appears
  useEffect(() => {
    if (editingKey) editInputRef.current?.focus();
  }, [editingKey]);

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

  const handleStartEdit = useCallback((key: string, currentDisplayVal: any) => {
    setEditingKey(key);
    // Pre-populate with corrected value if present, otherwise LLM value
    const current = corrections[key]?.value ?? currentDisplayVal;
    setEditValue(current != null ? String(current) : '');
  }, [corrections]);

  const handleSaveEdit = useCallback(async (key: keyof LLMExtractedSpecs) => {
    const trimmed = editValue.trim();
    setEditingKey(null);
    if (trimmed === '') return; // empty — discard

    const parsed = EDITABLE_NUMERIC_KEYS.has(key) ? parseFloat(trimmed) : trimmed;
    if (EDITABLE_NUMERIC_KEYS.has(key) && isNaN(parsed as number)) return; // invalid number

    const originalValue = result?.extracted?.[key] ?? null;
    try {
      await api.saveCorrection(jobId, key, parsed as number | string, originalValue as string | number | null);
      const updated: CorrectionsMap = {
        ...corrections,
        [key]: { field: key, value: parsed as number | string, original_value: originalValue as string | number | null, corrected_at: new Date().toISOString() },
      };
      setCorrections(updated);
      onCorrectionsChange?.(updated);
    } catch {
      setError('Failed to save correction — please try again.');
    }
  }, [editValue, corrections, jobId, result, onCorrectionsChange]);

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
                  // Determine displayed value: corrections → geometry-derived → LLM
                  const llmVal = result.extracted?.[key];
                  const correction = corrections[key];
                  const vinfo = result.validation?.fields?.[key];
                  const conf = vinfo?.confidence;
                  const issue = vinfo?.issue;
                  let displayVal: any = llmVal;
                  let displaySource = 'LLM';
                  // Corrections override everything
                  const isCorrected = correction != null && correction.value != null;
                  if (isCorrected) {
                    displayVal = correction!.value;
                    displaySource = 'Corrected';
                  }
                  const isEditable = EDITABLE_NUMERIC_KEYS.has(key);
                  const isEditing = editingKey === key;

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
                      <tr key={key} className={[issue ? 'row-issue' : '', isCorrected ? 'row-corrected' : ''].filter(Boolean).join(' ')}>
                        <td className="field-label">{label}</td>
                        <td className="value-cell">
                          {isEditing ? (
                            <input
                              ref={editInputRef}
                              className="dim-edit-input"
                              value={editValue}
                              onChange={e => setEditValue(e.target.value)}
                              onBlur={() => handleSaveEdit(key)}
                              onKeyDown={e => {
                                if (e.key === 'Enter') handleSaveEdit(key);
                                if (e.key === 'Escape') { setEditingKey(null); }
                              }}
                            />
                          ) : (
                            <>
                              {displayVal !== null && displayVal !== undefined
                                ? `${displayVal}${unit ? ' ' + unit : ''} `
                                : <span className="null-val">—</span>}
                              {isCorrected
                                ? <span className="badge-corrected">✏ Corrected</span>
                                : displaySource && <span className="source-badge">{displaySource}</span>}
                              {isEditable && !isEditing && (
                                <button
                                  className="edit-pencil"
                                  title="Edit value"
                                  onClick={() => handleStartEdit(key, displayVal)}
                                >✏</button>
                              )}
                            </>
                          )}
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
