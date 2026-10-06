import { render, screen, cleanup } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import LatheViewer from '../components/AutoConvertResults/LatheViewer';

vi.mock('@react-three/fiber', () => ({
  Canvas: () => <div data-testid="canvas" />,
  useThree: vi.fn(),
  useFrame: vi.fn(),
}));
vi.mock('@react-three/drei', () => ({
  OrbitControls: () => null, GizmoHelper: () => null,
  GizmoViewcube: () => null, Line: () => null, Grid: () => null,
}));

afterEach(cleanup);

describe('LatheViewer missing geometry', () => {
  it.each([
    {},
    { maxOd: 0.001, lengthIn: 0.001 },
    { maxOd: 2.235 },
    { lengthIn: 1 },
    { maxOd: Infinity, lengthIn: 1 },
    { segments: [{ z_start: 0, z_end: NaN, od_diameter: 2, id_diameter: 0 }] },
  ])('does not invent a cylinder for incomplete input %j', (props) => {
    render(<LatheViewer {...props} />);
    expect(screen.getByRole('status').textContent).toContain('3D preview unavailable');
    expect(screen.queryByTestId('canvas')).toBeNull();
    expect(screen.queryByText('1.0000')).toBeNull();
  });

  it('still renders when profile geometry supplies both dimensions', () => {
    render(<LatheViewer segments={[{ z_start: 0, z_end: 4.88, od_diameter: 2.251, id_diameter: 0 }]} />);
    expect(screen.getByTestId('canvas')).toBeTruthy();
    expect(screen.queryByRole('status')).toBeNull();
  });
});
