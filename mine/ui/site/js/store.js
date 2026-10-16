// Current state of every twin, kept up to date from change events, and the
// relations between twins that the page shows: static links, the parent, and
// the relations a twin keeps in its features (a truck's loader, its route).

// Namespace of the mine's twins. The page's server names it (/config.json);
// main.js sets it before the twins are loaded and again if the mine moves to
// another namespace while the page is open.
export let NAMESPACE = '';
export function setNamespace(namespace) { NAMESPACE = namespace; }
export const twins = new Map();          // thingId -> thing
const kinds = new Map();                 // kind -> [thing]
const patchListeners = new Set();        // (twin) at once, for motion timing
const changeListeners = new Set();       // (Set of thingIds | null) batched, for the DOM
const pending = new Set();
let flushTimer = 0;
const FLUSH_MS = 150;

export const onPatch = (listener) => patchListeners.add(listener);
export const onChange = (listener) => changeListeners.add(listener);

export const shortName = (thingId) => thingId.slice(thingId.indexOf(':') + 1);
export const kindOf = (twin) => twin?.attributes?.kind ?? '';
export const titleOf = (twin) => twin?.attributes?.name ?? shortName(twin.thingId);
export const feature = (twin, name) => twin?.features?.[name]?.properties?.value;
export const unitOf = (twin, name) => twin?.features?.[name]?.properties?.unit;
export const byKind = (kind) => kinds.get(kind) ?? [];
export const first = (kind) => byKind(kind)[0];
export const titleById = (thingId) => (twins.has(thingId) ? titleOf(twins.get(thingId)) : shortName(thingId));
export const kindsPresent = () => [...kinds.keys()];

function merge(target, patch) {
  for (const [key, value] of Object.entries(patch)) {
    const both = value && typeof value === 'object' && !Array.isArray(value)
      && target[key] && typeof target[key] === 'object' && !Array.isArray(target[key]);
    if (both) merge(target[key], value);
    else target[key] = value;
  }
}

export function replaceAll(things) {
  twins.clear();
  kinds.clear();
  segmentsByNodes = null;
  for (const thing of things) {
    twins.set(thing.thingId, thing);
    const kind = kindOf(thing);
    if (!kinds.has(kind)) kinds.set(kind, []);
    kinds.get(kind).push(thing);
  }
  for (const list of kinds.values()) list.sort((a, b) => a.thingId.localeCompare(b.thingId, 'en', { numeric: true }));
  pending.clear();
  for (const listener of changeListeners) listener(null);
}

function flush() {
  flushTimer = 0;
  if (!pending.size) return;
  const changed = new Set(pending);
  pending.clear();
  for (const listener of changeListeners) listener(changed);
}

// Apply the changed part of one twin. Returns false for a twin the page does
// not know yet (the model has grown; the caller loads everything again).
export function applyChange(patch) {
  const twin = twins.get(patch.thingId);
  if (!twin) return false;
  merge(twin, patch);
  for (const listener of patchListeners) listener(twin);
  pending.add(twin.thingId);
  if (!flushTimer) flushTimer = setTimeout(flush, FLUSH_MS);
  return true;
}

// -- relations ----------------------------------------------------------------

// A string that names a twin, as a full thing ID or as the short name.
export function resolveRef(value) {
  if (typeof value !== 'string' || !value) return null;
  if (twins.has(value)) return value;
  const full = `${NAMESPACE}:${value.trim()}`;
  return twins.has(full) ? full : null;
}

// Features that hold a list of twins in one string.
const LIST_FEATURES = { route: '>', affected: ',', units_at_risk: ',', valid_targets: ',' };
// Features that name a twin but are not a relation worth drawing.
const NOT_RELATIONS = new Set(['thing']);

export function listRefs(twin, name) {
  const value = feature(twin, name);
  if (typeof value !== 'string' || !value) return [];
  const ids = [];
  for (const part of value.split(LIST_FEATURES[name] ?? ',')) {
    const id = resolveRef(part) ?? idByTitle(part.trim());
    if (id && !ids.includes(id)) ids.push(id);
  }
  return ids;
}

function idByTitle(title) {
  if (!title) return null;
  for (const twin of twins.values()) if (titleOf(twin) === title) return twin.thingId;
  return null;
}

let segmentsByNodes = null;
function segmentBetween(a, b) {
  if (!segmentsByNodes) {
    segmentsByNodes = new Map();
    for (const segment of byKind('road-segment')) {
      const { from, to } = segment.attributes.links ?? {};
      segmentsByNodes.set(`${from}|${to}`, segment.thingId);
      segmentsByNodes.set(`${to}|${from}`, segment.thingId);
    }
  }
  return segmentsByNodes.get(`${a}|${b}`) ?? null;
}

// Road segments of a route given as node IDs.
export function routeSegments(nodeIds) {
  const ids = [];
  for (let i = 1; i < nodeIds.length; i += 1) {
    const id = segmentBetween(nodeIds[i - 1], nodeIds[i]);
    if (id) ids.push(id);
  }
  return ids;
}

// What a twin points to: [{relation, ids, source}], source being 'parent',
// 'link' (static topology) or 'feature' (changes while the mine runs).
export function outgoing(twin) {
  const out = [];
  const attrs = twin.attributes ?? {};
  if (attrs._parents && twins.has(attrs._parents)) out.push({ relation: '_parents', ids: [attrs._parents], source: 'parent' });
  for (const [relation, target] of Object.entries(attrs.links ?? {})) {
    const ids = [].concat(target).filter((id) => twins.has(id));
    if (ids.length) out.push({ relation, ids, source: 'link' });
  }
  for (const [name, body] of Object.entries(twin.features ?? {})) {
    if (NOT_RELATIONS.has(name)) continue;
    if (name in LIST_FEATURES) {
      const ids = listRefs(twin, name);
      if (ids.length) out.push({ relation: name, ids, source: 'feature' });
      continue;
    }
    const id = resolveRef(body?.properties?.value);
    if (id && id !== twin.thingId) out.push({ relation: name, ids: [id], source: 'feature' });
  }
  return out;
}

// Twins that point to this one: [{relation, id, source}].
export function incoming(thingId) {
  const found = [];
  for (const other of twins.values()) {
    if (other.thingId === thingId) continue;
    for (const { relation, ids, source } of outgoing(other)) {
      if (source !== 'parent' && ids.includes(thingId)) found.push({ relation, id: other.thingId, source });
    }
  }
  return found;
}

// Everything to highlight together with a selected twin.
export function relatedIds(thingId) {
  const twin = twins.get(thingId);
  const related = new Set();
  if (!twin) return related;
  for (const { relation, ids } of outgoing(twin)) {
    ids.forEach((id) => related.add(id));
    if (relation === 'route') routeSegments(ids).forEach((id) => related.add(id));
  }
  // The mine and the journal are linked to everything; following links back
  // from them would light up the whole plan and say nothing.
  for (const { id } of incoming(thingId)) {
    if (kindOf(twins.get(id)) !== 'journal') related.add(id);
  }
  related.delete(thingId);
  return related;
}

// -- scenarios ----------------------------------------------------------------

// The truck cycle the page shows in the top bar and under the plan: while a
// scenario that measures the cycle runs, the figure «now» of its card (so the
// bar, the chain and the card agree); otherwise the mine's own average.
// {value, scenario}: scenario is the twin the figure comes from, or null.
export function cycleNow() {
  for (const twin of byKind('scenario')) {
    const status = feature(twin, 'status');
    if (status !== 'active' && status !== 'cancelling') continue;
    for (let n = 1; n <= 6; n += 1) {
      const label = String(feature(twin, `kpi${n}_label`) ?? '');
      if (/^время рейса/i.test(label) && typeof feature(twin, `kpi${n}_current`) === 'number') {
        // A forecast from the new routes until enough trips begun after the start have finished: written with ≈.
        return { value: feature(twin, `kpi${n}_current`), scenario: twin, forecast: feature(twin, `kpi${n}_forecast`) === true };
      }
    }
    if (!feature(twin, 'kpi1_label') && typeof feature(twin, 'current_cycle_min') === 'number') {
      return { value: feature(twin, 'current_cycle_min'), scenario: twin };
    }
  }
  return { value: feature(first('mine'), 'avg_cycle_min'), scenario: null };
}

// Scenarios that are running or being cancelled, with the twins they touch.
export function activeScenarios() {
  const active = [];
  for (const twin of byKind('scenario')) {
    const status = feature(twin, 'status');
    if (status !== 'active' && status !== 'cancelling') continue;
    const target = resolveRef(feature(twin, 'target'));
    const affected = listRefs(twin, 'affected').filter((id) => id !== twin.thingId);
    if (target && !affected.includes(target)) affected.unshift(target);
    active.push({ twin, status, target, affected });
  }
  return active;
}
