import { useEffect, useRef, useState } from 'react';
import { api } from '../services/api';
import type { DocumentRegistry } from '../services/documentRegistry';
import type { AcceptedPart, BaseRecipe, FeatureRecipe, GeometryBuild, Recipe } from '../services/acceptedParts';
import RecipeFeatureEditor from './RecipeFeatureEditor';
import AcceptedGeometryViewer from './AcceptedGeometryViewer';

export default function AcceptedPartPanel({ jobId }: { jobId: string }) {
  const [registry,setRegistry]=useState<DocumentRegistry|null>(null);
  const [history,setHistory]=useState<AcceptedPart[]>([]);
  const [source,setSource]=useState('');
  const [kind,setKind]=useState<BaseRecipe['kind']>('cylinder');
  const [dimensions,setDimensions]=useState({diameter:'',length:'',width:'',height:'',body:'0'});
  const [profile,setProfile]=useState('');
  const [features,setFeatures]=useState<FeatureRecipe[]>([]);
  const [locator,setLocator]=useState('');
  const [page,setPage]=useState('1');
  const [note,setNote]=useState('');
  const [material,setMaterial]=useState('');
  const [unresolved,setUnresolved]=useState('');
  const [saved,setSaved]=useState<AcceptedPart|null>(null);
  const [build,setBuild]=useState<GeometryBuild|null>(null);
  const [busy,setBusy]=useState(false);
  const [error,setError]=useState('');
  const [sourcePreview,setSourcePreview]=useState('');
  const [analysisNotes,setAnalysisNotes]=useState<string[]>([]);
  const active=useRef(true);
  useEffect(()=>{ active.current=true; return ()=>{active.current=false;}; },[]);
  useEffect(()=>{
    let stopped=false;let url='';setSourcePreview('');
    const document=registry?.documents.find(d=>d.document_id===source);
    if(document?.path.toLowerCase().endsWith('.pdf')){
      api.getSourceOriginal(jobId,source).then(blob=>{
        if(stopped)return;
        url=URL.createObjectURL(new Blob([blob],{type:'application/pdf'}));setSourcePreview(url);
      }).catch(e=>{if(!stopped)setError(e.message);});
    }
    return ()=>{stopped=true;if(url)URL.revokeObjectURL(url);};
  },[jobId,source,registry]);
  // Any edit invalidates the currently displayed build, before a correction is submitted.
  useEffect(()=>{setSaved(null);setBuild(null);},[source,kind,dimensions,profile,features,locator,page,note,material,unresolved]);
  useEffect(()=>{setAnalysisNotes([]);},[source]);

  useEffect(()=>{
    if (!build || !['queued','running','complete'].includes(build.status) || build.stale) return;
    let stopped=false;
    const timer=setInterval(()=>{
      api.getGeometryBuild(jobId,build.build_id).then(result=>{if(!stopped)setBuild(result);}).catch(e=>{if(!stopped){setError(e.message);setBuild(null);}});
    },build.status==='complete'?10000:1500);
    return ()=>{stopped=true;clearInterval(timer);};
  },[jobId,build?.build_id,build?.status]);

  async function load(){
    setBusy(true);setError('');setBuild(null);setSaved(null);
    try {
      const [docs,parts]=await Promise.all([api.getDocuments(jobId),api.getAcceptedParts(jobId)]);
      if(!active.current)return;
      setRegistry(docs);setHistory(parts);
    }catch(e){if(active.current)setError(e instanceof Error?e.message:'Unable to load review');}
    finally{if(active.current)setBusy(false);}
  }

  function edit(part:AcceptedPart){
    const p=part.payload,b=p.base;
    setSource(p.governing_document_id);setKind(b.kind);setFeatures(p.features);
    setDimensions({diameter:b.kind==='cylinder'?String(b.diameter):'',length:b.kind==='cylinder'||b.kind==='box'?String(b.length):'',width:b.kind==='box'?String(b.width):'',height:b.kind==='box'?String(b.height):'',body:b.kind==='step'?String(b.body_index):'0'});
    setProfile(b.kind==='revolve'?b.profile.map(x=>x.join(', ')).join('\n'):'');
    setLocator(p.evidence[0]?.locator??'');setPage(String(p.evidence[0]?.page??''));
    setNote(p.review_note);setMaterial(p.material??'');setUnresolved(p.unresolved.join('\n'));
  }

  async function save(){
    if(!registry)return;
    setBusy(true);setError('');setBuild(null);
    try{
      const doc=registry.documents.find(d=>d.document_id===source), association=doc?.association;
      if(!association?.revision)throw new Error('Select a finished source with an explicit part number and revision. Use -- for a drawing explicitly marked unrevised.');
      const num=(key:keyof typeof dimensions)=>{
        if(!dimensions[key].trim()||!Number.isFinite(Number(dimensions[key])))throw new Error(`Enter ${key}.`);
        return Number(dimensions[key]);
      };
      let base:BaseRecipe;
      if(kind==='cylinder')base={kind,diameter:num('diameter'),length:num('length')};
      else if(kind==='box')base={kind,width:num('width'),height:num('height'),length:num('length')};
      else if(kind==='step')base={kind,document_id:source,body_index:num('body')};
      else base={kind,profile:profile.trim().split('\n').map(line=>{
        const numbers=line.split(',').map(x=>Number(x.trim()));
        if(numbers.length!==2||numbers.some(x=>!Number.isFinite(x)))throw new Error('Each profile row needs radius, Z.');
        return numbers as [number,number];
      })};
      const gaps=unresolved.split('\n').map(s=>s.trim()).filter(Boolean);
      const recipe:Recipe={part_number:association.part_number,revision:association.revision,governing_document_id:source,
        evidence:[{document_id:source,page:page.trim()?Number(page):null,locator}],base,features,review_note:note,
        completeness:gaps.length?'partial':'complete',unresolved:gaps,material:material.trim()||null};
      const version=Math.max(0,...history.filter(p=>p.payload.part_number===recipe.part_number&&p.payload.revision===recipe.revision).map(p=>p.version));
      const result=await api.acceptPart(jobId,recipe,version,registry.version);
      if(!active.current)return;
      setSaved(result);setHistory(await api.getAcceptedParts(jobId));
    }catch(e){if(active.current)setError(e instanceof Error?e.message:'Unable to accept specification');}
    finally{if(active.current)setBusy(false);}
  }

  async function start(){if(!saved)return;setError('');setBusy(true);try{const task=await api.buildAcceptedPart(jobId,saved.spec_id);if(active.current)setBuild(task);}catch(e){if(active.current)setError(e instanceof Error?e.message:'Build failed');}finally{if(active.current)setBusy(false);}}
  async function analyze(){
    setBusy(true);setError('');setAnalysisNotes([]);
    try{
      const result=await api.analyzeRegisteredSource(jobId,source);
      if(!active.current)return;
      setDimensions(d=>({...d,diameter:String(result.candidates.find(x=>x.name==='maximum_outer_diameter')?.value_mm??''),length:String(result.candidates.find(x=>x.name==='axial_length')?.value_mm??'')}));
      setAnalysisNotes(result.limitations);
    }catch(e){if(active.current)setError(e instanceof Error?e.message:'Source analysis failed');}
    finally{if(active.current)setBusy(false);}
  }
  async function restore(part:AcceptedPart){setError('');setBusy(true);try{const tasks=await api.getGeometryBuilds(jobId,part.spec_id);if(active.current){setSaved(part);setBuild(tasks[0]??null);}}catch(e){if(active.current)setError(e instanceof Error?e.message:'Unable to load build history');}finally{if(active.current)setBusy(false);}}
  async function download(filename:string){if(!build)return;try{const blob=await api.getGeometryArtifact(jobId,build.build_id,filename);const url=URL.createObjectURL(blob);const link=document.createElement('a');link.href=url;link.download=filename;link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}catch(e){setError(e instanceof Error?e.message:'Download failed');}}
  function dimension(key:keyof typeof dimensions,label:string){return <label key={key} style={{display:'inline-block',margin:6}}>{label}<input type="number" step="any" value={dimensions[key]} onChange={e=>setDimensions(d=>({...d,[key]:e.target.value}))} style={{width:100,display:'block'}}/></label>;}
  const eligible=registry?.documents.filter(d=>d.association?.manufacturing_state==='finished'&&['finished_drawing','cad'].includes(d.association.role))??[];
  const metrics=build?.manifest?.measurements;
  return <section aria-label="Reviewed geometry" style={{padding:16,border:'1px solid #cbd5e1',marginBottom:16}}>
    <h2>Review → build → inspect → export</h2>
    <p>Review source dimensions before accepting. Construction checks verify the recipe; they do not independently certify the drawing or a manufactured part.</p>
    <button onClick={load} disabled={busy}>Load sources and current results</button>
    {error&&<p role="alert">{error}</p>}
    {registry&&<fieldset disabled={busy}><legend>Reviewed specification — millimetres</legend>
      <label>Governing source <select value={source} onChange={e=>setSource(e.target.value)}><option value="">Select a finished drawing or CAD source</option>{eligible.map(d=><option value={d.document_id} key={d.document_id}>{d.original_name} — {d.association?.part_number} / {d.association?.revision??'revision unknown'}</option>)}</select></label>
      {!eligible.length&&<p>Assign a finished source in Document associations, then reload.</p>}
      <button type="button" onClick={analyze} disabled={!source||!registry.documents.find(d=>d.document_id===source)?.path.toLowerCase().endsWith('.pdf')}>Extract candidates from this source</button>
      {analysisNotes.length>0&&<ul>{analysisNotes.map(n=><li key={n}>{n}</li>)}</ul>}
      {sourcePreview&&<details><summary>Open retained source drawing</summary><iframe title="Governing source drawing" src={sourcePreview} style={{width:'100%',height:500}}/></details>}
      <p>Base solids use XY centred at zero and Z from zero to length. Revolved profiles and imported STEP preserve their entered/source frame.</p>
      <label>Base geometry <select value={kind} onChange={e=>setKind(e.target.value as BaseRecipe['kind'])}><option value="cylinder">Cylinder</option><option value="box">Rectangular block</option><option value="revolve">Revolved radial profile</option><option value="step">Imported STEP solid</option></select></label>
      <div>{kind==='cylinder'&&dimension('diameter','Diameter')}{(kind==='cylinder'||kind==='box')&&dimension('length','Z length')}{kind==='box'&&<>{dimension('width','X width')}{dimension('height','Y height')}</>}{kind==='step'&&dimension('body','Solid index, starts at 0')}</div>
      {kind==='revolve'&&<label>Closed section points: radius, Z — one point per line<textarea value={profile} onChange={e=>setProfile(e.target.value)} rows={6} style={{display:'block'}}/></label>}
      <RecipeFeatureEditor onAdd={items=>setFeatures(v=>[...v,...items])}/>
      <ol>{features.map((f,i)=><li key={f.feature_id}>{f.kind} at ({f.origin.join(', ')}) <button type="button" onClick={()=>setFeatures(v=>v.filter((_,index)=>index!==i))}>Remove feature {i+1}</button></li>)}</ol>
      <label>Source page <input type="number" min={1} value={page} onChange={e=>setPage(e.target.value)}/></label>{' '}
      <label>Dimension / view references <input value={locator} onChange={e=>setLocator(e.target.value)} placeholder="Section A-A, overall length and hole callout"/></label>
      <p><label>Review note <textarea value={note} onChange={e=>setNote(e.target.value)} placeholder="Describe what you verified and any modelling choices"/></label></p>
      <label>Material, if verified <input value={material} onChange={e=>setMaterial(e.target.value)}/></label>
      <p><label>Unresolved features, one per line<textarea value={unresolved} onChange={e=>setUnresolved(e.target.value)}/></label></p>
      <p>{unresolved.trim()?'This will be saved as a partial model.':'Accepting declares this recipe complete for the reviewed source. Check all features before saving.'}</p>
      <button onClick={save} disabled={!source||!note.trim()||!locator.trim()}>Accept specification / save correction</button>
    </fieldset>}
    {history.length>0&&<details><summary>Accepted specification history</summary><ul>{history.map(p=><li key={p.spec_id}>{p.payload.part_number} / {p.payload.revision}, version {p.version} {p.stale?'(stale)':''} <button onClick={()=>edit(p)} disabled={busy}>Load for review</button> <button onClick={()=>restore(p)} disabled={busy||p.stale}>Load build history</button></li>)}</ul></details>}
    {saved&&<p>Active result: {saved.payload?.part_number} / {saved.payload?.revision}, accepted version {saved.version}. <button onClick={start} disabled={busy||saved.stale||build?.status==='running'||build?.status==='queued'}>Build actual geometry</button></p>}
    {build&&<div><p role="status">Build: {build.stale?'stale — review corrections':build.status}</p>{build.error&&<p role="alert">{build.error}</p>}
      {['queued','running'].includes(build.status)&&<button onClick={()=>api.cancelGeometryBuild(jobId,build.build_id).then(setBuild).catch(e=>setError(e.message))}>Cancel build</button>}
      {build.status==='complete'&&!build.stale&&metrics&&<>
        <p>Constructed from the reviewed recipe. Completeness: {build.manifest?.completeness}. Quote export contains dimensions, not pricing.</p>
        <table><thead><tr><th>Measured field</th><th>mm</th><th>in</th></tr></thead><tbody>{[
          ['Maximum outer cylindrical diameter',metrics.maximum_outer_cylindrical_diameter_mm],['X envelope',metrics.envelope_width_mm],['Y envelope',metrics.envelope_height_mm],['Z axial length',metrics.axial_length_mm],
          ['Minimum coaxial through bore',metrics.minimum_coaxial_through_bore_mm],
        ].map(([label,value])=><tr key={String(label)}><td>{label}</td><td>{typeof value==='number'?Number(value.toFixed(6)):'Not applicable / unresolved'}</td><td>{typeof value==='number'?Number((value/25.4).toFixed(6)):'—'}</td></tr>)}</tbody></table>
        <ul>{build.manifest?.warnings.map((w,i)=><li key={i}>{w}</li>)}</ul>
        <AcceptedGeometryViewer key={build.build_id} jobId={jobId} buildId={build.build_id}/>
        {['model.step','model.glb','rfq_review.xlsx','manifest.json'].map(name=><button key={name} onClick={()=>download(name)}>Download {name}</button>)}
      </>}
    </div>}
  </section>;
}
