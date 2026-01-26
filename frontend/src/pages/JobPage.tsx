import { useState, useEffect } from 'react';
import { useParams } from 'react-router-dom';
import { api } from '../services/api';
import PDFViewer from '../components/PDFViewer/PDFViewer';
import ProfileBuilder from '../components/ProfileBuilder/ProfileBuilder';
import ResultsView from '../components/ResultsView/ResultsView';
import LogsView from '../components/LogsView/LogsView';
import AssistedManualView from '../components/AssistedManualView/AssistedManualView';
import SegmentStackBuilder from '../components/SegmentStackBuilder/SegmentStackBuilder';
import AutoConvertResults from '../components/AutoConvertResults/AutoConvertResults';
import type { JobResponse, FileInfo } from '../services/types';
import './JobPage.css';

type Tab = 'pdf' | 'assisted' | 'auto' | 'profile' | 'results' | 'logs';

function JobPage() {
  const { id } = useParams<{ id: string }>();
  const [job, setJob] = useState<JobResponse | null>(null);
  const [files, setFiles] = useState<FileInfo[]>([]);
  const [selectedPdf, setSelectedPdf] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState<Tab>('pdf');
  const [assistedSubTab, setAssistedSubTab] = useState<'view' | 'dimensions'>('view');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!id) return;

    const loadJob = async () => {
      try {
        setLoading(true);
        const [jobData, filesData] = await Promise.all([
          api.getJob(id),
          api.getJobFiles(id),
        ]);

        setJob(jobData);
        setFiles(filesData.files);

        // Filter PDF files and set first one as selected
        const pdfFiles = filesData.files.filter((f) =>
          f.name.toLowerCase().endsWith('.pdf')
        );
        if (pdfFiles.length > 0) {
          setSelectedPdf(pdfFiles[0].path);
        }
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Failed to load job');
      } finally {
        setLoading(false);
      }
    };

    loadJob();
  }, [id]);

  const pdfFiles = files.filter((f) => f.name.toLowerCase().endsWith('.pdf'));

  if (loading) {
    return (
      <div className="job-page">
        <div className="loading">Loading job...</div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="job-page">
        <div className="error">Error: {error}</div>
      </div>
    );
  }

  if (!job) {
    return (
      <div className="job-page">
        <div className="error">Job not found</div>
      </div>
    );
  }

  return (
    <div className="job-page">
      <div className="job-header">
        <h1>{job.name || `Job: ${job.job_id.substring(0, 8)}`}</h1>
        <div className="job-status">Status: {job.status}</div>
      </div>

      <div className="job-tabs">
        <button
          className={activeTab === 'pdf' ? 'active' : ''}
          onClick={() => setActiveTab('pdf')}
        >
          PDF Viewer
        </button>
        {job.mode === 'assisted_manual' && (
          <button
            className={activeTab === 'assisted' ? 'active' : ''}
            onClick={() => setActiveTab('assisted')}
          >
            Assisted Manual
          </button>
        )}
        {job.mode === 'auto_convert' && (
          <button
            className={activeTab === 'auto' ? 'active' : ''}
            onClick={() => setActiveTab('auto')}
          >
            Auto Convert
          </button>
        )}
        <button
          className={activeTab === 'profile' ? 'active' : ''}
          onClick={() => setActiveTab('profile')}
        >
          Profile Builder
        </button>
        <button
          className={activeTab === 'results' ? 'active' : ''}
          onClick={() => setActiveTab('results')}
        >
          Results
        </button>
        <button
          className={activeTab === 'logs' ? 'active' : ''}
          onClick={() => setActiveTab('logs')}
        >
          Logs
        </button>
      </div>

      <div className="job-content-area">
        {activeTab === 'pdf' && (
          <div className="pdf-tab-content">
            {pdfFiles.length > 1 && (
              <div className="pdf-selector-pane">
                <label htmlFor="pdf-selector">Select PDF:</label>
                <select
                  id="pdf-selector"
                  value={selectedPdf || ''}
                  onChange={(e) => setSelectedPdf(e.target.value)}
                  className="pdf-selector"
                >
                  {pdfFiles.map((file) => (
                    <option key={file.path} value={file.path}>
                      {file.name}
                    </option>
                  ))}
                </select>
              </div>
            )}

            <div className="pdf-viewer-pane">
              {selectedPdf && id ? (
                <PDFViewer url={api.getPdfUrl(id, selectedPdf)} />
              ) : pdfFiles.length === 0 ? (
                <div className="no-pdf">No PDF files found in this job.</div>
              ) : (
                <div className="no-pdf">Please select a PDF to view.</div>
              )}
            </div>
          </div>
        )}

        {activeTab === 'assisted' && job.mode === 'assisted_manual' && id && (
          <div className="assisted-tab-content">
            <div className="assisted-tabs">
              <button
                className={assistedSubTab === 'view' ? 'active' : ''}
                onClick={() => setAssistedSubTab('view')}
              >
                View Selection
              </button>
              <button
                className={assistedSubTab === 'dimensions' ? 'active' : ''}
                onClick={() => setAssistedSubTab('dimensions')}
              >
                Dimension Entry
              </button>
            </div>
            {assistedSubTab === 'view' && (
              <AssistedManualView
                jobId={id}
                onViewSelected={(page, viewIndex) => {
                  setSelectedView({ page, viewIndex });
                  console.log('View selected:', { page, viewIndex });
                }}
              />
            )}
            {assistedSubTab === 'dimensions' && (
              <SegmentStackBuilder
                jobId={id}
                onSuccess={(result) => {
                  console.log('Stack processed:', result);
                  // Optionally switch to results tab
                }}
              />
            )}
          </div>
        )}

        {activeTab === 'auto' && job.mode === 'auto_convert' && id && (
          <div className="auto-tab-content">
            <AutoConvertResults 
              jobId={id}
              onSwitchToManual={() => {
                // Note: This would require changing job mode, which is not implemented in this step
                // For now, just show a message or redirect to create new job in assisted mode
                alert('Please create a new job in Assisted Manual mode for manual entry.');
              }}
            />
          </div>
        )}

        {activeTab === 'profile' && (
          <div className="profile-tab-content">
            <ProfileBuilder jobId={id!} />
          </div>
        )}

        {activeTab === 'results' && (
          <div className="results-tab-content">
            <ResultsView 
              jobId={id!} 
              job={job}
              onSwitchMode={async (targetMode) => {
                if (!id) return;
                
                try {
                  // Update job mode
                  await api.setJobMode(id, targetMode);
                  
                  // Reload job to get updated mode
                  const updatedJob = await api.getJob(id);
                  setJob(updatedJob);
                  
                  // If switching from auto to assisted, prefill form
                  if (targetMode === 'assisted_manual' && job?.mode === 'auto_convert') {
                    // Load inferred stack and prefill
                    try {
                      const files = await api.getJobFiles(id);
                      const inferredStackFile = files.files.find(f => f.path === 'outputs/inferred_stack.json');
                      if (inferredStackFile) {
                        const response = await fetch(api.getPdfUrl(id, inferredStackFile.path));
                        if (response.ok) {
                          const inferredData = await response.json();
                          // Switch to assisted tab and prefill will be handled by SegmentStackBuilder
                          setActiveTab('assisted');
                          setAssistedSubTab('dimensions');
                          // Store inferred data for prefilling (could use context or state)
                          sessionStorage.setItem(`prefill_${id}`, JSON.stringify(inferredData));
                        }
                      }
                    } catch (err) {
                      console.error('Failed to load inferred stack for prefilling:', err);
                    }
                  } else if (targetMode === 'auto_convert') {
                    // Switch to auto tab
                    setActiveTab('auto');
                  }
                } catch (err) {
                  alert(`Failed to switch mode: ${err instanceof Error ? err.message : 'Unknown error'}`);
                }
              }}
            />
          </div>
        )}

        {activeTab === 'logs' && (
          <div className="logs-tab-content">
            <LogsView jobId={id!} />
          </div>
        )}
      </div>
    </div>
  );
}

export default JobPage;

