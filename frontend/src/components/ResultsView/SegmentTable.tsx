import type { PartSummary } from '../../services/types';
import './SegmentTable.css';

interface SegmentTableProps {
  summary: PartSummary;
  highlightedSegment?: number;
  onSegmentClick?: (index: number) => void;
  showConfidence?: boolean;
}

function SegmentTable({ summary, highlightedSegment, onSegmentClick, showConfidence = false }: SegmentTableProps) {
  const handleRowClick = (index: number) => {
    if (onSegmentClick) {
      onSegmentClick(index);
    }
  };

  const units = summary.units ?? { length: 'in', area: 'in^2', volume: 'in^3' };
  const formatUnit = (value: string) => value.replace('^3', '³').replace('^2', '²');

  return (
    <div className="segment-table">
      <h3>Segments</h3>
      <div className="table-container">
        <table>
          <thead>
            <tr>
              <th>#</th>
              <th>Z Start ({formatUnit(units.length)})</th>
              <th>Z End ({formatUnit(units.length)})</th>
              <th>OD ({formatUnit(units.length)})</th>
              <th>ID ({formatUnit(units.length)})</th>
              <th>Wall ({formatUnit(units.length)})</th>
              <th>Volume ({formatUnit(units.volume)})</th>
              <th>OD Area ({formatUnit(units.area)})</th>
              <th>ID Area ({formatUnit(units.area)})</th>
              {showConfidence && <th>Confidence</th>}
            </tr>
          </thead>
          <tbody>
            {summary.segments.map((seg, index) => {
              const segWithConfidence = seg as any;
              const confidence = segWithConfidence.confidence;
              
              return (
                <tr
                  key={index}
                  className={highlightedSegment === index ? 'highlighted' : ''}
                  onClick={() => handleRowClick(index)}
                >
                  <td>{index + 1}</td>
                  <td>{seg.z_start.toFixed(3)}</td>
                  <td>{seg.z_end.toFixed(3)}</td>
                  <td>{seg.od_diameter.toFixed(3)}</td>
                  <td>{seg.id_diameter.toFixed(3)}</td>
                  <td>{seg.wall_thickness.toFixed(3)}</td>
                  <td>{seg.volume_in3.toFixed(6)}</td>
                  <td>{seg.od_area_in2.toFixed(6)}</td>
                  <td>{seg.id_area_in2.toFixed(6)}</td>
                  {showConfidence && (
                    <td>
                      {confidence !== undefined ? (
                        <span className={`confidence-badge ${getConfidenceBadgeClass(confidence)}`}>
                          {getConfidenceLabel(confidence)} ({(confidence * 100).toFixed(0)}%)
                        </span>
                      ) : (
                        <span className="confidence-badge confidence-unknown">—</span>
                      )}
                    </td>
                  )}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
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

export default SegmentTable;

