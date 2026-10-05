import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import AcceptedPartPanel from '../components/AcceptedPartPanel';
import { api } from '../services/api';

vi.mock('../services/api', () => ({ api: {
  getDocuments:vi.fn(), getAcceptedParts:vi.fn(), getPartSpec:vi.fn(), acceptPart:vi.fn(),
  buildAcceptedPart:vi.fn(), getGeometryBuild:vi.fn(), getGeometryArtifact:vi.fn(), cancelGeometryBuild:vi.fn(),
  getSourceOriginal:vi.fn(),getGeometryBuilds:vi.fn(),
  analyzeRegisteredSource:vi.fn(),
} }));
vi.mock('../components/AcceptedGeometryViewer',()=>({default:()=> <div>Actual solid viewer</div>}));
const doc='a'.repeat(64);
const documents={version:2,documents:[{document_id:doc,path:'inputs/part.pdf',original_name:'part.pdf',association:{part_number:'PART',revision:'A',manufacturing_state:'finished',role:'finished_drawing'}}]};

describe('AcceptedPartPanel',()=>{
  beforeEach(()=>{
    vi.resetAllMocks();
    vi.mocked(api.getDocuments).mockResolvedValue(documents as any);
    vi.mocked(api.getAcceptedParts).mockResolvedValue([]);
    vi.mocked(api.getPartSpec).mockResolvedValue({dimensions:[{name:'maximum_outer_diameter',value:20},{name:'axial_length',value:10}]} as any);
    vi.mocked(api.getSourceOriginal).mockResolvedValue(new Blob(['pdf']));
    URL.createObjectURL=vi.fn(()=> 'blob:source');URL.revokeObjectURL=vi.fn();
  });
  async function review(){
    fireEvent.click(screen.getByRole('button',{name:'Load sources and current results'}));
    await screen.findByLabelText('Governing source');
    fireEvent.change(screen.getByLabelText('Governing source'),{target:{value:doc}});
    fireEvent.change(screen.getByLabelText('Diameter'),{target:{value:'20'}});
    fireEvent.change(screen.getByLabelText('Z length'),{target:{value:'10'}});
    fireEvent.change(screen.getByLabelText('Dimension / view references'),{target:{value:'Section A-A'}});
    fireEvent.change(screen.getByLabelText('Review note'),{target:{value:'All dimensions checked'}});
  }
  it('connects accepted source identity, construction and measured exports',async()=>{
    vi.mocked(api.acceptPart).mockResolvedValue({spec_id:'spec',version:1} as any);
    vi.mocked(api.buildAcceptedPart).mockResolvedValue({build_id:'build',status:'complete',stale:false,manifest:{completeness:'complete',warnings:[],measurements:{envelope_width_mm:20,envelope_height_mm:20,axial_length_mm:10,maximum_outer_cylindrical_diameter_mm:20}}} as any);
    render(<AcceptedPartPanel jobId="job"/>);
    await review();
    fireEvent.click(screen.getByRole('button',{name:'Accept specification / save correction'}));
    await screen.findByRole('button',{name:'Build actual geometry'});
    expect(api.acceptPart).toHaveBeenCalledWith('job',expect.objectContaining({part_number:'PART',revision:'A',governing_document_id:doc,base:{kind:'cylinder',diameter:20,length:10}}),0,2);
    fireEvent.click(screen.getByRole('button',{name:'Build actual geometry'}));
    await screen.findByText('Actual solid viewer');
    expect(screen.getByRole('button',{name:'Download rfq_review.xlsx'})).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('Z length'),{target:{value:'15'}});
    await waitFor(()=>expect(screen.queryByText('Actual solid viewer')).not.toBeInTheDocument());
    expect(screen.queryByRole('button',{name:'Build actual geometry'})).not.toBeInTheDocument();
  });
  it('keeps stale builds out of viewer and export controls',async()=>{
    vi.mocked(api.acceptPart).mockResolvedValue({spec_id:'spec',version:1} as any);
    vi.mocked(api.buildAcceptedPart).mockResolvedValue({build_id:'build',status:'complete',stale:true,manifest:null} as any);
    render(<AcceptedPartPanel jobId="job"/>);
    await review();
    fireEvent.click(screen.getByRole('button',{name:'Accept specification / save correction'}));
    fireEvent.click(await screen.findByRole('button',{name:'Build actual geometry'}));
    await screen.findByText(/Build: stale/);
    expect(screen.queryByText('Actual solid viewer')).not.toBeInTheDocument();
    expect(screen.queryByRole('button',{name:'Download model.step'})).not.toBeInTheDocument();
  });
  it('uses source-specific candidates without automatically accepting them',async()=>{
    vi.mocked(api.analyzeRegisteredSource).mockResolvedValue({document_id:doc,candidates:[{name:'maximum_outer_diameter',value_mm:50.8}],limitations:['Candidate extraction only']} as any);
    render(<AcceptedPartPanel jobId="job"/>);
    fireEvent.click(screen.getByRole('button',{name:'Load sources and current results'}));
    await screen.findByLabelText('Governing source');
    expect(screen.getByLabelText('Diameter')).toHaveValue(null);
    fireEvent.change(screen.getByLabelText('Governing source'),{target:{value:doc}});
    fireEvent.click(screen.getByRole('button',{name:'Extract candidates from this source'}));
    await screen.findByText('Candidate extraction only');
    expect(api.analyzeRegisteredSource).toHaveBeenCalledWith('job',doc);
    expect(screen.getByLabelText('Diameter')).toHaveValue(50.8);
    expect(api.acceptPart).not.toHaveBeenCalled();
  });
});
