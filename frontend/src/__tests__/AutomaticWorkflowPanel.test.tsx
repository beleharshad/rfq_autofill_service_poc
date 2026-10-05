import {fireEvent,render,screen,waitFor} from '@testing-library/react';
import {beforeEach,describe,expect,it,vi} from 'vitest';
import AutomaticWorkflowPanel from '../components/AutomaticWorkflowPanel';
import {api} from '../services/api';
vi.mock('../services/api',()=>({api:{getAutomaticRun:vi.fn(),startAutomaticRun:vi.fn(),cancelAutomaticRun:vi.fn(),getGeometryBuild:vi.fn(),getGeometryArtifact:vi.fn()}}));
vi.mock('../components/AcceptedGeometryViewer',()=>({default:()=> <div>Actual solid viewer</div>}));
const item={document_id:'doc',name:'source.pdf',part_number:'P',revision:'A',status:'complete',issues:[],build_id:'build',measurements:{envelope_width_mm:50.8,envelope_height_mm:50.8,axial_length_mm:25.4,maximum_outer_cylindrical_diameter_mm:50.8,minimum_coaxial_through_bore_mm:null},warnings:['Automatically constructed']};
const complete={run_id:'run',status:'complete',registry_version:2,stale:false,result:{phase:'finished',items:[item]}};
describe('AutomaticWorkflowPanel',()=>{
 beforeEach(()=>{vi.resetAllMocks();vi.mocked(api.getAutomaticRun).mockResolvedValue(complete as any);vi.mocked(api.getGeometryBuild).mockResolvedValue({status:'complete',stale:false} as any);});
 it('displays measured model and connected exports',async()=>{
  render(<AutomaticWorkflowPanel jobId="job"/>);
  await screen.findByText('Actual solid viewer');
  expect(screen.getByText('Not established')).toBeInTheDocument();
  expect(screen.getByRole('button',{name:'Download model.step'})).toBeInTheDocument();
  expect(api.getGeometryBuild).toHaveBeenCalledWith('job','build');
 });
 it('withholds geometry and exports for stale results',async()=>{
  vi.mocked(api.getAutomaticRun).mockResolvedValue({...complete,stale:true} as any);
  render(<AutomaticWorkflowPanel jobId="job"/>);
  await screen.findByText(/sources changed/);
  expect(screen.queryByText('Actual solid viewer')).not.toBeInTheDocument();
  expect(screen.queryByRole('button',{name:'Download model.step'})).not.toBeInTheDocument();
 });
 it('checks specification staleness even when source registry has not changed',async()=>{
  vi.mocked(api.getGeometryBuild).mockResolvedValue({status:'complete',stale:true} as any);
  render(<AutomaticWorkflowPanel jobId="job"/>);
  await screen.findByText(/Geometry is stale/);
  expect(screen.queryByText('Actual solid viewer')).not.toBeInTheDocument();
 });
 it('shows actionable provider blocks and retries',async()=>{
  const blocked={...complete,status:'review_required',result:{phase:'finished',items:[{...item,status:'blocked',build_id:undefined,issues:['Configure GOOGLE_API_KEY in the worker environment']}]}};
  vi.mocked(api.getAutomaticRun).mockResolvedValue(blocked as any);
  vi.mocked(api.startAutomaticRun).mockResolvedValue({...complete,status:'queued',result:{phase:'queued',items:[]}} as any);
  render(<AutomaticWorkflowPanel jobId="job"/>);
  await screen.findByText(/Configure GOOGLE_API_KEY/);
  fireEvent.click(screen.getByRole('button',{name:'Retry automatic processing'}));
  await waitFor(()=>expect(api.startAutomaticRun).toHaveBeenCalledWith('job',true));
  expect(screen.queryByText('Actual solid viewer')).not.toBeInTheDocument();
  await screen.findByRole('button',{name:'Cancel automatic processing'});
 });
 it('starts an existing uploaded job without manual dimension entry',async()=>{
  vi.mocked(api.getAutomaticRun).mockResolvedValue(null);
  vi.mocked(api.startAutomaticRun).mockResolvedValue({...complete,status:'queued'} as any);
  render(<AutomaticWorkflowPanel jobId="job"/>);
  fireEvent.click(await screen.findByRole('button',{name:'Process uploaded sources'}));
  await waitFor(()=>expect(api.startAutomaticRun).toHaveBeenCalledWith('job',false));
 });
});
