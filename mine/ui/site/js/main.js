// Page start-up: load the twins, keep them current, connect the 3D view (the
// page's main view), the plan and the panels.

import { loadTwins, loadNamespaces, findMine, subscribe } from './api.js';
import { NAMESPACE, setNamespace, twins, replaceAll, applyChange, onChange, onPatch, shortName, kindOf } from './store.js';
import { createMap } from './map.js';
import { $ } from './dom.js';
import * as topbar from './topbar.js';
import * as planui from './planui.js';
import * as objects from './objects.js';
import * as control from './control.js';
import * as forecast from './forecast.js';
import * as rates from './rates.js';
import * as about from './about.js';
import { initialStyle } from './style3d.js';

const RETRY_MS = 3000;

function selectedFromHash() {
  const match = /^#\/twin\/(.+)$/.exec(window.location.hash);
  if (!match) return null;
  let name = match[1];
  try { name = decodeURIComponent(name); } catch { /* keep the text as typed */ }
  return name.includes(':') ? name : `${NAMESPACE}:${name}`;
}

// Selecting an object is a navigation to #/twin/<name>, so every selection
// has an address and the browser's back button works.
const select = (thingId) => { window.location.hash = `#/twin/${shortName(thingId)}`; };
const map = createMap({
  staticCanvas: $('#plan-static'),
  liveCanvas: $('#plan-live'),
  onSelect: select,
  onHover: planui.showTip,
  onView: planui.showScale,
});

// -- 3D view or plan -----------------------------------------------------------------
// The 3D view is the page; the plan is one click away (and the address
// ?view=plan opens it). Both are kept up to date; only the one shown draws.

const wanted = new URLSearchParams(window.location.search).get('view') === 'plan' ? 'plan' : '3d';
let mode = null;
let scene = null;
let sceneLoading = null;

function note(text, ms = 8000) {
  const node = $('#scene-note');
  node.textContent = text ?? '';
  node.hidden = !text;
  if (text) setTimeout(() => { if (node.textContent === text) note(null); }, ms);
}

async function openScene() {
  if (scene) return scene;
  if (!sceneLoading) {
    sceneLoading = (async () => {
      const module = await import('./scene3d.js');
      // Without WebGL the same scene is drawn on a 2D canvas, simpler and less often.
      const soft = !module.webglAvailable();
      const made = module.createScene({
        canvas: $('#scene-gl'),
        labelCanvas: $('#scene-labels'),
        panelLayer: $('#scene-panels'),
        style: initialStyle(),
        onSelect: select,
        onHover: planui.showTip,
        soft,
      });
      if (soft) note('Браузер не рисует WebGL, поэтому 3D-вид нарисован упрощённо и обновляется реже. План рудника — кнопка «План» внизу.');
      made.setLayer(planui.currentLayer());
      if (twins.size) made.reload();
      made.select(shownSelection ?? null);
      scene = made;
      planui.reportOverlays();
      // An object chosen before the scene was ready (a deep link) is flown to now.
      if (shownSelection) setTimeout(() => made.reveal(shownSelection, true), 60);
      return made;
    })();
  }
  try {
    return await sceneLoading;
  } catch (error) {
    sceneLoading = null;
    note('3D-вид не открылся. План рудника показывает всё то же самое.');
    return null;
  }
}

async function setMode(next) {
  if (next === mode) return;
  if (next === '3d') {
    const made = await openScene();
    if (!made) { if (mode !== 'plan') setMode('plan'); return; }
    mode = '3d';
    map.setActive(false);
    planui.showMode('3d');
    made.setActive(true);
  } else {
    mode = 'plan';
    scene?.setActive(false);
    planui.showMode('plan');
    map.setActive(true);
  }
  // The view is part of the address, beside the selected twin.
  const url = new URL(window.location.href);
  if (mode === 'plan') url.searchParams.set('view', 'plan'); else url.searchParams.delete('view');
  window.history.replaceState(null, '', url);
}

const current = () => (mode === '3d' && scene ? scene : map);

let shownSelection;
function applySelection({ reveal = true } = {}) {
  const wantedId = selectedFromHash();
  const thingId = wantedId && twins.has(wantedId) ? wantedId : null;
  const changed = thingId !== shownSelection;
  shownSelection = thingId;
  map.select(thingId);
  scene?.select(thingId);
  objects.select(thingId);
  // A selected object is flown to, so it is seen up close beside its card.
  if (thingId && changed && reveal) current().reveal(thingId, kindOf(twins.get(thingId)) !== 'mine');
}

topbar.init({
  reveal: (thingId) => { current().reveal(thingId, true); },
  openList: (query) => objects.openDrawer(true, query),
  layout: () => planui.reportOverlays(),
});
planui.init(map, { mode: () => mode ?? wanted, scene: () => scene, setMode });
control.init();
// Notes of an open forecast stand next to their objects on the plan and in the scene.
forecast.init({ setNotes: (list) => { map.setNotes(list); scene?.setNotes(list); }, get notes() { return map.notes; } });
objects.init({
  reveal: (thingId) => {
    current().reveal(thingId, true);
    (mode === '3d' ? $('#scene-gl') : $('#plan-live')).focus({ preventScroll: true });
  },
  placed: (thingId) => map.hasPlace(thingId),
});

onPatch((twin) => {
  map.patch(twin);
  scene?.patch(twin);
  const kind = kindOf(twin);
  if (kind === 'journal') control.journal(twin);
  else if (kind === 'forecast' || /:forecast-\d+$/.test(twin.thingId)) forecast.patch(twin);
});
onChange((changed) => {
  if (!changed) { rates.reset(); map.reload(); scene?.reload(); about.refresh(); }
  topbar.update();
  planui.update(changed);
  objects.update(changed);
  control.update(changed);
});
window.addEventListener('hashchange', () => applySelection());

// The view asked for opens at once: the 3D view builds itself as soon as the twins arrive.
setMode(wanted);

// -- data -----------------------------------------------------------------------------

// The namespace of the mine: the first of those the page's server names that
// holds a mine twin. While the mine moves to a new namespace the server names
// both, and the page follows the move by itself (see watchNamespace).
let namespaces = [];
let stream = null;
const NAMESPACE_CHECK_MS = 20000;

async function chooseNamespace() {
  if (!namespaces.length) namespaces = await loadNamespaces();
  const found = await findMine(namespaces);
  if (found && found !== NAMESPACE) {
    setNamespace(found);
    if (stream) stream.restart();
  }
  return found;
}

let reloading = false;
let reloadTimer = 0;
async function reload() {
  if (reloading) return;
  reloading = true;
  clearTimeout(reloadTimer);
  let things = [];
  try {
    things = await chooseNamespace() ? await loadTwins() : [];
  } catch {
    things = [];
  } finally {
    reloading = false;
  }
  if (!things.length) {
    // The platform is starting or restarting: say so and try again.
    if (!twins.size) $('#list-wait')?.replaceChildren('Платформа пока не отвечает. Страница подключится сама, как только она будет готова.');
    reloadTimer = setTimeout(reload, RETRY_MS);
    return;
  }
  replaceAll(things);
  shownSelection = undefined;
  applySelection({ reveal: true });
  planui.reportOverlays();
}

async function watchNamespace() {
  if (namespaces.length > 1 && !reloading) {
    const found = await findMine(namespaces).catch(() => null);
    if (found && found !== NAMESPACE) reload();
  }
  setTimeout(watchNamespace, NAMESPACE_CHECK_MS);
}

let wasOffline = false;
let lastCatchUp = 0;
stream = subscribe(
  (patch) => {
    // A change to a twin the page does not know means the model has grown:
    // load everything again, but not more often than once in ten seconds.
    if (!applyChange(patch) && twins.size && Date.now() - lastCatchUp > 10000) {
      lastCatchUp = Date.now();
      reload();
    }
  },
  (online) => {
    topbar.setLinkState(online);
    if (online && wasOffline) reload();       // catch up on what was missed
    wasOffline = !online;
  },
);
reload();
setTimeout(watchNamespace, NAMESPACE_CHECK_MS);

// -- help ---------------------------------------------------------------------------

const help = $('#help');
$('#help-open').addEventListener('click', () => help.showModal());
$('#help-close').addEventListener('click', () => help.close());
help.addEventListener('click', (event) => { if (event.target === help) help.close(); });
about.init();

// Frame cost of the plan and of the 3D view, for checking the page on a
// machine without a graphics card, and the state of both for the page check.
window.minePlan = {
  frameStats: () => map.frameStats(),
  notes: () => map.notes.map((item) => item.text),
  captions: () => map.captions(),
  mode: () => mode,
  setMode,
  scene: () => scene,
};
