import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import DocumentRegistryPanel from '../components/DocumentRegistryPanel';
import { api } from '../services/api';
import type { DocumentRegistry } from '../services/documentRegistry';

vi.mock('../services/api', () => ({ api: {
  registerDocuments: vi.fn(), getDocuments: vi.fn(), associateDocument: vi.fn(),
} }));

const registry: DocumentRegistry = { version: 3, documents: [{
  document_id: 'doc-1', original_name: 'drawing.pdf', path: 'inputs/drawing.pdf', sha256: '12345678',
  size_bytes: 100, association: null,
}] };

describe('DocumentRegistryPanel', () => {
  beforeEach(() => vi.resetAllMocks());

  async function selectDocument() {
    fireEvent.click(screen.getByRole('button', { name: 'Register / refresh source documents' }));
    await screen.findByText('1 retained source documents.');
    fireEvent.change(screen.getByLabelText('Document'), { target: { value: 'doc-1' } });
    fireEvent.change(screen.getByLabelText('Part number'), { target: { value: 'PART-1' } });
  }

  it('saves source identity with version guard and invalidates the verification snapshot', async () => {
    vi.mocked(api.registerDocuments).mockResolvedValue(registry);
    vi.mocked(api.associateDocument).mockResolvedValue({ version: 4, document_id: 'doc-1' });
    vi.mocked(api.getDocuments).mockResolvedValue({ ...registry, version: 4 });
    const changed = vi.fn();
    render(<DocumentRegistryPanel jobId="job" onChanged={changed} />);
    await selectDocument();
    fireEvent.change(screen.getByLabelText('Revision'), { target: { value: 'B' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save association' }));
    await screen.findByText(/Association saved/);
    expect(api.associateDocument).toHaveBeenCalledWith('job', 'doc-1', {
      part_number: 'PART-1', revision: 'B', role: 'finished_drawing', manufacturing_state: 'finished',
    }, 3);
    expect(changed).toHaveBeenCalledTimes(2);
  });

  it('does not automatically retry stale edits', async () => {
    vi.mocked(api.registerDocuments).mockResolvedValue(registry);
    vi.mocked(api.associateDocument).mockRejectedValue(new Error('Document registry changed. Refresh before saving your association.'));
    render(<DocumentRegistryPanel jobId="job" onChanged={vi.fn()} />);
    await selectDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Save association' }));
    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('Refresh before saving'));
    expect(api.associateDocument).toHaveBeenCalledTimes(1);
    expect(screen.getByLabelText('Part number')).toHaveValue('PART-1');
  });

  it('leaves mixed process operations unresolved instead of defaulting to finished', async () => {
    vi.mocked(api.registerDocuments).mockResolvedValue(registry);
    render(<DocumentRegistryPanel jobId="job" onChanged={vi.fn()} />);
    await selectDocument();
    fireEvent.change(screen.getByLabelText('Document role'), { target: { value: 'process_drawing' } });
    expect(screen.getByLabelText('Manufacturing state')).toHaveValue('unresolved');
  });
});
