// Two futures. A card asks the simulator to fork the live mine and play it
// 12 hours ahead twice: branch A as things are, branch B with one change (a
// scenario started or cancelled, a module switched off). The answer arrives as
// the twin `forecast-1`, the page's own slot; slots 2 to 5 belong to the
// advisor. Only one panel is open at a time: a newer request replaces it.

import { sendMessage } from './api.js';
import { NAMESPACE, twins, first, feature, shortName, resolveRef } from './store.js';
import { $, el, setText } from './dom.js';
import { formatFixed, formatNumber, formatMillions, formatTenge, formatDelta, formatSimClock, parseSeries } from './labels.js';

export const HORIZON_H = 12;
export const PAGE_SLOT = 1;
export const slotId = (slot) => `${NAMESPACE}:forecast-${slot}`;
export const available = () => twins.has(slotId(PAGE_SLOT));

const LATE_MS = 90000;               // no answer by then: say so and offer to ask again
const SAME_SUBJECT_MS = 15000;       // an unchanged answer to the same question is accepted after this

// Who asks: one name per open page, so that two visitors do not take each other's answers.
const REQUESTER = `page-${Math.random().toString(36).slice(2, 7)}`;

let map = null;
let active = null;                   // the open panel and the request behind it
let counter = 0;                     // requests sent from this page

export function init(planMap) {
  map = planMap;
}

// -- reading a forecast twin --------------------------------------------------

const RESULT = ['a_cu_t', 'b_cu_t', 'a_ore_t', 'b_ore_t', 'a_margin_kzt', 'b_margin_kzt', 'delta_margin_kzt', 'delta_total_kzt', 'a_co2_t', 'b_co2_t',
  'a_series_mill_tph', 'b_series_mill_tph', 'a_series_stockpile_pct', 'b_series_stockpile_pct', 'summary_text'];

export function readForecast(twin) {
  const f = (name) => feature(twin, name);
  const num = (name) => (typeof f(name) === 'number' ? f(name) : undefined);
  const branch = (x) => {
    const revenue = num(`${x}_revenue_kzt`);
    const diesel = num(`${x}_diesel_kzt`);
    const power = num(`${x}_power_kzt`);
    // The variable margin proper (copper less diesel and power), and apart
    // from it the adjustment: what the ore left on the stockpiles and the waste
    // already stripped are worth at the end of the horizon, less rehandling.
    // A model that publishes the adjustment has a plain margin already; an
    // older one folded stocks into the margin, so it is taken apart here.
    const own = num(`${x}_adjust_kzt`);
    const plain = typeof own === 'number' ? num(`${x}_margin_kzt`)
      : typeof revenue === 'number' && typeof diesel === 'number' && typeof power === 'number' ? revenue - diesel - power : num(`${x}_margin_kzt`);
    const stock = num(`${x}_stock_kzt`);
    const strip = num(`${x}_strip_kzt`);
    const adjust = own ?? (typeof stock === 'number' || typeof strip === 'number' ? (stock ?? 0) + (strip ?? 0) : undefined);
    const total = num(`${x}_total_kzt`) ?? (typeof plain === 'number' && typeof adjust === 'number' ? plain + adjust : undefined);
    return { cu: num(`${x}_cu_t`), ore: num(`${x}_ore_t`), milled: num(`${x}_milled_t`), margin: plain, adjust, total,
      revenue, grade: num(`${x}_grade_kzt`), stockEnd: num(`${x}_stock_end_h`), overloads: num(`${x}_overloads`),
      co2: num(`${x}_co2_t`), mill: parseSeries(f(`${x}_series_mill_tph`)), stockpile: parseSeries(f(`${x}_series_stockpile_pct`)) };
  };
  const a = branch('a');
  const b = branch('b');
  const diff = (x, y) => (typeof x === 'number' && typeof y === 'number' ? y - x : undefined);
  return {
    status: f('status') ?? 'idle',
    requestId: String(f('request_id') ?? ''),
    subject: f('subject') ?? '',
    requestedBy: f('requested_by') ?? '',
    horizon: num('horizon_h') ?? HORIZON_H,
    labelA: String(f('label_a') ?? ''),
    labelB: String(f('label_b') ?? ''),
    summary: String(f('summary_text') ?? ''),
    verdictText: String(f('verdict_text') ?? ''),
    copperText: String(f('copper_text') ?? ''),
    assumption: String(f('assumption_text') ?? ''),
    error: String(f('error_text') ?? ''),
    forkedAt: String(f('fork_sim_time') ?? ''),
    a,
    b,
    delta: {
      cu: num('delta_cu_t') ?? diff(a.cu, b.cu),
      ore: diff(a.ore, b.ore),
      milled: diff(a.milled, b.milled),
      margin: diff(a.margin, b.margin) ?? num('delta_margin_kzt'),
      adjust: num('delta_adjust_kzt') ?? diff(a.adjust, b.adjust),
      total: num('delta_total_kzt') ?? diff(a.total, b.total),
      // The copper's own worth: the revenue less the copper above the mine's average grade that the stocks gave up.
      cuNet: typeof diff(a.revenue, b.revenue) === 'number' && typeof diff(a.grade, b.grade) === 'number' ? diff(a.revenue, b.revenue) + diff(a.grade, b.grade) : undefined,
      stockEnd: diff(a.stockEnd, b.stockEnd),
      co2: num('delta_co2_t') ?? diff(a.co2, b.co2),
    },
  };
}

const snapshot = (twin) => RESULT.map((name) => String(feature(twin, name) ?? '')).join('|');

// -- charts -------------------------------------------------------------------

const SVG = 'http://www.w3.org/2000/svg';
const COLOR_A = '#9aa6b1';
const COLOR_B = '#f2a65a';

function svgNode(tag, attrs = {}, text) {
  const node = document.createElementNS(SVG, tag);
  for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, String(value));
  if (text !== undefined) node.textContent = text;
  return node;
}

// A round top for the value axis: 2 980 -> 3 000, 64 -> 70, 100 stays.
function niceTop(value) {
  if (!(value > 0)) return 1;
  const power = 10 ** Math.floor(Math.log10(value));
  const step = [1, 1.2, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10].find((m) => m * power >= value * 1.02) ?? 10;
  return step * power;
}

const pathOf = (values, x, y) => values.map((v, i) => `${i ? 'L' : 'M'}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join('');

// Line A (dashed grey) against line B (solid, warm), each named at its end.
// `top` fixes the top of the axis (100 for a percentage); the axis starts at 0.
export function chartSvg({ a, b, width, height = 74, unit, top, title, horizon = HORIZON_H }) {
  const left = 34;
  const right = 18;
  const upper = 6;
  const lower = 15;
  const count = Math.max(a.length, b.length);
  const max = top ?? niceTop(Math.max(...a, ...b, 0));
  const plotW = width - left - right;
  const plotH = height - upper - lower;
  // Hourly values: one per hour from the first hour on, or with the moment of the fork in front.
  const hourOf = (i) => (count === horizon ? i + 1 : count > 1 ? (i / (count - 1)) * horizon : 0);
  const at = (hour) => left + (hour / horizon) * plotW;
  const x = (i) => at(hourOf(i));
  const y = (v) => upper + plotH - (Math.max(0, Math.min(v, max)) / max) * plotH;
  const svg = svgNode('svg', { viewBox: `0 0 ${width} ${height}`, width, height, class: 'fc-svg', role: 'img' });
  const end = (values) => values[values.length - 1];
  const words = (values, name) => (values.length
    ? `${name}: через ${horizon} ч ${formatNumber(end(values), 0)} ${unit}, наименьшее за срок ${formatNumber(Math.min(...values), 0)}, наибольшее ${formatNumber(Math.max(...values), 0)}`
    : `${name}: нет данных`);
  svg.append(svgNode('title', {}, `${title}. ${words(a, 'вариант А')}; ${words(b, 'вариант Б')}.`));

  for (const level of [0, max / 2, max]) {
    const py = y(level);
    svg.append(svgNode('line', { x1: left, x2: width - right + 4, y1: py, y2: py, class: level ? 'fc-grid' : 'fc-axis' }));
    svg.append(svgNode('text', { x: left - 4, y: py + 3.5, 'text-anchor': 'end', class: 'fc-tick' }, formatNumber(level, 0)));
  }
  const ticks = [[0, 'сейчас', 'start'], [horizon / 2, `+${horizon / 2} ч`, 'middle'], [horizon, `+${horizon} ч`, 'end']];
  for (const [hour, text, anchor] of ticks) {
    svg.append(svgNode('text', { x: at(hour) + (anchor === 'start' ? -2 : anchor === 'end' ? 2 : 0), y: height - 3, 'text-anchor': anchor, class: 'fc-tick' }, text));
  }
  if (a.length) svg.append(svgNode('path', { d: pathOf(a, x, y), class: 'fc-line fc-line-a', stroke: COLOR_A }));
  if (b.length) svg.append(svgNode('path', { d: pathOf(b, x, y), class: 'fc-line fc-line-b', stroke: COLOR_B }));

  // Names at the ends, pushed apart when the lines end close together.
  const top_ = upper + 4;
  const bottom = upper + plotH + 3;
  const clampY = (v) => Math.max(top_, Math.min(bottom, v));
  let ya = a.length ? clampY(y(end(a))) : null;
  let yb = b.length ? clampY(y(end(b))) : null;
  if (ya !== null && yb !== null && Math.abs(ya - yb) < 10) {
    // The higher line keeps the upper name; both stay inside the chart.
    const aFirst = end(a) >= end(b);
    let high = Math.min(ya, yb);
    let low = high + 10;
    if (low > bottom) { low = bottom; high = low - 10; }
    ya = aFirst ? high : low;
    yb = aFirst ? low : high;
  }
  if (ya !== null) svg.append(svgNode('text', { x: width - right + 6, y: ya + 3.5, class: 'fc-end', fill: COLOR_A }, 'А'));
  if (yb !== null) svg.append(svgNode('text', { x: width - right + 6, y: yb + 3.5, class: 'fc-end', fill: COLOR_B }, 'Б'));
  return svg;
}

// A small pair of lines for a list row: no axes, just the shape of both futures.
export function sparkSvg(a, b, width = 76, height = 24, label = '') {
  const svg = svgNode('svg', { viewBox: `0 0 ${width} ${height}`, width, height, class: 'fc-spark', role: 'img' });
  if (label) svg.append(svgNode('title', {}, label));
  const values = [...a, ...b];
  if (!values.length) return svg;
  // The shape matters here, not the level: the range of both lines fills the height.
  const low = Math.min(...values);
  const high = Math.max(...values);
  const pad = Math.max((high - low) * 0.12, Math.abs(high) * 0.01, 1e-6);
  const span = Math.max(a.length, b.length) - 1 || 1;
  const x = (i) => 1.5 + (i / span) * (width - 3);
  const y = (v) => height - 2 - ((v - (low - pad)) / (high - low + 2 * pad)) * (height - 4);
  svg.append(svgNode('rect', { x: 0, y: 0, width, height, rx: 3, class: 'fc-spark-bg' }));
  if (a.length) svg.append(svgNode('path', { d: pathOf(a, x, y), class: 'fc-line fc-line-a', stroke: COLOR_A }));
  if (b.length) svg.append(svgNode('path', { d: pathOf(b, x, y), class: 'fc-line fc-line-b', stroke: COLOR_B }));
  return svg;
}

// Line samples for the legend.
export function keySample(which) {
  const svg = svgNode('svg', { viewBox: '0 0 22 8', width: 22, height: 8, class: 'fc-sample', 'aria-hidden': 'true' });
  svg.append(svgNode('path', { d: 'M1,4L21,4', class: `fc-line fc-line-${which}`, stroke: which === 'a' ? COLOR_A : COLOR_B }));
  return svg;
}

// -- the table of results -----------------------------------------------------

// [key, caption, read, format of a value, format of a difference, which direction is good for the shift].
// The shift's goal is copper in concentrate together with the ore left for the next shift, so the tone of a
// difference says whether it is good for that goal, not whether it is positive: the decision row (the total
// with the adjustment) leads; the copper is good only for what it adds beyond copper taken from the stocks;
// margin and adjustment are the two parts of the total and carry no tone of their own.
const ROWS = [
  ['total', 'Итог с поправкой, млн ₸', (s) => s.total, (v) => formatMillions(v), (v) => formatMillions(v, { sign: true }), 'up'],
  ['cu', 'Медь в концентрате, т', (s) => s.cu, (v) => formatFixed(v, 1), (v) => formatDelta(v, 1), 'copper'],
  ['ore', 'Руда добыта, т', (s) => s.ore, (v) => formatFixed(v, 0), (v) => formatDelta(v, 0), 'up'],
  ['milled', 'Руда переработана, т', (s) => s.milled, (v) => formatFixed(v, 0), (v) => formatDelta(v, 0), 'up'],
  ['stockEnd', 'Запас склада к концу, ч', (s) => s.stockEnd, (v) => formatFixed(v, 1), (v) => formatDelta(v, 1), 'up'],
  ['margin', 'Переменная маржа, млн ₸', (s) => s.margin, (v) => formatMillions(v), (v) => formatMillions(v, { sign: true }), ''],
  ['adjust', 'Поправка: запасы руды и вскрыша, млн ₸', (s) => s.adjust, (v) => formatMillions(v), (v) => formatMillions(v, { sign: true }), ''],
  ['co2', 'Выбросы CO₂, т', (s) => s.co2, (v) => formatFixed(v, 1), (v) => formatDelta(v, 1), 'down'],
];

const ROW_NOTES = {
  total: 'По этой цифре сравниваются варианты: маржа за срок прогноза плюс поправка на то, что срок оставляет следующей смене',
  cu: 'Цвет — по меди сверх той, что взята из запасов: богатая руда со склада даёт медь сейчас, но склад теряет столько же',
  stockEnd: 'На сколько часов питания мельницы хватит склада дроблёной руды к концу срока, при уставке мельницы на тот момент',
  margin: 'Выручка за оплачиваемую медь минус затраты на дизельное топливо и электроэнергию за срок прогноза',
  adjust: 'Не деньги за срок прогноза, а задел на потом: руда везде оценена одинаково — медь сверх средней руды месторождения плюс работа парка, уже вложенная в добытую руду и снятую вскрышу, минус перевалка, которая ещё нужна руде на рудных складах',
};

// Whether the difference B − A of a row is good or bad for the shift's goal ('' when it is neither, or when it
// is within the scatter of a forecast). Copper is good only for what it adds beyond the copper taken from the
// stocks: more copper from a richer stock is no gain, and less copper with the rich ore kept is no loss.
const CU_SCATTER_T = 1.4;            // two standard deviations of a forecast's copper (params.yaml, forecast.noise_cu_t)
function toneOf(key, better, result) {
  const d = result.delta[key];
  if (typeof d !== 'number' || !better || d === 0) return '';
  if (better === 'copper') {
    if (Math.abs(d) < CU_SCATTER_T) return '';
    const net = result.delta.cuNet;
    if (typeof net === 'number' && (Math.abs(net) < 1e6 || Math.sign(net) !== Math.sign(d))) return '';
    return d > 0 ? 'better' : 'worse';
  }
  if (key === 'total' && /в пределах разброса/.test(result.verdictText)) return '';
  // Half a per cent of a branch's figure is no difference worth a colour (a few tonnes of CO₂ in 850).
  const base = Math.abs(result.a[key] ?? 0);
  const small = key === 'total' ? 5e4 : key === 'stockEnd' ? 0.25 : 0.005 * base;
  if (Math.abs(d) <= small) return '';
  return (better === 'up') === (d > 0) ? 'better' : 'worse';
}

function resultTable(result) {
  const head = el('tr', {}, el('th', { scope: 'col' }, `За ${formatNumber(result.horizon, 0)} ч`),
    el('th', { scope: 'col', title: result.labelA }, 'А'), el('th', { scope: 'col', title: result.labelB }, 'Б'),
    el('th', { scope: 'col' }, 'Б − А'));
  const present = ROWS.filter(([, , read]) => typeof read(result.a) === 'number' || typeof read(result.b) === 'number');
  const rows = present.map(([key, caption, read, format, formatDiff, better]) => {
    const a = read(result.a);
    const b = read(result.b);
    const d = result.delta[key];
    const text = typeof d === 'number' ? formatDiff(d) : '—';
    const tone = /[1-9]/.test(text) ? toneOf(key, better, result) : '';
    return el('tr', { dataset: { row: key, lead: String(key === 'total') } },
      el('th', { scope: 'row', title: ROW_NOTES[key] ?? '' }, caption),
      el('td', { dataset: { cell: 'a' } }, typeof a === 'number' ? format(a) : '—'),
      el('td', { dataset: { cell: 'b' } }, typeof b === 'number' ? format(b) : '—'),
      el('td', { dataset: { cell: 'delta', tone } }, text));
  });
  return el('table', { className: 'fc-table' }, el('thead', {}, head), el('tbody', {}, ...rows));
}

// The decision in one line: the total with the adjustment, as the forecast says it; then copper and CO₂.
function verdict(result, lead) {
  const parts = [];
  // The forecast's own sentence names the total; without it (an older model) the figure leads here.
  if (!result.verdictText && typeof result.delta.total === 'number') parts.push(`итог с поправкой ${formatTenge(result.delta.total, { sign: true })}`);
  if (typeof result.delta.cu === 'number') parts.push(`медь ${formatDelta(result.delta.cu, 1)} т`);
  if (typeof result.delta.co2 === 'number') parts.push(`CO₂ ${formatDelta(result.delta.co2, 1)} т`);
  if (result.verdictText) return `${result.verdictText}${parts.length ? ` (${parts.join(', ')})` : ''}.`;
  return parts.length ? `${lead}: ${parts.join(', ')} за ${formatNumber(result.horizon, 0)} ч.` : '';
}

// -- the panel ----------------------------------------------------------------

function buildPanel(request) {
  const close = el('button', { type: 'button', className: 'fc-close', 'aria-label': `Закрыть прогноз: ${request.title}`, title: 'Закрыть прогноз' }, '×');
  close.addEventListener('click', () => closePanel());
  const panel = el('section', { className: 'forecast', dataset: { state: 'sent' }, 'aria-label': `Прогноз на ${HORIZON_H} часов: ${request.title}` },
    el('header', { className: 'fc-head' },
      el('h4', {}, request.heading),
      close),
    request.subheading ? el('p', { className: 'fc-sub' }, request.subheading) : null,
    el('p', { className: 'fc-legend' },
      el('span', { className: 'fc-key' }, keySample('a'), el('b', {}, 'А'), ` ${request.labelA}`),
      el('span', { className: 'fc-key' }, keySample('b'), el('b', {}, 'Б'), ` ${request.labelB}`)),
    el('p', { className: 'fc-status', role: 'status' }, 'Запрос отправлен, модель готовится…'),
    el('div', { className: 'fc-body', hidden: true }));
  return panel;
}

function setState(state, text) {
  if (!active) return;
  active.panel.dataset.state = state;
  setText($('.fc-status', active.panel), text);
  $('.fc-status', active.panel).hidden = !text;
}

function renderResult(result) {
  const { panel, request } = active;
  const body = $('.fc-body', panel);
  const width = Math.max(220, Math.round(body.parentElement.clientWidth || 280));
  const charts = [
    ['Подача руды на мельницу, т/ч', result.a.mill, result.b.mill, 'т/ч', undefined],
    ['Склад дроблёной руды, %', result.a.stockpile, result.b.stockpile, '%', 100],
  ].filter(([, a, b]) => a.length || b.length).map(([title, a, b, unit, top]) => el('figure', { className: 'fc-chart' },
    el('figcaption', {}, title), chartSvg({ a, b, width, unit, top, title, horizon: result.horizon })));
  const lead = verdict(result, request.verdictLead);
  const at = formatSimClock(result.forkedAt || feature(first('mine'), 'sim_time'));
  const tone = toneOf('total', 'up', result);
  body.replaceChildren(
    lead ? el('p', { className: 'fc-verdict', dataset: { tone } }, lead) : null,
    result.copperText ? el('p', { className: 'fc-copper' }, result.copperText) : null,
    result.assumption ? el('p', { className: 'fc-assume' }, result.assumption) : null,
    ...charts,
    resultTable(result),
    result.summary ? el('details', { className: 'fc-more' }, el('summary', {}, 'Подробнее о прогнозе'),
      el('p', { className: 'fc-summary' }, result.summary)) : null,
    el('p', { className: 'fc-foot' }, `Копия рудника снята в ${at} модельного времени; настоящий рудник прогноз не меняет.`));
  body.hidden = false;
  setState('ready', '');
  showNotes(result);
}

// The end of both futures on the plan, next to the objects they concern.
function showNotes(result) {
  if (!map) return;
  const notes = [];
  const end = (values) => (values.length ? formatNumber(values[values.length - 1], 0) : '—');
  const hours = formatNumber(result.horizon, 0);
  const stockpile = first('stockpile');
  if (stockpile && (result.a.stockpile.length || result.b.stockpile.length)) {
    notes.push({ id: stockpile.thingId, text: `Склад через ${hours} ч: А ${end(result.a.stockpile)} % · Б ${end(result.b.stockpile)} %` });
  }
  const mill = first('mill');
  if (mill && (result.a.mill.length || result.b.mill.length)) {
    notes.push({ id: mill.thingId, text: `Мельница через ${hours} ч: А ${end(result.a.mill)} · Б ${end(result.b.mill)} т/ч` });
  }
  map.setNotes(notes);
}

export function closePanel() {
  if (!active) return;
  clearTimeout(active.lateTimer);
  const { panel, request } = active;
  active = null;
  panel.remove();
  request.button?.setAttribute('aria-expanded', 'false');
  if (map) map.setNotes([]);
}

// Is this state of the slot the answer to the open request?
function answers(twin) {
  if (!active) return false;
  const f = (name) => feature(twin, name);
  // The engine echoes the request's own name: nothing else needs comparing.
  const id = String(f('request_id') ?? '');
  if (id && /^page-/.test(id)) return id === active.requestId;
  const by = String(f('requested_by') ?? '');
  if (by && by !== REQUESTER && (/^page-/.test(by) || /advisor/.test(by))) return false;
  const { request } = active;
  const labels = f('label_a') === request.labelA && f('label_b') === request.labelB;
  const subject = String(f('subject') ?? '');
  if (!subject) return labels;
  const named = resolveRef(subject);
  // A subject naming another twin is another question.
  if (named) return named === resolveRef(request.subject) && (labels || !f('label_a'));
  // A subject in words of its own: the labels tell the questions apart.
  return subject === request.subject || labels;
}

// Every change of the page's slot passes here, in the order it arrives.
export function patch(twin) {
  if (!active || twin.thingId !== slotId(PAGE_SLOT)) return;
  const status = feature(twin, 'status');
  if (!answers(twin)) {
    if (status === 'running' && active.panel.dataset.state === 'sent') setState('sent', 'Модель занята другим прогнозом, ваш запрос следующий…');
    return;
  }
  if (status === 'running') {
    active.sawRunning = true;
    setState('running', `Модель проигрывает ${HORIZON_H} часов вперёд в двух вариантах…`);
    return;
  }
  const own = feature(twin, 'request_id') === active.requestId;
  if (status === 'ready') {
    const fresh = own || active.sawRunning || snapshot(twin) !== active.before || Date.now() - active.sentAt > SAME_SUBJECT_MS;
    if (!fresh) return;
    clearTimeout(active.lateTimer);
    renderResult(readForecast(twin));
  } else if (status === 'error' && (own || active.sawRunning)) {
    clearTimeout(active.lateTimer);
    setState('late', `Прогноз не получен: ${feature(twin, 'error_text') || 'модель не смогла его посчитать'}. Можно запросить ещё раз.`);
  }
}

// Open a panel in `holder` and ask for the forecast. A second press on the same
// button asks again; a request from another card closes the open panel first.
export async function request({ key, holder, button, title, heading = `Прогноз на ${HORIZON_H} ч`, subheading = '', labelA, labelB, actionsA = [], actionsB = [], subject, verdictLead }) {
  const mine = first('mine');
  if (!mine) return;
  closePanel();
  const req = { key, holder, button, title, heading, subheading, labelA, labelB, subject, verdictLead: verdictLead ?? labelB };
  const panel = buildPanel(req);
  holder.append(panel);
  button?.setAttribute('aria-expanded', 'true');
  const slot = twins.get(slotId(PAGE_SLOT));
  counter += 1;
  active = { request: req, panel, sentAt: Date.now(), sawRunning: false, before: slot ? snapshot(slot) : '', requestId: `${REQUESTER}-${counter}` };
  const mine_ = active;
  active.lateTimer = setTimeout(() => {
    if (active !== mine_) return;
    setState('late', 'Прогноз не пришёл за полторы минуты: модель занята или не ответила. Нажмите кнопку прогноза ещё раз.');
  }, LATE_MS);
  panel.scrollIntoView({ block: 'nearest' });
  try {
    await sendMessage(mine.thingId, 'forecast', {
      slot: PAGE_SLOT,
      horizon_h: HORIZON_H,
      label_a: labelA,
      label_b: labelB,
      actions_a: actionsA,
      actions_b: actionsB,
      subject: shortName(resolveRef(subject) ?? subject),
      requested_by: REQUESTER,
      request_id: mine_.requestId,
    });
  } catch (error) {
    if (active === mine_) setState('late', `Запрос не принят платформой: ${error.message}`);
  }
}

// The card that owns the open panel was rebuilt: the panel went with it.
export function forget() {
  if (active && !active.panel.isConnected) closePanel();
}

export const openKey = () => active?.request.key ?? null;
