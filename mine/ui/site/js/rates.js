// Rates that the page shows in more than one place (the top bar, the process
// chain under the plan, the plant on the plan) come from here, so the same
// quantity reads the same everywhere.
//
// A rate such as the crusher's throughput is published as it is at that
// moment: the crusher works in bursts as trucks tip, so it jumps between 0 and
// its rated 4 100 t/h within minutes. What the page shows is the average over
// the last WINDOW_MIN minutes of simulated time: every published value counts
// for as long as it stood. Until the page has seen a whole window, the
// history database fills in the minutes before it was opened.

import { twins, first, feature } from './store.js';
import { loadHistory } from './api.js';

export const WINDOW_MIN = 15;
const WINDOW_MS = WINDOW_MIN * 60000;

const tracks = new Map();          // `${thingId}|${name}` -> {samples: [{t, v}], seeded}

const simNow = () => Date.parse(`${feature(first('mine'), 'sim_time')}Z`);

function trackOf(thingId, name) {
  const key = `${thingId}|${name}`;
  let track = tracks.get(key);
  if (!track) {
    track = { thingId, name, samples: [], seeded: false };
    tracks.set(key, track);
  }
  return track;
}

function add(track, t, v) {
  const { samples } = track;
  const last = samples[samples.length - 1];
  if (last && t < last.t) {
    // The clock went back: the model was restarted. What was averaged belongs to the old run.
    track.samples = [{ t, v }];
    return;
  }
  if (last && last.v === v) return;                     // nothing changed: the last sample still stands
  if (last && t === last.t) last.v = v;
  else samples.push({ t, v });
  // Keep one sample from before the window: it stood at the window's start.
  while (samples.length > 2 && samples[1].t <= t - WINDOW_MS) samples.shift();
}

// Time-weighted mean of the track over the window that ends now.
function mean(track, now) {
  const { samples } = track;
  if (!samples.length) return undefined;
  const start = now - WINDOW_MS;
  let sum = 0;
  let span = 0;
  for (let i = 0; i < samples.length; i += 1) {
    const from = Math.max(samples[i].t, start);
    const to = i + 1 < samples.length ? samples[i + 1].t : now;
    if (to <= from) continue;
    sum += samples[i].v * (to - from);
    span += to - from;
  }
  return span > 0 ? sum / span : samples[samples.length - 1].v;
}

// The minutes before the page was opened, from the history database: its
// steps are in real time, which the speed of the model turns into simulated time.
async function seed(track) {
  track.seeded = true;
  const speedup = Number(feature(first('mine'), 'speedup')) || 20;
  let series;
  try {
    series = (await loadHistory(track.thingId, '5m')).get(track.name) ?? [];
  } catch {
    return;
  }
  const realNow = Date.now();
  const now = simNow();
  if (!Number.isFinite(now) || !series.length) return;
  const old = series.map((point) => ({ t: now - (realNow - point.t) * speedup, v: point.v }))
    .filter((point) => point.t > now - 2 * WINDOW_MS && point.t < now);
  // Live samples taken meanwhile stay; history only fills in before them.
  const firstLive = track.samples.length ? track.samples[0].t : Infinity;
  track.samples = [...old.filter((point) => point.t < firstLive), ...track.samples];
}

// Smoothed value of a rate feature of a twin, or the raw value while there is
// nothing to average yet. `thing` is a twin or a thing ID.
export function rate(thing, name) {
  const twin = typeof thing === 'string' ? twins.get(thing) : thing;
  if (!twin) return undefined;
  const raw = feature(twin, name);
  if (typeof raw !== 'number') return raw;
  const track = trackOf(twin.thingId, name);
  const now = simNow();
  if (!Number.isFinite(now)) return raw;
  add(track, now, raw);
  if (!track.seeded) seed(track);
  return mean(track, now);
}

// A rate summed over several twins (all loaders digging): smoothed as one series.
export function rateSum(key, list, name) {
  const now = simNow();
  const total = list.reduce((sum, twin) => sum + (typeof feature(twin, name) === 'number' ? feature(twin, name) : 0), 0);
  if (!Number.isFinite(now)) return total;
  const track = trackOf(key, name);
  track.seeded = true;
  add(track, now, total);
  return mean(track, now);
}

// Feed every tracked rate the values of this moment, so that a rate keeps its
// history even while nothing on the page asks for it.
export function sample() {
  const now = simNow();
  if (!Number.isFinite(now)) return;
  for (const track of tracks.values()) {
    const twin = twins.get(track.thingId);
    const value = feature(twin, track.name);
    if (typeof value === 'number') add(track, now, value);
  }
}

// The twins were loaded again (a restart of the model may have moved its clock back).
export function reset() {
  for (const track of tracks.values()) {
    const now = simNow();
    const last = track.samples[track.samples.length - 1];
    if (last && Number.isFinite(now) && now < last.t) { track.samples = []; track.seeded = false; }
  }
}

export const RATE_NOTE = `среднее за ${WINDOW_MIN} мин модельного времени`;
