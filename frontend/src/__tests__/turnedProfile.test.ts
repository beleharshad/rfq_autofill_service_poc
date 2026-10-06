import { describe, it, expect } from 'vitest';
import { LatheGeometry, Mesh, MeshBasicMaterial, DoubleSide, Raycaster, Vector3 } from 'three';
import { profilePoints, resizeProfile, validProfile } from '../components/AutoConvertResults/turnedProfile';

const shaft = [
  { z_start: 0, z_end: 1.25, od_diameter: .996, id_diameter: 0 },
  { z_start: 1.25, z_end: 3.63, od_diameter: 2.25, id_diameter: 0 },
  { z_start: 3.63, z_end: 4.88, od_diameter: .996, id_diameter: 0 },
];
describe('turned profile geometry', () => {
  it('renders both reduced ends and a solid end face', () => {
    const geo = new LatheGeometry(profilePoints(shaft), 128);
    const mesh = new Mesh(geo, new MeshBasicMaterial({ side: DoubleSide }));
    mesh.updateMatrixWorld();
    for (const [y, radius] of [[-2, .498], [0, 1.125], [2, .498]]) {
      const hits = new Raycaster(new Vector3(3, y, 0), new Vector3(-1, 0, 0)).intersectObject(mesh);
      expect(hits[0].point.x).toBeCloseTo(radius, 3);
    }
    const end = new Raycaster(new Vector3(.1, -4, 0), new Vector3(0, 1, 0)).intersectObject(mesh);
    expect(end[0].point.y).toBeCloseTo(-2.44, 3);
    geo.dispose();
  });
  it('preserves shoulders when overall dimensions change', () => {
    const scaled = resizeProfile(shaft, 4.5, 9.76);
    expect(scaled).toHaveLength(3);
    expect(scaled[0].z_end).toBe(2.5);
    expect(scaled[1].od_diameter).toBe(4.5);
    expect(scaled.every(s => s.id_diameter === 0)).toBe(true);
  });
  it('retains a blind bore floor instead of cutting through the whole length', () => {
    const blind = [{ z_start: 0, z_end: 1, od_diameter: 2, id_diameter: 1 },
      { z_start: 1, z_end: 2, od_diameter: 2, id_diameter: 0 }];
    const mesh = new Mesh(new LatheGeometry(profilePoints(blind), 64), new MeshBasicMaterial({ side: DoubleSide }));
    mesh.updateMatrixWorld();
    const hits = new Raycaster(new Vector3(.1, -3, 0), new Vector3(0, 1, 0)).intersectObject(mesh);
    expect(hits[0].point.y).toBeCloseTo(0, 5);
  });
  it('rejects missing, intersecting and disconnected profiles', () => {
    expect(validProfile([])).toEqual([]);
    expect(validProfile([{ ...shaft[0], id_diameter: 2 }])).toEqual([]);
    expect(validProfile([shaft[0], shaft[2]])).toEqual([]);
  });
});
