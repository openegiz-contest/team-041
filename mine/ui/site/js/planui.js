// What floats around the mine: the view controls at the bottom (3D or plan,
// points of view, zoom, layers), the popovers for layers and the journal, the
// chips of running scenarios and blasts under the figures, the process chain
// in the figures' detail, the tooltip and the scale bar. It also tells the
// scene and the plan which parts of the screen the panels cover, so captions
// keep out from under them and views are framed in what is left.

import { byKind, first, feature, titleOf, titleById, activeScenarios, resolveRef, cycleNow } from './store.js';
import { $, $$, el, setText, twinHref, keyState, hoverLines } from './dom.js';
import { C, LAYERS, TRUCK_GROUPS, stateColor } from './theme.js';
import { stateText, formatNumber, formatDuration, kindName, simMinutesBetween } from './labels.js';
import { rate, rateSum, RATE_NOTE } from './rates.js';
import { millTwin, grindingUnits } from './plant.js';
import { initialStyle, keepStyle } from './style3d.js';

let map = null;
let views = { mode: () => '3d', scene: () => null, setMode: () => {} };
let layer = 'none';

// -- controls at the bottom -----------------------------------------------------

// The plan has its own framings for the points of view of the 3D view.
const PLAN_VIEW = { overview: 'all', faces: 'pit', ramps: 'pit', plant: 'plant' };

function popover(id, toggle, open) {
  const panel = $(`#${id}`);
  const next = open ?? panel.hidden;
  panel.hidden = !next;
  $(`#${toggle}`).setAttribute('aria-expanded', String(next));
  if (next) document.dispatchEvent(new CustomEvent('mine:popover', { detail: id }));
  reportOverlays();
}

function wireTools() {
  for (const button of $$('#mode-buttons [data-mode]')) {
    button.addEventListener('click', () => views.setMode(button.dataset.mode));
  }
  for (const button of $$('#camera-buttons [data-camera]')) {
    button.addEventListener('click', () => {
      // «Разрез» is a view of the 3D scene only: it presses itself while it is shown (event mine:cutaway).
      if (button.dataset.camera === 'cut') {
        if (views.mode() === '3d') views.scene()?.preset('cut');
        else Promise.resolve(views.setMode('3d')).then(() => { if (views.mode() === '3d') views.scene()?.preset('cut'); });
        return;
      }
      for (const other of $$('#camera-buttons [data-camera]')) other.setAttribute('aria-pressed', String(other === button));
      if (views.mode() === '3d') views.scene()?.preset(button.dataset.camera);
      else if (PLAN_VIEW[button.dataset.camera] === 'all') map.fitAll();
      else map.fitGroup(PLAN_VIEW[button.dataset.camera]);
    });
  }
  // A point of view stays pressed only until the camera is moved some other way.
  // «Разрез» stays on while the block is turned: it stays pressed.
  const unpress = () => { for (const other of $$('#camera-buttons [data-camera]:not([data-camera="cut"])')) other.setAttribute('aria-pressed', 'false'); };
  document.addEventListener('mine:cutaway', (event) => showCutaway(event.detail));
  for (const node of [$('#scene-gl'), $('#plan-live')]) {
    node.addEventListener('pointerdown', unpress);
    node.addEventListener('wheel', unpress, { passive: true });
  }
  window.addEventListener('hashchange', unpress);
  for (const button of $$('[data-zoom]')) {
    button.addEventListener('click', () => {
      const factor = button.dataset.zoom === 'in' ? 1.6 : 1 / 1.6;
      if (views.mode() === '3d') views.scene()?.zoomBy(factor);
      else map.zoomBy(factor);
    });
  }
  for (const button of $$('#layer-buttons [data-layer]')) {
    button.addEventListener('click', () => setLayer(button.dataset.layer));
  }
  // The look of the 3D view: hologram or realistic, kept for the visitor.
  const pressStyle = (style) => { for (const other of $$('#style-buttons [data-style]')) other.setAttribute('aria-pressed', String(other.dataset.style === style)); };
  pressStyle(initialStyle());
  for (const button of $$('#style-buttons [data-style]')) {
    button.addEventListener('click', () => {
      const style = button.dataset.style;
      pressStyle(style);
      keepStyle(style);
      views.scene()?.setStyle(style);
    });
  }
  $('#layers-toggle').addEventListener('click', () => popover('layers', 'layers-toggle'));
  $('#layers-close').addEventListener('click', () => popover('layers', 'layers-toggle', false));
  $('#journal-toggle').addEventListener('click', () => popover('journal-panel', 'journal-toggle'));
  $('#journal-close').addEventListener('click', () => popover('journal-panel', 'journal-toggle', false));
  document.addEventListener('mine:popover', (event) => {
    if (event.detail !== 'layers' && !$('#layers').hidden) popover('layers', 'layers-toggle', false);
    if (event.detail !== 'journal-panel' && event.detail !== 'kpi' && !$('#journal-panel').hidden) popover('journal-panel', 'journal-toggle', false);
  });
  document.addEventListener('keydown', (event) => {
    if (event.key !== 'Escape' || document.querySelector('dialog[open]')) return;
    if (!$('#layers').hidden) popover('layers', 'layers-toggle', false);
    if (!$('#journal-panel').hidden) popover('journal-panel', 'journal-toggle', false);
    document.dispatchEvent(new CustomEvent('mine:popover', { detail: 'escape' }));
  });
}

// Block colours: none (the rock as it is) or one of the layers. The plan
// always colours its blocks, by state when nothing else is chosen.
function setLayer(name) {
  layer = name;
  map.setLayer(name === 'none' ? 'status' : name);
  views.scene()?.setLayer(name);
  for (const other of $$('#layer-buttons [data-layer]')) other.setAttribute('aria-pressed', String(other.dataset.layer === name));
  buildLegend();
}
export const currentLayer = () => layer;

// Controls for the plan or for the 3D view.
export function showMode(mode) {
  const three = mode === '3d';
  for (const button of $$('#mode-buttons [data-mode]')) button.setAttribute('aria-pressed', String(button.dataset.mode === mode));
  $('#plan-view').dataset.shown = three ? '3d' : 'plan';
  $('[data-layer="none"]').hidden = !three;
  $('#style-buttons').hidden = !three;
  $('#camera-buttons [data-camera="cut"]').hidden = !three || views.scene()?.canCutaway === false;
  if (!three && layer === 'none') {
    for (const other of $$('#layer-buttons [data-layer]')) other.setAttribute('aria-pressed', String(other.dataset.layer === 'status'));
  } else {
    for (const other of $$('#layer-buttons [data-layer]')) other.setAttribute('aria-pressed', String(other.dataset.layer === layer));
  }
  showTip(null);
  buildLegend();
  reportOverlays();
}

// Tell the plan and the 3D view what the panels cover: rectangles for the
// captions to keep out of, and insets for framing views.
// «Разрез»: its legend and caption while it is shown, the button pressed with it.
function showCutaway(info) {
  const on = Boolean(info?.on);
  $('#camera-buttons [data-camera="cut"]').setAttribute('aria-pressed', String(on));
  if (on) for (const other of $$('#camera-buttons [data-camera]:not([data-camera="cut"])')) other.setAttribute('aria-pressed', 'false');
  const box = $('#cut-legend');
  if (on) {
    for (const [key, color] of Object.entries(info.colors ?? {})) box.style.setProperty(`--cut-${key}`, color);
    const km = (m) => formatNumber(m / 1000, 1);
    $('#cut-caption').textContent = `Блок ${km(info.sizeX)} × ${km(info.sizeY)} км на ${formatNumber(info.depth, 0)} м вглубь, передняя грань — вдоль карьера. `
      + `Каолинизация затухает с глубиной (вдвое за ${formatNumber(info.weatheringM * Math.LN2, 0)} м) и сильнее на западе — поэтому глинистая руда идёт с верхних уступов.`;
    $('#cut-source').textContent = `Глинистость — по правилу модели месторождения и ${info.blocks} блокам-двойникам; рудное тело — схема (класс совпадает у ${info.bodyAgree} из ${info.blocks} блоков).`;
  }
  box.hidden = !on;
  reportOverlays();
}

export function reportOverlays() {
  const stage = $('#stage').getBoundingClientRect();
  const ribbon = $('#ribbon').getBoundingClientRect();
  const top = Math.round(Math.max($('.topbar').getBoundingClientRect().bottom, ribbon.bottom) - stage.top);
  const panelTop = `${top + 10}px`;
  if (document.documentElement.style.getPropertyValue('--panel-top') !== panelTop) document.documentElement.style.setProperty('--panel-top', panelTop);
  const rects = [];
  const visible = (node) => node && !node.hidden && node.getClientRects().length;
  const add = (node) => {
    if (!visible(node)) return null;
    const box = node.getBoundingClientRect();
    if (!box.width || !box.height) return null;
    rects.push([box.left - stage.left - 4, box.top - stage.top - 4, box.right - stage.left + 4, box.bottom - stage.top + 4]);
    return box;
  };
  add($('.topbar'));
  for (const node of $$('#kpis .kpi, #plan-chips .chip')) add(node);
  add($('#kpi-detail'));
  const drawer = add($('#drawer'));
  const details = add($('#details'));
  let stackBottom = 0;
  for (const node of $$('#control > *')) { const box = add(node); if (box) stackBottom = Math.max(stackBottom, box.bottom); }
  for (const node of $$('#dock > *')) add(node);
  add($('#layers'));
  add($('#journal-panel'));
  add($('#scale-bar'));
  const cutLegend = add($('#cut-legend'));
  const stack = $('#control').getBoundingClientRect();
  const dock = $('#dock').getBoundingClientRect();
  const left = drawer || details ? Math.round((drawer ?? details).right - stage.left + 8) : 0;
  // «Разрез» frames its block above its legend.
  const bottom = Math.round(stage.bottom - (cutLegend ? cutLegend.top : dock.top) + 8);
  const insets = { top: top + 6, right: Math.round(stage.right - stack.left + 8), bottom, left };
  map.setOverlays(rects);
  map.setInsets?.(insets);
  views.scene()?.setOverlays(rects);
  views.scene()?.setInsets?.(insets);
}

// -- chips: running scenarios and blasts ---------------------------------------------

function scenarioElapsed(twin) {
  const elapsed = feature(twin, 'elapsed_min');
  if (typeof elapsed === 'number') return elapsed;
  return simMinutesBetween(feature(twin, 'started_sim_time'), feature(first('mine'), 'sim_time'));
}

let chipsKey = '';
function updateChips() {
  const chips = [];
  for (const { twin, status, target, affected } of activeScenarios()) {
    const elapsed = scenarioElapsed(twin);
    const parts = [status === 'cancelling' ? 'отменяется' : typeof elapsed === 'number' ? formatDuration(elapsed) : 'активен'];
    if (target) parts.push(titleById(target));
    if (affected.length > 1) parts.push(`затронуто: ${affected.length}`);
    chips.push({ key: `s:${twin.thingId}`, tone: 'scenario', href: twinHref(twin.thingId), title: `Сценарий: ${titleOf(twin)}`, text: parts.join(' · ') });
  }
  const pit = first('pit');
  const blast = feature(pit, 'blast_state');
  if (pit && blast && blast !== 'idle') {
    const block = resolveRef(feature(pit, 'blast_block'));
    chips.push({ key: 'blast', tone: 'bad', href: twinHref(pit.thingId), title: 'Взрывные работы', text: `${stateText(blast)}${block ? ` · ${titleById(block)}` : ''}` });
  }
  const key = chips.map((chip) => `${chip.key}|${chip.tone}|${chip.text}`).join('\n');
  if (key === chipsKey) return;
  const layoutChanged = key.split('\n').length !== chipsKey.split('\n').length || !key !== !chipsKey;
  chipsKey = key;
  $('#plan-chips').replaceChildren(...chips.map((chip) => el('a', { className: 'chip', href: chip.href, dataset: { tone: chip.tone } },
    el('span', { className: 'chip-title' }, chip.title), el('span', { className: 'chip-text' }, chip.text))));
  if (layoutChanged) reportOverlays();
}

// -- legend ---------------------------------------------------------------------------

const swatch = (className, style = {}) => el('span', { className: `sw ${className}`, style, 'aria-hidden': 'true' });
const item = (mark, title) => el('li', {}, mark, title);
const row = (title, ...items) => el('div', { className: 'legend-row' }, el('span', { className: 'legend-title' }, title), el('ul', {}, ...items));

function buildLegend() {
  const three = views.mode() === '3d';
  const rows = [];
  const shown = three ? layer : (layer === 'none' ? 'status' : layer);
  if (shown === 'none') {
    rows.push(el('p', { className: 'legend-note' }, 'Блоки показаны как есть: взорванная порода лежит на уступе развалом. Выберите слой, чтобы окрасить блоки.'));
  } else {
    const entry = LAYERS[shown];
    rows.push(row(`Блоки: ${entry.legendTitle}`, ...entry.legend.map((e) => item(swatch(`sw-block${e.hatch ? ' sw-hatch' : ''}`, { background: e.hatch ? '' : e.color }), e.title))));
  }
  if (three) {
    rows.push(row('Круг под машиной',
      item(swatch('sw-halo', { '--c': stateColor('loading') }), 'работает'),
      item(swatch('sw-halo', { '--c': stateColor('hauling') }), 'в пути'),
      item(swatch('sw-halo', { '--c': stateColor('queue_load') }), 'очередь'),
      item(swatch('sw-halo', { '--c': stateColor('waiting') }), 'ждёт самосвал'),
      item(swatch('sw-halo', { '--c': stateColor('down') }), 'неисправна, с маячком'),
      item(swatch('sw-halo', { '--c': C.idle }), 'простой, ТО')));
    rows.push(row('На сцене',
      item(swatch('sw-barrier'), 'дорога закрыта'),
      item(swatch('sw-sign'), 'въезд запрещён'),
      item(swatch('sw-dome'), 'опасная зона взрыва'),
      item(el('span', { className: 'sw sw-queue', 'aria-hidden': 'true' }, '2'), 'очередь')));
  } else {
    rows.push(row('Самосвалы', ...TRUCK_GROUPS.map((group) => item(
      swatch(`sw-truck${group.filled ? '' : ' sw-hollow'}${group.cross ? ' sw-cross' : ''}`, { '--c': group.color }), group.title))));
    rows.push(row('Дороги',
      item(swatch('sw-road', { '--c': C.road }), 'открыта'),
      item(swatch('sw-road sw-dashed', { '--c': C.warn }), 'ограничение'),
      item(swatch('sw-road sw-dashed', { '--c': C.bad }), 'закрыта'),
      item(swatch('sw-road sw-wet', { '--c': C.road }), 'мокрая'),
      item(el('span', { className: 'sw sw-queue', 'aria-hidden': 'true' }, '2'), 'очередь')));
  }
  rows.push(row('Отметки',
    item(swatch('sw-ring', { '--c': C.select }), 'выбрано и его связи'),
    item(swatch('sw-ring sw-ring-dashed', { '--c': C.scenario }), 'затронуто сценарием')));
  $('#legend').replaceChildren(...rows);
}

// -- process chain ---------------------------------------------------------------------

const FLOW = ['faces', 'haul', 'crusher', 'conveyor', 'stockpile', 'mill', 'flotation'];

function flowData(node) {
  const num = (value, digits = 0) => (typeof value === 'number' ? formatNumber(value, digits) : '—');
  switch (node) {
    case 'faces': {
      const loaders = byKind('loader');
      const working = loaders.filter((loader) => !['down', 'idle'].includes(feature(loader, 'state'))).length;
      const dug = rateSum('faces', loaders, 'dig_rate_tph');
      return { twin: first('pit'), title: 'Забои', value: `${num(dug)} т/ч`, sub: `экск. ${working} из ${loaders.length}`,
        tone: working === loaders.length ? 'good' : working ? 'warn' : 'bad', note: RATE_NOTE };
    }
    case 'haul': {
      const mine = first('mine');
      const trucks = byKind('truck');
      const active = feature(mine, 'trucks_active') ?? trucks.filter((truck) => !['down', 'service', 'standby'].includes(feature(truck, 'state'))).length;
      return { twin: mine, title: 'Откатка', value: `${num(active)} из ${trucks.length}`, sub: `рейс ${num(cycleNow().value, 1)} мин`,
        tone: active >= trucks.length * 0.75 ? 'good' : 'warn' };
    }
    case 'crusher': {
      const twin = first('crusher');
      const state = feature(twin, 'state');
      const queue = feature(twin, 'queue_len');
      return { twin, title: 'Дробилка', value: `${num(rate(twin, 'throughput_tph'))} т/ч`,
        sub: state !== 'running' ? stateText(state) : queue > 0 ? `очередь ${queue}` : `бункер ${num(feature(twin, 'hopper_t'))} т`,
        tone: keyState(twin).tone, note: RATE_NOTE,
        meter: (feature(twin, 'hopper_t') ?? 0) / (twin?.attributes?.spec?.hopper_capacity_t || 300) };
    }
    case 'conveyor': {
      const twin = first('conveyor');
      return { twin, title: 'Конвейер', value: `${num(rate(twin, 'load_tph'))} т/ч`, sub: stateText(feature(twin, 'state')), tone: keyState(twin).tone, note: RATE_NOTE };
    }
    case 'stockpile': {
      const twin = first('stockpile');
      return { twin, title: 'Склад руды', value: `${num(feature(twin, 'level_pct'))} %`, sub: `${num(feature(twin, 'hours_of_feed'), 1)} ч подачи`,
        tone: keyState(twin).tone, meter: (feature(twin, 'level_pct') ?? 0) / 100 };
    }
    case 'mill': {
      const twin = millTwin();
      const mills = grindingUnits();
      const state = feature(twin, 'state');
      const power = mills.reduce((sum, mill) => sum + (typeof feature(mill, 'power_mw') === 'number' ? feature(mill, 'power_mw') : 0), 0);
      const split = mills.length > 1 ? mills.map((mill) => `${num(feature(mill, 'power_mw'), 1)}`).join(' + ') : '';
      return { twin, title: mills.length > 1 ? 'Измельчение' : 'Мельница', value: `${num(rate(twin, 'feed_tph'))} т/ч`,
        sub: state && state !== 'running' ? stateText(state) : `${num(power, 1)} МВт`, tone: keyState(twin).tone,
        note: split ? `${RATE_NOTE}; мощность мельниц ${split} МВт` : RATE_NOTE };
    }
    default: {
      const twin = first('flotation');
      const state = feature(twin, 'state');
      return { twin, title: 'Флотация', value: `${num(feature(twin, 'recovery_pct'), 1)} %`,
        sub: state !== 'running' ? stateText(state) : `медь ${num(rate(twin, 'cu_tph'), 1)} т/ч`, tone: keyState(twin).tone };
    }
  }
}

function buildFlow() {
  const nodes = [];
  FLOW.forEach((node, i) => {
    const data = flowData(node);
    if (!data.twin) return;
    if (nodes.length) nodes.push(el('span', { className: 'flow-arrow', 'aria-hidden': 'true' }, '›'));
    nodes.push(el('a', { className: 'flow-node', href: twinHref(data.twin.thingId), dataset: { node, index: String(i) } },
      el('span', { className: 'flow-title' }, el('span', { className: 'dot' }), data.title),
      el('span', { className: 'flow-value' }, '—'),
      data.meter !== undefined ? el('span', { className: 'meter', 'aria-hidden': 'true' }, el('span', {})) : null,
      el('span', { className: 'flow-sub' }, '')));
  });
  $('#flow').replaceChildren(...nodes);
  updateFlow();
}

function updateFlow() {
  for (const link of $$('#flow .flow-node')) {
    const data = flowData(link.dataset.node);
    $('.dot', link).dataset.tone = data.tone;
    const title = data.note ? `${data.title}: ${data.note}` : data.title;
    if (link.title !== title) link.title = title;
    setText($('.flow-value', link), data.value);
    setText($('.flow-sub', link), data.sub);
    const meter = $('.meter span', link);
    if (meter) meter.style.width = `${Math.round(Math.min(Math.max(data.meter, 0), 1) * 100)}%`;
  }
}

// -- tooltip and scale ---------------------------------------------------------------------

export function showTip(twin, clientX, clientY) {
  const tip = $('#plan-tip');
  if (!twin) { tip.hidden = true; return; }
  const [first_, ...rest] = hoverLines(twin);
  tip.replaceChildren(
    el('strong', {}, titleOf(twin)),
    el('span', { className: 'tip-kind' }, kindName(twin.attributes?.kind)),
    el('span', { className: 'tip-state' }, first_ ?? ''),
    ...rest.map((line) => el('span', { className: 'tip-line' }, line)));
  tip.hidden = false;
  const origin = $('#plan-view').getBoundingClientRect();
  const box = tip.getBoundingClientRect();
  let x = clientX - origin.left + 14;
  let y = clientY - origin.top + 14;
  if (x + box.width > origin.width - 6) x = clientX - origin.left - box.width - 12;
  if (y + box.height > origin.height - 6) y = clientY - origin.top - box.height - 12;
  tip.style.transform = `translate(${Math.round(Math.max(4, x))}px, ${Math.round(Math.max(4, y))}px)`;
}

let scaleKey = '';
export function showScale({ scale }) {
  const options = [10, 20, 50, 100, 200, 500, 1000, 2000];
  const metres = options.find((value) => value * scale >= 60) ?? 2000;
  const key = `${metres}|${Math.round(metres * scale)}`;
  if (key === scaleKey) return;
  scaleKey = key;
  $('#scale-line').style.width = `${Math.round(metres * scale)}px`;
  setText($('#scale-text'), metres >= 1000 ? `${metres / 1000} км` : `${metres} м`);
}

// -- wiring -----------------------------------------------------------------------------------

export function init(planMap, hooks) {
  map = planMap;
  if (hooks) views = hooks;
  wireTools();
  map.setLayer('status');
  buildLegend();
  new ResizeObserver(reportOverlays).observe(document.body);
  for (const node of ['#ribbon', '#dock', '#advisor', '#scenarios-card', '#modules-card', '#drawer', '#details', '#kpi-detail']) new ResizeObserver(reportOverlays).observe($(node));
}

// Call with null after the twins were (re)loaded, or with the set of changed IDs.
export function update(changed) {
  if (!changed) {
    buildLegend();
    buildFlow();
    chipsKey = '';
  } else {
    updateFlow();
  }
  updateChips();
}
