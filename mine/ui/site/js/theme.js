// Colours of the plan and what they mean. The canvas and the legend beside it
// both read this file, so a swatch in the legend is always the colour drawn.

import { feature, kindOf, shortName, titleById, resolveRef } from './store.js';
import { stateText } from './labels.js';

export const C = {
  ground: '#161b20',
  grid: '#1b2127',
  lease: '#343e47',
  ink: '#e8edf1',
  inkSoft: '#aab5bf',
  inkMuted: '#7c8791',
  halo: 'rgba(13, 17, 20, 0.88)',
  outline: '#0d1114',
  // pit: the rim slope, then the bench floors from the top down
  slope: '#4a443d',
  levels: ['#433d36', '#37322d', '#2b2824', '#211f1c'],
  crest: '#62594d',
  crestLine: 'rgba(196, 182, 160, 0.55)',
  toeLine: 'rgba(8, 7, 6, 0.75)',
  roadCut: '#4a443c',
  windrow: 'rgba(200, 188, 168, 0.55)',
  dump: '#242a2d',
  dumpEdge: '#56606a',
  padRich: '#4b3524',
  padPoor: '#38322b',
  padEdge: '#7a6850',
  // roads
  road: '#7f8a94',
  roadWet: '#3d8fd1',
  roadClosedBase: '#3a2427',
  // plant and equipment bodies
  body: '#20272d',
  bodyEdge: '#8b949c',
  ore: '#c9772f',
  conc: '#3c4f45',
  concEdge: '#7fb39a',
  tails: '#2b3d48',
  tailsEdge: '#6f93a8',
  pipe: '#59646e',
  flow: '#dfe6ec',
  // states
  good: '#1baf7a',
  moving: '#3987e5',
  warn: '#fab219',
  bad: '#e5484d',
  idle: '#6f7a84',
  ready: '#b9c3cc',
  // marks
  select: '#5ad1ff',
  scenario: '#f0a845',
};

// -- trucks -------------------------------------------------------------------

// Colour says what the truck is doing; a filled body says it carries a load.
export const TRUCK_GROUPS = [
  { id: 'loaded', title: 'с грузом', color: C.moving, filled: true },
  { id: 'empty', title: 'порожний', color: C.moving, filled: false },
  { id: 'work', title: 'погрузка / разгрузка', color: C.good, filled: true },
  { id: 'queue', title: 'очередь', color: C.warn, filled: true },
  { id: 'down', title: 'неисправен', color: C.bad, filled: true, cross: true },
  { id: 'out', title: 'заправка, ТО, простой', color: C.idle, filled: true },
];

export function truckColor(state) {
  switch (state) {
    case 'hauling': case 'returning': case 'approach': return C.moving;
    case 'loading': case 'dumping': return C.good;
    case 'queue_load': case 'queue_dump': return C.warn;
    case 'down': return C.bad;
    default: return C.idle;
  }
}

// -- equipment and plant ------------------------------------------------------

export function stateColor(state) {
  switch (state) {
    case 'loading': case 'drilling': case 'running': case 'dumping': return C.good;
    case 'waiting': return C.ready;
    case 'down': case 'stopped': return C.bad;
    case 'blocked': case 'starved': case 'overload': case 'queue_load': case 'queue_dump': return C.warn;
    case 'hauling': case 'returning': case 'relocating': case 'setup': case 'tramming': case 'positioning': return C.moving;
    default: return C.idle;
  }
}

// -- block layers -------------------------------------------------------------

const ru = (value, digits) => value.toLocaleString('ru-RU', { minimumFractionDigits: digits, maximumFractionDigits: digits });

function binned(name, unit, digits, edges, colors) {
  const legend = colors.map((color, i) => ({
    color,
    title: i === 0 ? `до ${ru(edges[0], digits)}`
      : i === colors.length - 1 ? `от ${ru(edges[i - 1], digits)}`
        : `${ru(edges[i - 1], digits)}–${ru(edges[i], digits)}`,
  }));
  return {
    unit,
    legend,
    color(block) {
      const value = feature(block, name);
      if (typeof value !== 'number') return C.idle;
      let i = 0;
      while (i < edges.length && value >= edges[i]) i += 1;
      return colors[i];
    },
    caption(block) {
      const value = feature(block, name);
      return typeof value === 'number' ? ru(value, digits) : '';
    },
  };
}

const STATUS_COLORS = { insitu: '#56616b', drilled: '#199e70', charged: '#e5484d', blasted: '#c98500', mining: '#3987e5' };
export const DEPLETED = '#22272c';

const DESTINATION_COLORS = ['#3987e5', '#199e70', '#c98500', '#9085e9'];
const WASTE_COLOR = '#535c65';

export const LAYERS = {
  status: {
    title: 'Состояние',
    legendTitle: 'состояние',
    unit: '',
    legend: [...Object.entries(STATUS_COLORS).map(([value, color]) => ({ color, title: stateText(value) })),
      { color: DEPLETED, title: stateText('depleted'), hatch: true }],
    color: (block) => STATUS_COLORS[feature(block, 'status')] ?? C.idle,
    caption: (block) => stateText(feature(block, 'status')),
  },
  grade: {
    title: 'Содержание меди',
    legendTitle: '% Cu',
    ...binned('grade_pct', '% Cu', 2, [0.15, 0.25, 0.35, 0.45, 0.6],
      ['#4d3d35', '#77492e', '#a35b2b', '#cf7730', '#f0a14c', '#ffd896']),
  },
  hardness: {
    title: 'Крепость руды (индекс Бонда)',
    legendTitle: 'крепость, кВт·ч/т',
    ...binned('hardness_bwi', 'кВт·ч/т', 1, [12.5, 13.5, 14.5, 15.5],
      ['#27455b', '#2f6789', '#3d8eb6', '#63b7da', '#aae1f6']),
  },
  destination: {
    title: 'Куда везти породу блока',
    legendTitle: 'куда везти',
    unit: '',
    // Filled in from the twins: one entry per destination that blocks name.
    legend: [],
    color(block) {
      const value = feature(block, 'destination');
      const id = resolveRef(value);
      const name = id ? shortName(id) : value;
      const entry = LAYERS.destination.legend.find((item) => item.value === name);
      return entry ? entry.color : C.idle;
    },
    caption(block) {
      const id = resolveRef(feature(block, 'destination'));
      return id ? destinationShort(id) : String(feature(block, 'destination') ?? '');
    },
  },
};

function destinationShort(thingId) {
  const name = shortName(thingId);
  if (name === 'crusher') return 'дробилка';
  if (name === 'waste-dump') return 'отвал';
  const number = /(\d+)$/.exec(name);
  return number ? `склад №${Number(number[1])}` : titleById(thingId);
}

// Destinations in a fixed order (crusher, ore pads, waste dump), so a colour
// stays with its destination whichever blocks happen to use it.
export function refreshDestinationLegend(blocks, twins) {
  const order = ['crusher', 'rom-pad', 'waste-dump'];
  const sinks = [...twins.values()].filter((twin) => order.includes(kindOf(twin)))
    .sort((a, b) => order.indexOf(kindOf(a)) - order.indexOf(kindOf(b)) || a.thingId.localeCompare(b.thingId));
  let next = 0;
  LAYERS.destination.legend = sinks.map((twin) => {
    const waste = kindOf(twin) === 'waste-dump';
    const color = waste ? WASTE_COLOR : DESTINATION_COLORS[Math.min(next, DESTINATION_COLORS.length - 1)];
    if (!waste) next += 1;
    return { value: shortName(twin.thingId), color, title: destinationShort(twin.thingId) };
  });
  // A destination named by a block but unknown as a twin still gets a legend entry.
  for (const block of blocks) {
    const raw = feature(block, 'destination');
    const value = resolveRef(raw) ? shortName(resolveRef(raw)) : raw;
    if (value && !LAYERS.destination.legend.some((item) => item.value === value)) {
      LAYERS.destination.legend.push({ value, color: C.idle, title: String(value) });
    }
  }
}
