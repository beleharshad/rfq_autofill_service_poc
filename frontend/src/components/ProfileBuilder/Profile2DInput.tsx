import { useState, useEffect } from 'react';
import { api } from '../../services/api';
import './Profile2DInput.css';

interface Profile2DInputProps {
  jobId: string;
}

interface Point2D {
  x: number;
  y: number;
}

interface LineSegment {
  type: 'line';
  start: Point2D;
  end: Point2D;
}

// Sample preset: 2 OD steps + 2 ID steps (closed loop profile)
// Matches Segment Stack sample geometry:
// - Segment 1: z=0-1.5, OD=2.0 (r=1.0), ID=0.5 (r=0.25)
// - Segment 2: z=1.5-3.0, OD=2.5 (r=1.25), ID=0.5 (r=0.25) - OD step up
// - Segment 3: z=3.0-4.5, OD=2.5 (r=1.25), ID=1.0 (r=0.5) - ID step up
// - Segment 4: z=4.5-6.0, OD=2.0 (r=1.0), ID=1.0 (r=0.5) - OD step down
// Profile structure (closed loop, counterclockwise):
// - Start at inner-left bottom: (ID_radius=0.25, y=0)
// - ID: 0.25 from y=0 to y=3.0, then step to 0.5 at y=3.0, then 0.5 to y=6.0
// - Right face: vertical from (ID=0.5, y=6.0) to (OD=1.0, y=6.0)
// - OD: 1.0 from y=6.0 to y=4.5, then step to 1.25 at y=4.5, then 1.25 to y=1.5, then step to 1.0 at y=1.5, then 1.0 to y=0
// - Left face: vertical from (OD=1.0, y=0) back to (ID=0.25, y=0)
const SAMPLE_PROFILE2D: LineSegment[] = [
  // ID region - Segment 1: ID (0.25) from y=0 to y=1.5
  { type: 'line', start: { x: 0.25, y: 0.0 }, end: { x: 0.25, y: 1.5 } },
  // ID region - Segment 2: ID (0.25) continues from y=1.5 to y=3.0
  { type: 'line', start: { x: 0.25, y: 1.5 }, end: { x: 0.25, y: 3.0 } },
  // ID step - Segment 3: horizontal from ID (0.25) to ID (0.5) at y=3.0
  { type: 'line', start: { x: 0.25, y: 3.0 }, end: { x: 0.5, y: 3.0 } },
  // ID region - Segment 4: ID (0.5) from y=3.0 to y=4.5
  { type: 'line', start: { x: 0.5, y: 3.0 }, end: { x: 0.5, y: 4.5 } },
  // ID region - Segment 5: ID (0.5) continues from y=4.5 to y=6.0
  { type: 'line', start: { x: 0.5, y: 4.5 }, end: { x: 0.5, y: 6.0 } },
  // Right face - Segment 6: vertical from (ID=0.5, y=6.0) to (OD=1.0, y=6.0)
  { type: 'line', start: { x: 0.5, y: 6.0 }, end: { x: 1.0, y: 6.0 } },
  // OD region - Segment 7: OD (1.0) from y=6.0 to y=4.5
  { type: 'line', start: { x: 1.0, y: 6.0 }, end: { x: 1.0, y: 4.5 } },
  // OD step - Segment 8: horizontal from OD (1.0) to OD (1.25) at y=4.5
  { type: 'line', start: { x: 1.0, y: 4.5 }, end: { x: 1.25, y: 4.5 } },
  // OD region - Segment 9: OD (1.25) from y=4.5 to y=3.0
  { type: 'line', start: { x: 1.25, y: 4.5 }, end: { x: 1.25, y: 3.0 } },
  // OD region - Segment 10: OD (1.25) continues from y=3.0 to y=1.5
  { type: 'line', start: { x: 1.25, y: 3.0 }, end: { x: 1.25, y: 1.5 } },
  // OD step - Segment 11: horizontal from OD (1.25) to OD (1.0) at y=1.5
  { type: 'line', start: { x: 1.25, y: 1.5 }, end: { x: 1.0, y: 1.5 } },
  // OD region - Segment 12: OD (1.0) from y=1.5 to y=0
  { type: 'line', start: { x: 1.0, y: 1.5 }, end: { x: 1.0, y: 0.0 } },
  // Left face - Segment 13: vertical from (OD=1.0, y=0) back to (ID=0.25, y=0)
  { type: 'line', start: { x: 1.0, y: 0.0 }, end: { x: 0.25, y: 0.0 } },
];

const SAMPLE_AXIS_POINT: Point2D = { x: 0.0, y: 0.0 };

function Profile2DInput({ jobId }: Profile2DInputProps) {
  const [primitives, setPrimitives] = useState<LineSegment[]>([]);
  const [axisPoint, setAxisPoint] = useState<Point2D>({ x: 0, y: 0 });
  const [processing, setProcessing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState(false);
  const [hasStepFile, setHasStepFile] = useState(false);

  // Check if STEP file exists
  useEffect(() => {
    const checkStepFile = async () => {
      try {
        const files = await api.getJobFiles(jobId);
        const stepFile = files.files.find((f) => f.name === 'model.step');
        setHasStepFile(!!stepFile);
      } catch (err) {
        // Ignore errors
      }
    };
    checkStepFile();
    // Check periodically
    const interval = setInterval(checkStepFile, 2000);
    return () => clearInterval(interval);
  }, [jobId]);

  const loadSample = () => {
    setPrimitives(SAMPLE_PROFILE2D.map((p) => ({ ...p })));
    setAxisPoint({ ...SAMPLE_AXIS_POINT });
    setError(null);
  };

  const addLineSegment = () => {
    const lastPrimitive = primitives[primitives.length - 1];
    const newSegment: LineSegment = {
      type: 'line',
      start: lastPrimitive ? lastPrimitive.end : { x: 1.0, y: 0.0 },
      end: lastPrimitive ? { x: lastPrimitive.end.x, y: lastPrimitive.end.y + 1.0 } : { x: 1.0, y: 1.0 },
    };
    setPrimitives([...primitives, newSegment]);
  };

  const removePrimitive = (index: number) => {
    setPrimitives(primitives.filter((_, i) => i !== index));
  };

  const updatePrimitive = (index: number, field: 'start' | 'end', pointField: 'x' | 'y', value: number) => {
    const updated = [...primitives];
    updated[index] = {
      ...updated[index],
      [field]: {
        ...updated[index][field],
        [pointField]: value,
      },
    };
    setPrimitives(updated);
  };

  const handleProcess = async () => {
    if (primitives.length === 0) {
      setError('Please add at least one line segment');
      return;
    }

    // Check if profile is closed (last end should connect to first start)
    const firstStart = primitives[0].start;
    const lastEnd = primitives[primitives.length - 1].end;
    const isClosed = Math.abs(firstStart.x - lastEnd.x) < 0.001 && Math.abs(firstStart.y - lastEnd.y) < 0.001;

    if (!isClosed) {
      setError('Profile must be closed (last point must connect to first point)');
      return;
    }

    setProcessing(true);
    setError(null);
    setSuccess(false);

    try {
      const result = await api.processProfile2D(jobId, {
        primitives: primitives.map((p) => ({
          type: p.type,
          start: p.start,
          end: p.end,
        })),
        axis_point: axisPoint,
      });

      if (result.status === 'DONE') {
        setSuccess(true);
        setHasStepFile(result.outputs.includes('model.step'));
        setTimeout(() => setSuccess(false), 3000);
      } else {
        setError(result.validation_errors.join(', ') || 'Processing failed');
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to process Profile2D');
    } finally {
      setProcessing(false);
    }
  };

  const handleDownloadStep = () => {
    api.downloadFile(jobId, 'outputs/model.step', 'model.step');
  };

  return (
    <div className="profile2d-input">
      <div className="profile2d-header">
        <h3>Profile2D Input (Line Segments)</h3>
        <div className="axis-input">
          <label>Revolution Axis Point:</label>
          <div className="axis-point-inputs">
            <input
              type="number"
              step="0.001"
              value={axisPoint.x}
              onChange={(e) => setAxisPoint({ ...axisPoint, x: parseFloat(e.target.value) || 0 })}
              placeholder="X (radius)"
            />
            <input
              type="number"
              step="0.001"
              value={axisPoint.y}
              onChange={(e) => setAxisPoint({ ...axisPoint, y: parseFloat(e.target.value) || 0 })}
              placeholder="Y (axial)"
            />
          </div>
        </div>
      </div>

      <div className="profile2d-actions">
        <button onClick={loadSample} className="load-sample-btn">
          Load Sample
        </button>
        <button onClick={addLineSegment} className="add-primitive-btn">
          + Add Line Segment
        </button>
        {primitives.length > 0 && (
          <button onClick={handleProcess} disabled={processing} className="process-btn">
            {processing ? 'Processing...' : 'Process Profile2D'}
          </button>
        )}
        {hasStepFile && (
          <button onClick={handleDownloadStep} className="download-step-btn">
            Download STEP
          </button>
        )}
      </div>

      {error && <div className="error-message">{error}</div>}
      {success && <div className="success-message">Profile2D processed successfully!</div>}

      {primitives.length === 0 ? (
        <div className="no-primitives">
          <p>No line segments added yet. Click "Add Line Segment" to start.</p>
          <p className="hint">
            Profile must form a closed loop. The last point should connect to the first point.
          </p>
        </div>
      ) : (
        <div className="primitives-list">
          {primitives.map((primitive, index) => (
            <div key={index} className="primitive-card">
              <div className="primitive-header">
                <h4>Line Segment {index + 1}</h4>
                <button
                  onClick={() => removePrimitive(index)}
                  className="remove-btn"
                  title="Remove segment"
                >
                  ×
                </button>
              </div>
              <div className="primitive-fields">
                <div className="field-group">
                  <label>Start X (radius)</label>
                  <input
                    type="number"
                    step="0.001"
                    value={primitive.start.x}
                    onChange={(e) =>
                      updatePrimitive(index, 'start', 'x', parseFloat(e.target.value) || 0)
                    }
                  />
                </div>
                <div className="field-group">
                  <label>Start Y (axial)</label>
                  <input
                    type="number"
                    step="0.001"
                    value={primitive.start.y}
                    onChange={(e) =>
                      updatePrimitive(index, 'start', 'y', parseFloat(e.target.value) || 0)
                    }
                  />
                </div>
                <div className="field-group">
                  <label>End X (radius)</label>
                  <input
                    type="number"
                    step="0.001"
                    value={primitive.end.x}
                    onChange={(e) =>
                      updatePrimitive(index, 'end', 'x', parseFloat(e.target.value) || 0)
                    }
                  />
                </div>
                <div className="field-group">
                  <label>End Y (axial)</label>
                  <input
                    type="number"
                    step="0.001"
                    value={primitive.end.y}
                    onChange={(e) =>
                      updatePrimitive(index, 'end', 'y', parseFloat(e.target.value) || 0)
                    }
                  />
                </div>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

export default Profile2DInput;

