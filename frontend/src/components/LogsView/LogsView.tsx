import { useState, useEffect } from 'react';
import { api } from '../../services/api';
import type { RunReport } from '../../services/types';
import './LogsView.css';

interface LogsViewProps {
  jobId: string;
}

function LogsView({ jobId }: LogsViewProps) {
  const [report, setReport] = useState<RunReport | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const loadReport = async () => {
      try {
        setLoading(true);
        setError(null);
        const data = await api.getRunReport(jobId);
        if (data === null) {
          // Report doesn't exist yet - this is normal, not an error
          setReport(null);
          setError(null); // Clear any previous error
        } else {
          setReport(data);
        }
      } catch (err) {
        // Only set error for actual failures, not missing reports
        setError(err instanceof Error ? err.message : 'Failed to load run report');
      } finally {
        setLoading(false);
      }
    };

    loadReport();
  }, [jobId]);

  if (loading) {
    return (
      <div className="logs-view">
        <div className="loading">Loading run report...</div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="logs-view">
        <div className="error">Error: {error}</div>
        <p className="error-hint">Failed to load run report. Please try again later.</p>
      </div>
    );
  }

  if (!report) {
    return (
      <div className="logs-view">
        <div className="no-report">
          <h3>No Run Report Available</h3>
          <p>No run report has been generated for this job yet.</p>
          <p className="hint">
            Run an analysis to generate a report. The report will show:
          </p>
          <ul className="hint-list">
            <li>Pipeline stages and their status</li>
            <li>Processing duration and timestamps</li>
            <li>Generated output files</li>
            <li>Any errors or warnings</li>
          </ul>
          <p className="hint">
            To generate a report, process a stack (Assisted Manual or Auto Convert mode) or run a Profile2D analysis.
          </p>
        </div>
      </div>
    );
  }

  const formatDuration = (ms: number | undefined): string => {
    if (ms === undefined) return 'N/A';
    if (ms < 1000) return `${ms.toFixed(2)} ms`;
    return `${(ms / 1000).toFixed(2)} s`;
  };

  const getStatusClass = (status: string): string => {
    switch (status) {
      case 'completed':
        return 'status-completed';
      case 'failed':
        return 'status-failed';
      case 'running':
        return 'status-running';
      case 'pending':
        return 'status-pending';
      case 'skipped':
        return 'status-skipped';
      default:
        return 'status-unknown';
    }
  };

  return (
    <div className="logs-view">
      <div className="report-header">
        <h3>Run Report</h3>
        <div className="report-summary">
          <div className="summary-item">
            <span className="summary-label">Status:</span>
            <span className={`summary-value ${getStatusClass(report.status)}`}>
              {report.status.toUpperCase()}
            </span>
          </div>
          <div className="summary-item">
            <span className="summary-label">Duration:</span>
            <span className="summary-value">{formatDuration(report.duration_ms)}</span>
          </div>
          <div className="summary-item">
            <span className="summary-label">Started:</span>
            <span className="summary-value">
              {report.started_at ? new Date(report.started_at).toLocaleString() : 'N/A'}
            </span>
          </div>
          {report.finished_at && (
            <div className="summary-item">
              <span className="summary-label">Finished:</span>
              <span className="summary-value">
                {new Date(report.finished_at).toLocaleString()}
              </span>
            </div>
          )}
        </div>
      </div>

      <div className="report-sections">
        <div className="report-section">
          <h4>Pipeline Stages</h4>
          <div className="stages-list">
            {report.stages.map((stage, index) => (
              <div key={index} className={`stage-card ${getStatusClass(stage.status)}`}>
                <div className="stage-header">
                  <span className="stage-name">{stage.name}</span>
                  <span className={`stage-status ${getStatusClass(stage.status)}`}>
                    {stage.status}
                  </span>
                </div>
                <div className="stage-details">
                  {stage.started_at && (
                    <div className="stage-detail">
                      <span>Started:</span> {new Date(stage.started_at).toLocaleTimeString()}
                    </div>
                  )}
                  {stage.finished_at && (
                    <div className="stage-detail">
                      <span>Finished:</span> {new Date(stage.finished_at).toLocaleTimeString()}
                    </div>
                  )}
                  {stage.duration_ms !== undefined && (
                    <div className="stage-detail">
                      <span>Duration:</span> {formatDuration(stage.duration_ms)}
                    </div>
                  )}
                  {stage.error && (
                    <div className="stage-error">
                      <strong>Error:</strong> {stage.error}
                    </div>
                  )}
                  {stage.warning && (
                    <div className="stage-warning">
                      <strong>Warning:</strong> {stage.warning}
                    </div>
                  )}
                </div>
              </div>
            ))}
          </div>
        </div>

        {report.outputs.length > 0 && (
          <div className="report-section">
            <h4>Generated Outputs</h4>
            <ul className="outputs-list">
              {report.outputs.map((output, index) => (
                <li key={index}>{output}</li>
              ))}
            </ul>
          </div>
        )}

        {report.errors.length > 0 && (
          <div className="report-section">
            <h4>Errors</h4>
            <ul className="errors-list">
              {report.errors.map((error, index) => (
                <li key={index} className="error-item">{error}</li>
              ))}
            </ul>
          </div>
        )}

        {report.warnings.length > 0 && (
          <div className="report-section">
            <h4>Warnings</h4>
            <ul className="warnings-list">
              {report.warnings.map((warning, index) => (
                <li key={index} className="warning-item">{warning}</li>
              ))}
            </ul>
          </div>
        )}
      </div>

      <div className="report-raw">
        <details>
          <summary>Raw JSON</summary>
          <pre className="json-preview">
            {JSON.stringify(report, null, 2)}
          </pre>
        </details>
      </div>
    </div>
  );
}

export default LogsView;

