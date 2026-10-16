// Screenshots of the Blender models in the 3D view, for looking at them the
// way a visitor would: in each look («Голограмма», «Реалистичный») and each
// window size,
//
//   overview  the opening view
//   faces     a shovel loading a truck, mid-cycle (the «Забои» view)
//   dump      a truck tipping into the crusher's dump pocket
//   drill     a drill with its mast up
//   plant     the plant (the «Фабрика» view)
//   mill      the mill hall close up, its roof lifted off over the mills
//   clay      the clay-ore plant (ОФ-2), when its twins are on the stand
//
//   node mine/ui/tests/models_shots.mjs <output directory> [base URL] [sizes] [styles]
//
// e.g. node mine/ui/tests/models_shots.mjs /tmp/shots http://localhost:8088 1280x720,1920x1080 hologram,realistic
// SHOTS=a,b takes only those. Fails (exit 1) on a console error or a failed request.

import { mkdirSync } from 'node:fs';
import { join } from 'node:path';
import { launch, watchProblems, sleep } from './cdp.mjs';

const out = process.argv[2];
const base = (process.argv[3] ?? 'http://localhost:8088').replace(/\/$/, '');
const sizes = (process.argv[4] ?? '1280x720,1920x1080').split(',').map((size) => size.split('x').map(Number));
const styles = (process.argv[5] ?? 'hologram,realistic').split(',');
if (!out) {
  console.error('usage: node models_shots.mjs <output directory> [base URL] [sizes] [styles]');
  process.exit(2);
}
mkdirSync(out, { recursive: true });
const only = (process.env.SHOTS ?? '').split(',').map((name) => name.trim()).filter(Boolean);
const want = (name) => !only.length || only.includes(name);
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
    // The first twin of a kind whose state is one of `states` (and passes `extra`), waiting up to `ms`.
    const findTwin = (kind, states, ms, extra = 'true') => page.waitFor(`(async () => {
      const s = await import('/js/store.js');
      const sc = ${scene};
      for (const twin of s.byKind('${kind}')) {
        const where = sc.where(twin.thingId);
        if (${JSON.stringify(states)}.includes(s.feature(twin, 'state')) && where && (${extra})) return { id: twin.thingId, where };
      }
      return null;
    })()`, ms, `a ${kind} in ${states.join('/')}`).catch(() => null);
    try {
      await page.goto(`${base}/?style=${style}`);
      await page.waitFor(`${scene}?.firstFrameAt`, 60000, 'the first frame');
      const models = await page.waitFor(`(() => { const m = ${scene}.models(); return ['ready', 'failed'].includes(m.state) && m; })()`, 60000, 'the models');
      console.log(`${style} ${width}x${height}: models ${models.state} in ${models.loadMs} ms: ${models.loaded.join(', ')}`);
      await page.evaluate('document.querySelectorAll("#control button.expand[aria-expanded=\\"true\\"]").forEach((b) => b.click())');
      await sleep(1500);
      if (want('overview')) {
        await page.evaluate(`${scene}.preset('overview')`);
        await sleep(3000);
        await shot('overview');
      }
      if (want('faces')) {
        await page.evaluate(`${scene}.preset('faces')`);
        await sleep(3000);
        await shot('faces');
      }
      if (want('dump')) {
        // A truck in a bay of the crusher's dump pocket with its body up (the tipping lasts a few
        // seconds at the model's speed: the camera is put there as it starts).
        let done = false;
        const until = Date.now() + 300000;
        const crusher = await page.evaluate(`(async () => { const s = await import('/js/store.js'); return ${scene}.where(s.first('crusher').thingId); })()`);
        while (!done && Date.now() < until) {
          const truck = await findTwin('truck', ['dumping'], Math.max(1000, until - Date.now()), 'where.bay');
          if (!truck) break;
          const vx = truck.where.x - crusher.x; const vy = truck.where.y - crusher.y;
          const azimuth = Math.atan2(vx, -vy) + 0.95;
          await page.evaluate(`${scene}.look({ x: ${truck.where.x}, y: ${truck.where.y}, z: ${truck.where.z + 4}, distance: 75, azimuth: ${azimuth}, elevation: 0.32 })`);
          for (let i = 0; i < 12 && !done; i += 1) {
            await sleep(150);
            const pose = await page.evaluate(`${scene}.pose('${truck.id}')`);
            if (pose && pose.tilt > 0.45) {
              await shot('dump');
              done = true;
            }
          }
        }
        if (!done) console.log('no truck tipped at the crusher in 5 minutes: no «dump» shot');
      }
      if (want('drill')) {
        const rig = await findTwin('drill-rig', ['drilling'], 60000);
        if (rig) {
          await page.evaluate(`${scene}.look({ x: ${rig.where.x}, y: ${rig.where.y}, z: ${rig.where.z + 9}, distance: 62, azimuth: ${scene}.probe().camera.azimuth, elevation: 0.22 })`);
          await sleep(2500);
          await shot('drill');
        } else console.log('no drill drilling: no «drill» shot');
      }
      if (want('plant')) {
        await page.evaluate(`${scene}.preset('plant')`);
        await sleep(3500);
        await shot('plant');
      }
      if (want('mill')) {
        const at = await page.evaluate(`(async () => { const s = await import('/js/store.js'); return ${scene}.where((s.first('sag-mill') ?? s.first('mill')).thingId); })()`);
        if (at) {
          await page.evaluate(`${scene}.look({ x: ${at.x}, y: ${at.y}, z: ${at.base + 8}, distance: 230, azimuth: ${scene}.probe().camera.azimuth, elevation: 0.62 })`);
          await sleep(3000);
          await shot('mill');
        }
      }
      if (want('clay')) {
        const at = await page.evaluate(`(async () => { const s = await import('/js/store.js'); const t = s.first('clay-plant'); return t ? ${scene}.where(t.thingId) : null; })()`);
        if (at) {
          await page.evaluate(`${scene}.look({ x: ${at.x}, y: ${at.y}, z: ${at.base + 8}, distance: 330, azimuth: ${scene}.probe().camera.azimuth, elevation: 0.5 })`);
          await sleep(3000);
          await shot('clay');
        } else console.log('no clay-ore plant twin on the stand: no «clay» shot');
      }
      if (problems.length) {
        failed += 1;
        console.log(`problems:\n  ${problems.join('\n  ')}`);
      }
    } catch (error) {
      failed += 1;
      console.log(`stopped: ${error.message}`);
    } finally {
      await page.close();
    }
  }
}
process.exit(failed ? 1 : 0);
