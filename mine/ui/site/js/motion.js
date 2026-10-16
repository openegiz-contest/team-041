// Truck motion between reports. A truck reports its position about once a
// second; in between it is drawn travelling along the road geometry from
// where it is shown now to where it reported, so it follows the bends of a
// segment and passes through the junction when it changes segments.

import { byKind, feature } from './store.js';

const SNAP_M = 1800;            // a longer way is a jump (restart, reload): show it at once
const ON_ROAD_M = 4;            // a report this close to the last segment is still on it

const clamp = (value, low, high) => Math.min(Math.max(value, low), high);

export function createMotion() {
  const segments = new Map();   // thingId -> {id, from, to, pts, cum, length}
  const neighbours = new Map(); // node thingId -> [{segment, node}]
  const tracks = new Map();     // truck thingId -> track

  // -- road graph -------------------------------------------------------------

  function rebuild() {
    segments.clear();
    neighbours.clear();
    tracks.clear();
    for (const twin of byKind('road-segment')) {
      const path = twin.attributes?.geo?.path;
      const { from, to } = twin.attributes?.links ?? {};
      if (!path || path.length < 2) continue;
      const pts = path.map(([x, y]) => [x, y]);
      const cum = [0];
      for (let i = 1; i < pts.length; i += 1) {
        cum.push(cum[i - 1] + Math.hypot(pts[i][0] - pts[i - 1][0], pts[i][1] - pts[i - 1][1]));
      }
      const segment = { id: twin.thingId, from, to, pts, cum, length: cum[cum.length - 1] };
      segments.set(twin.thingId, segment);
      for (const [a, b] of [[from, to], [to, from]]) {
        if (!neighbours.has(a)) neighbours.set(a, []);
        neighbours.get(a).push({ segment, node: b });
      }
    }
    for (const truck of byKind('truck')) report(truck);
  }

  // Distance along the segment of the point nearest to (x, y), and how far off it is.
  function project(segment, x, y) {
    let best = { s: 0, d: Infinity };
    for (let i = 1; i < segment.pts.length; i += 1) {
      const [ax, ay] = segment.pts[i - 1];
      const [bx, by] = segment.pts[i];
      const lengthSq = (bx - ax) ** 2 + (by - ay) ** 2 || 1;
      const f = clamp(((x - ax) * (bx - ax) + (y - ay) * (by - ay)) / lengthSq, 0, 1);
      const d = Math.hypot(x - (ax + (bx - ax) * f), y - (ay + (by - ay) * f));
      if (d < best.d) best = { s: segment.cum[i - 1] + f * (segment.cum[i] - segment.cum[i - 1]), d };
    }
    return best;
  }

  function pointAt(segment, s) {
    const { pts, cum } = segment;
    for (let i = 1; i < pts.length; i += 1) {
      if (s <= cum[i] || i === pts.length - 1) {
        const f = clamp((s - cum[i - 1]) / ((cum[i] - cum[i - 1]) || 1), 0, 1);
        return [pts[i - 1][0] + (pts[i][0] - pts[i - 1][0]) * f, pts[i - 1][1] + (pts[i][1] - pts[i - 1][1]) * f];
      }
    }
    return pts[0];
  }

  // Points of the segment between two distances along it, in travel order.
  function slice(segment, s0, s1) {
    const out = [pointAt(segment, s0)];
    const { pts, cum } = segment;
    if (s1 >= s0) {
      for (let i = 0; i < pts.length; i += 1) if (cum[i] > s0 && cum[i] < s1) out.push(pts[i]);
    } else {
      for (let i = pts.length - 1; i >= 0; i -= 1) if (cum[i] < s0 && cum[i] > s1) out.push(pts[i]);
    }
    out.push(pointAt(segment, s1));
    return out;
  }

  // Shortest chain of whole segments between two nodes.
  function nodeRoute(start, goal) {
    if (start === goal) return { length: 0, legs: [] };
    const best = new Map([[start, { length: 0, legs: [] }]]);
    const open = [start];
    while (open.length) {
      open.sort((a, b) => best.get(a).length - best.get(b).length);
      const node = open.shift();
      if (node === goal) break;
      for (const { segment, node: next } of neighbours.get(node) ?? []) {
        const length = best.get(node).length + segment.length;
        if (!best.has(next) || length < best.get(next).length) {
          best.set(next, { length, legs: [...best.get(node).legs, { segment, forward: segment.from === node }] });
          if (!open.includes(next)) open.push(next);
        }
      }
    }
    return best.get(goal) ?? null;
  }

  const endS = (segment, node) => (segment.from === node ? 0 : segment.length);

  // The way from a to b as [{x, y, seg}], seg naming the segment of the leg that ends at the point.
  function build(a, b) {
    const way = [{ x: a.x, y: a.y, seg: a.seg }];
    const add = (points, seg) => {
      for (const [x, y] of points) {
        const last = way[way.length - 1];
        if (Math.hypot(x - last.x, y - last.y) > 0.05) way.push({ x, y, seg });
      }
    };
    const sa = a.seg ? segments.get(a.seg) : null;
    const sb = b.seg ? segments.get(b.seg) : null;
    if (sa && sb && sa === sb) {
      add(slice(sa, project(sa, a.x, a.y).s, project(sb, b.x, b.y).s), sa.id);
    } else if (sa && sb) {
      const pa = project(sa, a.x, a.y).s;
      const pb = project(sb, b.x, b.y).s;
      let choice = null;
      for (const ea of [sa.from, sa.to]) {
        for (const eb of [sb.from, sb.to]) {
          const between = nodeRoute(ea, eb);
          if (!between) continue;
          const length = Math.abs(pa - endS(sa, ea)) + between.length + Math.abs(endS(sb, eb) - pb);
          if (!choice || length < choice.length) choice = { length, ea, eb, between };
        }
      }
      if (choice) {
        add(slice(sa, pa, endS(sa, choice.ea)), sa.id);
        for (const { segment, forward } of choice.between.legs) {
          add(slice(segment, forward ? 0 : segment.length, forward ? segment.length : 0), segment.id);
        }
        add(slice(sb, endS(sb, choice.eb), pb), sb.id);
      }
    } else if (sa || sb) {
      // Leaving the road for a face or a tip, or coming back: along the
      // segment to its end node, straight between the node and the place.
      const segment = sa ?? sb;
      const on = sa ? a : b;
      const off = sa ? b : a;
      const s = project(segment, on.x, on.y).s;
      const direct = Math.hypot(b.x - a.x, b.y - a.y);
      const ends = [segment.from, segment.to].map((node) => {
        const es = endS(segment, node);
        const [nx, ny] = pointAt(segment, es);
        return { es, length: Math.abs(s - es) + Math.hypot(off.x - nx, off.y - ny) };
      }).sort((p, q) => p.length - q.length);
      if (ends[0].length <= 3 * direct + 100) {
        if (sa) add(slice(segment, s, ends[0].es), segment.id);
        else add(slice(segment, ends[0].es, s), segment.id);
      }
    }
    add([[b.x, b.y]], b.seg ?? null);
    return way;
  }

  // -- tracks -----------------------------------------------------------------

  function setWay(track, way, now, duration) {
    track.way = way;
    track.cum = [0];
    for (let i = 1; i < way.length; i += 1) {
      track.cum.push(track.cum[i - 1] + Math.hypot(way[i].x - way[i - 1].x, way[i].y - way[i - 1].y));
    }
    track.total = track.cum[track.cum.length - 1];
    track.start = now;
    track.duration = duration;
  }

  function sample(track, now) {
    const { way, cum, total } = track;
    const f = total > 0 ? clamp((now - track.start) / track.duration, 0, 1) : 1;
    const d = f * total;
    let i = 1;
    while (i < way.length - 1 && cum[i] < d) i += 1;
    let x = way[way.length - 1].x;
    let y = way[way.length - 1].y;
    let seg = way[way.length - 1].seg;
    if (way.length > 1 && f < 1) {
      const g = clamp((d - cum[i - 1]) / ((cum[i] - cum[i - 1]) || 1), 0, 1);
      x = way[i - 1].x + (way[i].x - way[i - 1].x) * g;
      y = way[i - 1].y + (way[i].y - way[i - 1].y) * g;
      seg = way[i].seg;
      // Turn towards the direction of travel, quickly but not in one frame.
      const target = Math.atan2(way[i].x - way[i - 1].x, way[i].y - way[i - 1].y);
      const turn = Math.atan2(Math.sin(target - track.heading), Math.cos(target - track.heading));
      const dt = clamp((now - track.sampled) / 1000, 0, 0.25);
      track.heading += turn * Math.min(1, dt * 10);
    }
    track.sampled = now;
    track.x = x;
    track.y = y;
    track.at = seg;
    track.moving = f < 1 && total > 0.5;
    return track;
  }

  function report(twin) {
    const now = performance.now();
    const x = feature(twin, 'pos_x');
    const y = feature(twin, 'pos_y');
    if (typeof x !== 'number' || typeof y !== 'number') return;
    const named = feature(twin, 'segment');
    let seg = named && segments.has(named) ? named : null;
    let track = tracks.get(twin.thingId);
    if (!track) {
      track = { heading: ((feature(twin, 'heading_deg') ?? 0) * Math.PI) / 180, sampled: now, interval: 1000, last: now };
      setWay(track, [{ x, y, seg }], now, 1);
      sample(track, now);
      tracks.set(twin.thingId, track);
      return;
    }
    const shown = sample(track, now);
    const from = { x: shown.x, y: shown.y, seg: shown.at };
    // Trucks waiting in a queue stand on the segment they arrived by and name no segment.
    if (!seg && from.seg && project(segments.get(from.seg), x, y).d <= ON_ROAD_M) seg = from.seg;
    const direct = Math.hypot(x - from.x, y - from.y);
    const interval = clamp(now - track.last, 200, 4000);
    track.last = now;
    if (direct < 0.05) {
      if (!track.moving) track.way[track.way.length - 1].seg = seg;
      return;
    }
    track.interval += (interval - track.interval) * 0.3;
    const way = build(from, { x, y, seg });
    let length = 0;
    for (let i = 1; i < way.length; i += 1) length += Math.hypot(way[i].x - way[i - 1].x, way[i].y - way[i - 1].y);
    if (length > SNAP_M) {
      setWay(track, [{ x, y, seg }], now, 1);
      track.heading = ((feature(twin, 'heading_deg') ?? 0) * Math.PI) / 180;
    } else {
      // A little longer than the reports are apart, so the truck is still
      // rolling when the next one arrives instead of stopping to wait for it.
      setWay(track, way, now, clamp(track.interval * 1.15, 300, 3500));
    }
  }

  return {
    rebuild,
    report,
    // Where to draw a truck now: {x, y, heading (radians, 0 = north, clockwise), moving}.
    shown(thingId, now) {
      const track = tracks.get(thingId);
      return track ? sample(track, now) : null;
    },
    anyMoving() {
      for (const track of tracks.values()) if (track.moving) return true;
      return false;
    },
  };
}
