// Floating data panels of the 3D view (hologram look): a dark translucent
// card above a machine or a unit of the plant, with a thin leader line down
// to it, its name in white and its live figures from the twin in green
// (amber and red when they need attention). They are HTML over the scene, so
// they always face the viewer and stay sharp; their places are worked out on
// the screen so that they never overlap each other or the page's panels. The
// figures are read from the twins at most once a second per panel, and a
// panel's text is rebuilt only when a figure has changed.

import { feature, kindOf, titleOf } from './store.js';
import { stateText, formatNumber, MATERIAL_TEXT } from './labels.js';
import { rate } from './rates.js';
import { holoTone } from './holo3d.js';

const FONT = '"Segoe UI", system-ui, -apple-system, Roboto, "Helvetica Neue", Arial, sans-serif';
const FONT_TITLE = `600 11px ${FONT}`;
const FONT_LABEL = `400 10.5px ${FONT}`;
const FONT_VALUE = `600 10.5px ${FONT}`;
const PAD_X = 8;
const TITLE_H = 16;
const ROW_H = 14;
const PAD_Y = 5;
const REFRESH_MS = 1000;
const NB = ' ';

const num = (value, digits = 0) => formatNumber(typeof value === 'number' ? Number(value.toFixed(digits)) : value, digits);
function plural(n, one, few, many) {
  const a = Math.abs(n) % 100;
  const b = a % 10;
  if (a > 10 && a < 20) return many;
  if (b === 1) return one;
  if (b >= 2 && b <= 4) return few;
  return many;
}
const trucks = (n) => `${num(n)}${NB}${plural(n, 'самосвал', 'самосвала', 'самосвалов')}`;

const TITLES = {
  crusher: 'Дробилка', conveyor: 'Конвейер', stockpile: 'Склад руды', 'sag-mill': 'МПСИ', 'ball-mill': 'Шаровые мельницы', mill: 'Мельницы',
  flotation: 'Флотация', concentrate: 'Склад концентрата', tailings: 'Хвостохранилище', workshop: 'Мастерская', 'fuel-station': 'Заправка',
  substation: 'Подстанция', 'clay-plant': 'ОФ-2 (глинистая руда)', desliming: 'Обесшламливание ОФ-2', 'clay-flotation': 'Флотация ОФ-2', 'clay-stockpile': 'Склад глинистой руды',
};

// What a panel says about a twin: {title, rows: [{label, value, tone, feature, raw}]}.
// `compact`: the name and the first two figures (panels seen from afar).
export function panelContent(twin, compact = false) {
  const kind = kindOf(twin);
  const f = (name) => feature(twin, name);
  const rows = [];
  const row = (label, value, tone = 'ok', name = null, raw = undefined) => rows.push({ label, value, tone, feature: name, raw: raw ?? (name ? f(name) : undefined) });
  const state = () => { const s = f('state'); if (s) row('Состояние', stateText(s), holoTone(s), 'state'); };
  switch (kind) {
    case 'truck': {
      state();
      const payload = Number(f('payload_t')) || 0;
      if (payload > 1) row('Груз', `${num(payload)}${NB}т · ${MATERIAL_TEXT[f('material')] ?? f('material') ?? ''}`.replace(/ · $/, ''), 'ok', 'payload_t');
      else row('Груз', 'порожний', 'dim', 'payload_t');
      row('Скорость', `${num(f('speed_kmh'))}${NB}км/ч`, 'ok', 'speed_kmh');
      const fuel = Number(f('fuel_pct'));
      row('Топливо', `${num(fuel)}${NB}%`, fuel < 12 ? 'bad' : fuel < 25 ? 'warn' : 'ok', 'fuel_pct');
      break;
    }
    case 'loader': {
      state();
      row('Производительность', `${num(f('dig_rate_tph'))}${NB}т/ч`, 'ok', 'dig_rate_tph');
      const queue = Number(f('queue_len')) || 0;
      row('Очередь', queue ? trucks(queue) : 'нет', queue > 2 ? 'warn' : 'ok', 'queue_len');
      break;
    }
    case 'drill-rig': {
      state();
      row('Скважины', `${num(f('holes_done'))} из ${num(f('holes_planned'))}`, 'ok', 'holes_done');
      row('Проходка', `${num(f('penetration_m_h'))}${NB}м/ч`, 'ok', 'penetration_m_h');
      row('Пробурено', `${num(f('metres_drilled'))}${NB}м`, 'ok', 'metres_drilled');
      break;
    }
    case 'crusher': {
      state();
      const tph = rate(twin, 'throughput_tph');
      row('Производительность', `${num(tph)}${NB}т/ч`, 'ok', 'throughput_tph', tph);
      row('Бункер', `${num(f('hopper_t'))}${NB}т`, 'ok', 'hopper_t');
      const queue = Number(f('queue_len')) || 0;
      row('Очередь', queue ? trucks(queue) : 'нет', queue > 3 ? 'warn' : 'ok', 'queue_len');
      break;
    }
    case 'conveyor': {
      const tph = rate(twin, 'load_tph');
      row('Нагрузка', `${num(tph)}${NB}т/ч`, 'ok', 'load_tph', tph);
      state();
      row('Мощность', `${num(f('power_kw'))}${NB}кВт`, 'ok', 'power_kw');
      break;
    }
    case 'stockpile': {
      const pct = Number(f('level_pct'));
      row('Уровень', `${num(pct)}${NB}% · ${num(f('level_t'))}${NB}т`, pct < 15 ? 'warn' : 'ok', 'level_pct');
      const hours = Number(f('hours_of_feed'));
      row('Питание мельниц', `${num(hours, 1)}${NB}ч`, hours < 2 ? 'bad' : hours < 4 ? 'warn' : 'ok', 'hours_of_feed');
      row('Медь', `${num(f('grade_pct'), 2)}${NB}%`, 'ok', 'grade_pct');
      break;
    }
    case 'sag-mill': case 'ball-mill': case 'mill': {
      state();
      const name = kind === 'mill' ? 'feed_tph' : 'throughput_tph';
      row('Питание', `${num(f(name))}${NB}т/ч`, 'ok', name);
      row('Мощность', `${num(f('power_mw'), 1)}${NB}МВт`, 'ok', 'power_mw');
      if (typeof f('drive_load_pct') === 'number') row('Загрузка привода', `${num(f('drive_load_pct'))}${NB}%`, f('drive_load_pct') > 99 ? 'warn' : 'ok', 'drive_load_pct');
      break;
    }
    case 'flotation': {
      state();
      row('Извлечение', `${num(f('recovery_pct'), 1)}${NB}%`, 'ok', 'recovery_pct');
      row('Медь', `${num(f('cu_tph'), 1)}${NB}т/ч`, 'ok', 'cu_tph');
      row('Концентрат', `${num(f('conc_tph'), 1)}${NB}т/ч`, 'ok', 'conc_tph');
      break;
    }
    case 'clay-plant': {
      state();
      row('Переработка', `${num(f('feed_tph'))}${NB}т/ч`, 'ok', 'feed_tph');
      const bin = Number(f('bin_pct'));
      row('Приёмный склад', `${num(bin)}${NB}% · ${num(f('bin_hours'), 1)}${NB}ч`, bin < 10 ? 'warn' : 'ok', 'bin_pct');
      row('Извлечение', `${num(f('recovery_pct'), 1)}${NB}%`, 'ok', 'recovery_pct');
      row('Медь', `${num(f('cu_tph'), 1)}${NB}т/ч`, 'ok', 'cu_tph');
      break;
    }
    case 'clay-stockpile': {
      row('На складе', `${num(f('level_t'))}${NB}т · ${num(f('level_pct'))}${NB}%`, 'ok', 'level_t');
      row('Медь', `${num(f('grade_pct'), 2)}${NB}%`, 'ok', 'grade_pct');
      row('Глина', `${num(f('clay_pct'))}${NB}%`, 'ok', 'clay_pct');
      break;
    }
    case 'desliming': {
      state();
      row('Питание', `${num(f('feed_tph'))}${NB}т/ч`, 'ok', 'feed_tph');
      row('Шламы', `${num(f('slimes_pct'), 1)}${NB}%`, 'ok', 'slimes_pct');
      break;
    }
    case 'clay-flotation': {
      state();
      row('Извлечение', `${num(f('recovery_pct'), 1)}${NB}%`, 'ok', 'recovery_pct');
      row('Медь', `${num(f('cu_tph'), 1)}${NB}т/ч`, 'ok', 'cu_tph');
      break;
    }
    case 'concentrate': {
      row('На складе', `${num(f('level_t'), 1)}${NB}т`, 'ok', 'level_t');
      row('Поступление', `${num(f('inflow_tph'), 1)}${NB}т/ч`, 'ok', 'inflow_tph');
      row('Отгружено', `${num(f('shipped_t'))}${NB}т`, 'ok', 'shipped_t');
      break;
    }
    case 'tailings': {
      row('Поступление', `${num(f('inflow_tph'))}${NB}т/ч`, 'ok', 'inflow_tph');
      row('Медь в хвостах', `${num(f('grade_pct'), 3)}${NB}%`, 'ok', 'grade_pct');
      row('Потери меди', `${num(f('cu_lost_t'))}${NB}т`, 'ok', 'cu_lost_t');
      break;
    }
    case 'workshop': {
      const busy = Number(f('bays_busy')) || 0;
      row('Боксы заняты', `${num(busy)} из ${num(f('bays_total'))}`, busy >= (Number(f('bays_total')) || 99) ? 'warn' : 'ok', 'bays_busy');
      row('Очередь работ', num(f('jobs_queued')), Number(f('jobs_queued')) > 0 ? 'warn' : 'ok', 'jobs_queued');
      row('Выполнено', num(f('jobs_done')), 'ok', 'jobs_done');
      break;
    }
    case 'fuel-station': {
      const queue = Number(f('queue_len')) || 0;
      row('Очередь', queue ? trucks(queue) : 'нет', queue > 2 ? 'warn' : 'ok', 'queue_len');
      row('Заправляются', num(f('trucks_fuelling')), 'ok', 'trucks_fuelling');
      row('Выдано', `${num(f('litres_issued'))}${NB}л`, 'ok', 'litres_issued');
      break;
    }
    case 'substation': {
      row('Нагрузка', `${num(f('load_mw'), 1)}${NB}МВт`, 'ok', 'load_mw');
      row('Фабрика', `${num(f('plant_mw'), 1)}${NB}МВт`, 'ok', 'plant_mw');
      row('Карьер', `${num(f('pit_mw'), 1)}${NB}МВт`, 'ok', 'pit_mw');
      break;
    }
    default:
      state();
  }
  return { title: TITLES[kind] ?? titleOf(twin), rows: compact ? rows.slice(0, 2) : rows };
}

export const PANEL_KINDS = new Set(['truck', 'loader', 'drill-rig', 'crusher', 'conveyor', 'stockpile', 'sag-mill', 'ball-mill', 'flotation',
  'concentrate', 'tailings', 'workshop', 'fuel-station', 'substation', 'clay-plant', 'desliming', 'clay-flotation', 'clay-stockpile']);

const overlap = (a, b, m = 0) => a[0] < b[2] + m && a[2] > b[0] - m && a[1] < b[3] + m && a[3] > b[1] - m;

// The panels over the scene, in `container` (positioned over the canvas).
export function createPanels(container, measureCtx) {
  const entries = new Map();       // thingId -> {node, at, key, content, w, h}
  const widths = new Map();
  let placed = [];
  let refreshTimer = 0;

  function textWidth(font, text) {
    const key = `${font}|${text}`;
    let value = widths.get(key);
    if (value === undefined) {
      measureCtx.font = font;
      value = measureCtx.measureText(text).width;
      if (widths.size > 4000) widths.clear();
      widths.set(key, value);
    }
    return value;
  }

  function size(content) {
    let label = 0;
    let value = 0;
    for (const row of content.rows) {
      label = Math.max(label, textWidth(FONT_LABEL, row.label));
      value = Math.max(value, textWidth(FONT_VALUE, row.value));
    }
    const w = Math.ceil(Math.max(textWidth(FONT_TITLE, content.title) + 4, label + 14 + value) + PAD_X * 2 + 2);
    const h = PAD_Y * 2 + TITLE_H + content.rows.length * ROW_H + 2;
    return { w: Math.max(118, w), h };
  }

  function build(entry, content) {
    const node = entry.node;
    node.replaceChildren();
    const title = document.createElement('div');
    title.className = 'hp-title';
    title.textContent = content.title;
    node.append(title);
    for (const row of content.rows) {
      const line = document.createElement('div');
      line.className = 'hp-row';
      if (row.feature) line.dataset.feature = row.feature;
      if (row.raw !== undefined && row.raw !== null) line.dataset.raw = typeof row.raw === 'number' ? String(Math.round(row.raw * 1000) / 1000) : String(row.raw);
      const k = document.createElement('span');
      k.className = 'hp-k';
      k.textContent = row.label;
      const v = document.createElement('span');
      v.className = 'hp-v';
      v.dataset.tone = row.tone;
      v.textContent = row.value;
      line.append(k, v);
      node.append(line);
    }
  }

  function entryOf(twin, compact, now, wake) {
    let entry = entries.get(twin.thingId);
    if (!entry) {
      const node = document.createElement('div');
      node.className = 'holo-panel';
      node.dataset.id = twin.thingId;
      node.dataset.kind = kindOf(twin);
      node.hidden = true;
      container.append(node);
      entry = { node, at: -Infinity, key: '', content: null, w: 0, h: 0, compact: null };
      entries.set(twin.thingId, entry);
    }
    if (now - entry.at >= REFRESH_MS || entry.compact !== compact) {
      const content = panelContent(twin, compact);
      const key = JSON.stringify(content);
      entry.at = now;
      entry.compact = compact;
      if (key !== entry.key) {
        entry.key = key;
        entry.content = content;
        Object.assign(entry, size(content));
        build(entry, content);
      }
    } else wake(entry.at + REFRESH_MS - now);
    return entry;
  }

  // Place the panels asked for, most important first. `requests`:
  // [{twin, at: {x, y} (screen, top of the object), accent, forced, compact}].
  // Returns the panels placed: [{id, title, rect, at, accent, forced}].
  function layout(requests, { width, height, avoid = [], max = 10, now = performance.now(), onStale = () => {} }) {
    const taken = [];
    const out = [];
    const anchors = [];
    let soonest = Infinity;
    const wake = (ms) => { soonest = Math.min(soonest, ms); };
    for (const request of requests) {
      if (out.length >= max) break;
      const entry = entryOf(request.twin, Boolean(request.compact), now, wake);
      const { w, h } = entry;
      const { x: ax, y: ay } = request.at;
      let rect = null;
      // Above the object first (nearest, then higher, then to the sides), then beside it, then below.
      const tries = [];
      for (const dy of [24, 46, 72, 102, 136]) {
        for (const dx of [0, -(w * 0.5 + 8), w * 0.5 + 8, -(w + 14), w + 14]) tries.push([ax + dx - w / 2, ay - dy - h]);
      }
      for (const dy of [-h / 2, -h - 6, 6]) tries.push([ax + 22, ay + dy], [ax - 22 - w, ay + dy]);
      for (const dy of [24, 56, 90]) for (const dx of [0, -(w * 0.5 + 8), w * 0.5 + 8]) tries.push([ax + dx - w / 2, ay + dy]);
      for (const [tx, ty] of tries) {
        const x0 = Math.round(tx);
        const y0 = Math.round(ty);
        const r = [x0, y0, x0 + w, y0 + h];
        if (r[0] < 4 || r[1] < 4 || r[2] > width - 4 || r[3] > height - 4) continue;
        if (taken.some((t) => overlap(r, t, 6))) continue;
        if (avoid.some((t) => overlap(r, t, 2))) continue;
        // Not over the point another panel stands on, nor over its own object.
        if (anchors.some((p) => overlap(r, [p.x - 5, p.y - 5, p.x + 5, p.y + 5]))) continue;
        if (request.box && overlap(r, request.box)) continue;
        rect = r;
        break;
      }
      if (!rect) continue;
      taken.push(rect);
      anchors.push(request.at);
      if (request.box) taken.push(request.box);
      out.push({ id: request.twin.thingId, kind: kindOf(request.twin), title: entry.content.title, rect, at: { x: ax, y: ay }, accent: request.accent ?? null, forced: Boolean(request.forced), entry });
    }
    const keep = new Set(out.map((p) => p.id));
    for (const [id, entry] of entries) {
      if (!keep.has(id) && !entry.node.hidden) entry.node.hidden = true;
    }
    for (const p of out) {
      const node = p.entry.node;
      const transform = `translate(${p.rect[0]}px, ${p.rect[1]}px)`;
      if (node.style.transform !== transform) node.style.transform = transform;
      if (node.style.width !== `${p.entry.w}px`) node.style.width = `${p.entry.w}px`;
      if (node.style.height !== `${p.entry.h}px`) node.style.height = `${p.entry.h}px`;
      const accent = p.accent ?? '';
      if ((node.dataset.accent ?? '') !== accent) { if (accent) node.dataset.accent = accent; else delete node.dataset.accent; }
      if (node.hidden) node.hidden = false;
    }
    // A figure that changed while its panel waited out its second is shown at the end of that second.
    if (Number.isFinite(soonest)) {
      clearTimeout(refreshTimer);
      refreshTimer = setTimeout(onStale, Math.max(50, soonest + 20));
    }
    placed = out;
    return out;
  }

  // Read every panel's figures again at the next layout.
  function stale() { for (const entry of entries.values()) entry.at = -Infinity; }

  function hide() {
    for (const entry of entries.values()) entry.node.hidden = true;
    placed = [];
  }

  // What the panels show, for the page check.
  function shown() {
    return placed.map((p) => ({ id: p.id, kind: p.kind, title: p.title, rect: p.rect.slice(), accent: p.accent, forced: p.forced,
      rows: p.entry.content.rows.map((row) => ({ label: row.label, value: row.value, feature: row.feature, raw: row.raw, tone: row.tone })) }));
  }

  function clear() {
    for (const entry of entries.values()) entry.node.remove();
    entries.clear();
    placed = [];
  }

  return { layout, hide, shown, clear, stale, get placed() { return placed; } };
}
