import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { api } from '../services/api';
import './NewJobPage.css';

type JobMode = 'assisted_manual' | 'auto_convert';

function NewJobPage() {
  const navigate = useNavigate();
  const [mode, setMode] = useState<JobMode>('auto_convert');
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [files, setFiles] = useState<File[]>([]);
  const [uploading, setUploading] = useState(false);
  const [loadingSample, setLoadingSample] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    if (e.target.files) {
      const fileArray = Array.from(e.target.files);
      // Validate file types
      const validFiles = fileArray.filter(
        (file) =>
          file.type === 'application/pdf' ||
          file.type === 'application/zip' ||
          file.name.toLowerCase().endsWith('.pdf') ||
          file.name.toLowerCase().endsWith('.zip')
      );
      setFiles(validFiles);
      if (validFiles.length !== fileArray.length) {
        setError('Some files were skipped. Only PDF and ZIP files are allowed.');
      } else {
        setError(null);
      }
    }
  };

  const handleSubmit = async (e: React.FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    
    if (files.length === 0) {
      setError('Please select at least one PDF or ZIP file.');
      return;
    }

    setUploading(true);
    setError(null);

    try {
      const job = await api.createJob(
        files,
        name || undefined,
        description || undefined,
        mode
      );
      
      navigate(`/jobs/${job.job_id}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to create job');
      setUploading(false);
    }
  };

  const handleUseSamplePdf = async () => {
    setLoadingSample(true);
    setError(null);

    try {
      const response = await fetch('/sample-demo.pdf');
      if (!response.ok) {
        throw new Error('Failed to load sample PDF.');
      }

      const blob = await response.blob();
      const sampleFile = new File([blob], 'sample-demo.pdf', { type: 'application/pdf' });
      setFiles([sampleFile]);

      if (!name) {
        setName('Sample Demo Job');
      }
      if (!description) {
        setDescription('Demo upload using the built-in sample PDF.');
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load sample PDF');
    } finally {
      setLoadingSample(false);
    }
  };

  return (
    <div className="new-job-page">
      <h1>Create New Job</h1>
      <form onSubmit={handleSubmit} className="new-job-form">
        <div className="form-group">
          <label htmlFor="job-mode">Processing Mode *</label>
          <select
            id="job-mode"
            value={mode}
            onChange={(e) => setMode(e.target.value as JobMode)}
            className="mode-selector"
          >
            <option value="assisted_manual">Assisted Manual (default)</option>
            <option value="auto_convert">Auto Convert (experimental)</option>
          </select>
          <p className="mode-description">
            {mode === 'assisted_manual'
              ? 'You will manually enter dimensions while viewing the PDF.'
              : 'The system will attempt to automatically detect and extract dimensions from the PDF.'}
          </p>
        </div>
        <div className="form-group">
          <label htmlFor="job-files">Upload PDF or ZIP Files *</label>
          <div className="sample-upload-row">
            <button
              type="button"
              className="sample-file-button"
              onClick={handleUseSamplePdf}
              disabled={loadingSample || uploading}
            >
              {loadingSample ? 'Loading sample…' : 'Use Free Sample PDF'}
            </button>
            <span className="sample-file-note">Try the app instantly with a built-in demo file.</span>
          </div>
          <input
            type="file"
            id="job-files"
            name="files"
            multiple
            accept=".pdf,.zip,application/pdf,application/zip"
            onChange={handleFileChange}
            className="file-input"
          />
          {files.length > 0 && (
            <div className="file-list">
              <p>Selected files ({files.length}):</p>
              <ul>
                {files.map((file, index) => (
                  <li key={index}>{file.name} ({(file.size / 1024 / 1024).toFixed(2)} MB)</li>
                ))}
              </ul>
            </div>
          )}
        </div>
        <div className="form-group">
          <label htmlFor="job-name">Job Name (Optional)</label>
          <input
            type="text"
            id="job-name"
            name="name"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="e.g., Part-001"
          />
        </div>
        <div className="form-group">
          <label htmlFor="job-description">Description (Optional)</label>
          <textarea
            id="job-description"
            name="description"
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            rows={3}
            placeholder="Optional description..."
          />
        </div>
        {error && <div className="error-message">{error}</div>}
        <div className="form-actions">
          <button type="submit" disabled={uploading}>
            {uploading ? 'Uploading...' : 'Create Job'}
          </button>
        </div>
      </form>
    </div>
  );
}

export default NewJobPage;

