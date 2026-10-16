// The process chain of the plant as the twins describe it: from the crusher
// along the links `feeds` (and the conveyor's `to`) through the stockpile,
// the grinding mills and the flotation to its products. The plan draws it and
// the strip under the plan reads its figures from here, both through
// rates.js, so a flow reads the same in both places.
//
// A flotation that names no product twins still has its products in its own
// features (concentrate t/h and grade, tailings grade): they are shown as the
// ends of the chain, attached to the flotation twin.

import { twins, byKind, first, feature, kindOf } from './store.js';
import { rate } from './rates.js';

export const PLANT_KINDS = ['crusher', 'conveyor', 'stockpile', 'mill', 'sag-mill', 'ball-mill', 'flotation', 'concentrate', 'tailings'];
export const MILL_KINDS = ['mill', 'sag-mill', 'ball-mill'];

// The twin whose feed is the plant's feed: the grinding circuit, or its first mill.
export function millTwin() {
  return first('mill') ?? first('sag-mill') ?? first('ball-mill');
}

// The mills that grind: the units of the circuit when it names them, else the circuit itself.
export function grindingUnits() {
  const circuit = first('mill');
  const units = [].concat(circuit?.attributes?.links?.units ?? []).map((id) => twins.get(id)).filter(Boolean);
  if (units.length) return units;
  const own = byKind('sag-mill').concat(byKind('ball-mill'));
  return own.length ? own : [circuit].filter(Boolean);
}

function downstream(twin) {
  const links = twin?.attributes?.links ?? {};
  const ids = [].concat(links.feeds ?? [], kindOf(twin) === 'conveyor' ? links.to ?? [] : [], links.products ?? []);
  return [...new Set(ids)].filter((id) => twins.has(id));
}

const num = (twin, ...names) => {
  for (const name of names) {
    const value = feature(twin, name);
    if (typeof value === 'number') return { name, value };
  }
  return null;
};

// t/h going from one unit to the next.
function flowBetween(from, to) {
  const toKind = to.virtual ?? kindOf(to.twin);
  const fromTwin = from.twin;
  const smooth = (twin, found) => (found ? rate(twin, found.name) : undefined);
  if (toKind === 'concentrate') {
    if (!to.virtual) {
      const own = num(to.twin, 'inflow_tph', 'feed_tph', 'tph', 'conc_tph');
      if (own) return smooth(to.twin, own);
    }
    return smooth(fromTwin, num(fromTwin, 'conc_tph'));
  }
  if (toKind === 'tailings') {
    if (!to.virtual) {
      const own = num(to.twin, 'inflow_tph', 'feed_tph', 'tph', 'tails_tph');
      if (own) return smooth(to.twin, own);
    }
    const tails = num(fromTwin, 'tails_tph');
    if (tails) return smooth(fromTwin, tails);
    const feed = flotationFeed(fromTwin);
    const conc = smooth(fromTwin, num(fromTwin, 'conc_tph'));
    return typeof feed === 'number' && typeof conc === 'number' ? Math.max(0, feed - conc) : undefined;
  }
  if (kindOf(fromTwin) === 'stockpile') {
    return smooth(to.twin, num(to.twin, 'feed_tph', 'throughput_tph'));
  }
  return smooth(fromTwin, num(fromTwin, 'throughput_tph', 'load_tph', 'discharge_tph', 'product_tph', 'feed_tph'));
}

// What enters the flotation: its own feature, else the last mill's throughput.
function flotationFeed(flotation) {
  const own = num(flotation, 'feed_tph', 'throughput_tph');
  if (own) return rate(flotation, own.name);
  const mills = byKind('ball-mill').concat(byKind('mill'), byKind('sag-mill'));
  for (const mill of mills) {
    const found = num(mill, 'throughput_tph', 'feed_tph');
    if (found) return rate(mill, found.name);
  }
  return undefined;
}

// {nodes: [{key, twin, virtual?, kind}], edges: [{from, to, tph}]} in the order of the flow.
export function plantChain() {
  const start = first('crusher');
  const nodes = [];
  const edges = [];
  if (!start) return { nodes, edges };
  const seen = new Map();
  const visit = (twin) => {
    if (seen.has(twin.thingId)) return seen.get(twin.thingId);
    const node = { key: twin.thingId, twin, kind: kindOf(twin) };
    seen.set(twin.thingId, node);
    nodes.push(node);
    for (const id of downstream(twin)) {
      const next = visit(twins.get(id));
      edges.push({ from: node, to: next });
    }
    return node;
  };
  visit(start);
  // Products a flotation keeps in its own features.
  for (const node of [...nodes]) {
    if (node.kind !== 'flotation') continue;
    const has = (kind) => edges.some((edge) => edge.from === node && edge.to.kind === kind);
    if (!has('concentrate') && typeof feature(node.twin, 'conc_tph') === 'number') {
      const product = { key: `${node.key}#concentrate`, twin: node.twin, virtual: 'concentrate', kind: 'concentrate' };
      nodes.push(product);
      edges.push({ from: node, to: product });
    }
    if (!has('tailings') && typeof feature(node.twin, 'tails_grade_pct') === 'number') {
      const product = { key: `${node.key}#tailings`, twin: node.twin, virtual: 'tailings', kind: 'tailings' };
      nodes.push(product);
      edges.push({ from: node, to: product });
    }
  }
  for (const edge of edges) edge.tph = flowBetween(edge.from, edge.to);
  return { nodes, edges };
}

// The grade and the rate of a product, from its twin or from the flotation.
export function productFigures(node) {
  const twin = node.twin;
  if (node.kind === 'concentrate') {
    const grade = node.virtual ? feature(twin, 'conc_grade_pct') : feature(twin, 'grade_pct') ?? feature(first('flotation'), 'conc_grade_pct');
    return { grade };
  }
  const grade = node.virtual ? feature(twin, 'tails_grade_pct') : feature(twin, 'grade_pct') ?? feature(first('flotation'), 'tails_grade_pct');
  return { grade };
}

// -- where the units stand ----------------------------------------------------

function centroidOf(points) {
  let x = 0;
  let y = 0;
  for (const p of points) { x += p[0]; y += p[1]; }
  return { x: x / points.length, y: y / points.length };
}

function midpointOf(path) {
  const lengths = path.slice(1).map((p, i) => Math.hypot(p[0] - path[i][0], p[1] - path[i][1]));
  let half = lengths.reduce((a, b) => a + b, 0) / 2;
  for (let i = 0; i < lengths.length; i += 1) {
    if (half <= lengths[i]) {
      const f = half / (lengths[i] || 1);
      return { x: path[i][0] + (path[i + 1][0] - path[i][0]) * f, y: path[i][1] + (path[i + 1][1] - path[i][1]) * f };
    }
    half -= lengths[i];
  }
  return { x: path[0][0], y: path[0][1] };
}

// Where a unit of the chain stands on the mine grid: its discharge point (a
// tailings pond), its own point, or the middle of its outline or path.
export function placeOf(twin) {
  const geo = twin?.attributes?.geo ?? {};
  const spec = twin?.attributes?.spec ?? {};
  if (typeof spec.discharge_x === 'number') return { x: spec.discharge_x, y: spec.discharge_y };
  if (typeof geo.x === 'number') return { x: geo.x, y: geo.y };
  if (geo.polygon) return centroidOf(geo.polygon);
  if (geo.path) return midpointOf(geo.path);
  return null;
}

// The chain with a place for every unit. A unit without a place of its own
// (a product the flotation reports in its features, a mill the model has not
// placed) stands one step after the unit that feeds it, along the chain.
const PLANT_STEP_M = 135;

export function plantLayout() {
  const { nodes, edges } = plantChain();
  const pos = new Map();
  for (const node of nodes) {
    const at = node.virtual ? null : placeOf(node.twin);
    if (at) pos.set(node.key, at);
  }
  for (const node of nodes) {
    if (pos.has(node.key)) continue;
    const feed = edges.find((edge) => edge.to === node && pos.has(edge.from.key));
    if (!feed) continue;
    const from = pos.get(feed.from.key);
    const before = edges.find((edge) => edge.to === feed.from && pos.has(edge.from.key));
    let dx = 1;
    let dy = 0.4;
    if (before) { dx = from.x - pos.get(before.from.key).x; dy = from.y - pos.get(before.from.key).y; }
    const length = Math.hypot(dx, dy) || 1;
    const siblings = edges.filter((edge) => edge.from === feed.from).map((edge) => edge.to);
    const angle = siblings.length > 1 ? ((siblings.length - 1) / 2 - siblings.indexOf(node)) * 0.95 : 0;
    const ux = dx / length;
    const uy = dy / length;
    pos.set(node.key, {
      x: from.x + (ux * Math.cos(angle) - uy * Math.sin(angle)) * PLANT_STEP_M,
      y: from.y + (ux * Math.sin(angle) + uy * Math.cos(angle)) * PLANT_STEP_M,
    });
  }
  return { nodes, edges, pos };
}
