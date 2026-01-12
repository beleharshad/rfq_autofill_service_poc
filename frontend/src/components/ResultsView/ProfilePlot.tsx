import { useMemo } from 'react';
import type { PartSummary } from '../../services/types';
import './ProfilePlot.css';

interface ProfilePlotProps {
  summary: PartSummary;
  highlightedSegment?: number;
  onSegmentHover?: (index: number | null) => void;
  onSegmentClick?: (index: number) => void;
}

function ProfilePlot({ summary, highlightedSegment, onSegmentHover, onSegmentClick }: ProfilePlotProps) {
  const plotData = useMemo(() => {
    const [minZ, maxZ] = summary.z_range;
    const zRange = maxZ - minZ;
    
    // Find max radius for scaling
    let maxRadius = 0;
    summary.segments.forEach((seg) => {
      maxRadius = Math.max(maxRadius, seg.od_diameter / 2);
    });
    
    // Add some padding
    const padding = maxRadius * 0.1;
    maxRadius += padding;
    
    return {
      minZ,
      maxZ,
      zRange,
      maxRadius,
      padding,
    };
  }, [summary]);

  const { minZ, maxZ, zRange, maxRadius, padding } = plotData;

  // SVG dimensions
  const width = 800;
  const height = 400;
  const margin = { top: 40, right: 40, bottom: 60, left: 80 };
  const plotWidth = width - margin.left - margin.right;
  const plotHeight = height - margin.top - margin.bottom;

  // Scale functions
  const scaleX = (z: number) => margin.left + ((z - minZ) / zRange) * plotWidth;
  const scaleY = (radius: number) => margin.top + plotHeight - (radius / maxRadius) * plotHeight;

  const handleSegmentMouseEnter = (index: number) => {
    if (onSegmentHover) {
      onSegmentHover(index);
    }
  };

  const handleSegmentMouseLeave = () => {
    if (onSegmentHover) {
      onSegmentHover(null);
    }
  };

  const handleSegmentClick = (index: number) => {
    if (onSegmentClick) {
      onSegmentClick(index);
    }
  };

  return (
    <div className="profile-plot">
      <h3>2D Turned Profile</h3>
      <div className="plot-container">
        <svg width={width} height={height} className="plot-svg">
          {/* Grid lines */}
          <defs>
            <pattern id="grid" width="20" height="20" patternUnits="userSpaceOnUse">
              <path d="M 20 0 L 0 0 0 20" fill="none" stroke="#333" strokeWidth="0.5" />
            </pattern>
          </defs>
          <rect width={width} height={height} fill="url(#grid)" />

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
            Z ({summary.units.length})
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
            Radius ({summary.units.length})
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
          {summary.segments.map((seg, index) => {
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

          {/* Segment bands */}
          {summary.segments.map((seg, index) => {
            const zStart = scaleX(seg.z_start);
            const zEnd = scaleX(seg.z_end);
            const isHighlighted = highlightedSegment === index;
            const isEven = index % 2 === 0;

            return (
              <rect
                key={`band-${index}`}
                x={zStart}
                y={margin.top}
                width={zEnd - zStart}
                height={plotHeight}
                fill={isHighlighted ? '#646cff40' : isEven ? '#2a2a2a' : '#1a1a1a'}
                stroke={isHighlighted ? '#646cff' : 'transparent'}
                strokeWidth={isHighlighted ? 2 : 0}
                className="segment-band"
                onMouseEnter={() => handleSegmentMouseEnter(index)}
                onMouseLeave={handleSegmentMouseLeave}
                onClick={() => handleSegmentClick(index)}
                style={{ cursor: 'pointer' }}
              />
            );
          })}

          {/* Station lines (vertical at segment boundaries) */}
          {summary.segments.map((seg, index) => {
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
            points={summary.segments.flatMap((seg, index) => {
              const zStart = scaleX(seg.z_start);
              const zEnd = scaleX(seg.z_end);
              const odRadius = seg.od_diameter / 2;
              const y = scaleY(odRadius);
              // Connect segments: end of previous connects to start of current
              if (index === 0) {
                return [`${zStart},${y}`, `${zEnd},${y}`];
              } else {
                const prevSeg = summary.segments[index - 1];
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
            points={summary.segments.flatMap((seg, index) => {
              const zStart = scaleX(seg.z_start);
              const zEnd = scaleX(seg.z_end);
              const idRadius = seg.id_diameter > 0 ? seg.id_diameter / 2 : 0;
              const y = scaleY(idRadius);
              // Connect segments: end of previous connects to start of current
              if (index === 0) {
                return [`${zStart},${y}`, `${zEnd},${y}`];
              } else {
                const prevSeg = summary.segments[index - 1];
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

          {/* Tooltip (shown on hover) */}
          {highlightedSegment !== undefined && highlightedSegment !== null && (
            <g className="tooltip">
              <rect
                x={scaleX(summary.segments[highlightedSegment].z_start) + 10}
                y={margin.top + 10}
                width="200"
                height="120"
                fill="#2a2a2a"
                stroke="#646cff"
                strokeWidth="2"
                rx="4"
              />
              <text
                x={scaleX(summary.segments[highlightedSegment].z_start) + 20}
                y={margin.top + 30}
                fill="#fff"
                fontSize="12"
                fontWeight="600"
              >
                Segment {highlightedSegment + 1}
              </text>
              <text
                x={scaleX(summary.segments[highlightedSegment].z_start) + 20}
                y={margin.top + 50}
                fill="#ccc"
                fontSize="11"
              >
                Z: {summary.segments[highlightedSegment].z_start.toFixed(3)} -{' '}
                {summary.segments[highlightedSegment].z_end.toFixed(3)}
              </text>
              <text
                x={scaleX(summary.segments[highlightedSegment].z_start) + 20}
                y={margin.top + 70}
                fill="#ccc"
                fontSize="11"
              >
                OD: {summary.segments[highlightedSegment].od_diameter.toFixed(3)}{' '}
                {summary.units.length}
              </text>
              <text
                x={scaleX(summary.segments[highlightedSegment].z_start) + 20}
                y={margin.top + 90}
                fill="#ccc"
                fontSize="11"
              >
                ID: {summary.segments[highlightedSegment].id_diameter.toFixed(3)}{' '}
                {summary.units.length}
              </text>
              <text
                x={scaleX(summary.segments[highlightedSegment].z_start) + 20}
                y={margin.top + 110}
                fill="#ccc"
                fontSize="11"
              >
                Vol: {summary.segments[highlightedSegment].volume_in3.toFixed(6)}{' '}
                {summary.units.volume}
              </text>
            </g>
          )}

          {/* Legend */}
          <g className="legend" transform={`translate(${width - 150}, ${margin.top + 20})`}>
            <rect width="140" height="60" fill="#2a2a2a" stroke="#555" rx="4" />
            <line x1="10" y1="15" x2="30" y2="15" stroke="#4a9eff" strokeWidth="2" />
            <text x="35" y="18" fill="#fff" fontSize="11">OD</text>
            <line x1="10" y1="35" x2="30" y2="35" stroke="#ff6b6b" strokeWidth="2" />
            <text x="35" y="38" fill="#fff" fontSize="11">ID</text>
            <rect x="10" y="50" width="20" height="8" fill="#646cff40" stroke="#646cff" />
            <text x="35" y="58" fill="#fff" fontSize="11">Selected</text>
          </g>
        </svg>
      </div>
    </div>
  );
}

export default ProfilePlot;

