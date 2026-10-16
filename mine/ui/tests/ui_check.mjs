// End-to-end check of the page against a running stand, in headless Chrome
// rendering in software (WebGL on Chrome's software renderer):
//
//   1. the page opens on the 3D view across the whole window, the first frame
//      of the mine is drawn within 6 s, nothing scrolls; ?view=plan opens the
//      plan and ?view=3d the 3D view;
//   2. the 3D scene is drawn and not empty, holds every truck, shovel and
//      drill of the twins, the trucks move, the points of view (Обзор, Забои,
//      Съезды, Фабрика) move the camera, the mouse and the keys turn it, its
//      captions never overlap, it reports the sun of the simulated time, and
//      it keeps at least 15 frames a second while the camera moves; with
//      nothing changing it draws nothing;
//   3. the ribbon of figures: six of them, each opens its detail with the
//      process chain, and the chain shows the same rates as the figures;
//   4. the object list is a drawer, closed by default; it holds every twin of
//      the Ditto search, the search field filters it, choosing a row closes it,
//      opens the object's card and flies the camera to the object;
//   5. the road closure started and cancelled with the page's buttons changes
//      the scenario twin, the closed segment and the page; the card says how
//      long the closure is expected to last and why, before and after the
//      start; the crusher card offers its causes, each with its duration;
//   6. a module switch sends `set-enabled` to the module twin;
//   7. «Прогноз на 12 ч» of a scenario sends `forecast` for slot 1, the slot
//      twin turns ready and the panel shows both branches, the table and the
//      end state next to the objects; «Что даёт модуль?» replaces that panel;
//   8. during a shovel failure the advisor card shows when the repair is
//      expected to end, ranks its options with the cost of their resources and
//      a sentence on why each pays or not, and «Применить» sends `apply` and
//      shows the advisor's journal entry;
//   9. the blast card offers only blocks the model accepts (the others with
//      their reason) and starts with its defaults at the first click;
//  10. the plan: «План» shows it, its points of view frame it, its frame is
//      cheap, its captions of the plant view do not overlap; «3D» brings the
//      3D view back;
//  11. deep links #/twin/<name> select their twin, on navigation and on a fresh load;
//  12. the link «История в Grafana» opens the dashboard «Актогай — история»
//      without a login;
//  13. no console error and no failed request in a run of three minutes;
//  14. without WebGL (Chrome started with --disable-3d-apis) the 3D view is
//      still drawn, simplified, on a 2D canvas.
//
// The namespace of the twins is the one the page itself shows: the first the
// page's server names in /config.json that holds a mine twin.
//
//   node mine/ui/tests/ui_check.mjs [base URL] [WIDTHxHEIGHT]
//
// Needs Node 22+ and Chrome (CHROME_BIN if it is not in a usual place).
// Exit code 0 when every check passed.

import { launch, watchProblems, sleep, mineNamespace } from './cdp.mjs';

const base = (process.argv[2] ?? 'http://localhost:8088').replace(/\/$/, '');
const [width, height] = (process.argv[3] ?? '1280x720').split('x').map(Number);
const NS = await mineNamespace(base);
const RUN_MS = 180000;
console.log(`      namespace of the mine: ${NS}`);

let failed = 0;
function check(ok, text) {
  console.log(`${ok ? 'ok  ' : 'FAIL'}  ${text}`);
  if (!ok) failed += 1;
}

async function gateway(path) {
  const response = await fetch(`${base}/ditto/api/2/${path}`);
  if (!response.ok) throw new Error(`${response.status} for ${path}`);
  return response.json();
}
const featureOf = async (name, feature) => gateway(`things/${NS}:${name}/features/${feature}/properties/value`);

async function searchIds() {
  const ids = [];
  let cursor = null;
  do {
    const option = `size(200)${cursor ? `,cursor(${cursor})` : ''}`;
    const result = await gateway(`search/things?namespaces=${NS}&fields=thingId&option=${encodeURIComponent(option)}`);
    ids.push(...result.items.map((item) => item.thingId));
    cursor = result.cursor ?? null;
  } while (cursor);
  return ids;
}

async function until(read, wanted, timeoutMs = 30000) {
  const deadline = Date.now() + timeoutMs;
  for (;;) {
    const value = await read().catch(() => undefined);
    if (wanted(value)) return value;
    if (Date.now() > deadline) return value;
    await sleep(500);
  }
}

let failureStarted = false;
const page = await launch({ width, height });
const problems = watchProblems(page);
const posts = [];
page.on('Network.requestWillBeSent', ({ request }) => {
  if (request.method === 'POST') posts.push({ url: request.url, body: request.postData ?? '' });
});

// Open a card of the right-hand stack (cards are compact rows until opened).
const openCard = async (selector) => {
  await page.evaluate(`(() => {
    const button = document.querySelector('${selector} button.expand');
    if (button && button.getAttribute('aria-expanded') !== 'true') button.click();
  })()`);
  await sleep(250);
};
const busy = async () => {
  const { metrics } = await page.send('Performance.getMetrics');
  return Object.fromEntries(metrics.map((metric) => [metric.name, metric.value]));
};
const overlaps = (list) => {
  let count = 0;
  for (let i = 0; i < list.length; i += 1) {
    for (let j = i + 1; j < list.length; j += 1) {
      const [a, b] = [list[i].rect, list[j].rect];
      if (!(list[i].forced && list[j].forced) && a[0] < b[2] && a[2] > b[0] && a[1] < b[3] && a[3] > b[1]) count += 1;
    }
  }
  return count;
};

try {
  const started = Date.now();
  await page.send('Performance.enable');
  await page.goto(`${base}/`);
  await page.waitFor('document.querySelectorAll("#twin-list a.row").length > 0 && document.querySelectorAll(".scenario").length > 0', 60000, 'the page to load the twins');
  const renderer = await page.evaluate(`(() => {
    const gl = document.createElement('canvas').getContext('webgl');
    const info = gl && gl.getExtension('WEBGL_debug_renderer_info');
    return gl ? (info ? gl.getParameter(info.UNMASKED_RENDERER_WEBGL) : 'WebGL without renderer info') : 'no WebGL (software only)';
  })()`);
  console.log(`      renderer: ${renderer}; viewport ${width}x${height}`);

  // 1. the 3D view is the page
  const firstFrame = await page.waitFor('window.minePlan?.scene?.()?.firstFrameAt', 30000, 'the first frame of the mine').catch(() => 0);
  const opening = await page.evaluate(`(() => {
    const canvas = document.querySelector('#scene-gl').getBoundingClientRect();
    return { mode: window.minePlan.mode(), w: canvas.width, h: canvas.height,
      scroll: document.scrollingElement.scrollHeight <= window.innerHeight && document.documentElement.scrollWidth <= window.innerWidth,
      pressed: document.querySelector('[data-mode="3d"]').getAttribute('aria-pressed') };
  })()`);
  check(opening.mode === '3d' && opening.pressed === 'true' && opening.w === width && opening.h === height,
    `the page opens on the 3D view across the window (${opening.w}x${opening.h})`);
  check(firstFrame > 0 && firstFrame < 6000, `the first frame of the mine is drawn ${(firstFrame / 1000).toFixed(2)} s after the page started loading`);
  check(opening.scroll, 'nothing on the page scrolls (no page scrollbars)');

  // 2. the 3D scene
  await sleep(3000);
  const counts = async (kind) => (await gateway(`search/things?namespaces=${NS}&filter=${encodeURIComponent(`eq(attributes/kind,"${kind}")`)}&fields=thingId&option=size(200)`)).items.length;
  const wanted = { trucks: await counts('truck'), loaders: await counts('loader'), rigs: await counts('drill-rig') };
  const probe = await page.evaluate('window.minePlan.scene().probe()');
  console.log(`      3D scene: ${probe.triangles} triangles in ${probe.drawCalls} draw calls, ${(100 * probe.filled).toFixed(0)} % of the picture is the mine (not sky); ${probe.benches} benches, ${probe.blocks} blocks, ${probe.roads} roads, ${probe.plantUnits} plant units, ${probe.captions} captions; sun ${probe.sun.elevation.toFixed(1)}°, ${probe.sun.weather}`);
  check(probe.filled > 0.3 && probe.triangles > 20000 && probe.benches > 0 && probe.blocks > 0 && probe.roads > 0 && probe.plantUnits >= 8,
    `the 3D scene is drawn and not empty (${(100 * probe.filled).toFixed(0)} % of the picture, ${probe.triangles} triangles)`);
  check(probe.trucks === wanted.trucks && probe.loaders === wanted.loaders && probe.rigs === wanted.rigs,
    `the 3D scene holds every machine of the twins: trucks ${probe.trucks}/${wanted.trucks}, shovels ${probe.loaders}/${wanted.loaders}, drills ${probe.rigs}/${wanted.rigs}`);
  const simTime = await featureOf('mine', 'sim_time');
  check(typeof probe.sun.elevation === 'number' && probe.sun.elevation >= -90 && probe.sun.elevation <= 90,
    `the scene lights the mine for the simulated time ${simTime}: sun at ${probe.sun.elevation.toFixed(1)}°, night ${probe.sun.night.toFixed(2)}`);
  const before3d = await page.evaluate('window.minePlan.scene().positions()');
  await sleep(4000);
  const after3d = await page.evaluate('window.minePlan.scene().positions()');
  const moved = Object.keys(after3d).filter((id) => before3d[id] && Math.hypot(after3d[id][0] - before3d[id][0], after3d[id][1] - before3d[id][1]) > 1).length;
  check(moved > 0, `trucks move in the 3D view (${moved} of ${Object.keys(after3d).length} moved in 4 s)`);
  const cameraOf = async () => (await page.evaluate('window.minePlan.scene().probe()')).camera;
  const presets = {};
  for (const name of ['faces', 'ramps', 'plant', 'overview']) {
    await page.click(`[data-camera="${name}"]`);
    await sleep(1500);
    presets[name] = await cameraOf();
  }
  const apart = (a, b) => Math.hypot(a.x - b.x, a.y - b.y) > 50 || Math.abs(a.distance - b.distance) > 50;
  const plantTwin = await gateway(`things/${NS}:stockpile`).catch(() => null);
  const pile = plantTwin?.attributes?.geo;
  const nearPlant = pile ? Math.hypot(presets.plant.x - pile.x, presets.plant.y - pile.y) < 800 : true;
  check(apart(presets.faces, presets.overview) && apart(presets.plant, presets.overview) && apart(presets.ramps, presets.overview) && apart(presets.faces, presets.plant)
    && nearPlant && presets.faces.distance < presets.overview.distance,
  `points of view: overview (${presets.overview.distance.toFixed(0)} m away), faces (${presets.faces.distance.toFixed(0)} m), ramps (${presets.ramps.distance.toFixed(0)} m), plant (${presets.plant.distance.toFixed(0)} m${pile ? `, ${Math.hypot(presets.plant.x - pile.x, presets.plant.y - pile.y).toFixed(0)} m from the stockpile` : ''})`);
  const centre = await page.evaluate('(() => { const r = document.querySelector("#scene-gl").getBoundingClientRect(); return { x: r.left + r.width * 0.45, y: r.top + r.height * 0.62 }; })()');
  const start = await cameraOf();
  await page.send('Input.dispatchMouseEvent', { type: 'mousePressed', x: centre.x, y: centre.y, button: 'left', clickCount: 1 });
  for (let i = 1; i <= 6; i += 1) await page.send('Input.dispatchMouseEvent', { type: 'mouseMoved', x: centre.x + i * 15, y: centre.y, button: 'left', buttons: 1 });
  await page.send('Input.dispatchMouseEvent', { type: 'mouseReleased', x: centre.x + 90, y: centre.y, button: 'left', clickCount: 1 });
  await sleep(300);
  const dragged = await cameraOf();
  await page.evaluate('document.querySelector("#scene-gl").focus()');
  for (const key of ['ArrowLeft', 'ArrowLeft', 'ArrowUp']) {
    await page.send('Input.dispatchKeyEvent', { type: 'keyDown', key, code: key, windowsVirtualKeyCode: { ArrowLeft: 37, ArrowUp: 38 }[key] });
    await page.send('Input.dispatchKeyEvent', { type: 'keyUp', key, code: key, windowsVirtualKeyCode: { ArrowLeft: 37, ArrowUp: 38 }[key] });
  }
  await sleep(300);
  const keyed = await cameraOf();
  check(Math.abs(dragged.azimuth - start.azimuth) > 0.2 && Math.abs(keyed.azimuth - dragged.azimuth) > 0.1 && keyed.elevation > dragged.elevation,
    `dragging turns the view (azimuth ${start.azimuth.toFixed(2)} → ${dragged.azimuth.toFixed(2)}), the arrow keys turn and tilt it (→ ${keyed.azimuth.toFixed(2)}, elevation ${dragged.elevation.toFixed(2)} → ${keyed.elevation.toFixed(2)})`);
  await page.click('[data-camera="overview"]');
  await sleep(1500);
  const captionsOverview = await page.evaluate('window.minePlan.scene().captions()');
  await page.click('[data-camera="faces"]');
  await sleep(1500);
  const captionsFaces = await page.evaluate('window.minePlan.scene().captions()');
  check(captionsOverview.length > 3 && overlaps(captionsOverview) === 0 && overlaps(captionsFaces) === 0,
    `3D captions never overlap: ${captionsOverview.length} on the overview, ${captionsFaces.length} on the faces, overlaps ${overlaps(captionsOverview)} and ${overlaps(captionsFaces)}`);
  // Frame cost while the camera turns all the time (drawn to the end of each frame).
  await page.click('[data-camera="overview"]');
  await sleep(1500);
  await page.evaluate('window.minePlan.scene().setTiming(true); window.minePlan.scene().frameStats()');
  const busyBefore = await busy();
  const turnUntil = Date.now() + 15000;
  let step = 0;
  await page.send('Input.dispatchMouseEvent', { type: 'mousePressed', x: centre.x, y: centre.y, button: 'left', clickCount: 1 });
  while (Date.now() < turnUntil) {
    step += 1;
    await page.send('Input.dispatchMouseEvent', { type: 'mouseMoved', x: centre.x + 120 * Math.sin(step / 20), y: centre.y, button: 'left', buttons: 1 });
    await sleep(16);
  }
  await page.send('Input.dispatchMouseEvent', { type: 'mouseReleased', x: centre.x, y: centre.y, button: 'left', clickCount: 1 });
  const turning = await page.evaluate('window.minePlan.scene().frameStats()');
  const busyAfter = await busy();
  await page.evaluate('window.minePlan.scene().setTiming(false)');
  const fps = turning.frames / turning.seconds;
  console.log(`      3D view while the camera turns, ${turning.seconds.toFixed(0)} s: ${turning.frames} frames (${fps.toFixed(1)} per second) on a ${turning.canvas} canvas, ${turning.triangles} triangles in ${turning.drawCalls} draw calls`);
  console.log(`      3D frame drawn to the end: average ${turning.avgCostMs.toFixed(1)} ms, worst ${turning.worstCostMs.toFixed(1)} ms; between frames average ${turning.avgIntervalMs.toFixed(1)} ms, longest ${turning.longestIntervalMs.toFixed(1)} ms; main thread busy ${(100 * (busyAfter.TaskDuration - busyBefore.TaskDuration) / turning.seconds).toFixed(0)} % of the time`);
  check(fps >= 15, `the 3D view keeps at least 15 frames a second while the camera moves (${fps.toFixed(1)})`);
  // With the model paused and the camera still, the scene draws nothing.
  const idle = await page.evaluate(`(async () => {
    const scene = window.minePlan.scene();
    scene.frameStats();
    await new Promise((r) => setTimeout(r, 2500));
    return scene.frameStats();
  })()`);
  console.log(`      3D view with the camera still for ${idle.seconds.toFixed(1)} s: ${idle.frames} frames drawn, ${idle.idleChecks} frames skipped (machines in motion are drawn, at most every ${idle.movingFrameMs} ms)`);
  check(idle.frames / idle.seconds <= 31, `with the camera still the scene draws only for the machines in motion, at most ~30 frames a second (${(idle.frames / idle.seconds).toFixed(1)})`);
  // A deep link while the 3D view is shown selects the twin there and flies to it.
  const far = await cameraOf();
  await page.evaluate('location.hash = "#/twin/loader-01"');
  await sleep(2000);
  const captionsSelected = await page.evaluate('window.minePlan.scene().captions()');
  const near = await cameraOf();
  check(captionsSelected.some((item) => item.forced && /Экскаватор/.test(item.text)) && near.distance < far.distance,
    `a selected twin is named in the 3D view and the camera flies to it (${far.distance.toFixed(0)} → ${near.distance.toFixed(0)} m): ${captionsSelected.filter((item) => item.forced).map((item) => item.text).join(', ')}`);
  await page.evaluate('location.hash = ""');
  await page.click('[data-camera="overview"]');
  await sleep(800);

  // 3. the ribbon of figures and their detail
  const ribbon = await page.evaluate(`(() => {
    const tiles = [...document.querySelectorAll('#kpis .kpi')];
    return tiles.map((tile) => ({ key: tile.dataset.key, value: tile.querySelector('.kpi-value').textContent, label: tile.querySelector('.kpi-label').textContent }));
  })()`);
  check(ribbon.length === 6 && ribbon.every((tile) => tile.value && tile.value !== '—'), `the ribbon shows six figures: ${ribbon.map((tile) => `${tile.label} ${tile.value}`).join('; ')}`);
  await page.click('#kpis .kpi[data-key="mill"]');
  await sleep(500);
  const detail = await page.evaluate(`({ open: !document.querySelector('#kpi-detail').hidden, title: document.querySelector('#kpi-detail-title').textContent,
    expanded: document.querySelector('#kpis .kpi[data-key="mill"]').getAttribute('aria-expanded'), nodes: document.querySelectorAll('#flow .flow-node').length })`);
  check(detail.open && detail.expanded === 'true' && detail.nodes >= 6, `a figure opens its detail with the process chain («${detail.title}», ${detail.nodes} steps)`);
  // 10b. the same rate reads the same in the figures and in the chain
  const rates = await page.evaluate(`(() => {
    const kpi = (key) => document.querySelector('[data-kpi="' + key + '"]')?.textContent;
    const node = (key) => document.querySelector('#flow .flow-node[data-node="' + key + '"] .flow-value')?.textContent.replace(/\\s*т\\/ч$/, '');
    return { crusherTop: kpi('crusher'), crusherStrip: node('crusher'), millTop: kpi('mill'), millStrip: node('mill') };
  })()`);
  check(rates.crusherTop === rates.crusherStrip && rates.millTop === rates.millStrip,
    `figures and process chain agree: crusher ${rates.crusherTop} / ${rates.crusherStrip}, mill ${rates.millTop} / ${rates.millStrip}`);
  await page.click('#kpi-detail-close');
  await sleep(300);

  // 4. the object drawer
  const drawerClosed = await page.evaluate('document.querySelector("#drawer").hidden && document.querySelector("#drawer-toggle").getAttribute("aria-expanded") === "false"');
  const ids = await searchIds();
  const listed = await page.evaluate('[...document.querySelectorAll("#twin-list a.row")].map((row) => row.dataset.id)');
  const missing = ids.filter((id) => !listed.includes(id));
  check(drawerClosed && ids.length > 0 && missing.length === 0, `the object list is closed by default and holds all ${ids.length} twins of the search (rows: ${listed.length}${missing.length ? `, missing: ${missing.join(', ')}` : ''})`);
  await page.click('#drawer-toggle');
  await sleep(300);
  const drawerOpen = await page.evaluate('!document.querySelector("#drawer").hidden');
  await page.evaluate(`(() => { const input = document.querySelector('#twin-search'); input.value = 'Самосвал №12'; input.dispatchEvent(new Event('input')); })()`);
  await sleep(300);
  const filtered = await page.evaluate('[...document.querySelectorAll("#twin-list a.row")].filter((row) => !row.parentElement.hidden && !row.closest(".group").hidden).map((row) => row.textContent)');
  const cameraBefore = await cameraOf();
  await page.click(`#twin-list a.row[data-id="${NS}:truck-12"]`);
  await sleep(2200);
  const chosen = await page.evaluate(`({ drawer: document.querySelector('#drawer').hidden, card: !document.querySelector('#details').hidden, title: document.querySelector('#details h2')?.textContent, hash: location.hash })`);
  const cameraAfter = await cameraOf();
  check(drawerOpen && filtered.length >= 1 && filtered.every((text) => /Самосвал №12/.test(text)) && chosen.drawer && chosen.card && chosen.hash === '#/twin/truck-12'
    && (Math.hypot(cameraAfter.x - cameraBefore.x, cameraAfter.y - cameraBefore.y) > 30 || cameraAfter.distance < cameraBefore.distance * 0.7),
  `«Объекты» opens the list, the search narrows it (${filtered.length} row), choosing a row closes it, opens the card «${chosen.title}» and flies the camera (${cameraBefore.distance.toFixed(0)} → ${cameraAfter.distance.toFixed(0)} m)`);
  await page.evaluate(`(() => { const input = document.querySelector('#twin-search'); input.value = ''; input.dispatchEvent(new Event('input')); document.querySelector('#drawer-close').click(); location.hash = ''; })()`);
  await sleep(500);

  // 5. road closure through the page's buttons
  const card = `.scenario[data-id="${NS}:scenario-road-closure"]`;
  const cardStatus = `document.querySelector('${card}')?.dataset.status`;
  await openCard(card);
  if (await featureOf('scenario-road-closure', 'status') !== 'inactive') {
    await page.click(`${card} [data-action="cancel"]`);
    await until(() => featureOf('scenario-road-closure', 'status'), (value) => value === 'inactive');
    await sleep(2000);
  }
  const target = await page.evaluate(`document.querySelector('${card} select').value`);
  const duration = await page.evaluate(`document.querySelector('${card} .duration')?.textContent ?? ''`);
  check(/^Ожидаемая длительность 6\sч: осыпь/.test(duration), `before the start the card says how long a closure lasts: «${duration}»`);
  // The crusher stop offers its causes, each with its own duration and basis.
  const crusherCard = `.scenario[data-id="${NS}:scenario-crusher-stop"]`;
  await openCard(crusherCard);
  const causes = await page.evaluate(`(() => {
    const select = document.querySelector('${crusherCard} select.cause');
    if (!select) return null;
    const first = document.querySelector('${crusherCard} .duration').textContent;
    select.value = 'jam';
    select.dispatchEvent(new Event('change'));
    const jam = document.querySelector('${crusherCard} .duration').textContent;
    select.value = 'mechanical';
    select.dispatchEvent(new Event('change'));
    return { options: [...select.options].map((option) => option.textContent), first, jam };
  })()`);
  check(causes && causes.options.length === 2 && /8\sч/.test(causes.first) && /3\sч: заклинивание/.test(causes.jam),
    `the crusher card offers its causes (${causes ? causes.options.join(' / ') : 'none'}) with their duration: «${causes?.jam ?? ''}»`);
  await page.evaluate(`document.querySelector('${crusherCard} button.expand')?.click()`);
  await page.click(`${card} [data-action="activate"]`);
  const active = await until(() => featureOf('scenario-road-closure', 'status'), (value) => value === 'active');
  check(active === 'active', `«Запустить» made the scenario twin active (status: ${active})`);
  check(posts.some((post) => post.url.includes(':scenario-road-closure/inbox/messages/activate') && post.body.includes(target)), `the page sent activate with target ${target}`);
  const segment = await until(() => gateway(`things/${target}/features/status/properties/value`), (value) => value === 'closed');
  check(segment === 'closed', `the target segment twin is closed (status: ${segment})`);
  await page.waitFor(`${cardStatus} === 'active'`, 15000, 'the card to turn active').catch(() => {});
  await sleep(1500);
  const shown = await page.evaluate(`(() => {
    const cardNode = document.querySelector('${card}');
    const captions = window.minePlan.scene().captions().map((item) => item.text);
    return {
      status: cardNode.dataset.status,
      badge: cardNode.querySelector('.badge').textContent,
      open: !cardNode.querySelector('.card-body').hidden,
      startDisabled: cardNode.querySelector('[data-action="activate"]').disabled,
      cancelDisabled: cardNode.querySelector('[data-action="cancel"]').disabled,
      compareRows: cardNode.querySelectorAll('.compare tbody tr').length,
      impact: cardNode.querySelector('.impact').textContent,
      chip: document.querySelector('#plan-chips .chip[data-tone="scenario"]')?.textContent ?? '',
      row: document.querySelector('#twin-list a.row[data-id="${target}"] .row-state').textContent,
      cycleTop: document.querySelector('[data-kpi="cycle"]')?.textContent,
      cycleCard: [...cardNode.querySelectorAll('.compare tbody tr')].find((tr) => /^(→ )?Время рейса/.test(tr.children[0].textContent))
        ?.querySelector('[data-cell="now"]').textContent,
      closedCaption: captions.some((text) => text === 'Дорога закрыта'),
      expected: cardNode.querySelector('.expected')?.textContent ?? '',
      expectedShown: !cardNode.querySelector('.expected')?.hidden,
    };
  })()`);
  check(shown.expectedShown && /^Ожидаемое окончание \d\d:\d\d, через [^·]+ · осыпь/.test(shown.expected),
    `the running card says when the closure is expected to end and why: «${shown.expected.slice(0, 120)}»`);
  const asNumber = (text) => Number(String(text ?? '').replace(/[\s  ]/g, '').replace(',', '.'));
  check(shown.cycleCard === undefined || asNumber(shown.cycleTop) === asNumber(shown.cycleCard),
    `the figures and the card show the same truck cycle (${shown.cycleTop} / ${shown.cycleCard})`);
  check(shown.status === 'active' && shown.badge === 'активен' && shown.open && shown.startDisabled && !shown.cancelDisabled, `the card shows the scenario active and open (badge «${shown.badge}»)`);
  check(shown.compareRows >= 2 && shown.impact.length > 0, `the card compares before and now (${shown.compareRows} rows) and states the impact`);
  check(shown.chip.includes('Закрытие дороги'), 'a chip under the figures names the running scenario');
  check(shown.row.includes('закрыт'), `the list row of the segment says «${shown.row}»`);
  await page.evaluate(`location.hash = '#/twin/${target.split(':')[1]}'`);
  await sleep(2500);
  const closedNamed = await page.evaluate('window.minePlan.scene().captions().some((item) => item.text === "Дорога закрыта")');
  check(closedNamed, 'the scene marks the closed road and names it «Дорога закрыта»');
  await page.evaluate('location.hash = ""');
  await page.click(`${card} [data-action="cancel"]`);
  const inactive = await until(() => featureOf('scenario-road-closure', 'status'), (value) => value === 'inactive');
  check(inactive === 'inactive', `«Отменить» made the scenario twin inactive (status: ${inactive})`);
  const reopened = await until(() => gateway(`things/${target}/features/status/properties/value`), (value) => value === 'open');
  check(reopened === 'open', `the segment twin is open again (status: ${reopened})`);
  await page.waitFor(`${cardStatus} === 'inactive'`, 15000, 'the card to turn inactive').catch(() => {});
  check(await page.evaluate(`${cardStatus} === 'inactive' && !document.querySelector('#plan-chips .chip[data-tone="scenario"]')`), 'the card and the chips are back to normal');

  // 6. module switch
  const moduleName = 'module-dispatch';
  const toggle = `.module[data-id="${NS}:${moduleName}"] .switch`;
  const before = await featureOf(moduleName, 'enabled');
  // Fold the open cards, so the module's row stands still where it is clicked.
  await page.evaluate(`document.querySelectorAll('#control button.expand[aria-expanded="true"]').forEach((b) => b.click())`);
  await page.evaluate(`document.querySelector('${toggle}').scrollIntoView({ block: 'center' })`);
  await sleep(600);
  posts.length = 0;
  await page.click(toggle);
  await sleep(2500);
  const sent = posts.find((post) => post.url.includes(`:${moduleName}/inbox/messages/set-enabled`));
  check(Boolean(sent) && sent.body === JSON.stringify({ enabled: !before }), `the switch sent set-enabled ${JSON.stringify({ enabled: !before })} (${sent ? sent.body : 'nothing sent'})`);
  const after = await until(() => featureOf(moduleName, 'enabled'), (value) => value !== before, 6000);
  console.log(`      module twin enabled: ${before} -> ${after}; the card says: «${await page.evaluate(`document.querySelector('.module[data-id="${NS}:${moduleName}"] .hint').textContent || document.querySelector('${toggle} .switch-text').textContent`)}»`);
  if (after !== before) {
    await page.click(toggle);
    await until(() => featureOf(moduleName, 'enabled'), (value) => value === before, 10000);
  }

  // 7. two futures: a scenario forecast, then a module's, which replaces it
  const slotTwin = await gateway(`things/${NS}:forecast-1`).catch(() => null);
  if (!slotTwin) {
    check(false, 'the forecast slot twin forecast-1 is on the stand');
  } else {
    await sleep(1500);
    await openCard(card);
    posts.length = 0;
    await page.click(`${card} [data-action="forecast"]`);
    await sleep(1500);
    const asked = posts.find((post) => post.url.includes(`/things/${NS}:mine/inbox/messages/forecast`));
    let body = {};
    try { body = JSON.parse(asked?.body ?? '{}'); } catch { /* checked below */ }
    check(body.slot === 1 && body.horizon_h === 12 && body.actions_b?.[0]?.subject === 'activate' && body.actions_b?.[0]?.thing === `${NS}:scenario-road-closure`
      && Array.isArray(body.actions_a) && body.actions_a.length === 0,
    `«Прогноз на 12 ч» sent forecast for slot 1 with B = activate the road closure (${asked ? asked.body.slice(0, 160) : 'nothing sent'})`);
    const panelState = `document.querySelector('${card} .forecast')?.dataset.state`;
    const t0 = Date.now();
    const state = await page.waitFor(`${panelState} === 'ready' && ${panelState}`, 120000, 'the forecast panel to be ready').catch(() => page.evaluate(panelState));
    const slot = await gateway(`things/${NS}:forecast-1`).then((twin) => Object.fromEntries(Object.entries(twin.features).map(([k, v]) => [k, v.properties.value])));
    check(state === 'ready' && slot.status === 'ready', `the slot twin turned ready and the panel shows it after ${((Date.now() - t0) / 1000).toFixed(1)} s (panel: ${state}, slot: ${slot.status}, subject: ${slot.subject}, requested_by: ${slot.requested_by})`);
    const shownForecast = await page.evaluate(`(() => {
      const panel = document.querySelector('${card} .forecast');
      if (!panel) return null;
      return {
        legend: panel.querySelector('.fc-legend').textContent,
        charts: panel.querySelectorAll('.fc-chart').length,
        linesA: panel.querySelectorAll('.fc-chart .fc-line-a').length,
        linesB: panel.querySelectorAll('.fc-chart .fc-line-b').length,
        rows: [...panel.querySelectorAll('.fc-table tbody tr')].map((row) => [...row.children].map((cell) => cell.textContent)),
        summary: panel.querySelector('.fc-summary')?.textContent ?? '',
        verdict: panel.querySelector('.fc-verdict')?.textContent ?? '',
        copper: panel.querySelector('.fc-copper')?.textContent ?? '',
        notes: window.minePlan.notes(),
        sceneNotes: window.minePlan.scene().notes.length,
      };
    })()`);
    check(Boolean(shownForecast) && shownForecast.legend.includes('Без изменений') && shownForecast.legend.includes('Если запустить сейчас')
      && shownForecast.linesA >= 2 && shownForecast.linesB >= 2,
    `the panel shows both branches: «${shownForecast?.legend}», ${shownForecast?.charts} charts with ${shownForecast?.linesA} A and ${shownForecast?.linesB} B lines`);
    const cuRow = shownForecast?.rows.find((row) => row[0].startsWith('Медь'));
    const fmt = (value) => (typeof value === 'number' ? value.toLocaleString('ru-RU', { minimumFractionDigits: 1, maximumFractionDigits: 1 }).replace(/ /g, ' ') : '—');
    const rowNames = (shownForecast?.rows ?? []).map((row) => row[0]);
    const wantedRows = ['Медь', 'Руда добыта', 'Переменная маржа', 'Выбросы CO₂'];
    check(wantedRows.every((name) => rowNames.some((row) => row.startsWith(name))) && cuRow && cuRow[1] === fmt(slot.a_cu_t) && cuRow[2] === fmt(slot.b_cu_t),
      `the table holds copper, ore, margin and CO₂ (${rowNames.length} rows); copper A ${cuRow?.[1]} / B ${cuRow?.[2]} matches the twin (${slot.a_cu_t} / ${slot.b_cu_t})`);
    // The decision figure leads: the first row and the bold line are the total with the adjustment, and a
    // sentence says where the copper difference comes from.
    check(rowNames[0]?.startsWith('Итог с поправкой') && /^(Итог с поправкой|По итогу с поправкой)/.test(shownForecast?.verdict ?? '')
      && (shownForecast?.copper ?? '').length > 20,
    `the panel leads with the total with the adjustment («${rowNames[0]}», «${(shownForecast?.verdict ?? '').slice(0, 90)}») and explains the copper («${(shownForecast?.copper ?? '').slice(0, 90)}»)`);
    console.log(`      verdict: «${shownForecast?.verdict}»`);
    console.log(`      summary: «${shownForecast?.summary}»`);
    check((shownForecast?.notes ?? []).length > 0 && shownForecast.sceneNotes > 0, `the end state stands next to the objects on the plan and in the scene: ${JSON.stringify(shownForecast?.notes)}`);

    const moduleCard = `.module[data-id="${NS}:module-plant"]`;
    await openCard(moduleCard);
    posts.length = 0;
    await page.click(`${moduleCard} [data-action="forecast"]`);
    await sleep(1000);
    const replaced = await page.evaluate(`!document.querySelector('${card} .forecast') && Boolean(document.querySelector('${moduleCard} .forecast'))`);
    const askedModule = posts.find((post) => post.url.includes('/messages/forecast'));
    check(replaced && Boolean(askedModule) && askedModule.body.includes('set-enabled'), `«Что даёт модуль?» replaced the scenario panel and asked with B = module off (${askedModule ? askedModule.body.slice(0, 140) : 'nothing sent'})`);
    const moduleState = await page.waitFor(`document.querySelector('${moduleCard} .forecast')?.dataset.state === 'ready' && 'ready'`, 120000, 'the module forecast').catch(() => page.evaluate(`document.querySelector('${moduleCard} .forecast')?.dataset.state`));
    const moduleLegend = await page.evaluate(`document.querySelector('${moduleCard} .fc-legend')?.textContent`);
    check(moduleState === 'ready' && /С модулем/.test(moduleLegend) && /Без модуля/.test(moduleLegend), `the module panel is ready with «${moduleLegend}»`);
    await page.click(`${moduleCard} .fc-close`);
    await sleep(500);
    check(await page.evaluate(`!document.querySelector('.forecast') && window.minePlan.notes().length === 0 && window.minePlan.scene().notes.length === 0`), 'closing the panel removes it and its notes');
  }

  // 8. the advisor during a shovel failure
  const advisorTwin = await gateway(`things/${NS}:module-advisor`).catch(() => null);
  if (!advisorTwin) {
    check(false, 'the advisor twin module-advisor is on the stand');
  } else {
    const failure = `.scenario[data-id="${NS}:scenario-shovel-failure"]`;
    await openCard(failure);
    if (await featureOf('scenario-shovel-failure', 'status') !== 'inactive') {
      await page.click(`${failure} [data-action="cancel"]`);
      await until(() => featureOf('scenario-shovel-failure', 'status'), (value) => value === 'inactive');
      await sleep(3000);
    }
    failureStarted = true;
    await page.click(`${failure} [data-action="activate"]`);
    const failing = await until(() => featureOf('scenario-shovel-failure', 'status'), (value) => value === 'active');
    check(failing === 'active', `the shovel failure is running (status: ${failing})`);
    const t1 = Date.now();
    await page.waitFor(`document.querySelector('#advisor .advisor[data-status="ready"]') && document.querySelector('#advisor .adv-option[data-recommended="true"]')`,
      180000, 'the advisor to rank its options').catch(() => {});
    const options = await page.evaluate('document.querySelectorAll("#advisor .adv-option").length');
    const rankedS = (Date.now() - t1) / 1000;
    const advice = await page.evaluate(`(() => ({
      incident: document.querySelector('#advisor .adv-incident')?.textContent ?? '',
      end: document.querySelector('#advisor .adv-end')?.textContent ?? '',
      top: document.querySelector('#control').scrollTop,
      options: [...document.querySelectorAll('#advisor .adv-option')].map((row) => ({
        n: row.dataset.option, recommended: row.dataset.recommended, label: row.querySelector('.adv-label').textContent,
        money: row.querySelector('.adv-figures').textContent, spark: Boolean(row.querySelector('svg.fc-spark')),
        cost: row.querySelector('.adv-cost')?.textContent ?? '', reason: row.querySelector('.adv-reason')?.textContent ?? '' })),
    }))()`);
    console.log(`      incident after ${rankedS.toFixed(0)} s: «${advice.incident}» ${advice.end}`);
    for (const option of advice.options) console.log(`      ${option.n}. ${option.label} · ${option.money}${option.recommended === 'true' ? ' (рекомендуется)' : ''}${option.spark ? ' · sparkline' : ''}${option.reason ? `\n         ${option.reason}` : ''}`);
    check(options >= 2 && advice.incident && advice.options.filter((option) => option.recommended === 'true').length === 1,
      `the advisor card shows the incident and ${options} ranked options, one of them recommended`);
    check(rankedS <= 25, `the advisor ranked its options ${rankedS.toFixed(1)} s after the failure (at most 25 s)`);
    check(/^Ожидаемое окончание \d\d:\d\d · отказ экскаватора/.test(advice.end) && !/Ожидаемое окончание/.test(advice.incident),
      `the card says when the repair is expected to end and why, on a line of its own: «${advice.end}»`);
    const others = advice.options.filter((option) => !/^Ничего не менять/.test(option.label));
    check(others.length > 0 && others.every((option) => option.reason.length > 20),
      `every option but «Ничего не менять» says why it pays or does not (${others.map((option) => option.reason.split(':')[0]).join(' | ')})`);
    check(advice.options.some((option) => /^Вторая бригада/.test(option.label) && /ресурсы \d/.test(option.cost)),
      `the second crew shows what its resources cost («${advice.options.find((option) => /^Вторая бригада/.test(option.label))?.cost ?? ''}»)`);
    if (options) {
      const pick = advice.options.find((option) => option.recommended === 'true') ?? advice.options[0];
      const journalBefore = await page.evaluate('document.querySelectorAll(`#journal a[href="#/twin/module-advisor"]`).length');
      posts.length = 0;
      await page.click(`#advisor button.apply[data-option="${pick.n}"]`);
      await sleep(1500);
      const applied = posts.find((post) => post.url.includes(`/things/${NS}:module-advisor/inbox/messages/apply`));
      check(Boolean(applied) && JSON.parse(applied.body).option === Number(pick.n), `«Применить» sent apply ${applied ? applied.body : '(nothing sent)'}`);
      const result = await page.waitFor('document.querySelector("#advisor .adv-result")?.textContent', 30000, 'the advisor to answer').catch(() => '');
      const journalAfter = await page.evaluate('document.querySelectorAll(`#journal a[href="#/twin/module-advisor"]`).length');
      check(Boolean(result) && journalAfter > journalBefore, `the journal entry of the advisor is shown in the card and the journal: «${result}»`);
    }
    await openCard(failure);
    await page.click(`${failure} [data-action="cancel"]`);
    const over = await until(() => featureOf('scenario-shovel-failure', 'status'), (value) => value === 'inactive', 60000);
    check(over === 'inactive', `the shovel failure is cancelled (status: ${over})`);
  }

  // 9. the blast with the card's defaults
  const blastCard = `.scenario[data-id="${NS}:scenario-blast"]`;
  await openCard(blastCard);
  if (await page.evaluate(`Boolean(document.querySelector('${blastCard} select'))`)) {
    if (await featureOf('scenario-blast', 'status') !== 'inactive') {
      await page.click(`${blastCard} [data-action="cancel"]`).catch(() => {});
      await until(() => featureOf('scenario-blast', 'status'), (value) => value === 'inactive', 60000);
    }
    await until(() => featureOf('pit', 'blast_state'), (value) => value === 'idle', 120000);
    await sleep(1500);
    const choice = await page.evaluate(`(() => {
      const select = document.querySelector('${blastCard} select');
      const options = [...select.options];
      return { value: select.value, chosen: options.find((o) => o.value === select.value)?.disabled === false,
        valid: options.filter((o) => !o.disabled).length, invalid: options.filter((o) => o.disabled).map((o) => o.textContent),
        start: !document.querySelector('${blastCard} [data-action="activate"]').disabled };
    })()`);
    const statusOf = async (id) => gateway(`things/${id}/features/status/properties/value`).catch(() => '');
    const blockStatus = await statusOf(choice.value);
    check(choice.chosen && ['insitu', 'drilled', 'charged'].includes(blockStatus) && choice.invalid.every((text) => / — /.test(text)),
      `the blast card defaults to a valid block (${choice.value}: ${blockStatus}); ${choice.valid} valid, ${choice.invalid.length} shown with a reason, e.g. «${choice.invalid[0] ?? ''}»`);
    const journalBefore = await page.evaluate('document.querySelectorAll("#journal li").length');
    posts.length = 0;
    if (choice.start) await page.click(`${blastCard} [data-action="activate"]`);
    const blasting = await until(() => featureOf('scenario-blast', 'status'), (value) => value === 'active', 20000);
    const refusal = await page.evaluate(`[...document.querySelectorAll('#journal li')].slice(0, Math.max(0, document.querySelectorAll('#journal li').length - ${journalBefore}))
      .map((li) => li.textContent).find((text) => /не запущен/.test(text)) ?? ''`);
    check(blasting === 'active' && !refusal, `«Запустить» with the defaults started the blast at the first click (status: ${blasting}${refusal ? `; refused: ${refusal}` : ''})`);
    if (blasting === 'active') {
      await until(() => featureOf('pit', 'blast_state'), (value) => value === 'clearing', 30000);
      await sleep(2000);
      await page.evaluate(`location.hash = '#/twin/${choice.value.split(':')[1]}'`);
      await sleep(2500);
      const blastCaption = await page.evaluate('window.minePlan.scene().captions().find((item) => /^Взрыв через \\d+ мин|^Опасная зона взрыва|^Взрыв произведён/.test(item.text))?.text ?? ""');
      check(Boolean(blastCaption), `the scene shows the exclusion zone with its countdown: «${blastCaption}»`);
      await page.evaluate('location.hash = ""');
      await page.click(`${blastCard} [data-action="cancel"]`).catch(() => {});
      await until(() => featureOf('scenario-blast', 'status'), (value) => value === 'inactive', 90000);
    }
  } else {
    check(false, 'the blast card has a list of blocks');
  }

  // 10. the plan
  await page.click('[data-mode="plan"]');
  await sleep(1500);
  await page.evaluate('window.minePlan.scene().frameStats()');
  await sleep(2000);
  const hidden = await page.evaluate('window.minePlan.scene().frameStats()');
  check(hidden.frames === 0, `the 3D view draws nothing while the plan is shown (${hidden.frames} frames in ${hidden.seconds.toFixed(1)} s)`);
  const planShown = await page.evaluate('window.minePlan.mode() === "plan" && document.querySelector("#plan-view").dataset.shown === "plan" && document.querySelector(\'[data-mode="plan"]\').getAttribute("aria-pressed") === "true"');
  check(planShown && new URL(await page.evaluate('location.href')).searchParams.get('view') === 'plan', '«План» shows the plan and puts ?view=plan in the address');
  await page.evaluate('window.minePlan.frameStats()');
  await sleep(10000);
  const frames = await page.evaluate('window.minePlan.frameStats()');
  console.log(`      plan over ${frames.seconds.toFixed(0)} s: ${frames.frames} frames, drawing average ${frames.avgCostMs.toFixed(2)} ms, worst ${frames.worstCostMs.toFixed(1)} ms; between frames longest ${frames.longestIntervalMs.toFixed(1)} ms; full redraws ${frames.staticRedraws}, average ${frames.avgStaticFrameMs.toFixed(1)} ms`);
  check(frames.frames > 0 && frames.avgCostMs < 6 && frames.longestIntervalMs < 150, 'a frame of the plan is cheap in software rendering');
  await page.click('[data-camera="plant"]');
  await sleep(1500);
  const plantCaptions = await page.evaluate('window.minePlan.captions()');
  const overlap = (p, q) => p[0] < q[2] && p[2] > q[0] && p[1] < q[3] && p[3] > q[1];
  const crossesLine = (rect, [x1, y1, x2, y2, pad]) => {
    const dx = x2 - x1;
    const dy = y2 - y1;
    const p = [-dx, dx, -dy, dy];
    const q = [x1 - (rect[0] - pad), rect[2] + pad - x1, y1 - (rect[1] - pad), rect[3] + pad - y1];
    let t0 = 0;
    let t1 = 1;
    for (let i = 0; i < 4; i += 1) {
      if (p[i] === 0) { if (q[i] < 0) return false; continue; }
      const u = q[i] / p[i];
      if (p[i] < 0) { if (u > t1) return false; t0 = Math.max(t0, u); } else { if (u < t0) return false; t1 = Math.min(t1, u); }
    }
    return true;
  };
  const clashes = [];
  plantCaptions.captions.forEach((c, i) => {
    plantCaptions.captions.slice(i + 1).forEach((d) => { if (overlap(c.rect, d.rect)) clashes.push(`«${c.text}» × «${d.text}»`); });
    if (plantCaptions.glyphs.some((g) => overlap(c.rect, g))) clashes.push(`«${c.text}» × glyph`);
    if (!c.pill && plantCaptions.lines.some((line) => crossesLine(c.rect, line))) clashes.push(`«${c.text}» × pipe`);
  });
  const named = plantCaptions.captions.filter((c) => !c.pill).length;
  check(named >= 5 && clashes.length === 0, `captions of the plan's plant view do not overlap (${named} captions, ${plantCaptions.captions.length - named} flow figures${clashes.length ? `; ${clashes.join('; ')}` : ''})`);
  await page.click('[data-mode="3d"]');
  await sleep(1500);
  const backTo3d = await page.evaluate('window.minePlan.mode() === "3d" && document.querySelector("#plan-view").dataset.shown === "3d" && !new URL(location.href).searchParams.has("view")');
  const backFrames = await page.evaluate('window.minePlan.scene().frameStats()');
  check(backTo3d, `«3D» brings the 3D view back and clears ?view from the address (${backFrames.frames} frames since)`);

  // 11. deep links
  await page.evaluate('location.hash = "#/twin/loader-02"');
  await sleep(1200);
  const linked = await page.evaluate(`({
    title: document.querySelector('#details h2')?.textContent,
    current: document.querySelector('#twin-list a.row[aria-current="true"]')?.dataset.id,
    ident: document.querySelector('#details .ident code')?.textContent,
    features: document.querySelectorAll('#details-features tr').length,
  })`);
  check(linked.current === `${NS}:loader-02` && linked.ident === `${NS}:loader-02` && linked.features > 5, `#/twin/loader-02 selects «${linked.title}» with ${linked.features} features`);

  // 13. nothing went wrong for three minutes on this page
  const left = RUN_MS - (Date.now() - started);
  if (left > 0) {
    console.log(`      watching the page for ${Math.ceil(left / 1000)} more seconds ...`);
    await sleep(left);
  }
  const sparks = await page.evaluate('document.querySelectorAll("#details svg.spark").length');
  check(sparks > 0, `history sparklines are drawn for the selected twin (${sparks})`);
  check(problems.length === 0, `no console errors and no failed requests in ${Math.round((Date.now() - started) / 1000)} s${problems.length ? `:\n        ${problems.join('\n        ')}` : ''}`);

  // 11b. a deep link on a fresh load, and the view in the address
  await page.goto('about:blank');
  await sleep(300);
  await page.goto(`${base}/#/twin/truck-03`);
  await page.waitFor('document.querySelector("#details h2")', 30000, 'the deep link to select').catch(() => {});
  await sleep(2500);
  const fresh = await page.evaluate('document.querySelector("#twin-list a.row[aria-current=\\"true\\"]")?.dataset.id');
  const freshCamera = await cameraOf().catch(() => ({ distance: Infinity }));
  check(fresh === `${NS}:truck-03` && freshCamera.distance < 1000, `a fresh load of #/twin/truck-03 selects it and flies to it (${fresh}, ${freshCamera.distance.toFixed(0)} m)`);
  await page.goto(`${base}/?view=plan`);
  await page.waitFor('window.minePlan?.mode?.() === "plan"', 30000, 'the plan').catch(() => {});
  const viaPlan = await page.evaluate('window.minePlan.mode()');
  await page.goto(`${base}/?view=3d`);
  await page.waitFor('window.minePlan?.mode?.() === "3d"', 30000, 'the 3D view').catch(() => {});
  const via3d = await page.evaluate('window.minePlan.mode()');
  check(viaPlan === 'plan' && via3d === '3d', `?view=plan opens the plan (${viaPlan}), ?view=3d the 3D view (${via3d})`);

  // 12. «История в Grafana» opens the dashboard without a login
  const link = await page.evaluate('document.querySelector(".journal a.head-link")?.getAttribute("href")');
  const hop = await fetch(`${base}${link}`, { redirect: 'manual' });
  const landing = hop.headers.get('location') ?? '';
  check(hop.status === 302 && /\/d\/aktogay-history$/.test(landing), `the page's link ${link} leads to ${landing} (${hop.status})`);
  const viewer = await launch({ width, height });
  try {
    await viewer.goto(`${base}${link}`);
    const seen = await viewer.waitFor(`(() => {
      const panels = document.querySelectorAll('[data-viz-panel-key], .react-grid-item').length;
      const title = document.title;
      return panels >= 6 && /Актогай — история/.test(title) && { panels, title, url: location.href,
        login: Boolean(document.querySelector('input[name="user"], input[name="password"]')) };
    })()`, 60000, 'the Grafana dashboard').catch(() => viewer.evaluate('({ panels: 0, title: document.title, url: location.href, login: Boolean(document.querySelector(\'input[name="password"]\')) })'));
    await sleep(6000);
    const drawn = await viewer.evaluate(`(() => ({
      errors: [...document.querySelectorAll('[data-testid*="error"], .panel-info-corner--error')].length,
      noData: [...document.querySelectorAll('*')].filter((n) => n.childElementCount === 0 && n.textContent === 'No data').length,
      canvases: document.querySelectorAll('canvas').length,
    }))()`);
    check(seen.panels >= 6 && !seen.login && /\/d\/aktogay-history/.test(seen.url),
      `Grafana shows «${seen.title}» with ${seen.panels} panels at ${seen.url} without a login (panel errors: ${drawn.errors}, «No data»: ${drawn.noData}, charts drawn: ${drawn.canvases})`);
  } finally {
    await viewer.close();
  }
} catch (error) {
  check(false, `the check stopped: ${error.message}`);
} finally {
  await page.close();
  if (await featureOf('scenario-shovel-failure', 'status').catch(() => 'inactive') !== 'inactive' && failureStarted) {
    await fetch(`${base}/ditto/api/2/things/${NS}:scenario-shovel-failure/inbox/messages/cancel?timeout=0`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' }).catch(() => {});
  }
}

// 14. without WebGL the 3D view is drawn on a 2D canvas
{
  const saved = process.env.CHROME_FLAGS;
  process.env.CHROME_FLAGS = `${saved ?? ''} --disable-3d-apis`.trim();
  let soft = null;
  try {
    const { launch: launchSoft } = await import(`./cdp.mjs?soft=${Date.now()}`);
    soft = await launchSoft({ width, height });
    const softProblems = watchProblems(soft);
    await soft.goto(`${base}/`);
    await soft.waitFor('window.minePlan?.scene?.()?.firstFrameAt > 0', 60000, 'the 2D-canvas scene').catch(() => {});
    await sleep(4000);
    const result = await soft.evaluate('(() => { const s = window.minePlan.scene(); const p = s.probe(); return { soft: p.soft, filled: p.filled, triangles: p.triangles, note: document.querySelector("#scene-note").textContent }; })()').catch((error) => ({ error: error.message }));
    await soft.evaluate('window.minePlan.scene().frameStats()').catch(() => {});
    await sleep(5000);
    const stats = await soft.evaluate('window.minePlan.scene().frameStats()').catch(() => ({ frames: 0, seconds: 1, avgCostMs: 0 }));
    console.log(`      without WebGL: ${result.triangles} triangles on a 2D canvas, ${(100 * (result.filled ?? 0)).toFixed(0)} % of the picture drawn; ${stats.frames} frames in ${stats.seconds.toFixed(0)} s, ${stats.avgCostMs.toFixed(0)} ms each`);
    check(result.soft === true && result.filled > 0.3 && /WebGL/.test(result.note ?? '') && softProblems.length === 0,
      `without WebGL the 3D view is drawn on a 2D canvas and says so («${result.note ?? result.error}»)${softProblems.length ? `; problems: ${softProblems.join('; ')}` : ''}`);
  } catch (error) {
    check(false, `the check without WebGL stopped: ${error.message}`);
  } finally {
    if (soft) await soft.close();
    if (saved === undefined) delete process.env.CHROME_FLAGS; else process.env.CHROME_FLAGS = saved;
  }
}

console.log(failed ? `\n${failed} check(s) failed` : '\nall checks passed');
process.exit(failed ? 1 : 0);
