// Screenshots of the clay-ore side of the mine in the 3D view, in each look
// («Голограмма», «Реалистичный») and window size:
//
//   clay-plant      the clay-ore plant ОФ-2 with its desliming and flotation (panels in the hologram)
//   clay-stockpile  the clay-ore stockpile beside it
//   layer-clay      the blocks coloured by their clay content («Глина»), on the 3D view and on the plan
//   clay-face       the scenario «Глинистая руда в забое» started from its card, with the advisor's card
//                   once it has ranked its options; the scenario is cancelled afterwards
//
//   node mine/ui/tests/clay_shots.mjs <output directory> [base URL] [sizes] [styles]
//
// SHOTS=a,b takes only those. Fails (exit 1) on a console error or a failed request.

import { mkdirSync } from 'node:fs';
import { join } from 'node:path';
import { launch, watchProblems, sleep, mineNamespace } from './cdp.mjs';

const out = process.argv[2];
const base = (process.argv[3] ?? 'http://localhost:8088').replace(/\/$/, '');
const sizes = (process.argv[4] ?? '1280x720,1920x1080').split(',').map((size) => size.split('x').map(Number));
const styles = (process.argv[5] ?? 'hologram,realistic').split(',');
if (!out) {
  console.error('usage: node clay_shots.mjs <output directory> [base URL] [sizes] [styles]');
  process.exit(2);
}
mkdirSync(out, { recursive: true });
const NS = await mineNamespace(base);
const only = (process.env.SHOTS ?? '').split(',').map((name) => name.trim()).filter(Boolean);
const want = (name) => !only.length || only.includes(name);
const featureOf = (name, feature) => fetch(`${base}/ditto/api/2/things/${NS}:${name}/features/${feature}/properties/value`)
  .then((r) => (r.ok ? r.json() : undefined)).catch(() => undefined);
let failed = 0;
const scene = 'window.minePlan.scene()';

for (const style of styles) {
  for (const [width, height] of sizes) {
    const page = await launch({ width, height });
    const problems = watchProblems(page);
    const shot = async (name) => {
      await page.send('Input.dispatchMouseEvent', { type: 'mouseMoved', x: Math.round(width * 0.56), y: 6 });
      await sleep(200);
      const file = join(out, `${width}x${height}-${style}-${name}.png`);
      await page.screenshot(file);
      console.log(`saved ${file}`);
    };
    const where = (kind) => page.evaluate(`(async () => { const s = await import('/js/store.js'); const t = s.first('${kind}'); return t ? ${scene}.where(t.thingId) : null; })()`);
    const card = `.scenario[data-id="${NS}:scenario-clay-face"]`;
    let started = false;
    try {
      await page.goto(`${base}/?style=${style}`);
      await page.waitFor(`${scene}?.firstFrameAt`, 60000, 'the first frame');
      await page.waitFor(`['ready', 'failed'].includes(${scene}.models().state)`, 60000, 'the models');
      await page.evaluate('document.querySelectorAll("#control button.expand[aria-expanded=\\"true\\"]").forEach((b) => b.click())');
      await sleep(1500);
      if (want('clay-plant')) {
        const at = await where('clay-plant');
        if (at) {
          await page.evaluate(`${scene}.look({ x: ${at.x}, y: ${at.y}, z: ${at.base + 8}, distance: 300, azimuth: 0.35, elevation: 0.5 })`);
          await sleep(3000);
          await shot('clay-plant');
        } else console.log('no clay-ore plant on the stand');
      }
      if (want('clay-stockpile')) {
        const at = await where('clay-stockpile');
        if (at) {
          await page.evaluate(`${scene}.look({ x: ${at.x}, y: ${at.y}, z: ${at.base}, distance: 900, azimuth: 0.35, elevation: 0.55 })`);
          await sleep(3000);
          await shot('clay-stockpile');
        } else console.log('no clay-ore stockpile on the stand');
      }
      if (want('layer-clay')) {
        await page.click('#layers-toggle');
        await sleep(400);
        await page.click('#layer-buttons [data-layer="clay"]');
        await page.click('#layers-close');
        // The pit from above, closer than the overview, so that the colour of each block reads.
        await page.evaluate(`${scene}.preset('overview')`);
        await sleep(1500);
        await page.evaluate(`(() => { const c = ${scene}.probe().camera; ${scene}.look({ x: c.x, y: c.y, z: c.z, distance: c.distance * 0.55, azimuth: c.azimuth, elevation: 0.85 }); })()`);
        await sleep(3500);
        await shot('layer-clay');
        await page.click('[data-mode="plan"]');
        await sleep(800);
        await page.click('[data-camera="faces"]');
        await sleep(2500);
        await shot('layer-clay-plan');
        await page.click('[data-mode="3d"]');
        await page.click('#layers-toggle');
        await sleep(300);
        await page.click('#layer-buttons [data-layer="none"]');
        await page.click('#layers-close');
        await sleep(1000);
      }
      if (want('clay-face') && await featureOf('scenario-clay-face', 'status') === 'inactive') {
        await page.evaluate(`document.querySelector('${card} button.expand')?.click()`);
        await sleep(400);
        await page.click(`${card} [data-action="activate"]`);
        started = true;
        await page.waitFor(`document.querySelector('${card}')?.dataset.status === 'active'`, 30000, 'the scenario to start').catch(() => {});
        const target = await featureOf('scenario-clay-face', 'target');
        await page.waitFor('document.querySelector("#advisor .advisor[data-status=\\"ready\\"]") && document.querySelector("#advisor .adv-option")', 150000, 'the advisor').catch(() => {});
        if (target) await page.evaluate(`location.hash = '#/twin/${String(target).split(':')[1]}'`);
        await sleep(3500);
        await page.evaluate(`document.querySelector('${card} button.expand[aria-expanded="true"]')?.click()`);
        await sleep(500);
        await shot('clay-face');
        await page.evaluate("location.hash = ''");
        await page.evaluate(`document.querySelector('${card} button.expand[aria-expanded="false"]')?.click()`);
        await sleep(300);
        await page.click(`${card} [data-action="cancel"]`).catch(() => {});
        await page.waitFor(`document.querySelector('${card}')?.dataset.status === 'inactive'`, 90000, 'the scenario to end').catch(() => {});
      } else if (want('clay-face')) console.log('the clay-face scenario is running already: no «clay-face» shot');
      if (problems.length) {
        failed += 1;
        console.log(`problems:\n  ${problems.join('\n  ')}`);
      }
    } catch (error) {
      failed += 1;
      console.log(`stopped: ${error.message}`);
    } finally {
      if (started && await featureOf('scenario-clay-face', 'status') === 'active') {
        await fetch(`${base}/ditto/api/2/things/${NS}:scenario-clay-face/inbox/messages/cancel?timeout=0`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' }).catch(() => {});
      }
      await page.close();
    }
  }
}
process.exit(failed ? 1 : 0);
