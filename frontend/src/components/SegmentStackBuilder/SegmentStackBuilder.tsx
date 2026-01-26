import { useState, useEffect } from 'react';
import { api } from '../../services/api';
import './SegmentStackBuilder.css';

interface Segment {
  z_start: number;
  z_end: number;
  od_diameter: number;
  id_diameter: number;
}

interface SegmentErrors {
  z_start?: string;
  z_end?: string;
  od_diameter?: string;
  id_diameter?: string;
  wall_thickness?: string;
}

interface SegmentStackBuilderProps {
  jobId: string;
  onSuccess?: (result: any) => void;
}

function SegmentStackBuilder({ jobId, onSuccess }: SegmentStackBuilderProps) {
  const [units, setUnits] = useState('in');
  const [segments, setSegments] = useState<Segment[]>([
    { z_start: 0, z_end: 0, od_diameter: 0, id_diameter: 0 }
  ]);
  const [notes, setNotes] = useState('');
  const [errors, setErrors] = useState<{ [index: number]: SegmentErrors }>({});
  const [formErrors, setFormErrors] = useState<string[]>([]);
  const [warnings, setWarnings] = useState<string[]>([]);
  const [submitting, setSubmitting] = useState(false);
  const [result, setResult] = useState<any>(null);
  const [generatingStep, setGeneratingStep] = useState(false);
  const [stepResult, setStepResult] = useState<any>(null);

  // Check for prefilled data from mode switch
  useEffect(() => {
    const prefillKey = `prefill_${jobId}`;
    const prefillData = sessionStorage.getItem(prefillKey);
    if (prefillData) {
      try {
        const inferredData = JSON.parse(prefillData);
        if (inferredData.segments && inferredData.segments.length > 0) {
          // Prefill segments
          setSegments(inferredData.segments.map((seg: any) => ({
            z_start: seg.z_start,
            z_end: seg.z_end,
            od_diameter: seg.od_diameter,
            id_diameter: seg.id_diameter || 0
          })));
          setUnits(inferredData.units || 'in');
          // Clear prefill data after using it
          sessionStorage.removeItem(prefillKey);
        }
      } catch (err) {
        console.error('Failed to parse prefill data:', err);
      }
    }
  }, [jobId]);

  const calculateWallThickness = (od: number, id: number): number => {
    return (od - id) / 2.0;
  };

  const validateSegment = (segment: Segment, _index: number): SegmentErrors => {
    const segErrors: SegmentErrors = {};

    if (segment.z_start >= segment.z_end) {
      segErrors.z_end = 'z_end must be greater than z_start';
    }

    if (segment.od_diameter <= 0) {
      segErrors.od_diameter = 'OD must be greater than 0';
    }

    if (segment.id_diameter < 0) {
      segErrors.id_diameter = 'ID cannot be negative';
    }

    if (segment.id_diameter > segment.od_diameter) {
      segErrors.id_diameter = 'ID cannot be greater than OD';
    }

    const wallThickness = calculateWallThickness(segment.od_diameter, segment.id_diameter);
    if (wallThickness < 0.001) {
      segErrors.wall_thickness = `Wall thickness (${wallThickness.toFixed(6)}) is very thin (< 0.001 ${units})`;
    }

    return segErrors;
  };

  const validateAllSegments = (): boolean => {
    const newErrors: { [index: number]: SegmentErrors } = {};
    let hasErrors = false;

    segments.forEach((segment, index) => {
      const segErrors = validateSegment(segment, index);
      if (Object.keys(segErrors).length > 0) {
        newErrors[index] = segErrors;
        hasErrors = true;
      }
    });

    // Check z ranges are contiguous
    const sortedSegments = [...segments].sort((a, b) => a.z_start - b.z_start);
    for (let i = 0; i < sortedSegments.length - 1; i++) {
      const current = sortedSegments[i];
      const next = sortedSegments[i + 1];
      const tolerance = 0.000001;

      if (Math.abs(current.z_end - next.z_start) > tolerance) {
        hasErrors = true;
        if (!newErrors[i]) newErrors[i] = {};
        newErrors[i].z_end = `Segment must connect to next segment (ends at ${current.z_end}, next starts at ${next.z_start})`;
      }
    }

    setErrors(newErrors);
    return !hasErrors;
  };

  const handleSegmentChange = (index: number, field: keyof Segment, value: number) => {
    const newSegments = [...segments];
    newSegments[index] = { ...newSegments[index], [field]: value };
    setSegments(newSegments);

    // Clear errors for this segment
    const newErrors = { ...errors };
    if (newErrors[index]) {
      delete newErrors[index];
      setErrors(newErrors);
    }
  };

  const handleAddSegment = () => {
    const lastSegment = segments[segments.length - 1];
    const newSegment: Segment = {
      z_start: lastSegment.z_end,
      z_end: lastSegment.z_end + 1,
      od_diameter: lastSegment.od_diameter,
      id_diameter: lastSegment.id_diameter
    };
    setSegments([...segments, newSegment]);
  };

  const handleRemoveSegment = (index: number) => {
    if (segments.length > 1) {
      const newSegments = segments.filter((_, i) => i !== index);
      setSegments(newSegments);
      
      // Clear errors for removed segment
      const newErrors = { ...errors };
      delete newErrors[index];
      // Reindex errors
      const reindexed: { [index: number]: SegmentErrors } = {};
      Object.keys(newErrors).forEach(key => {
        const oldIndex = parseInt(key);
        if (oldIndex > index) {
          reindexed[oldIndex - 1] = newErrors[oldIndex];
        } else if (oldIndex < index) {
          reindexed[oldIndex] = newErrors[oldIndex];
        }
      });
      setErrors(reindexed);
    }
  };

  const handleGenerateStep = async () => {
    setGeneratingStep(true);
    setStepResult(null);

    try {
      const response = await api.generateStepFromStack(jobId);
      setStepResult(response);

      if (response.status === 'DONE' && onSuccess) {
        // Optionally refresh results
        onSuccess(response);
      }
    } catch (err) {
      setStepResult({
        status: 'FAILED',
        error: err instanceof Error ? err.message : 'Failed to generate STEP'
      });
    } finally {
      setGeneratingStep(false);
    }
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();

    if (!validateAllSegments()) {
      setFormErrors(['Please fix validation errors before submitting']);
      return;
    }

    setSubmitting(true);
    setFormErrors([]);
    setWarnings([]);
    setResult(null);

    try {
      const response = await api.processTurnedStack(jobId, {
        units,
        segments,
        notes: notes || undefined
      });

      setResult(response);
      setWarnings(response.warnings || []);

      if (response.status === 'FAILED') {
        const errorMessages = (response as any).errors || [];
        setFormErrors(errorMessages);
      } else if (response.status === 'DONE' && onSuccess) {
        onSuccess(response);
      }
    } catch (err) {
      setFormErrors([err instanceof Error ? err.message : 'Failed to process stack']);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="segment-stack-builder">
      <h2>Segment Stack Builder</h2>
      <p className="builder-description">
        Enter dimensions for each segment of the turned part. Segments must be contiguous along the Z-axis.
      </p>

      <form onSubmit={handleSubmit} className="stack-form">
        <div className="form-section">
          <label htmlFor="units">Units</label>
          <select
            id="units"
            value={units}
            onChange={(e) => setUnits(e.target.value)}
            className="units-select"
          >
            <option value="in">Inches (in)</option>
            <option value="mm">Millimeters (mm)</option>
          </select>
        </div>

        <div className="segments-section">
          <div className="segments-header">
            <h3>Segments</h3>
            <button
              type="button"
              onClick={handleAddSegment}
              className="add-segment-btn"
            >
              + Add Segment
            </button>
          </div>

          {segments.map((segment, index) => {
            const wallThickness = calculateWallThickness(segment.od_diameter, segment.id_diameter);
            const segErrors = errors[index] || {};

            return (
              <div key={index} className="segment-card">
                <div className="segment-header">
                  <h4>Segment {index + 1}</h4>
                  {segments.length > 1 && (
                    <button
                      type="button"
                      onClick={() => handleRemoveSegment(index)}
                      className="remove-segment-btn"
                    >
                      Remove
                    </button>
                  )}
                </div>

                <div className="segment-fields">
                  <div className="field-group">
                    <label htmlFor={`z_start_${index}`}>
                      Z Start ({units})
                    </label>
                    <input
                      type="number"
                      id={`z_start_${index}`}
                      step="0.001"
                      value={segment.z_start}
                      onChange={(e) => handleSegmentChange(index, 'z_start', parseFloat(e.target.value) || 0)}
                      className={segErrors.z_start ? 'error' : ''}
                    />
                    {segErrors.z_start && (
                      <span className="field-error">{segErrors.z_start}</span>
                    )}
                  </div>

                  <div className="field-group">
                    <label htmlFor={`z_end_${index}`}>
                      Z End ({units})
                    </label>
                    <input
                      type="number"
                      id={`z_end_${index}`}
                      step="0.001"
                      value={segment.z_end}
                      onChange={(e) => handleSegmentChange(index, 'z_end', parseFloat(e.target.value) || 0)}
                      className={segErrors.z_end ? 'error' : ''}
                    />
                    {segErrors.z_end && (
                      <span className="field-error">{segErrors.z_end}</span>
                    )}
                  </div>

                  <div className="field-group">
                    <label htmlFor={`od_${index}`}>
                      OD Diameter ({units})
                    </label>
                    <input
                      type="number"
                      id={`od_${index}`}
                      step="0.001"
                      min="0"
                      value={segment.od_diameter}
                      onChange={(e) => handleSegmentChange(index, 'od_diameter', parseFloat(e.target.value) || 0)}
                      className={segErrors.od_diameter ? 'error' : ''}
                    />
                    {segErrors.od_diameter && (
                      <span className="field-error">{segErrors.od_diameter}</span>
                    )}
                  </div>

                  <div className="field-group">
                    <label htmlFor={`id_${index}`}>
                      ID Diameter ({units})
                    </label>
                    <input
                      type="number"
                      id={`id_${index}`}
                      step="0.001"
                      min="0"
                      value={segment.id_diameter}
                      onChange={(e) => handleSegmentChange(index, 'id_diameter', parseFloat(e.target.value) || 0)}
                      className={segErrors.id_diameter ? 'error' : ''}
                    />
                    {segErrors.id_diameter && (
                      <span className="field-error">{segErrors.id_diameter}</span>
                    )}
                  </div>

                  <div className="field-group computed">
                    <label>Wall Thickness ({units})</label>
                    <div className="computed-value">
                      {wallThickness.toFixed(6)}
                      {segErrors.wall_thickness && (
                        <span className="field-warning">{segErrors.wall_thickness}</span>
                      )}
                    </div>
                  </div>
                </div>
              </div>
            );
          })}
        </div>

        <div className="form-section">
          <label htmlFor="notes">Notes (Optional)</label>
          <textarea
            id="notes"
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
            rows={3}
            placeholder="Optional notes about this part..."
          />
        </div>

        {formErrors.length > 0 && (
          <div className="form-errors">
            {formErrors.map((error, i) => (
              <div key={i} className="error-message">{error}</div>
            ))}
          </div>
        )}

        {warnings.length > 0 && (
          <div className="form-warnings">
            <strong>Warnings:</strong>
            <ul>
              {warnings.map((warning, i) => (
                <li key={i}>{warning}</li>
              ))}
            </ul>
          </div>
        )}

        <div className="form-actions">
          <button type="submit" disabled={submitting} className="submit-btn">
            {submitting ? 'Processing...' : 'Process Stack'}
          </button>
        </div>
      </form>

      {result && result.status === 'DONE' && (
        <div className="result-summary">
          <h3>Results</h3>
          <div className="totals-grid">
            <div className="total-card">
              <div className="total-label">Total Volume</div>
              <div className="total-value">
                {result.totals.volume_in3.toFixed(6)} {units}³
              </div>
            </div>
            <div className="total-card">
              <div className="total-label">Total Surface Area</div>
              <div className="total-value">
                {result.totals.total_surface_area_in2.toFixed(6)} {units}²
              </div>
            </div>
            <div className="total-card">
              <div className="total-label">OD Surface Area</div>
              <div className="total-value">
                {result.totals.od_area_in2.toFixed(6)} {units}²
              </div>
            </div>
            <div className="total-card">
              <div className="total-label">ID Surface Area</div>
              <div className="total-value">
                {result.totals.id_area_in2.toFixed(6)} {units}²
              </div>
            </div>
          </div>

          <div className="step-generation-section">
            <h4>3D Model Generation</h4>
            <p className="step-description">
              Generate a STEP file (3D CAD model) from the stack. This will create a solid model
              and regenerate the part summary with feature counts.
            </p>
            {stepResult && stepResult.status === 'DONE' && (
              <div className="step-success">
                <p>✓ STEP file generated successfully!</p>
                <div className="step-downloads">
                  {stepResult.outputs.includes('model.step') && (
                    <a
                      href={api.getPdfUrl(jobId, 'outputs/model.step')}
                      download
                      className="download-link"
                    >
                      Download STEP
                    </a>
                  )}
                  {stepResult.outputs.includes('model.glb') && (
                    <a
                      href={api.getPdfUrl(jobId, 'outputs/model.glb')}
                      download
                      className="download-link"
                    >
                      Download GLB
                    </a>
                  )}
                </div>
              </div>
            )}
            {stepResult && stepResult.status === 'FAILED' && (
              <div className="step-error">
                <p>Failed to generate STEP: {stepResult.error}</p>
              </div>
            )}
            <button
              type="button"
              onClick={handleGenerateStep}
              disabled={generatingStep || result.status !== 'DONE'}
              className="generate-step-btn"
            >
              {generatingStep ? 'Generating STEP...' : 'Generate STEP (3D model)'}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

export default SegmentStackBuilder;

