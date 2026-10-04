import { useEffect, useRef, useState } from 'react';
import { api } from '../services/api';
import type { DocumentAssociation, DocumentRegistry } from '../services/documentRegistry';

export default function DocumentRegistryPanel({ jobId, onChanged }: { jobId: string; onChanged: () => void }) {
  const [registry, setRegistry] = useState<DocumentRegistry | null>(null);
  const [selected, setSelected] = useState('');
  const [part, setPart] = useState('');
  const [revision, setRevision] = useState('');
  const [role, setRole] = useState<DocumentAssociation['role']>('finished_drawing');
  const [state, setState] = useState<DocumentAssociation['manufacturing_state']>('finished');
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');
  const active = useRef(true);
  useEffect(() => { active.current = true; return () => { active.current = false; }; }, []);

  function choose(id: string, data = registry) {
    setSelected(id);
    const association = data?.documents.find(d => d.document_id === id)?.association;
    setPart(association?.part_number ?? '');
    setRevision(association?.revision ?? '');
    setRole(association?.role ?? 'finished_drawing');
    setState(association?.manufacturing_state ?? 'finished');
  }

  async function load() {
    setBusy(true); setError(''); setMessage('');
    try {
      const result = await api.registerDocuments(jobId);
      if (!active.current) return;
      setRegistry(result); choose('', result); onChanged();
    } catch (e) {
      if (active.current) setError(e instanceof Error ? e.message : 'Unable to register documents');
    } finally { if (active.current) setBusy(false); }
  }

  async function save(event: React.FormEvent) {
    event.preventDefault();
    if (!registry || !selected) return;
    setBusy(true); setError(''); setMessage('');
    try {
      await api.associateDocument(jobId, selected, { part_number: part.trim(), revision: revision.trim() || null,
        role, manufacturing_state: state }, registry.version);
      if (!active.current) return;
      onChanged();
      // A saved edit changes the registry version. Obtain a fresh view before another edit.
      setRegistry(null);
      const updated = await api.getDocuments(jobId);
      if (!active.current) return;
      setRegistry(updated); choose(selected, updated);
      setMessage('Association saved. Model dimensions still require validation.');
    } catch (e) {
      if (active.current) setError(e instanceof Error ? e.message : 'Unable to save association');
    } finally { if (active.current) setBusy(false); }
  }

  return <section aria-label="Document associations" style={{ padding: '1rem', border: '1px solid #cbd5e1', marginBottom: '1rem' }}>
    <h2>Document associations</h2>
    <p>Assign each source to its part, revision and manufacturing state. Leave revision blank when unknown.</p>
    <button onClick={load} disabled={busy}>Register / refresh source documents</button>
    {error && <p role="alert">{error}</p>}
    {message && <p role="status">{message}</p>}
    {registry && <form onSubmit={save}>
      <p>{registry.documents.length} retained source documents.</p>
      <fieldset disabled={busy}>
        <legend>Source identity</legend>
        <label>Document <select value={selected} onChange={e => choose(e.target.value)} required>
          <option value="">Select a document</option>
          {registry.documents.map(d => <option key={d.document_id} value={d.document_id}>{d.original_name} — {d.sha256.slice(0, 8)}</option>)}
        </select></label>{' '}
        <label>Part number <input value={part} onChange={e => setPart(e.target.value)} required maxLength={200} /></label>{' '}
        <label>Revision <input value={revision} onChange={e => setRevision(e.target.value)} maxLength={100} /></label>{' '}
        <label>Document role <select value={role} onChange={e => {
          const next = e.target.value as DocumentAssociation['role']; setRole(next);
          setState(next === 'finished_drawing' ? 'finished' : 'unresolved');
        }}>
          <option value="finished_drawing">Finished drawing</option><option value="process_drawing">Process drawing</option>
          <option value="cad">CAD</option><option value="quote">Quote</option>
        </select></label>{' '}
        <label>Manufacturing state <select value={state} disabled={role === 'finished_drawing'} onChange={e => setState(e.target.value as DocumentAssociation['manufacturing_state'])}>
          <option value="finished">Finished</option><option value="raw">Raw stock</option>
          <option value="intermediate">Intermediate operation</option><option value="unresolved">Unresolved / mixed operations</option>
        </select></label>{' '}
        <button type="submit" disabled={!selected || !part.trim()}>Save association</button>
      </fieldset>
    </form>}
  </section>;
}
