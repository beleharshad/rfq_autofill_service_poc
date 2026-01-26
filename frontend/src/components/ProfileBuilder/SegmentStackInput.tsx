import { useState, useEffect } from 'react';
import { api } from '../../services/api';
import './SegmentStackInput.css';

interface SegmentStackInputProps {
  jobId: string;
}

interface Segment {
  z_start: number;
  z_end: number;
  od_diameter: number;
  id_diameter: number;
}

// Sample preset data: 2 OD steps + 2 ID steps
const SAMPLE_SEGMENTS: Segment[] = [
  { z_start: 0.0, z_end: 1.5, od_diameter: 2.0, id_diameter: 0.5 },  // Segment 1: OD1, ID1
  { z_start: 1.5, z_end: 3.0, od_diameter: 2.5, id_diameter: 0.5 },  // Segment 2: OD step up, ID same
  { z_start: 3.0, z_end: 4.5, od_diameter: 2.5, id_diameter: 1.0 }, // Segment 3: OD same, ID step up
  { z_start: 4.5, z_end: 6.0, od_diameter: 2.0, id_diameter: 1.0 },  // Segment 4: OD step down, ID same
];

function SegmentStackInput({ jobId }: SegmentStackInputProps) {
  const [segments, setSegments] = useState<Segment[]>([]);
  const [units, setUnits] = useState('in');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState(false);

  // Load existing stack input on mount
  useEffect(() => {
    const loadStackInput = async () => {
      try {
        const data = await api.getStackInput(jobId);
        setSegments(data.segments);
        setUnits(data.units);
      } catch (err) {
        // Not found is OK - means no input saved yet
        if (err instanceof Error && !err.message.includes('404')) {
          setError(err.message);
        }
      }
    };
    loadStackInput();
  }, [jobId]);

  const loadSample = () => {
    setSegments(SAMPLE_SEGMENTS.map((seg) => ({ ...seg })));
    setUnits('in');
    setError(null);
  };

  const addSegment = () => {
    const lastSegment = segments[segments.length - 1];
    const newSegment: Segment = {
      z_start: lastSegment ? lastSegment.z_end : 0,
      z_end: lastSegment ? lastSegment.z_end + 1 : 1,
      od_diameter: 1.0,
      id_diameter: 0,
    };
    setSegments([...segments, newSegment]);
  };

  const removeSegment = (index: number) => {
    setSegments(segments.filter((_, i) => i !== index));
  };

  const updateSegment = (index: number, field: keyof Segment, value: number) => {
    const updated = [...segments];
    updated[index] = { ...updated[index], [field]: value };
    setSegments(updated);
  };

  const computeWallThickness = (od: number, id: number): number => {
    if (od > 0 && id > 0) {
      return (od - id) / 2.0;
    } else if (od > 0) {
      return od / 2.0;
    }
    return 0;
  };

  const computeVolume = (seg: Segment): number => {
    const ro = seg.od_diameter / 2.0;
    const ri = seg.id_diameter / 2.0;
    const L = seg.z_end - seg.z_start;
    return Math.PI * L * (ro * ro - ri * ri);
  };

  const computeODArea = (seg: Segment): number => {
    const L = seg.z_end - seg.z_start;
    return Math.PI * seg.od_diameter * L;
  };

  const computeIDArea = (seg: Segment): number => {
    if (seg.id_diameter <= 0) return 0;
    const L = seg.z_end - seg.z_start;
    return Math.PI * seg.id_diameter * L;
  };

  const handleSave = async () => {
    // Validate segments
    for (let i = 0; i < segments.length; i++) {
      const seg = segments[i];
      if (seg.z_start >= seg.z_end) {
        setError(`Segment ${i + 1}: z_start must be less than z_end`);
        return;
      }
      if (seg.od_diameter <= 0) {
        setError(`Segment ${i + 1}: OD diameter must be greater than 0`);
        return;
      }
      if (seg.id_diameter < 0) {
        setError(`Segment ${i + 1}: ID diameter cannot be negative`);
        return;
      }
      if (seg.id_diameter > seg.od_diameter) {
        setError(`Segment ${i + 1}: ID diameter cannot be greater than OD diameter`);
        return;
      }
    }

    // Check continuity
    for (let i = 0; i < segments.length - 1; i++) {
      if (Math.abs(segments[i].z_end - segments[i + 1].z_start) > 0.001) {
        setError(`Segments ${i + 1} and ${i + 2} are not continuous`);
        return;
      }
    }

    setSaving(true);
    setError(null);
    setSuccess(false);

    try {
      await api.saveStackInput(jobId, {
        units,
        segments: segments.map((seg) => ({
          z_start: seg.z_start,
          z_end: seg.z_end,
          od_diameter: seg.od_diameter,
          id_diameter: seg.id_diameter,
        })),
      });
      setSuccess(true);
      setTimeout(() => setSuccess(false), 3000);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to save stack input');
    } finally {
      setSaving(false);
    }
  };

  const totalVolume = segments.reduce((sum, seg) => sum + computeVolume(seg), 0);
  const totalODArea = segments.reduce((sum, seg) => sum + computeODArea(seg), 0);
  const totalIDArea = segments.reduce((sum, seg) => sum + computeIDArea(seg), 0);

  return (
    <div className="segment-stack-input">
      <div className="stack-header">
        <h3>Segment Stack Input</h3>
        <div className="units-selector">
          <label>Units:</label>
          <select value={units} onChange={(e) => setUnits(e.target.value)}>
            <option value="in">inches</option>
            <option value="mm">millimeters</option>
          </select>
        </div>
      </div>

      <div className="stack-actions">
        <button onClick={loadSample} className="load-sample-btn">
          Load Sample
        </button>
        <button onClick={addSegment} className="add-segment-btn">
          + Add Segment
        </button>
        {segments.length > 0 && (
          <button onClick={handleSave} disabled={saving} className="save-btn">
            {saving ? 'Saving...' : 'Save Stack Input'}
          </button>
        )}
      </div>

      {error && <div className="error-message">{error}</div>}
      {success && <div className="success-message">Stack input saved successfully!</div>}

      {segments.length === 0 ? (
        <div className="no-segments">
          <p>No segments added yet. Click "Add Segment" to start.</p>
        </div>
      ) : (
        <>
          <div className="segments-list">
            {segments.map((segment, index) => (
              <div key={index} className="segment-card">
                <div className="segment-header">
                  <h4>Segment {index + 1}</h4>
                  <button
                    onClick={() => removeSegment(index)}
                    className="remove-btn"
                    title="Remove segment"
                  >
                    ×
                  </button>
                </div>
                <div className="segment-fields">
                  <div className="field-group">
                    <label>Z Start ({units})</label>
                    <input
                      type="number"
                      step="0.001"
                      value={segment.z_start}
                      onChange={(e) =>
                        updateSegment(index, 'z_start', parseFloat(e.target.value) || 0)
                      }
                    />
                  </div>
                  <div className="field-group">
                    <label>Z End ({units})</label>
                    <input
                      type="number"
                      step="0.001"
                      value={segment.z_end}
                      onChange={(e) =>
                        updateSegment(index, 'z_end', parseFloat(e.target.value) || 0)
                      }
                    />
                  </div>
                  <div className="field-group">
                    <label>OD Diameter ({units})</label>
                    <input
                      type="number"
                      step="0.001"
                      value={segment.od_diameter}
                      onChange={(e) =>
                        updateSegment(index, 'od_diameter', parseFloat(e.target.value) || 0)
                      }
                    />
                  </div>
                  <div className="field-group">
                    <label>ID Diameter ({units})</label>
                    <input
                      type="number"
                      step="0.001"
                      value={segment.id_diameter}
                      onChange={(e) =>
                        updateSegment(index, 'id_diameter', parseFloat(e.target.value) || 0)
                      }
                    />
                  </div>
                </div>
                <div className="segment-metrics">
                  <div className="metric">
                    <span className="metric-label">Length:</span>
                    <span className="metric-value">
                      {(segment.z_end - segment.z_start).toFixed(3)} {units}
                    </span>
                  </div>
                  <div className="metric">
                    <span className="metric-label">Wall Thickness:</span>
                    <span className="metric-value">
                      {computeWallThickness(segment.od_diameter, segment.id_diameter).toFixed(3)}{' '}
                      {units}
                    </span>
                  </div>
                  <div className="metric">
                    <span className="metric-label">Volume:</span>
                    <span className="metric-value">
                      {computeVolume(segment).toFixed(6)} {units}³
                    </span>
                  </div>
                  <div className="metric">
                    <span className="metric-label">OD Area:</span>
                    <span className="metric-value">
                      {computeODArea(segment).toFixed(6)} {units}²
                    </span>
                  </div>
                  <div className="metric">
                    <span className="metric-label">ID Area:</span>
                    <span className="metric-value">
                      {computeIDArea(segment).toFixed(6)} {units}²
                    </span>
                  </div>
                </div>
              </div>
            ))}
          </div>

          <div className="stack-totals">
            <h4>Stack Totals</h4>
            <div className="totals-grid">
              <div className="total-item">
                <span className="total-label">Total Volume:</span>
                <span className="total-value">
                  {totalVolume.toFixed(6)} {units}³
                </span>
              </div>
              <div className="total-item">
                <span className="total-label">Total OD Area:</span>
                <span className="total-value">
                  {totalODArea.toFixed(6)} {units}²
                </span>
              </div>
              <div className="total-item">
                <span className="total-label">Total ID Area:</span>
                <span className="total-value">
                  {totalIDArea.toFixed(6)} {units}²
                </span>
              </div>
            </div>
          </div>
        </>
      )}
    </div>
  );
}

export default SegmentStackInput;

