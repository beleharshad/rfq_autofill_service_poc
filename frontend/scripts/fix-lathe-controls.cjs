// Run from any directory: node frontend/scripts/fix-lathe-controls.cjs
// Repairs the specific inline hook wrapper reported in LatheViewer.
const fs = require('node:fs');
const path = require('node:path');
const file = path.resolve(__dirname, '../src/components/AutoConvertResults/LatheViewer.tsx');

function repair(source) {
  const wrapper = /\{\(\(\)\s*=>\s*\{\s*const\s*\{\s*camera\s*\}\s*=\s*useThree\(\);\s*if\s*\(!camera\)\s*return\s*null;\s*return\s*\(\s*(?:<ErrorBoundary>\s*)?(<OrbitControls\b[^>]*\/>)(?:\s*<\/ErrorBoundary>)?\s*\);?\s*\}\)\(\)\}/g;
  const matches = [...source.matchAll(wrapper)];
  if (matches.length > 1) throw new Error('Multiple matching wrappers found; no file changed.');
  if (matches.length === 1) return source.replace(wrapper, '$1');
  const parent = source.split('export default function LatheViewer')[1];
  if (parent && !/\buseThree\s*\(/.test(parent) && /<OrbitControls\b/.test(parent)) return source;
  throw new Error('Source differs from the reported wrapper; no file changed.');
}

if (require.main === module) {
  try {
    const source = fs.readFileSync(file, 'utf8');
    const fixed = repair(source);
    if (fixed === source) {
      console.log('LatheViewer already uses OrbitControls without an inline useThree hook.');
    } else {
      const backup = file + '.before-controls-fix-' + Date.now() + '.bak';
      fs.copyFileSync(file, backup, fs.constants.COPYFILE_EXCL);
      fs.writeFileSync(file, fixed, 'utf8');
      console.log('Fixed: ' + file);
      console.log('Original saved: ' + backup);
    }
  } catch (error) {
    console.error(error.message);
    process.exitCode = 1;
  }
}
module.exports = { repair };
