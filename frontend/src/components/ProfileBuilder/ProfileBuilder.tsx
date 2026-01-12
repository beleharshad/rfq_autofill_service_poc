import { useState } from 'react';
import SegmentStackInput from './SegmentStackInput';
import Profile2DInput from './Profile2DInput';
import './ProfileBuilder.css';

interface ProfileBuilderProps {
  jobId: string;
}

type InputMode = 'stack' | 'profile2d';

function ProfileBuilder({ jobId }: ProfileBuilderProps) {
  const [mode, setMode] = useState<InputMode>('stack');

  return (
    <div className="profile-builder">
      <div className="profile-builder-header">
        <h2>Manual Profile Builder</h2>
        <div className="mode-selector">
          <button
            className={mode === 'stack' ? 'active' : ''}
            onClick={() => setMode('stack')}
          >
            Segment Stack (Math-only)
          </button>
          <button
            className={mode === 'profile2d' ? 'active' : ''}
            onClick={() => setMode('profile2d')}
          >
            Profile2D (OCC Solid)
          </button>
        </div>
      </div>

      <div className="profile-builder-content">
        {mode === 'stack' && <SegmentStackInput jobId={jobId} />}
        {mode === 'profile2d' && <Profile2DInput jobId={jobId} />}
      </div>
    </div>
  );
}

export default ProfileBuilder;

