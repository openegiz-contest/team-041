// Small helpers for building and updating the DOM, and the one-line state of
// a twin that the object list, the tooltip of the plan and the chips share.

import { twins, feature, kindOf, shortName, titleById } from './store.js';
import { stateText, formatNumber, formatSmart, MATERIAL_TEXT, pageText } from './labels.js';

export const $ = (selector, root = document) => root.querySelector(selector);
export const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

export function el(tag, props = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props)) {
    if (value === undefined || value === null || value === false) continue;
    if (key === 'dataset') Object.assign(node.dataset, value);
    else if (key === 'style' && typeof value === 'object') {
      for (const [name, text] of Object.entries(value)) {
        if (name.startsWith('--')) node.style.setProperty(name, text);
        else node.style[name] = text;
      }
    }
    else if (key === 'title') node.title = pageText(String(value));
    else if (key in node && key !== 'list' && key !== 'form') node[key] = value;
    else node.setAttribute(key, value === true ? '' : value);
  }
  // Sums of money in the model's sentences are written the page's way (млн ₸).
  node.append(...children.flat().filter((child) => child !== null && child !== undefined && child !== false)
    .map((child) => (typeof child === 'string' ? pageText(child) : child)));
  return node;
}

export function setText(node, text) {
  const shown = pageText(text);
  if (node && node.textContent !== shown) node.textContent = shown;
}

export const twinHref = (thingId) => `#/twin/${encodeURIComponent(shortName(thingId))}`;
export const twinLink = (thingId, text) => el('a', { className: 'twin-link', href: twinHref(thingId) }, text ?? titleById(thingId));

const num = (value, digits = 0) => (typeof value === 'number' ? formatNumber(value, digits) : '—');

function truckTone(state) {
  if (state === 'hauling' || state === 'returning' || state === 'approach') return 'moving';
  if (state === 'loading' || state === 'dumping') return 'good';
  if (state === 'queue_load' || state === 'queue_dump') return 'warn';
  return state === 'down' ? 'bad' : 'idle';
}

function unitTone(state) {
  if (['running', 'loading', 'drilling'].includes(state)) return 'good';
  if (['down', 'stopped'].includes(state)) return 'bad';
  if (['blocked', 'starved', 'overload', 'starting'].includes(state)) return 'warn';
  if (['relocating', 'setup', 'tramming', 'positioning'].includes(state)) return 'moving';
  return state === 'waiting' ? 'ready' : 'idle';
}

// {text, tone}: what a twin is doing now, in a few words. Tone is one of
// good, moving, warn, bad, idle, ready, scenario and colours the state dot.
export function keyState(twin) {
  const f = (name) => feature(twin, name);
  switch (kindOf(twin)) {
    case 'truck': {
      const state = f('state');
      const load = f('payload_t') > 1 ? ` · ${num(f('payload_t'))} т` : '';
      return { text: `${stateText(state)}${load}`, tone: truckTone(state) };
    }
    case 'loader':
      return { text: `${stateText(f('state'))}${f('queue_len') > 0 ? ` · очередь ${f('queue_len')}` : ''}`, tone: unitTone(f('state')) };
    case 'drill-rig':
      return { text: `${stateText(f('state'))} · ${num(f('holes_done'))}/${num(f('holes_planned'))} скв.`, tone: unitTone(f('state')) };
    case 'crusher':
      return { text: `${stateText(f('state'))} · ${num(f('throughput_tph'))} т/ч${f('queue_len') > 0 ? ` · очередь ${f('queue_len')}` : ''}`, tone: unitTone(f('state')) };
    case 'conveyor':
      return { text: `${stateText(f('state'))} · ${num(f('load_tph'))} т/ч`, tone: unitTone(f('state')) };
    case 'stockpile': {
      const level = f('level_pct');
      return { text: `${num(level)} % · ${num(f('hours_of_feed'), 1)} ч подачи`, tone: level < 15 ? 'bad' : level < 30 ? 'warn' : 'good' };
    }
    case 'mill': case 'sag-mill': case 'ball-mill':
      return { text: `${stateText(f('state'))} · ${num(f('feed_tph') ?? f('throughput_tph'))} т/ч`, tone: unitTone(f('state')) };
    case 'concentrate': case 'tailings': {
      const flow = ['inflow_tph', 'feed_tph', 'tph', 'conc_tph', 'tails_tph', 'throughput_tph'].map(f).find((value) => typeof value === 'number');
      const grade = f('grade_pct');
      const parts = [];
      if (typeof flow === 'number') parts.push(`${num(flow, flow >= 100 ? 0 : 1)} т/ч`);
      if (typeof grade === 'number') parts.push(`${num(grade, grade >= 1 ? 1 : 3)} % Cu`);
      if (!parts.length && f('state') !== undefined) parts.push(stateText(f('state')));
      return { text: parts.join(' · '), tone: f('state') ? unitTone(f('state')) : 'idle' };
    }
    case 'flotation':
      return { text: `${stateText(f('state'))} · извлечение ${num(f('recovery_pct'), 1)} %`, tone: unitTone(f('state')) };
    case 'rom-pad':
      return { text: `${num(f('level_t'))} т · ${num(f('grade_pct'), 2)} % Cu`, tone: 'idle' };
    case 'clay-stockpile':
      return { text: `${num(f('level_t'))} т · ${num(f('grade_pct'), 2)} % Cu · глина ${num(f('clay_pct'))} %`, tone: 'idle' };
    case 'clay-plant': {
      const bin = f('bin_pct');
      const tone = unitTone(f('state'));
      return { text: `${stateText(f('state'))} · ${num(f('feed_tph'))} т/ч · приёмный склад ${num(bin)} %`, tone: tone === 'good' && bin < 10 ? 'warn' : tone };
    }
    case 'desliming':
      return { text: `${stateText(f('state'))} · шламы ${num(f('slimes_pct'), 1)} %`, tone: unitTone(f('state')) };
    case 'clay-flotation':
      return { text: `${stateText(f('state'))} · извлечение ${num(f('recovery_pct'), 1)} %`, tone: unitTone(f('state')) };
    case 'waste-dump':
      return { text: `${num(f('tonnes_total'))} т${f('queue_len') > 0 ? ` · очередь ${f('queue_len')}` : ''}`, tone: 'idle' };
    case 'substation':
      return { text: `${num(f('load_mw'), 1)} МВт`, tone: 'good' };
    case 'fuel-station': {
      const busy = (f('queue_len') ?? 0) + (f('trucks_fuelling') ?? 0);
      return { text: busy > 0 ? `самосвалов: ${busy}` : 'свободна', tone: busy > 0 ? 'moving' : 'idle' };
    }
    case 'workshop':
      return { text: `боксы ${num(f('bays_busy'))} из ${num(f('bays_total'))}${f('jobs_queued') > 0 ? ` · ждут ${f('jobs_queued')}` : ''}`, tone: f('bays_busy') > 0 ? 'moving' : 'idle' };
    case 'weather': {
      const rain = f('condition') === 'rain';
      return { text: `${stateText(f('condition'))}${rain ? ` · ${num(f('rain_mm_h'), 1)} мм/ч` : ''}`, tone: rain ? 'warn' : 'idle' };
    }
    case 'pit': {
      const blast = f('blast_state');
      if (blast && blast !== 'idle') return { text: `взрыв: ${stateText(blast)}`, tone: 'bad' };
      return { text: `забоев: ${num(f('active_faces'))} · глубина ${num(f('depth_m'))} м`, tone: 'idle' };
    }
    case 'bench':
      return { text: `блоков: ${num(f('blocks_remaining'))}`, tone: 'idle' };
    case 'block': {
      const status = f('status');
      const tone = status === 'mining' ? 'moving' : status === 'charged' ? 'bad' : status === 'blasted' ? 'warn' : status === 'drilled' ? 'good' : 'idle';
      return { text: `${stateText(status)} · ${num(f('grade_pct'), 2)} % Cu`, tone };
    }
    case 'road-segment': {
      const status = f('status');
      const parts = [stateText(status)];
      if (f('surface') === 'wet') parts.push('мокро');
      if (f('trucks_on') > 0) parts.push(`машин: ${f('trucks_on')}`);
      return { text: parts.join(' · '), tone: status === 'closed' ? 'bad' : status === 'restricted' ? 'warn' : 'idle' };
    }
    case 'road-node':
      return { text: f('trucks_waiting') > 0 ? `ждут: ${f('trucks_waiting')}` : '', tone: f('trucks_waiting') > 0 ? 'warn' : 'idle' };
    case 'scenario': {
      const status = f('status');
      return { text: stateText(status), tone: status === 'active' || status === 'cancelling' ? 'scenario' : 'idle' };
    }
    case 'module': {
      const on = f('enabled') === true;
      const incident = typeof f('incident') === 'string' && f('incident').trim() && f('incident') !== 'none';
      return { text: on ? (incident ? 'включён · происшествие' : 'включён') : 'выключен', tone: on ? (incident ? 'warn' : 'good') : 'idle' };
    }
    case 'forecast': {
      const status = f('status');
      const text = { idle: 'свободен', running: 'считает', ready: 'готов' }[status] ?? stateText(status);
      return { text: `${text}${status !== 'idle' && f('label_b') ? ` · ${f('label_b')}` : ''}`, tone: status === 'running' ? 'moving' : status === 'ready' ? 'good' : 'idle' };
    }
    case 'mine':
      return { text: `${stateText(f('sim_state'))} · ×${num(f('speedup'))}`, tone: f('sim_state') === 'paused' ? 'warn' : 'good' };
    case 'journal':
      return { text: typeof f('seq') === 'number' ? `событий: ${num(f('seq'))}` : '', tone: 'idle' };
    default: {
      const state = f('state') ?? f('status');
      return { text: state === undefined ? '' : stateText(state), tone: unitTone(state) };
    }
  }
}

// Extra line for the tooltip of the plan.
export function hoverLines(twin) {
  const f = (name) => feature(twin, name);
  const lines = [keyState(twin).text];
  switch (kindOf(twin)) {
    case 'truck': {
      const parts = [];
      if (twins.has(f('assigned_loader'))) parts.push(titleById(f('assigned_loader')));
      if (twins.has(f('assigned_dump'))) parts.push(titleById(f('assigned_dump')));
      if (parts.length) lines.push(parts.join(' → '));
      const material = MATERIAL_TEXT[f('material')];
      lines.push(`${num(f('speed_kmh'))} км/ч · топливо ${num(f('fuel_pct'))} %${f('payload_t') > 1 && material ? ` · ${material}` : ''}`);
      break;
    }
    case 'block':
      lines.push(`${MATERIAL_TEXT[f('material')] ?? ''} · крепость ${formatSmart(f('hardness_bwi'))}${typeof f('clay_pct') === 'number' ? ` · глина ${num(f('clay_pct'))} %` : ''} · остаток ${num(f('tonnes_remaining'))} т`);
      break;
    case 'loader':
      if (twins.has(f('block'))) lines.push(`${titleById(f('block'))} · ${num(f('dig_rate_tph'))} т/ч`);
      break;
    case 'drill-rig':
      if (twins.has(f('block'))) lines.push(titleById(f('block')));
      break;
    case 'road-segment':
      lines.push(`до ${num(f('speed_limit_kmh'))} км/ч · уклон ${num(f('grade_pct'), 1)} %`);
      break;
    default:
  }
  return lines.filter(Boolean);
}
