// Screenshots of the page in headless Chrome, for looking at it the way a
// visitor would. Names (SHOTS=a,b,... takes only those):
//
//   overview   the opening view: the mine in 3D across the window
//   faces      the pit up close: a shovel loading trucks
//   ramps      trucks on the ramp with the most switchbacks
//   ramptruck  a loaded truck climbing a ramp, up close
//   plant      the plant from the stockpile to the flotation
//   dark       the mine at dusk or at night, looking towards the sunset glow;
//              with WAIT_DARK=minutes the script first waits (up to that long)
//              until the simulated sun is below 4°
//   truck      the card of a truck on the road with a load, the camera on it
//   road       a road closure: barriers, sign and the scenario card
//   blast      a blast: the exclusion zone with its countdown
//   advisor    the advisor's card during a shovel failure
//   plan       the 2D plan
//   forecast   a scenario's 12-hour forecast in its card
//   help, about, grafana
//
// LIGHT_TIME=HH:MM opens the page with ?time=HH:MM: the scene is lit as at
// that hour (sun, sky, lamps), nothing else changes; the file names then
// carry "day" or "night" (by the hour) after the window size.
//
//   node mine/ui/tests/shots.mjs <output directory> [base URL] [sizes, e.g. 1280x720,1920x1080]
//
// Every scenario is started and cancelled through its card's own buttons with
// the card's defaults, and never left running.

import { mkdirSync } from 'node:fs';
import { join } from 'node:path';
import { launch, watchProblems, sleep, mineNamespace } from './cdp.mjs';

const out = process.argv[2];
const base = (process.argv[3] ?? 'http://localhost:8088').replace(/\/$/, '');
const sizes = (process.argv[4] ?? '1280x720,1920x1080').split(',').map((size) => size.split('x').map(Number));
if (!out) {
  console.error('usage: node shots.mjs <output directory> [base URL] [sizes]');
  process.exit(2);
}
mkdirSync(out, { recursive: true });

const NS = await mineNamespace(base);
const lightTime = /^\d{1,2}:\d{2}$/.test(process.env.LIGHT_TIME ?? '') ? process.env.LIGHT_TIME : null;
const lightTag = lightTime ? (Number(lightTime.split(':')[0]) >= 7 && Number(lightTime.split(':')[0]) < 17 ? 'day-' : 'night-') : '';
const only = (process.env.SHOTS ?? '').split(',').map((name) => name.trim()).filter(Boolean);
const want = (name) => !only.length || only.includes(name);
const started = new Set();
const featureOf = (name, feature) => fetch(`${base}/ditto/api/2/things/${NS}:${name}/features/${feature}/properties/value`)
  .then((r) => (r.ok ? r.json() : undefined)).catch(() => undefined);
const cardOf = (name) => `.scenario[data-id="${NS}:scenario-${name}"]`;
const statusOf = (name) => `document.querySelector('${cardOf(name)}')?.dataset.status`;

for (const [width, height] of sizes) {
  const page = await launch({ width, height });
  const problems = watchProblems(page);
  const shot = async (name, target = page) => {
    // The pointer rests on an empty part of the top bar: no hover on a button, no tooltip.
    if (target === page) await page.send('Input.dispatchMouseEvent', { type: 'mouseMoved', x: Math.round(width * 0.56), y: 6 });
    await sleep(150);
    const file = join(out, `${width}x${height}-${lightTag}${name}.png`);
    await target.screenshot(file);
    console.log(`saved ${file}`);
  };
  const openCard = async (selector) => {
    await page.evaluate(`(() => { const b = document.querySelector('${selector} button.expand'); if (b && b.getAttribute('aria-expanded') !== 'true') b.click(); })()`);
    await sleep(300);
  };
  const collapseCards = () => page.evaluate(`document.querySelectorAll('#control button.expand[aria-expanded="true"]').forEach((b) => b.click())`);
  const startScenario = async (name) => {
    const card = cardOf(name);
    await openCard(card);
    if (await page.evaluate(statusOf(name)) !== 'inactive') {
      await page.click(`${card} [data-action="cancel"]`).catch(() => {});
      await page.waitFor(`${statusOf(name)} === 'inactive'`, 120000, `scenario ${name} to end`).catch(() => {});
    }
    await page.waitFor(`!document.querySelector('${card} [data-action="activate"]').disabled`, 180000, `scenario ${name} to be startable`).catch(() => {});
    await page.click(`${card} [data-action="activate"]`);
    started.add(`scenario-${name}`);
    return page.waitFor(`${statusOf(name)} === 'active' && 'active'`, 30000, `scenario ${name} to start`).catch(() => 'not started');
  };
  const stopScenario = async (name) => {
    const card = cardOf(name);
    await openCard(card);
    if (await page.evaluate(`!document.querySelector('${card} [data-action="cancel"]').disabled`)) await page.click(`${card} [data-action="cancel"]`);
    await page.waitFor(`${statusOf(name)} === 'inactive'`, 150000, `scenario ${name} to end`).catch(() => {});
    if (await page.evaluate(`${statusOf(name)} === 'inactive'`)) started.delete(`scenario-${name}`);
    await collapseCards();
  };
  const select = async (name, wait = 3000) => { await page.evaluate(`location.hash = '#/twin/${name}'`); await sleep(wait); };
  const unselect = async () => { await page.evaluate('location.hash = ""'); await sleep(400); };
  try {
    await page.goto(`${base}/${lightTime ? `?time=${lightTime}` : ''}`);
    await page.waitFor('window.minePlan?.scene?.()?.firstFrameAt > 0 && document.querySelectorAll(".scenario").length > 0', 60000, 'the page to load');
    await page.waitFor('document.querySelectorAll("#journal li").length > 0', 15000, 'the journal').catch(() => {});
    await sleep(3500);
    if (want('overview')) await shot('01-overview');

    if (want('faces')) {
      // A shovel that has just started to load a truck, seen from the face.
      await page.waitFor(`(async () => {
        const s = await import('/js/store.js');
        return s.byKind('loader').some((l) => s.feature(l, 'state') === 'loading' && s.feature(l, 'passes') <= 1);
      })()`, 90000, 'a shovel loading').catch(() => {});
      await page.click('[data-camera="faces"]');
      await sleep(3000);
      await shot('02-faces');
    }
    if (want('ramps')) {
      await page.click('[data-camera="ramps"]');
      await sleep(3000);
      await shot('03-ramps');
    }
    if (want('ramptruck')) {
      // A loaded truck on a ramp, seen from close by as it climbs.
      const truck = await page.waitFor(`(async () => {
        const s = await import('/js/store.js');
        const ramp = (t) => s.twins.get(s.feature(t, 'segment'))?.attributes?.spec?.road_class === 'ramp';
        // Deep in the pit, moving: on the ramp itself, not queuing at its top.
        const rim = s.first('pit')?.attributes?.spec?.rim_elevation_m ?? 375;
        const climbing = (t) => ramp(t) && s.feature(t, 'pos_z') < rim - 30 && s.feature(t, 'speed_kmh') > 3;
        const found = s.byKind('truck').find((t) => s.feature(t, 'state') === 'hauling' && climbing(t))
          ?? s.byKind('truck').find((t) => climbing(t));
        return found ? found.thingId.split(':')[1] : false;
      })()`, 60000, 'a truck on a ramp').catch(() => null);
      if (truck) {
        // Flown to and followed, without opening its card.
        await page.evaluate(`window.minePlan.scene().reveal('${NS}:${truck}', true)`);
        await sleep(1500);
        await page.evaluate('window.minePlan.scene().zoomBy(1.6)');
        await sleep(2000);
        await shot('03b-ramp-truck');
        await page.click('[data-camera="overview"]');
        await sleep(1200);
      }
    }
    if (want('plant')) {
      await page.click('[data-camera="plant"]');
      await sleep(3000);
      await shot('04-plant');
    }
    if (want('dark')) {
      const limit = Number(process.env.WAIT_DARK ?? 0) * 60000;
      const deadline = Date.now() + limit;
      while (Date.now() < deadline && (await page.evaluate('window.minePlan.scene().probe().sun.elevation')) > 4) await sleep(20000);
      // Towards the west, where the sun has set, the pit in front.
      await page.click('[data-camera="overview"]');
      await sleep(1200);
      for (let i = 0; i < 16; i += 1) {
        await page.evaluate(`document.querySelector('#scene-gl').dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowLeft', bubbles: true }))`);
      }
      await page.evaluate(`document.querySelector('#scene-gl').dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowDown', bubbles: true }))`);
      await sleep(2500);
      const sun = await page.evaluate('window.minePlan.scene().probe().sun');
      console.log(`${width}x${height}: dark view at sun ${sun.elevation.toFixed(1)}°, night ${sun.night.toFixed(2)}`);
      await shot('05-dark');
      await page.click('[data-camera="overview"]');
      await sleep(1500);
    }

    if (want('truck')) {
      // A truck that is on the road with a load shows the most links: its route, shovel and dump.
      const truck = await page.evaluate(`(() => {
        const rows = [...document.querySelectorAll('#twin-list a.row[data-kind="truck"]')];
        const row = rows.find((r) => r.textContent.includes('везёт груз')) ?? rows[0];
        return row.dataset.id.split(':')[1];
      })()`);
      await select(truck, 3500);
      await page.waitFor('document.querySelectorAll("#details svg.spark").length > 0', 15000, 'history sparklines').catch(() => {});
      await sleep(1000);
      await shot('09-truck-card');
      await unselect();
      await page.click('[data-camera="overview"]');
      await sleep(1500);
    }

    if (want('road')) {
      await startScenario('road-closure');
      await sleep(8000);
      const target = String(await featureOf('scenario-road-closure', 'target') ?? '').split(':')[1];
      if (target) await select(target, 3500);
      await page.evaluate('document.querySelector("#details a.close")?.click()');
      await sleep(2500);
      await page.evaluate(`document.querySelector('${cardOf('road-closure')}').scrollIntoView({ block: 'nearest' })`);
      await sleep(500);
      await shot('06-road-closure');
      await stopScenario('road-closure');
      await page.click('[data-camera="overview"]');
      await sleep(1000);
    }

    if (want('blast')) {
      for (let i = 0; i < 60 && await featureOf('pit', 'blast_state') !== 'idle'; i += 1) await sleep(2000);
      await startScenario('blast');
      for (let i = 0; i < 30 && await featureOf('pit', 'blast_state') !== 'clearing'; i += 1) await sleep(1000);
      const block = String(await featureOf('pit', 'blast_block') ?? '').split(':')[1];
      if (block) await select(block, 3500);
      await page.evaluate('document.querySelector("#details a.close")?.click()');
      await sleep(2500);
      await shot('07-blast');
      await stopScenario('blast');
      await page.click('[data-camera="overview"]');
      await sleep(1000);
    }

    if (want('advisor')) {
      await startScenario('shovel-failure');
      await page.waitFor(`document.querySelector('#advisor .advisor[data-status="ready"]') && document.querySelector('#advisor .adv-option[data-recommended="true"]')`,
        180000, 'the advisor to rank its options').catch(() => {});
      const target = String(await featureOf('scenario-shovel-failure', 'target') ?? '').split(':')[1];
      await collapseCards();
      if (target) await select(target, 3500);
      await page.evaluate('document.querySelector("#details a.close")?.click()');
      await page.evaluate('document.querySelector("#control").scrollTop = 0');
      await sleep(2500);
      await shot('08-advisor-shovel-failure');
      await stopScenario('shovel-failure');
      await page.click('[data-camera="overview"]');
      await sleep(1000);
    }

    if (want('forecast') && await featureOf('forecast-1', 'status') !== undefined) {
      const card = cardOf('road-closure');
      await openCard(card);
      await page.click(`${card} [data-action="forecast"]`);
      await page.waitFor(`document.querySelector('${card} .forecast')?.dataset.state === 'ready'`, 90000, 'the scenario forecast').catch(() => {});
      await sleep(1200);
      await page.evaluate(`document.querySelector('${card} .forecast').scrollIntoView({ block: 'start' })`);
      await sleep(600);
      await shot('11-forecast');
      if (await page.evaluate('Boolean(document.querySelector(".forecast .fc-close"))')) await page.click('.forecast .fc-close');
      await collapseCards();
    }

    if (want('plan')) {
      await page.click('[data-mode="plan"]');
      await sleep(2500);
      await shot('10-plan');
      await page.click('[data-mode="3d"]');
      await sleep(1500);
    }

    if (want('help')) {
      await page.click('#help-open');
      await sleep(600);
      await shot('12-help');
      await page.click('#help-close');
    }
    if (want('about')) {
      await page.click('#about-open');
      await sleep(700);
      await shot('13-about');
      await page.click('#about-close');
    }
    if (want('grafana')) {
      const link = await page.evaluate('document.querySelector(".journal a.head-link").getAttribute("href")');
      const viewer = await launch({ width, height });
      try {
        await viewer.goto(`${base}${link}`);
        await viewer.waitFor('document.querySelectorAll(".react-grid-item").length >= 6', 60000, 'the Grafana dashboard').catch(() => {});
        await sleep(8000);
        await shot('14-grafana', viewer);
      } finally {
        await viewer.close();
      }
    }
  } catch (error) {
    console.error(`${width}x${height}: ${error.message}`);
    process.exitCode = 1;
  } finally {
    if (problems.length) console.log(`${width}x${height}: problems seen:\n  ${problems.join('\n  ')}`);
    await page.close();
    for (const name of started) {
      if (await featureOf(name, 'status') === 'active') {
        await fetch(`${base}/ditto/api/2/things/${NS}:${name}/inbox/messages/cancel?timeout=0`, {
          method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' }).catch(() => {});
      }
    }
    started.clear();
  }
}
