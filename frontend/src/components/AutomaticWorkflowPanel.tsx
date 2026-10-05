import { useEffect, useState } from 'react';
import { api } from '../services/api';
import type { AutomaticRun, AutomaticItem } from '../services/automatic';
import AcceptedGeometryViewer from './AcceptedGeometryViewer';

export default function AutomaticWorkflowPanel({jobId}: {jobId: string}) {
  const [run, setRun] = useState<AutomaticRun | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [selected, setSelected] = useState('');
  useEffect(() => {
    let active = true;
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try {
        const value = await api.getAutomaticRun(jobId);
        if (!active) return;
        setRun(value); setError(''); setLoading(false);
        timer = setTimeout(poll, value && ['queued','running'].includes(value.status) ? 2000 : 10000);
      } catch (e) {
        if (!active) return;
        setError(e instanceof Error ? e.message : 'Cannot load automatic workflow'); setLoading(false);
        timer = setTimeout(poll, 10000);
      }
    };
    setRun(null); setSelected(''); setLoading(true); void poll();
    return () => {active = false; clearTimeout(timer);};
  }, [jobId]);
  const activeRun = !!run && ['queued','running'].includes(run.status);
  async function start(retry: boolean) {
    setBusy(true); setError('');
    try {setRun(await api.startAutomaticRun(jobId, retry)); setSelected('');}
    catch (e) {setError(e instanceof Error ? e.message : 'Cannot start automatic workflow');}
    finally {setBusy(false);}
  }
  async function cancel() {
    if (!run) return;
    setBusy(true);
    try {setRun(await api.cancelAutomaticRun(jobId, run.run_id));}
    catch (e) {setError(e instanceof Error ? e.message : 'Cannot cancel workflow');}
    finally {setBusy(false);}
  }
  const completed = run?.stale ? [] : (run?.result.items || []).filter(i => i.status==='complete' && i.build_id);
  const item = completed.find(i => i.document_id===selected) || completed[0];
  return <section aria-label="Automatic drawing workflow" style={{padding:24,background:'#fff',borderRadius:12,marginBottom:20}}>
    <h2>Automatic drawing to 3D</h2>
    <p>Upload → interpret each source → check dimensions and features → construct solids → export.</p>
    <p>Automatic checks are not engineering certification. Incomplete or conflicting sources require review.</p>
    {loading ? <p>Loading workflow…</p> : <>
      <p role="status">{run ? `Status: ${run.stale ? 'stale — sources changed' : run.status} · ${run.result.phase}` : 'Ready to process uploaded sources'}</p>
      {!activeRun && <button disabled={busy} onClick={()=>void start(!!run)}>{run ? 'Retry automatic processing' : 'Process uploaded sources'}</button>}
      {activeRun && <button disabled={busy} onClick={()=>void cancel()}>Cancel automatic processing</button>}
    </>}
    {(error || run?.error) && <p role="alert">{error || run?.error}</p>}
    {!!run?.result.items.length && <table><thead><tr><th>Source</th><th>Part / revision</th><th>Result</th><th>Details</th></tr></thead><tbody>
      {run.result.items.map(i => <tr key={i.document_id}><td>{i.name}</td><td>{i.part_number || 'Unresolved'} {i.revision}</td><td>{run.stale ? 'stale' : i.status}</td><td>{i.issues.join('; ')}</td></tr>)}
    </tbody></table>}
    {item && <>
      <label>Constructed part <select value={item.document_id} onChange={e=>setSelected(e.target.value)}>
        {completed.map(i=><option key={i.document_id} value={i.document_id}>{i.part_number} — {i.name}</option>)}
      </select></label>
      <AutomaticResult key={item.build_id} jobId={jobId} item={item} onError={setError}/>
    </>}
  </section>;
}

function AutomaticResult({jobId,item,onError}: {jobId:string;item:AutomaticItem;onError:(s:string)=>void}) {
  const [current, setCurrent] = useState(false);
  const [message, setMessage] = useState('Checking current geometry…');
  useEffect(()=>{
    let active=true; let timer: ReturnType<typeof setTimeout>;
    const check = async()=>{
      try {
        const build=await api.getGeometryBuild(jobId,item.build_id!);
        if (!active) return;
        const valid=build.status==='complete' && !build.stale;
        setCurrent(valid); setMessage(valid ? '' : 'Geometry is stale or unavailable; review the latest specification.');
        timer=setTimeout(check,10000);
      } catch(e) {if(active){setCurrent(false);setMessage(e instanceof Error ? e.message : 'Geometry unavailable');}}
    };
    void check();return()=>{active=false;clearTimeout(timer);};
  },[jobId,item.build_id]);
  async function download(name:string) {
    try {
      const blob=await api.getGeometryArtifact(jobId,item.build_id!,name);
      const url=URL.createObjectURL(blob);const a=document.createElement('a');a.href=url;a.download=name;a.click();
      setTimeout(()=>URL.revokeObjectURL(url),1000);
    }catch(e){onError(e instanceof Error ? e.message : 'Export failed');}
  }
  if(!current) return <p>{message}</p>;
  const m=item.measurements;
  const rows: [string,number|null|undefined][] = [
    ['X envelope',m?.envelope_width_mm],['Y envelope',m?.envelope_height_mm],['Z length',m?.axial_length_mm],
    ['Maximum cylindrical OD',m?.maximum_outer_cylindrical_diameter_mm],['Minimum coaxial through-bore ID',m?.minimum_coaxial_through_bore_mm]];
  return <>
    <AcceptedGeometryViewer jobId={jobId} buildId={item.build_id!}/>
    <table><thead><tr><th>Measurement</th><th>mm</th><th>in</th></tr></thead><tbody>
      {rows.map(([label,value])=><tr key={label}><td>{label}</td><td>{value==null?'Not established':value.toFixed(4)}</td><td>{value==null?'—':(value/25.4).toFixed(5)}</td></tr>)}
    </tbody></table>
    <p>Dimensions are measured from constructed geometry in the source coordinate frame.</p>
    {item.warnings?.map((w,i)=><p key={i}>{w}</p>)}
    {['model.step','model.glb','rfq_review.xlsx','manifest.json'].map(name=><button key={name} onClick={()=>void download(name)}>Download {name}</button>)}
  </>;
}
