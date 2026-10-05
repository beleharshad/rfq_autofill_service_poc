import { useState } from 'react';
import type { FeatureRecipe, Vec3 } from '../services/acceptedParts';

export default function RecipeFeatureEditor({ onAdd }: { onAdd: (features: FeatureRecipe[]) => void }) {
  const [kind, setKind] = useState<FeatureRecipe['kind']>('hole');
  const [values, setValues] = useState<Record<string, string>>({ x:'0', y:'0', z:'0', diameter:'5', depth:'10', width:'10', height:'10',
    entry_diameter:'8', end_diameter:'5', major_diameter:'10', minor_diameter:'8', pitch:'2', length:'10', groove_width:'1', count:'1', bolt_circle:'20', offset:'0' });
  const [axis, setAxis] = useState('0,0,1');
  const [termination, setTermination] = useState<'through' | 'blind'>('through');
  const [side, setSide] = useState<'internal' | 'external'>('internal');
  const [hand, setHand] = useState<'right' | 'left'>('right');
  const [callout, setCallout] = useState('');
  const [error, setError] = useState('');
  const number = (key: string) => Number(values[key]);
  function add() {
    setError('');
    const keys = ['x','y','z','count','bolt_circle','offset', ...(kind === 'hole' ? ['diameter','depth'] : kind === 'pocket' ? ['width','height','depth'] : kind === 'cone_cut' ? ['entry_diameter','end_diameter','depth'] : ['major_diameter','minor_diameter','pitch','length','groove_width'])];
    if (keys.some(k => values[k].trim() === '' || !Number.isFinite(number(k)))) { setError('Enter finite dimensions in every field.'); return; }
    const count = number('count');
    if (!Number.isInteger(count) || count < 1 || count > 36) { setError('Pattern quantity must be an integer from 1 to 36.'); return; }
    const features: FeatureRecipe[] = [];
    for (let i=0;i<count;i++) {
      const angle = (number('offset') + i*360/count)*Math.PI/180;
      const radius = count > 1 ? number('bolt_circle')/2 : 0;
      const common = { feature_id: `f_${crypto.randomUUID()}`, axis: axis.split(',').map(Number) as Vec3,
        origin: [number('x')+radius*Math.cos(angle), number('y')+radius*Math.sin(angle), number('z')] as Vec3 };
      if (kind === 'hole') features.push({ ...common, kind, diameter:number('diameter'), depth:number('depth'), termination });
      else if (kind === 'pocket') features.push({ ...common, kind, width:number('width'), height:number('height'), depth:number('depth') });
      else if (kind === 'cone_cut') features.push({ ...common, kind, entry_diameter:number('entry_diameter'), end_diameter:number('end_diameter'), depth:number('depth') });
      else features.push({ ...common, kind, side, handedness:hand, major_diameter:number('major_diameter'), minor_diameter:number('minor_diameter'), pitch:number('pitch'), length:number('length'), groove_width:number('groove_width'), callout });
    }
    onAdd(features);
  }
  function input(key: string, label: string) { return <label key={key} style={{ display:'inline-block', margin:6 }}>{label}<input type="number" step="any" value={values[key]} onChange={e => setValues(v => ({ ...v, [key]:e.target.value }))} style={{ width:90, display:'block' }} /></label>; }
  const dimensions = kind === 'hole' ? ['diameter','depth'] : kind === 'pocket' ? ['width','height','depth'] : kind === 'cone_cut' ? ['entry_diameter','end_diameter','depth'] : ['major_diameter','minor_diameter','pitch','length','groove_width'];
  return <fieldset><legend>Add a feature or circular pattern — millimetres</legend>
    <label>Feature <select value={kind} onChange={e => setKind(e.target.value as FeatureRecipe['kind'])}>
      <option value="hole">Hole / counterbore</option><option value="pocket">Rectangular pocket / slot</option>
      <option value="cone_cut">Conical cut / countersink</option><option value="thread">Representative helical thread</option>
    </select></label>
    <div>{['x','y','z'].map(k => input(k, `Entry ${k.toUpperCase()}`))}
      <label>Cut direction <select value={axis} onChange={e => setAxis(e.target.value)}>
        {['0,0,1','0,0,-1','1,0,0','-1,0,0','0,1,0','0,-1,0'].map((v,i) => <option value={v} key={v}>{['+Z','−Z','+X','−X','+Y','−Y'][i]}</option>)}
      </select></label>
    </div>
    <div>{dimensions.map(k => input(k, k.replace(/_/g,' ')))}</div>
    {kind === 'hole' && <label>Termination <select value={termination} onChange={e => setTermination(e.target.value as 'through'|'blind')}><option value="through">Through</option><option value="blind">Blind</option></select></label>}
    {kind === 'thread' && <div>
      <label>Thread side <select value={side} onChange={e => setSide(e.target.value as 'internal'|'external')}><option value="internal">Internal</option><option value="external">External</option></select></label>{' '}
      <label>Handedness <select value={hand} onChange={e => setHand(e.target.value as 'right'|'left')}><option value="right">Right</option><option value="left">Left</option></select></label>{' '}
      <label>Drawing callout <input value={callout} onChange={e => setCallout(e.target.value)} /></label>
      <p>Add the bore before an internal thread. Enter reviewed major/minor diameters, pitch and groove width. This representative helix does not certify the fit class.</p>
    </div>}
    <div>{input('count','Pattern quantity')}{input('bolt_circle','XY bolt circle diameter')}{input('offset','Angular offset, degrees')}</div>
    <p>For quantity 1, entry coordinates specify the feature. For a pattern, they specify its centre in the XY plane.</p>
    <button type="button" onClick={add}>Add feature(s)</button>{error && <p role="alert">{error}</p>}
  </fieldset>;
}
