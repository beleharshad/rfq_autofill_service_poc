import { render, screen, fireEvent } from '@testing-library/react';
import { MemoryRouter, Routes, Route } from 'react-router-dom';
import { it, expect, vi } from 'vitest';
import JobPage from '../pages/JobPage';
vi.mock('../services/api', () => ({ api: { getJob: vi.fn(async () => ({job_id:'job',mode:'auto_convert',name:'Retainer',status:'CREATED'})) } }));
vi.mock('../components/AutomaticWorkflowPanel', () => ({default: () => <div>CAD workflow</div>}));
vi.mock('../components/AutoConvertResults/AutoConvertResults', () => ({default: () => <div>Legacy lathe preview</div>}));
vi.mock('../components/AcceptedPartPanel', () => ({default: () => <div>Geometry review</div>}));
vi.mock('../components/PartSpecPanel', () => ({default: () => null}));
vi.mock('../components/DocumentRegistryPanel', () => ({default: () => null}));
vi.mock('../components/ResultsView/ResultsView', () => ({default: () => null}));
vi.mock('../components/LogsView/LogsView', () => ({default: () => null}));

it('routes existing automatic jobs to CAD without a feature flag and keeps an explicit legacy fallback', async () => {
  vi.stubEnv('VITE_ENABLE_PART_SPEC','false');
  render(<MemoryRouter initialEntries={['/jobs/job']}><Routes><Route path="/jobs/:id" element={<JobPage/>}/></Routes></MemoryRouter>);
  expect(await screen.findByText('CAD workflow')).toBeInTheDocument();
  expect(screen.queryByText('Legacy lathe preview')).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button',{name:'Open legacy profile preview'}));
  expect(screen.getByText('Legacy lathe preview')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button',{name:'Open CAD workflow'}));
  expect(screen.getByText('CAD workflow')).toBeInTheDocument();
  vi.unstubAllEnvs();
});
