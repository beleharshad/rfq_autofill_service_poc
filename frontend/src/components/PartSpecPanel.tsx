import { useEffect, useRef, useState } from 'react';
import { api } from '../services/api';
import type { PartSpec } from '../services/partSpec';

export default function PartSpecPanel({ jobId }: { jobId: string }) {
  const [spec, setSpec] = useState<PartSpec | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const request = useRef<AbortController | null>(null);
  useEffect(() => () => request.current?.abort(), []);

  async function refresh() {
    request.current?.abort();
    const controller = new AbortController();
    request.current = controller;
    setLoading(true);
    setError(null);
    setSpec(null); // Do not retain a previous snapshot after a failed refresh.
    try {
      const result = await api.getPartSpec(jobId, controller.signal);
      if (!controller.signal.aborted) setSpec(result);
    } catch (err) {
      if (!controller.signal.aborted) setError(err instanceof Error ? err.message : 'Unable to load verification status');
    } finally {
      if (!controller.signal.aborted) setLoading(false);
    }
  }

  const labels = { maximum_outer_diameter: 'Maximum outer diameter', axial_length: 'Axial length' };
  return <section aria-label="Part verification" style={{ padding: '1rem', border: '1px solid #cbd5e1', borderRadius: 8, marginBottom: '1rem' }}>
    <h2>Part verification</h2>
    <p>Inspect a snapshot of current results. Refresh after conversion finishes.</p>
    <button onClick={refresh} disabled={loading}>{loading ? 'Loading…' : 'Refresh verification'}</button>
    {error && <p role="alert">{error}</p>}
    {spec && <>
      <p>Measurements: {spec.readiness.measurements.replace(/_/g, ' ')}. Geometry and quote: not validated.</p>
      <p>These legacy values are unverified model dimensions, not approved finished sizes or tolerance limits.</p>
      {spec.dimensions.length > 0 && <table>
        <thead><tr><th>Dimension</th><th>Original value</th><th>Millimetres</th><th>Evidence</th></tr></thead>
        <tbody>{spec.dimensions.map(d => <tr key={d.name}>
          <td>{labels[d.name]}</td><td>{d.source_value} {d.source_unit}</td><td>{Number(d.value.toPrecision(12))}</td>
          <td>{d.evidence.map(e => <div key={e.json_pointer}>
            {spec.sources.find(s => s.document_id === e.document_id)?.path ?? 'Unknown source'} {e.json_pointer}
          </div>)}</td>
        </tr>)}</tbody>
      </table>}
      <ul>{spec.issues.map(i => <li key={i.code}>{i.message}</li>)}</ul>
    </>}
  </section>;
}
