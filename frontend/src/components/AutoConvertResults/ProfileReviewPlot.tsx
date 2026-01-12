import { useMemo } from 'react';
import './ProfileReviewPlot.css';

interface Segment {
  z_start: number;
  z_end: number;
  od_diameter: number;
  id_diameter: number;
  confidence?: number;
  flags?: string[];
}

interface ProfileReviewPlotProps {
  segments: Segment[];
  zRange?: [number, number];
  units?: string;
}

function ProfileReviewPlot({ segments, zRange, units = 'in' }: ProfileReviewPlotProps) {
  const plotData = useMemo(() => {
    // Calculate z range from segments if not provided
    let minZ = 0;
    let maxZ = 0;
    if (zRange) {
      [minZ, maxZ] = zRange;
    } else if (segments.length > 0) {
      minZ = Math.min(...segments.map(s => s.z_start));
      maxZ = Math.max(...segments.map(s => s.z_end));
    }
    const zRangeValue = maxZ - minZ;
    
    // Find max radius for scaling
    let maxRadius = 0;
    segments.forEach((seg) => {
      maxRadius = Math.max(maxRadius, seg.od_diameter / 2);
    });
    
    // Add some padding
    const padding = maxRadius * 0.1;
    maxRadius += padding;
    
    return {
      minZ,
      maxZ,
      zRange: zRangeValue,
      maxRadius,
      padding,
    };
  }, [segments, zRange]);

  const { minZ, zRange: zRangeValue, maxRadius } = plotData;

  // SVG dimensions
  const width = 800;
  const height = 400;
  const margin = { top: 40, right: 40, bottom: 60, left: 80 };
  const plotWidth = width - margin.left - margin.right;
  const plotHeight = height - margin.top - margin.bottom;

  // Scale functions
  const scaleX = (z: number) => margin.left + ((z - minZ) / zRangeValue) * plotWidth;
  const scaleY = (radius: number) => margin.top + plotHeight - (radius / maxRadius) * plotHeight;

  const getConfidenceColor = (confidence?: number) => {
    if (confidence === undefined) return '#888';
    if (confidence >= 0.8) return '#4caf50';
    if (confidence >= 0.6) return '#ff9800';
    return '#f44336';
  };

  return (
    <div className="profile-review-plot">
      <h3>2D Turned Profile Review</h3>
      <div className="plot-container">
        <svg width={width} height={height} className="plot-svg">
          {/* Grid lines */}
          <defs>
            <pattern id="grid-review" width="20" height="20" patternUnits="userSpaceOnUse">
              <path d="M 20 0 L 0 0 0 20" fill="none" stroke="#333" strokeWidth="0.5" />
            </pattern>
          </defs>
          <rect width={width} height={height} fill="url(#grid-review)" />

          {/* Plot area background */}
          <rect
            x={margin.left}
            y={margin.top}
            width={plotWidth}
            height={plotHeight}
            fill="#1a1a1a"
            stroke="#555"
          />

          {/* Y-axis (radius) */}
          <line
            x1={margin.left}
            y1={margin.top}
            x2={margin.left}
            y2={margin.top + plotHeight}
            stroke="#fff"
            strokeWidth="2"
          />

          {/* X-axis (Z) */}
          <line
            x1={margin.left}
            y1={margin.top + plotHeight}
            x2={margin.left + plotWidth}
            y2={margin.top + plotHeight}
            stroke="#fff"
            strokeWidth="2"
          />

          {/* Axis labels */}
          <text
            x={margin.left + plotWidth / 2}
            y={height - 10}
            textAnchor="middle"
            fill="#fff"
            fontSize="14"
            fontWeight="500"
          >
            Z ({units})
          </text>
          <text
            x={20}
            y={margin.top + plotHeight / 2}
            textAnchor="middle"
            fill="#fff"
            fontSize="14"
            fontWeight="500"
            transform={`rotate(-90, 20, ${margin.top + plotHeight / 2})`}
          >
            Radius ({units})
          </text>

          {/* Y-axis tick marks and labels */}
          {[0, 0.25, 0.5, 0.75, 1.0].map((ratio) => {
            const radius = ratio * maxRadius;
            const y = scaleY(radius);
            return (
              <g key={`y-tick-${ratio}`}>
                <line
                  x1={margin.left - 5}
                  y1={y}
                  x2={margin.left}
                  y2={y}
                  stroke="#888"
                  strokeWidth="1"
                />
                <text
                  x={margin.left - 10}
                  y={y + 4}
                  textAnchor="end"
                  fill="#ccc"
                  fontSize="11"
                >
                  {radius.toFixed(3)}
                </text>
              </g>
            );
          })}

          {/* X-axis tick marks and labels */}
          {segments.map((seg, index) => {
            const zStart = scaleX(seg.z_start);
            const zEnd = scaleX(seg.z_end);
            return (
              <g key={`x-tick-${index}`}>
                {index === 0 && (
                  <>
                    <line
                      x1={zStart}
                      y1={margin.top + plotHeight}
                      x2={zStart}
                      y2={margin.top + plotHeight + 5}
                      stroke="#888"
                      strokeWidth="1"
                    />
                    <text
                      x={zStart}
                      y={margin.top + plotHeight + 20}
                      textAnchor="middle"
                      fill="#ccc"
                      fontSize="11"
                    >
                      {seg.z_start.toFixed(3)}
                    </text>
                  </>
                )}
                <line
                  x1={zEnd}
                  y1={margin.top + plotHeight}
                  x2={zEnd}
                  y2={margin.top + plotHeight + 5}
                  stroke="#888"
                  strokeWidth="1"
                />
                <text
                  x={zEnd}
                  y={margin.top + plotHeight + 20}
                  textAnchor="middle"
                  fill="#ccc"
                  fontSize="11"
                >
                  {seg.z_end.toFixed(3)}
                </text>
              </g>
            );
          })}

          {/* Segment bands with confidence coloring */}
          {segments.map((seg, index) => {
            const zStart = scaleX(seg.z_start);
            const zEnd = scaleX(seg.z_end);
            const isEven = index % 2 === 0;
            const confidenceColor = getConfidenceColor(seg.confidence);

            return (
              <rect
                key={`band-${index}`}
                x={zStart}
                y={margin.top}
                width={zEnd - zStart}
                height={plotHeight}
                fill={isEven ? '#2a2a2a' : '#1a1a1a'}
                stroke={confidenceColor}
                strokeWidth="1"
                opacity={0.3}
                className="segment-band"
              />
            );
          })}

          {/* Station lines (vertical at segment boundaries) */}
          {segments.map((seg, index) => {
            const zStart = scaleX(seg.z_start);
            const zEnd = scaleX(seg.z_end);
            return (
              <g key={`station-${index}`}>
                {index === 0 && (
                  <line
                    x1={zStart}
                    y1={margin.top}
                    x2={zStart}
                    y2={margin.top + plotHeight}
                    stroke="#555"
                    strokeWidth="1"
                    strokeDasharray="4,4"
                  />
                )}
                <line
                  x1={zEnd}
                  y1={margin.top}
                  x2={zEnd}
                  y2={margin.top + plotHeight}
                  stroke="#555"
                  strokeWidth="1"
                  strokeDasharray="4,4"
                />
              </g>
            );
          })}

          {/* OD step line */}
          <polyline
            points={segments.flatMap((seg, index) => {
              const zStart = scaleX(seg.z_start);
              const zEnd = scaleX(seg.z_end);
              const odRadius = seg.od_diameter / 2;
              const y = scaleY(odRadius);
              // Connect segments: end of previous connects to start of current
              if (index === 0) {
                return [`${zStart},${y}`, `${zEnd},${y}`];
              } else {
                const prevSeg = segments[index - 1];
                const prevZEnd = scaleX(prevSeg.z_end);
                const prevOdRadius = prevSeg.od_diameter / 2;
                const prevY = scaleY(prevOdRadius);
                return [`${prevZEnd},${prevY}`, `${zStart},${y}`, `${zEnd},${y}`];
              }
            }).join(' ')}
            fill="none"
            stroke="#4a9eff"
            strokeWidth="2"
            className="od-line"
          />

          {/* ID step line */}
          <polyline
            points={segments.flatMap((seg, index) => {
              const zStart = scaleX(seg.z_start);
              const zEnd = scaleX(seg.z_end);
              const idRadius = seg.id_diameter > 0 ? seg.id_diameter / 2 : 0;
              const y = scaleY(idRadius);
              // Connect segments: end of previous connects to start of current
              if (index === 0) {
                return [`${zStart},${y}`, `${zEnd},${y}`];
              } else {
                const prevSeg = segments[index - 1];
                const prevZEnd = scaleX(prevSeg.z_end);
                const prevIdRadius = prevSeg.id_diameter > 0 ? prevSeg.id_diameter / 2 : 0;
                const prevY = scaleY(prevIdRadius);
                return [`${prevZEnd},${prevY}`, `${zStart},${y}`, `${zEnd},${y}`];
              }
            }).join(' ')}
            fill="none"
            stroke="#ff6b6b"
            strokeWidth="2"
            className="id-line"
          />

          {/* Confidence indicators */}
          {segments.map((seg, index) => {
            if (seg.confidence === undefined) return null;
            const zStart = scaleX(seg.z_start);
            const zEnd = scaleX(seg.z_end);
            const zMid = (zStart + zEnd) / 2;
            const confidenceColor = getConfidenceColor(seg.confidence);
            
            return (
              <circle
                key={`confidence-${index}`}
                cx={zMid}
                cy={margin.top + 15}
                r="4"
                fill={confidenceColor}
                stroke="#fff"
                strokeWidth="1"
              />
            );
          })}

          {/* Legend */}
          <g className="legend" transform={`translate(${width - 150}, ${margin.top + 20})`}>
            <rect width="140" height="100" fill="#2a2a2a" stroke="#555" rx="4" />
            <line x1="10" y1="15" x2="30" y2="15" stroke="#4a9eff" strokeWidth="2" />
            <text x="35" y="18" fill="#fff" fontSize="11">OD</text>
            <line x1="10" y1="35" x2="30" y2="35" stroke="#ff6b6b" strokeWidth="2" />
            <text x="35" y="38" fill="#fff" fontSize="11">ID</text>
            <circle cx="20" cy="55" r="4" fill="#4caf50" stroke="#fff" strokeWidth="1" />
            <text x="30" y="59" fill="#fff" fontSize="11">High (≥80%)</text>
            <circle cx="20" cy="75" r="4" fill="#ff9800" stroke="#fff" strokeWidth="1" />
            <text x="30" y="79" fill="#fff" fontSize="11">Med (60-80%)</text>
            <circle cx="20" cy="95" r="4" fill="#f44336" stroke="#fff" strokeWidth="1" />
            <text x="30" y="99" fill="#fff" fontSize="11">Low (&lt;60%)</text>
          </g>
        </svg>
      </div>
    </div>
  );
}

export default ProfileReviewPlot;
