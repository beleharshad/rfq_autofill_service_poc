import './ViewerToolbar.css';

export type ViewMode = 'standard' | 'realistic' | 'xray';

interface ViewerToolbarProps {
  viewMode: ViewMode;
  onViewModeChange: (mode: ViewMode) => void;
  showOD: boolean;
  onShowODChange: (show: boolean) => void;
  showID: boolean;
  onShowIDChange: (show: boolean) => void;
  highlightThinWall: boolean;
  onHighlightThinWallChange: (show: boolean) => void;
  thinWallThreshold: number;
  onThinWallThresholdChange: (value: number) => void;
  showODOverlay: boolean;
  onShowODOverlayChange: (show: boolean) => void;
  showIDOverlay: boolean;
  onShowIDOverlayChange: (show: boolean) => void;
  showShoulderPlanes: boolean;
  onShowShoulderPlanesChange: (show: boolean) => void;
  hasGlb: boolean;
  forceGlbOnly?: boolean;
  glbUrl: string | null;
  units: string;
  onResetView: () => void;
  showDims?: boolean;
  onShowDimsChange?: (show: boolean) => void;
}

export function ViewerToolbar({
  viewMode,
  onViewModeChange,
  showOD,
  onShowODChange,
  showID,
  onShowIDChange,
  highlightThinWall,
  onHighlightThinWallChange,
  thinWallThreshold,
  onThinWallThresholdChange,
  showODOverlay,
  onShowODOverlayChange,
  showIDOverlay,
  onShowIDOverlayChange,
  showShoulderPlanes,
  onShowShoulderPlanesChange,
  hasGlb,
  forceGlbOnly = false,
  glbUrl,
  units,
  onResetView,
  showDims = true,
  onShowDimsChange,
}: ViewerToolbarProps) {
  return (
    <div className="viewer-toolbar">
      {/* View Mode Selector */}
      <div className="toolbar-section">
        <label className="toolbar-label">View Mode:</label>
        <div className="view-mode-selector">
          <button
            className={`mode-button ${viewMode === 'standard' ? 'active' : ''}`}
            onClick={() => onViewModeChange('standard')}
            title="Standard view with basic lighting"
          >
            Standard
          </button>
          <button
            className={`mode-button ${viewMode === 'realistic' ? 'active' : ''}`}
            onClick={() => onViewModeChange('realistic')}
            title="Realistic CAD view with professional lighting"
          >
            Realistic
          </button>
          <button
            className={`mode-button ${viewMode === 'xray' ? 'active' : ''}`}
            onClick={() => onViewModeChange('xray')}
            title="X-ray view with flat lighting"
          >
            X-ray
          </button>
        </div>
      </div>

      <div className="toolbar-separator" />

      {/* Procedural Model Controls (only when the viewer is not STEP/GLB-first) */}
      {!hasGlb && !forceGlbOnly && (
        <>
          <div className="toolbar-section">
            <label className="control-toggle">
              <input
                type="checkbox"
                checked={showOD}
                onChange={(e) => onShowODChange(e.target.checked)}
              />
              <span>Show OD</span>
            </label>
            <label className="control-toggle">
              <input
                type="checkbox"
                checked={showID}
                onChange={(e) => onShowIDChange(e.target.checked)}
              />
              <span>Show ID</span>
            </label>
          </div>
          <div className="toolbar-separator" />
        </>
      )}

      {/* Feature Overlay Controls */}
      <div className="toolbar-section">
        <label className="control-toggle">
          <input
            type="checkbox"
            checked={showODOverlay}
            onChange={(e) => onShowODOverlayChange(e.target.checked)}
          />
          <span>OD Overlay</span>
        </label>
        <label className="control-toggle">
          <input
            type="checkbox"
            checked={showIDOverlay}
            onChange={(e) => onShowIDOverlayChange(e.target.checked)}
          />
          <span>ID Overlay</span>
        </label>
        <label className="control-toggle">
          <input
            type="checkbox"
            checked={showShoulderPlanes}
            onChange={(e) => onShowShoulderPlanesChange(e.target.checked)}
          />
          <span>Shoulder Planes</span>
        </label>
        <label className="control-toggle">
          <input
            type="checkbox"
            checked={highlightThinWall}
            onChange={(e) => onHighlightThinWallChange(e.target.checked)}
          />
          <span>Thin Wall Highlight</span>
        </label>
        {highlightThinWall && (
          <label className="control-threshold">
            <span>Threshold ({units}):</span>
            <input
              type="number"
              step="0.01"
              min="0"
              value={thinWallThreshold}
              onChange={(e) => onThinWallThresholdChange(parseFloat(e.target.value) || 0.1)}
            />
          </label>
        )}
      </div>

      <div className="toolbar-separator" />

      {/* Actions */}
      <div className="toolbar-section">
        {onShowDimsChange && (
          <button
            className={`mode-button ${showDims ? 'active accent-gold' : ''}`}
            onClick={() => onShowDimsChange(!showDims)}
            title="Toggle dimensions overlay"
          >
            Dims
          </button>
        )}
        {hasGlb && glbUrl && (
          <a
            href={glbUrl}
            download="model.glb"
            className="download-glb-btn"
          >
            Download GLB
          </a>
        )}
        <button onClick={onResetView} className="reset-button">
          Reset View
        </button>
      </div>
    </div>
  );
}








