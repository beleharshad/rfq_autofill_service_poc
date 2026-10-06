const { test } = require('node:test');
const assert = require('node:assert/strict');
const { repair } = require('./fix-lathe-controls.cjs');

test('removes the reported inline hook while preserving camera driver and local edits', () => {
  const source = `function CameraDriver() { const { camera, controls } = useThree(); }
export default function LatheViewer() {
  return <Canvas>
    <CameraDriver />
    {(() => {
      const { camera } = useThree();
      if (!camera) return null;
      return (
        <ErrorBoundary>
          <OrbitControls makeDefault enablePan enableDamping dampingFactor={0.06}
            minDistance={size * 0.05} maxDistance={size * 12} />
        </ErrorBoundary>
      );
    })()}
    <GizmoHelper />
  </Canvas>;
}`;
  const fixed = repair(source);
  assert.match(fixed, /function CameraDriver\(\) \{ const \{ camera, controls \} = useThree\(\); \}/);
  assert.doesNotMatch(fixed.split('export default')[1], /useThree/);
  assert.match(fixed, /<GizmoHelper \/>/);
  assert.match(fixed, /minDistance=\{size \* 0.05\}/);
  assert.equal(repair(fixed), fixed);
  assert.equal(repair(source.replace(/\n/g, '\r\n')).replace(/\r\n/g, '\n'), fixed);
});

test('refuses an unfamiliar hook wrapper without rewriting it', () => {
  assert.throws(() => repair('export default function LatheViewer() { useThree(); }'), /no file changed/);
});
