import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { ViewerToolbar } from '../components/ResultsView/ViewerToolbar';

describe('ViewerToolbar STEP-backed mode', () => {
  const noop = vi.fn();

  it('hides procedural controls when forceGlbOnly is enabled', () => {
    render(
      <ViewerToolbar
        viewMode="standard"
        onViewModeChange={noop}
        showOD={true}
        onShowODChange={noop}
        showID={true}
        onShowIDChange={noop}
        highlightThinWall={false}
        onHighlightThinWallChange={noop}
        thinWallThreshold={0.1}
        onThinWallThresholdChange={noop}
        showODOverlay={false}
        onShowODOverlayChange={noop}
        showIDOverlay={false}
        onShowIDOverlayChange={noop}
        showShoulderPlanes={false}
        onShowShoulderPlanesChange={noop}
        hasGlb={false}
        forceGlbOnly={true}
        glbUrl={null}
        units="in"
        onResetView={noop}
      />,
    );

    expect(screen.queryByText('Show OD')).not.toBeInTheDocument();
    expect(screen.queryByText('Show ID')).not.toBeInTheDocument();
    expect(screen.getByText('OD Overlay')).toBeInTheDocument();
  });
});
