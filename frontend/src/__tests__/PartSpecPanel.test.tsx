import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import PartSpecPanel from '../components/PartSpecPanel';
import { api } from '../services/api';

vi.mock('../services/api', () => ({ api: { getPartSpec: vi.fn() } }));

const response = {
  readiness: { measurements: 'needs_review' },
  dimensions: [{ name: 'maximum_outer_diameter', value: 76.2, source_value: 3, source_unit: 'in',
    evidence: [{ document_id: 'one', json_pointer: '/segments/0/od_diameter' }] }],
  sources: [{ document_id: 'one', path: 'outputs/part_summary.json' }],
  issues: [{ code: 'review', message: 'Review source revision.' }],
};

describe('PartSpecPanel', () => {
  beforeEach(() => vi.resetAllMocks());

  it('loads on demand and identifies candidate dimensions as unverified', async () => {
    vi.mocked(api.getPartSpec).mockResolvedValue(response as any);
    render(<PartSpecPanel jobId="job-one" />);
    expect(api.getPartSpec).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: 'Refresh verification' }));
    expect(await screen.findByText('Maximum outer diameter')).toBeInTheDocument();
    expect(screen.getByText(/unverified model dimensions/)).toBeInTheDocument();
    expect(screen.getByText(/outputs\/part_summary.json/)).toBeInTheDocument();
  });

  it('removes stale values when refresh fails', async () => {
    vi.mocked(api.getPartSpec).mockResolvedValueOnce(response as any).mockRejectedValueOnce(new Error('Unavailable'));
    render(<PartSpecPanel jobId="job-one" />);
    fireEvent.click(screen.getByRole('button'));
    await screen.findByText('Maximum outer diameter');
    fireEvent.click(screen.getByRole('button'));
    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('Unavailable'));
    expect(screen.queryByText('Maximum outer diameter')).not.toBeInTheDocument();
  });
});
