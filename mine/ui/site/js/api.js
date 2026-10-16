// Access to the platform. Every request goes to this page's own server, which
// forwards /ditto/ to Ditto and /influx/ to the history database and adds the
// credentials there.

import { NAMESPACE } from './store.js';

const API = '/ditto/api/2';

async function getJson(url) {
  const response = await fetch(url, { headers: { Accept: 'application/json' } });
  if (!response.ok) throw new Error(`${response.status} ${response.statusText}: ${url}`);
  return response.json();
}

// Namespaces the page may show, in order of preference, as the page's server
// is configured (MINE_NAMESPACE in mine/compose.yml). More than one is named
// only while the mine moves from one namespace to another.
export async function loadNamespaces() {
  const config = await getJson('/config.json');
  return String(config.namespaces ?? '').split(/[\s,]+/).filter((name) => /^[a-z0-9.]+$/.test(name));
}

// The first of `namespaces` that holds a mine twin, or null when none does yet.
export async function findMine(namespaces) {
  if (!namespaces.length) return null;
  const filter = encodeURIComponent('eq(attributes/kind,"mine")');
  const page = await getJson(`${API}/search/things?namespaces=${namespaces.join(',')}&filter=${filter}&fields=thingId`);
  const found = new Set(page.items.map((item) => item.thingId.slice(0, item.thingId.indexOf(':'))));
  return namespaces.find((name) => found.has(name)) ?? null;
}

// All twins of the mine: IDs from the search index, then their current state
// from the things service (the index may lag a moment behind).
export async function loadTwins() {
  const ids = [];
  let cursor = null;
  do {
    const option = 'size(200)' + (cursor ? `,cursor(${cursor})` : '');
    const page = await getJson(`${API}/search/things?namespaces=${NAMESPACE}&fields=thingId&option=${encodeURIComponent(option)}`);
    ids.push(...page.items.map((item) => item.thingId));
    cursor = page.cursor ?? null;
  } while (cursor);

  const things = [];
  for (let i = 0; i < ids.length; i += 50) {
    things.push(...await getJson(`${API}/things?ids=${ids.slice(i, i + 50).join(',')}`));
  }
  return things;
}

// Server-sent events with every change in the namespace. `onChange` receives
// the changed part of a twin together with its thingId; `onState` is told
// whether the stream is alive. The browser reconnects by itself after a
// network error but gives up after an error answer (a restarting platform
// answers 502), and a stream can also go silent, so both cases are handled
// here: the stream is opened again until it delivers.
const RETRY_MS = 3000;
const SILENCE_MS = 45000;

export function subscribe(onChange, onState) {
  let source = null;
  let lastEvent = 0;
  let retry = 0;

  function open() {
    retry = 0;
    if (!NAMESPACE) { reopen(); return; }
    const stream = new EventSource(`${API}/things?namespaces=${NAMESPACE}`);
    source = stream;
    stream.onopen = () => { lastEvent = Date.now(); onState(true); };
    stream.onerror = () => {
      if (stream !== source) return;
      onState(false);
      if (stream.readyState === EventSource.CLOSED) reopen();
    };
    stream.onmessage = (event) => {
      if (stream !== source) return;
      lastEvent = Date.now();
      if (!event.data) return;
      let patch;
      try {
        patch = JSON.parse(event.data);
      } catch {
        return;                                   // not a change event
      }
      if (patch && patch.thingId) onChange(patch);
    };
  }

  function close() {
    if (source) source.close();
    source = null;
  }

  function reopen() {
    close();
    if (!retry) retry = setTimeout(open, RETRY_MS);
  }

  setInterval(() => {
    if (source && source.readyState === EventSource.OPEN && Date.now() - lastEvent > SILENCE_MS) {
      onState(false);
      reopen();
    }
  }, 5000);
  open();
  return {
    // The namespace changed: follow the new one at once.
    restart() {
      close();
      clearTimeout(retry);
      open();
    },
  };
}

// Ditto live message to a twin. The simulator or a module receives it over
// MQTT and answers by changing the twin's features, so there is no reply to
// wait for.
export async function sendMessage(thingId, subject, body = {}) {
  const response = await fetch(`${API}/things/${thingId}/inbox/messages/${subject}?timeout=0`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
}

// -- history ------------------------------------------------------------------

// Rows of an annotated CSV answer as objects; quoted fields may hold commas.
function parseCsv(text) {
  const rows = [];
  let header = null;
  for (const line of text.split(/\r?\n/)) {
    if (!line || line.startsWith('#')) { if (!line) header = null; continue; }
    const cells = [];
    let cell = '';
    let quoted = false;
    for (let i = 0; i < line.length; i += 1) {
      const ch = line[i];
      if (quoted) {
        if (ch === '"' && line[i + 1] === '"') { cell += '"'; i += 1; }
        else if (ch === '"') quoted = false;
        else cell += ch;
      } else if (ch === '"') quoted = true;
      else if (ch === ',') { cells.push(cell); cell = ''; }
      else cell += ch;
    }
    cells.push(cell);
    if (!header) { header = cells; continue; }
    rows.push(Object.fromEntries(header.map((name, i) => [name, cells[i]])));
  }
  return rows;
}

async function getCsv(url) {
  const response = await fetch(url);
  if (!response.ok) throw new Error(`${response.status} ${response.statusText}: ${url}`);
  return parseCsv(await response.text());
}

// Recent values of every numeric feature of one twin, averaged over short
// steps: Map of feature name -> [{t: ms, v: number}], oldest first.
export async function loadHistory(thingId, range = '5m') {
  // The thing ID goes as it is: the server checks it against a pattern and
  // does not decode it, and its characters need no escaping in a query.
  if (!/^[a-z0-9.]+:[a-z0-9-]+$/.test(thingId)) return new Map();
  const rows = await getCsv(`/influx/query?thing=${thingId}&range=${range}`);
  const series = new Map();
  for (const row of rows) {
    const match = /^value_(.+)_properties_value$/.exec(row._field ?? '');
    const value = Number(row._value);
    if (!match || row._value === '' || !Number.isFinite(value)) continue;
    if (!series.has(match[1])) series.set(match[1], []);
    series.get(match[1]).push({ t: Date.parse(row._time), v: value });
  }
  return series;
}

// Journal events of the last hours, oldest first.
export async function loadJournal() {
  const rows = await getCsv(`/influx/journal?ns=${NAMESPACE}`);
  return rows.map((row) => ({
    severity: row.value_severity_properties_value || 'info',
    thing: row.value_thing_properties_value || '',
    text: row.value_text_properties_value || '',
    sim_time: row.value_sim_time_properties_value || '',
  })).filter((event) => event.text);
}
