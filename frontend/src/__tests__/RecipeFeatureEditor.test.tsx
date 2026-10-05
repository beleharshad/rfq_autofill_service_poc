import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import RecipeFeatureEditor from '../components/RecipeFeatureEditor';

describe('RecipeFeatureEditor',()=>{
  it('constructs a four-hole bolt circle with exact placement and independent feature IDs',()=>{
    const add=vi.fn();
    render(<RecipeFeatureEditor onAdd={add}/>);
    fireEvent.change(screen.getByLabelText('Pattern quantity'),{target:{value:'4'}});
    fireEvent.change(screen.getByLabelText('XY bolt circle diameter'),{target:{value:'20'}});
    fireEvent.click(screen.getByRole('button',{name:'Add feature(s)'}));
    const features=add.mock.calls[0][0];
    expect(features).toHaveLength(4);
    expect(new Set(features.map((f:any)=>f.feature_id)).size).toBe(4);
    expect(features[0].origin).toEqual([10,0,0]);
    expect(features[1].origin[0]).toBeCloseTo(0);
    expect(features[1].origin[1]).toBeCloseTo(10);
    expect(features.every((f:any)=>f.termination==='through')).toBe(true);
  });
  it('rejects non-integer pattern quantities before adding geometry',()=>{
    const add=vi.fn();render(<RecipeFeatureEditor onAdd={add}/>);
    fireEvent.change(screen.getByLabelText('Pattern quantity'),{target:{value:'2.5'}});
    fireEvent.click(screen.getByRole('button',{name:'Add feature(s)'}));
    expect(screen.getByRole('alert')).toHaveTextContent('integer');expect(add).not.toHaveBeenCalled();
  });
});
