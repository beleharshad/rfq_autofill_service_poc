import { useState, useEffect } from 'react';
import { useParams } from 'react-router-dom';
import { api } from '../services/api';
import ResultsView from '../components/ResultsView/ResultsView';
import LogsView from '../components/LogsView/LogsView';
import AutoConvertResults from '../components/AutoConvertResults/AutoConvertResults';
import type { JobResponse } from '../services/types';
import './JobPage.css';

function JobPage() {
  const { id } = useParams<{ id: string }>();
  const [job, setJob]     = useState<JobResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError]     = useState<string | null>(null);
  const [showLogs, setShowLogs] = useState(false);

  useEffect(() => {
    if (!id) return;
    (async () => {
      try {
        setLoading(true);
        // Only fetch job metadata — AutoConvertResults fetches files itself
        const jobData = await api.getJob(id);
        setJob(jobData);
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Failed to load job');
      } finally {
        setLoading(false);
      }
    })();
  }, [id]);

  if (loading) return <div className="job-page"><div className="loading">Loading job…</div></div>;
  if (error)   return <div className="job-page"><div className="error">Error: {error}</div></div>;
  if (!job)    return <div className="job-page"><div className="error">Job not found</div></div>;

  return (
    <div className="job-page job-page--slim">
      {/* Slim header */}
      <div className="job-header job-header--slim">
        <h1 className="job-title">{job.name || `Job: ${job.job_id.substring(0, 8)}`}</h1>
        <span className={`job-mode-badge job-mode-badge--${job.mode}`}>{job.mode}</span>
        <span className="job-status-chip">{job.status}</span>
        <div className="job-header-actions">
          <button
            className={`job-tab-btn${showLogs ? ' active' : ''}`}
            onClick={() => setShowLogs((v) => !v)}
          >
            {showLogs ? 'Hide Logs' : 'Logs'}
          </button>
        </div>
      </div>

      {showLogs && id ? (
        <div className="job-logs-overlay">
          <LogsView jobId={id} />
        </div>
      ) : (
        <div className="job-main">
          {job.mode === 'auto_convert' && id ? (
            <AutoConvertResults
              jobId={id}
            />
          ) : id ? (
            <ResultsView
              jobId={id}
              job={job}
              onSwitchMode={async (targetMode) => {
                if (!id) return;
                await api.setJobMode(id, targetMode as 'assisted_manual' | 'auto_convert');
                const updated = await api.getJob(id);
                setJob(updated);
              }}
            />
          ) : null}
        </div>
      )}
    </div>
  );
}

export default JobPage;
