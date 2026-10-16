// Left panel: every twin in a searchable list grouped by kind, and the card of
// the selected twin with all its features, a short history of the numeric
// ones, its links and the actions that apply to it.

import { loadHistory, sendMessage } from './api.js';
import { twins, byKind, kindsPresent, feature, unitOf, kindOf, shortName, titleOf, titleById, outgoing, incoming, resolveRef, listRefs } from './store.js';
import { $, $$, el, setText, twinHref, twinLink, keyState } from './dom.js';
import { KIND_ORDER, kindTitle, kindName, featureLabel, specLabel, specValueText, relationLabel, valueText, unitText,
  formatNumber, actionsText } from './labels.js';

let selected = null;
let onReveal = () => {};
let hasPlace = () => false;

// -- list ---------------------------------------------------------------------

const OPEN_GROUPS = new Set(['truck', 'loader', 'drill-rig']);

function buildList() {
  const present = kindsPresent();
  const kinds = [...KIND_ORDER.filter((kind) => present.includes(kind)),
    ...present.filter((kind) => !KIND_ORDER.includes(kind)).sort()];
  const row = (twin, kind) => {
    const state = keyState(twin);
    return el('li', {}, el('a', { className: 'row', href: twinHref(twin.thingId), dataset: { id: twin.thingId, kind } },
      el('span', { className: 'dot', dataset: { tone: state.tone } }),
      el('span', { className: 'row-name', title: titleOf(twin) }, titleOf(twin)),
      el('span', { className: 'row-state', title: state.text }, state.text)));
  };
  const groups = kinds.map((kind) => {
    const items = byKind(kind);
    // A kind with a single twin is that twin's row, without a group to open.
    if (items.length === 1) {
      return el('ul', { className: 'group single', dataset: { kind }, 'aria-label': kindTitle(kind) }, row(items[0], kind));
    }
    return el('details', { className: 'group', open: OPEN_GROUPS.has(kind), dataset: { kind } },
      el('summary', {}, el('span', { className: 'group-title' }, kindTitle(kind)), el('span', { className: 'count' }, String(items.length))),
      el('ul', {}, ...items.map((twin) => row(twin, kind))));
  });
  $('#twin-list').replaceChildren(...groups);
  setText($('#twin-count'), String(twins.size));
  markSelected();
  applySearch();
}

function updateRow(twin) {
  const row = $(`#twin-list a[data-id="${twin.thingId}"]`);
  if (!row) return;
  const state = keyState(twin);
  setText(row.lastElementChild, state.text);
  // A long state is cut with an ellipsis in the narrow list: all of it is in the tooltip.
  if (row.lastElementChild.title !== state.text) row.lastElementChild.title = state.text;
  if (row.firstElementChild.dataset.tone !== state.tone) row.firstElementChild.dataset.tone = state.tone;
}

function markSelected() {
  for (const link of $$('#twin-list a[aria-current]')) link.removeAttribute('aria-current');
  const link = selected && $(`#twin-list a[data-id="${selected}"]`);
  if (!link) return;
  link.setAttribute('aria-current', 'true');
  const group = link.closest('details');
  if (group) group.open = true;
  const list = $('#twin-list');
  const box = link.getBoundingClientRect();
  const frame = list.getBoundingClientRect();
  if (box.top < frame.top + 26 || box.bottom > frame.bottom) list.scrollTop += box.top - frame.top - frame.height / 3;
}

// Rows that match every word of the query by name, ID, kind or state stay; groups without rows hide.
function applySearch() {
  const words = $('#twin-search').value.trim().toLowerCase().split(/\s+/).filter(Boolean);
  let shown = 0;
  for (const group of $$('#twin-list .group')) {
    let any = false;
    const title = kindTitle(group.dataset.kind).toLowerCase();
    for (const row of $$('a.row', group)) {
      const text = `${row.textContent} ${shortName(row.dataset.id)} ${title}`.toLowerCase();
      const match = words.every((word) => text.includes(word));
      row.parentElement.hidden = !match;
      if (match) { any = true; shown += 1; }
    }
    group.hidden = !any;
    if (words.length && any && group.tagName === 'DETAILS') group.open = true;
  }
  setText($('#twin-count'), words.length ? `${shown} из ${twins.size}` : String(twins.size));
}

// -- sparklines ---------------------------------------------------------------

const NO_HISTORY = new Set(['pos_x', 'pos_y', 'pos_z', 'heading_deg', 'seq', 'speedup']);
const HISTORY_RANGE = '5m';          // of real time: at ×20 that is 100 simulated minutes, a few truck cycles
const HISTORY_TEXT = 'за 5 минут';
const HISTORY_MS = 10000;
let history = new Map();
let historyFor = null;
let historyTimer = 0;

function sparkline(points, name, unit) {
  const w = 64;
  const h = 18;
  const values = points.map((p) => p.v);
  const min = Math.min(...values);
  const max = Math.max(...values);
  const t0 = points[0].t;
  const span = (points[points.length - 1].t - t0) || 1;
  const y = (v) => (max === min ? h / 2 : h - 2.5 - ((v - min) / (max - min)) * (h - 5));
  const path = points.map((p, i) => `${i ? 'L' : 'M'}${(1 + ((p.t - t0) / span) * (w - 5)).toFixed(1)},${y(p.v).toFixed(1)}`).join('');
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  svg.setAttribute('viewBox', `0 0 ${w} ${h}`);
  svg.setAttribute('class', 'spark');
  svg.setAttribute('role', 'img');
  const label = `${featureLabel(name)} ${HISTORY_TEXT}: от ${valueText(name, min, unit)} до ${valueText(name, max, unit)}`;
  svg.setAttribute('aria-label', label);
  svg.innerHTML = `<title></title><path d="${path}" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round" stroke-linecap="round"/>`
    + `<circle cx="${w - 4}" cy="${y(values[values.length - 1]).toFixed(1)}" r="2" fill="currentColor"/>`;
  svg.firstChild.textContent = label;
  return svg;
}

function paintHistory() {
  if (historyFor !== selected) return;
  const twin = twins.get(selected);
  for (const cell of $$('#details td.spark-cell')) {
    const name = cell.dataset.spark;
    const points = history.get(name);
    if (points && points.length >= 3 && !NO_HISTORY.has(name)) cell.replaceChildren(sparkline(points, name, unitOf(twin, name)));
    else cell.replaceChildren();
  }
}

async function refreshHistory() {
  clearTimeout(historyTimer);
  const thingId = selected;
  if (!thingId) return;
  try {
    const series = await loadHistory(thingId, HISTORY_RANGE);
    if (thingId !== selected) return;
    history = series;
    historyFor = thingId;
    paintHistory();
  } catch {
    // The history database may be starting; the next round tries again.
  }
  if (thingId === selected) historyTimer = setTimeout(refreshHistory, HISTORY_MS);
}

// -- card of the selected twin ------------------------------------------------

function valueNode(twin, name) {
  const value = feature(twin, name);
  if (name === 'route' || name === 'affected' || name === 'units_at_risk') {
    const ids = listRefs(twin, name);
    // A route is shown by its ends here; every node of it is under the links below.
    if (name === 'route' && ids.length > 2) {
      return el('span', { className: 'chain' }, twinLink(ids[0]), ' → … → ', twinLink(ids[ids.length - 1]), ` (узлов: ${ids.length})`);
    }
    if (ids.length) {
      const list = el('span', { className: 'chain' });
      ids.forEach((id, i) => list.append(i ? (name === 'route' ? ' → ' : ', ') : '', twinLink(id)));
      return list;
    }
  }
  if (/^actions_[ab]$|^refused_actions$|^option\d+_actions$/.test(name)) {
    return document.createTextNode(actionsText(value, (part) => (resolveRef(part) ? titleById(resolveRef(part)) : part)));
  }
  const ref = name === 'thing' || typeof value === 'string' ? resolveRef(value) : null;
  if (ref && ref !== twin.thingId) return twinLink(ref);
  return document.createTextNode(valueText(name, value, unitOf(twin, name)));
}

function featureRows(twin) {
  const kind = kindOf(twin);
  return Object.keys(twin.features ?? {}).map((name) => {
    const numeric = typeof feature(twin, name) === 'number';
    // A value that is not a number has no history: it takes the room of the graph too.
    return el('tr', {},
      el('th', { scope: 'row', title: `Имя в двойнике: ${name}` }, featureLabel(name, kind)),
      el('td', { className: 'value', colSpan: numeric ? 1 : 2, dataset: { feature: name } }, valueNode(twin, name)),
      numeric ? el('td', { className: 'spark-cell', dataset: { spark: name } }) : null);
  });
}

function linkGroups(twin) {
  const groups = [];
  const add = (title, ids) => {
    groups.push(el('div', { className: 'link-group' },
      el('span', { className: 'link-title' }, title),
      el('span', { className: 'link-list' }, ...ids.flatMap((id, i) => [i ? ', ' : '', twinLink(id)]))));
  };
  const out = outgoing(twin);
  for (const { relation, ids, source } of out) {
    if (source === 'feature' && relation === 'affected') continue;      // shown in the features and on the scenario card
    add(relationLabel(relation), ids);
  }
  // Twins that point here, by relation: "Экскаватор рейса у: Самосвал №1, ...".
  const back = new Map();
  for (const { relation, id } of incoming(twin.thingId)) {
    if (relation === 'targets' || relation === 'default_target' || relation === 'valid_targets') continue;
    if (!back.has(relation)) back.set(relation, []);
    back.get(relation).push(id);
  }
  for (const [relation, ids] of back) add(`${relationLabel(relation)} ←`, ids);
  return groups;
}

function geoText(geo) {
  if (geo.path) return `линия из ${geo.path.length} точек, от ${formatNumber(geo.path[0][0], 0)}; ${formatNumber(geo.path[0][1], 0)} до ${formatNumber(geo.path[geo.path.length - 1][0], 0)}; ${formatNumber(geo.path[geo.path.length - 1][1], 0)} м`;
  if (geo.polygon) return `контур из ${geo.polygon.length} точек, отметка ${formatNumber(geo.z, 0)} м`;
  if (typeof geo.x === 'number') return `X ${formatNumber(geo.x, 0)} м, Y ${formatNumber(geo.y, 0)} м, отметка ${formatNumber(geo.z, 0)} м`;
  return '';
}

// Buttons for what can be done with this twin from the page.
function actions(twin) {
  const kind = kindOf(twin);
  const buttons = [];
  const hint = el('p', { className: 'hint', role: 'status' });
  const send = async (thingId, subject, body, text) => {
    setText(hint, 'Команда отправлена…');
    try {
      await sendMessage(thingId, subject, body);
      setText(hint, text);
    } catch (error) {
      setText(hint, `Команда не принята: ${error.message}`);
    }
  };
  if (hasPlace(twin.thingId)) {
    buttons.push(el('button', { type: 'button', dataset: { act: 'reveal' }, onclick: () => onReveal(twin.thingId) }, 'Показать крупно'));
  }
  if (kind === 'mine') {
    const paused = feature(twin, 'sim_state') === 'paused';
    for (const speedup of [1, 10, 20, 60]) {
      buttons.push(el('button', { type: 'button', 'aria-pressed': String(feature(twin, 'speedup') === speedup), dataset: { act: 'speed' },
        onclick: () => send(twin.thingId, 'set-speed', { speedup }, `Скорость ×${speedup} отправлена.`) }, `Скорость ×${speedup}`));
    }
    buttons.push(el('button', { type: 'button', dataset: { act: 'pause' },
      onclick: () => send(twin.thingId, paused ? 'resume' : 'pause', {}, paused ? 'Модель продолжает работу.' : 'Пауза отправлена.') },
    paused ? 'Продолжить' : 'Пауза'));
  }
  if (kind === 'scenario') {
    const active = feature(twin, 'status') !== 'inactive';
    buttons.push(el('button', { type: 'button', className: 'start', disabled: active, dataset: { act: 'scenario-start' },
      onclick: () => send(twin.thingId, 'activate', {}, 'Запуск отправлен, сценарий берёт объект по умолчанию.') }, 'Запустить'));
    buttons.push(el('button', { type: 'button', disabled: !active, dataset: { act: 'scenario-cancel' },
      onclick: () => send(twin.thingId, 'cancel', {}, 'Отмена отправлена.') }, 'Отменить'));
  }
  if (kind === 'module') {
    const on = feature(twin, 'enabled') === true;
    buttons.push(el('button', { type: 'button', dataset: { act: 'module-toggle' },
      onclick: () => send(twin.thingId, 'set-enabled', { enabled: !(feature(twin, 'enabled') === true) }, 'Команда отправлена модулю.') },
    on ? 'Выключить модуль' : 'Включить модуль'));
  }
  // Scenarios that can be aimed at this twin.
  for (const scenario of byKind('scenario')) {
    const targets = [].concat(scenario.attributes?.links?.targets ?? []);
    if (!targets.includes(twin.thingId)) continue;
    const status = feature(scenario, 'status');
    const here = resolveRef(feature(scenario, 'target')) === twin.thingId;
    if (status !== 'inactive' && here) {
      buttons.push(el('button', { type: 'button', dataset: { act: 'cancel', scenario: scenario.thingId },
        onclick: () => send(scenario.thingId, 'cancel', {}, 'Отмена отправлена.') }, `Отменить сценарий «${titleOf(scenario)}»`));
    } else {
      buttons.push(el('button', { type: 'button', className: 'start', disabled: status !== 'inactive', dataset: { act: 'activate', scenario: scenario.thingId },
        onclick: () => send(scenario.thingId, 'activate', { target: twin.thingId }, 'Запуск отправлен.') }, `Сценарий «${titleOf(scenario)}» здесь`));
    }
  }
  return buttons.length ? [el('div', { className: 'actions' }, ...buttons), hint] : [];
}

function actionsKey(twin) {
  return byKind('scenario').map((scenario) => `${feature(scenario, 'status')}${feature(scenario, 'target')}`).join('|')
    + `|${feature(twin, 'status')}|${feature(twin, 'enabled')}|${feature(twin, 'sim_state')}|${feature(twin, 'speedup')}`;
}

let builtFeatures = '';
let builtActions = '';

function buildDetails() {
  const root = $('#details');
  const twin = twins.get(selected);
  if (!twin) {
    root.hidden = true;
    root.replaceChildren();
    return;
  }
  const attrs = twin.attributes ?? {};
  const kind = kindOf(twin);
  const state = keyState(twin);
  const spec = Object.entries(attrs.spec ?? {});
  const links = linkGroups(twin);
  // Moving machines keep their place in features (pos_x, pos_y); `home` is where they stood when the model started.
  const homeId = typeof attrs.home === 'string' ? resolveRef(attrs.home) : null;
  const homeText = attrs.home && typeof attrs.home === 'object' ? geoText(attrs.home) : '';
  const place = geoText(attrs.geo ?? {});
  builtFeatures = Object.keys(twin.features ?? {}).join(',');
  builtActions = actionsKey(twin);
  root.hidden = !$('#drawer').hidden;
  root.replaceChildren(
    el('header', { className: 'details-head' },
      el('div', { className: 'details-title' },
        el('h2', {}, titleOf(twin)),
        el('a', { className: 'close', href: '#/', 'aria-label': 'Закрыть карточку объекта', title: 'Закрыть' }, '×')),
      el('p', { className: 'ident' }, `${kindName(kind)} · `, el('code', {}, twin.thingId)),
      el('p', { className: 'state-line' }, el('span', { className: 'dot', dataset: { tone: state.tone } }), el('span', { id: 'details-state' }, state.text)),
      el('div', { id: 'details-actions' }, ...actions(twin))),
    el('div', { className: 'details-body scroll' },
      el('h3', {}, 'Показатели ', el('span', { className: 'muted', title: 'График: последние 5 минут настоящего времени' }, `· график ${HISTORY_TEXT}`)),
      el('table', { className: 'kv' }, el('tbody', { id: 'details-features' }, ...featureRows(twin))),
      ...(links.length ? [el('h3', {}, 'Связи'), el('div', { className: 'links', id: 'details-links' }, ...links)] : []),
      ...(spec.length ? [el('h3', {}, 'Паспорт'), el('table', { className: 'kv' }, el('tbody', {}, ...spec.map(([key, value]) => el('tr', {},
        el('th', { scope: 'row', title: `Имя в паспорте двойника: ${key}` }, specLabel(key)),
        el('td', { className: 'value', colSpan: 2 }, key === 'road_class' ? valueText(key, value)
          : typeof value === 'string' && resolveRef(value) ? twinLink(resolveRef(value)) : specValueText(key, value))))))] : []),
      ...(place ? [el('h3', {}, 'Положение'), el('p', { className: 'place' }, place)] : []),
      ...(homeId || homeText ? [el('h3', {}, 'Начальное положение'), el('p', { className: 'place', title: String(attrs.home?.label ?? '') },
        homeId ? twinLink(homeId) : homeText, el('br'), el('span', { className: 'muted' }, 'где машина стояла на начало работы модели; где она сейчас — в показателях «Координата X» и «Координата Y»'))] : [])));
  if (historyFor === selected) paintHistory();
}

let linksKey = '';
function updateDetails(twin) {
  if (Object.keys(twin.features ?? {}).join(',') !== builtFeatures) { buildDetails(); return; }
  for (const name of Object.keys(twin.features ?? {})) {
    const cell = $(`#details-features td[data-feature="${name}"]`);
    if (!cell) continue;
    const node = valueNode(twin, name);
    if (cell.textContent !== node.textContent) cell.replaceChildren(node);
  }
  const state = keyState(twin);
  setText($('#details-state'), state.text);
  const dot = $('#details .state-line .dot');
  if (dot && dot.dataset.tone !== state.tone) dot.dataset.tone = state.tone;
  // Links kept in features change while the mine runs (a truck's segment, its route).
  const key = outgoing(twin).map(({ relation, ids }) => `${relation}:${ids.join(',')}`).join(';');
  const holder = $('#details-links');
  if (holder && key !== linksKey) {
    linksKey = key;
    holder.replaceChildren(...linkGroups(twin));
  }
}

function updateActions() {
  const twin = twins.get(selected);
  if (!twin) return;
  const key = actionsKey(twin);
  if (key === builtActions) return;
  builtActions = key;
  const hint = $('#details-actions .hint')?.textContent ?? '';
  $('#details-actions').replaceChildren(...actions(twin));
  setText($('#details-actions .hint'), hint);
}

// -- wiring -------------------------------------------------------------------

// The list is a drawer, closed by default: the button «Объекты» and the
// search field open it; choosing an object closes it and opens its card.
export function openDrawer(open = true, query = null) {
  const drawer = $('#drawer');
  if (query !== null) { $('#twin-search').value = query; applySearch(); }
  drawer.hidden = !open;
  $('#drawer-toggle').setAttribute('aria-expanded', String(open));
  $('#details').hidden = open || !twins.has(selected);
  if (open) {
    document.dispatchEvent(new CustomEvent('mine:popover', { detail: 'drawer' }));
    markSelected();
  }
}

export function init({ reveal, placed }) {
  onReveal = reveal;
  hasPlace = placed;
  const search = $('#twin-search');
  search.addEventListener('input', () => { applySearch(); openDrawer(true); });
  search.addEventListener('focus', () => { if (search.value.trim()) openDrawer(true); });
  search.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') { search.value = ''; applySearch(); openDrawer(false); }
    if (event.key === 'Enter') {
      // The first object that matches.
      const row = $$('#twin-list a.row').find((link) => !link.parentElement.hidden && !link.closest('.group').hidden);
      if (row) { window.location.hash = row.getAttribute('href'); openDrawer(false); }
    }
  });
  $('#drawer-toggle').addEventListener('click', () => openDrawer($('#drawer').hidden));
  $('#drawer-close').addEventListener('click', () => openDrawer(false));
  $('#twin-list').addEventListener('click', (event) => {
    const row = event.target.closest('a.row');
    if (!row) return;
    // The same object again still flies to it.
    if (row.dataset.id === selected) onReveal(selected);
    openDrawer(false);
  });
  document.addEventListener('mine:popover', (event) => {
    if (event.detail === 'escape' && !$('#drawer').hidden) openDrawer(false);
  });
}

// Call with null after the twins were (re)loaded, or with the set of changed IDs.
export function update(changed) {
  if (!changed) {
    buildList();
    linksKey = '';
    buildDetails();
    if (selected) refreshHistory();
    return;
  }
  let scenarioChanged = false;
  for (const id of changed) {
    const twin = twins.get(id);
    if (!twin) continue;
    updateRow(twin);
    if (id === selected) updateDetails(twin);
    if (kindOf(twin) === 'scenario' || kindOf(twin) === 'module') scenarioChanged = true;
  }
  if (scenarioChanged || changed.has(selected)) updateActions();
}

export function select(thingId) {
  selected = thingId && twins.has(thingId) ? thingId : null;
  history = new Map();
  historyFor = null;
  linksKey = '';
  buildDetails();
  // The card takes the place of the list.
  if (selected && !$('#drawer').hidden) openDrawer(false);
  markSelected();
  refreshHistory();
}
