// Screenshots of the model preview page in headless Chrome (--disable-gpu,
// WebGL in software), each model close up and then the lineups; fails on any
// console error, exception or failed request.
//
//   node tools/blender/shots.mjs <output directory> [base URL] [id,id,...]
//
// The base URL serves mine/ui/site/ (e.g. `python3 -m http.server 8765` in that
// directory gives http://localhost:8765, the default).

import { mkdirSync } from 'node:fs';
import { join } from 'node:path';
import { launch, watchProblems, sleep } from '../../mine/ui/tests/cdp.mjs';

const [out, base = 'http://localhost:8765', filter] = process.argv.slice(2);
if (!out) {
  console.error('usage: node shots.mjs <output directory> [base URL] [ids]');
  process.exit(2);
}
mkdirSync(out, { recursive: true });
const manifest = await fetch(`${base}/models/models.json`).then((r) => r.json());
const wanted = filter ? filter.split(',') : null;
const shots = [];
for (const m of manifest.models) {
  if (wanted && !wanted.includes(m.id)) continue;
  shots.push([`${m.id}-lod0`, `model=${m.id}&lod=0&spin=0`]);
  if (m.kind === 'machine') {
    shots.push([`${m.id}-lod0-rear`, `model=${m.id}&lod=0&spin=0&yaw=35&pitch=14`]);
    shots.push([`${m.id}-work`, `model=${m.id}&lod=0&spin=0&pose=work&yaw=110`]);
    shots.push([`${m.id}-lods`, `model=${m.id}&compare=1&spin=0&yaw=215`]);
  } else {
    shots.push([`${m.id}-lod0-b`, `model=${m.id}&lod=0&spin=0&yaw=30&pitch=30`]);
    if (m.id === 'mill_hall') shots.push([`${m.id}-inside`, `model=${m.id}&lod=0&spin=0&yaw=200&pitch=64&hide=roof`]);
  }
}
if (!wanted) {
  shots.push(['lineup-machines', 'lineup=machines&spin=0&yaw=205&pitch=12']);
  shots.push(['lineup-plant', 'lineup=plant&spin=0&yaw=210&pitch=24']);
  shots.push(['lineup-all', 'lineup=all&spin=0&yaw=205&pitch=22']);
}

const page = await launch({ width: 1280, height: 720 });
const problems = watchProblems(page);
let failed = false;
try {
  for (const [name, query] of shots) {
    const before = problems.length;
    const t0 = Date.now();
    await page.goto(`${base}/models/preview.html?${query}`);
    await page.waitFor('window.__ready === true || (window.__errors && window.__errors.length > 0)', 60000, `${name} ready`);
    await sleep(400);
    await page.screenshot(join(out, `${name}.png`));
    const errors = await page.evaluate('window.__errors');
    console.log(`${name}: ${Date.now() - t0} ms${errors.length ? ' errors: ' + errors.join('; ') : ''}`);
    if (errors.length || problems.length > before) failed = true;
  }
} finally {
  await page.close();
}
if (problems.length) console.log(problems.join('\n'));
console.log(failed ? 'FAILED' : 'OK: no console errors');
process.exit(failed ? 1 : 0);
