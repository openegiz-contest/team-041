// The pit as a terraced surface, for the plan and the 3D view alike. The twins
// give the rim (pit outline at the rim elevation) and every working bench as
// the outline of its toe at its floor elevation. Here those outlines become
// closed lines with the same number of points, from the rim down, and the
// walls between them become benches of their own: a flat floor or safety
// berm, then a face at the bench face angle down to the next one. A drop of
// several bench heights between two outlines gets as many faces.
//
// Result: {centre, samples, rimZ, floorZ, loops, bands, floors}
//   loops   [{pts: [[x, y]], z, bench}] from the rim down; the last is the pit floor
//   bands   [{outer, inner, type: 'flat' | 'face', bench, depth}] between loop indices
//   floors  Map bench thingId -> {outer, inner}: the floor of a working bench
//           (inner is null for the lowest bench), for drawing its blocks

import { first, byKind } from './store.js';

const DEFAULT_FACE_ANGLE_DEG = 70;   // bench face, when the pit twin does not say
const DEFAULT_BENCH_M = 15;
const SAFETY_BERM_M = 10;

export function areaCentroid(points) {
  let area = 0;
  let cx = 0;
  let cy = 0;
  for (let i = 0; i < points.length; i += 1) {
    const [x0, y0] = points[i];
    const [x1, y1] = points[(i + 1) % points.length];
    const cross = x0 * y1 - x1 * y0;
    area += cross;
    cx += (x0 + x1) * cross;
    cy += (y0 + y1) * cross;
  }
  if (Math.abs(area) < 1e-6) {
    const n = points.length || 1;
    return { x: points.reduce((s, p) => s + p[0], 0) / n, y: points.reduce((s, p) => s + p[1], 0) / n };
  }
  return { x: cx / (3 * area), y: cy / (3 * area) };
}

function signedArea(points) {
  let area = 0;
  for (let i = 0; i < points.length; i += 1) {
    const [x0, y0] = points[i];
    const [x1, y1] = points[(i + 1) % points.length];
    area += x0 * y1 - x1 * y0;
  }
  return area / 2;
}

export function perimeter(points) {
  let length = 0;
  for (let i = 0; i < points.length; i += 1) {
    const [x0, y0] = points[i];
    const [x1, y1] = points[(i + 1) % points.length];
    length += Math.hypot(x1 - x0, y1 - y0);
  }
  return length;
}

// `n` points evenly spaced along a closed outline, counter-clockwise, the
// first where a ray from `centre` towards the east crosses it. Outlines of
// one pit resampled this way pair up point by point.
export function resample(points, centre, n) {
  let pts = points.map((p) => [p[0], p[1]]);
  if (signedArea(pts) < 0) pts.reverse();
  const m = pts.length;
  // Start: the crossing of the eastward ray with the largest x.
  let start = { edge: 0, f: 0, x: -Infinity };
  for (let i = 0; i < m; i += 1) {
    const [x0, y0] = pts[i];
    const [x1, y1] = pts[(i + 1) % m];
    if ((y0 - centre.y) * (y1 - centre.y) > 0 || y0 === y1) continue;
    const f = (centre.y - y0) / (y1 - y0);
    if (f < 0 || f > 1) continue;
    const x = x0 + (x1 - x0) * f;
    if (x > centre.x && x > start.x) start = { edge: i, f, x };
  }
  if (!Number.isFinite(start.x)) start = { edge: 0, f: 0 };
  const ordered = [];
  const [sx0, sy0] = pts[start.edge];
  const [sx1, sy1] = pts[(start.edge + 1) % m];
  ordered.push([sx0 + (sx1 - sx0) * start.f, sy0 + (sy1 - sy0) * start.f]);
  for (let k = 1; k <= m; k += 1) ordered.push(pts[(start.edge + k) % m]);
  ordered.push(ordered[0]);
  const cum = [0];
  for (let i = 1; i < ordered.length; i += 1) {
    cum.push(cum[i - 1] + Math.hypot(ordered[i][0] - ordered[i - 1][0], ordered[i][1] - ordered[i - 1][1]));
  }
  const total = cum[cum.length - 1] || 1;
  const out = [];
  let j = 1;
  for (let k = 0; k < n; k += 1) {
    const s = (k / n) * total;
    while (j < cum.length - 1 && cum[j] < s) j += 1;
    const f = (s - cum[j - 1]) / ((cum[j] - cum[j - 1]) || 1);
    out.push([ordered[j - 1][0] + (ordered[j][0] - ordered[j - 1][0]) * f, ordered[j - 1][1] + (ordered[j][1] - ordered[j - 1][1]) * f]);
  }
  return out;
}

const lerpLoop = (outer, inner, t) => outer.map((p, i) => [p[0] + (inner[i][0] - p[0]) * t, p[1] + (inner[i][1] - p[1]) * t]);

function meanGap(outer, inner) {
  let sum = 0;
  for (let i = 0; i < outer.length; i += 1) sum += Math.hypot(inner[i][0] - outer[i][0], inner[i][1] - outer[i][1]);
  return sum / outer.length || 1;
}

// Steps of a wall between two outlines as [t (0 at outer, 1 at inner), z]:
// with `floorFirst` a floor comes first (a working bench), otherwise the wall
// starts with a face at the outer line (the rim).
function wallSteps(outer, zTop, inner, zBottom, benchM, faceAngle, floorFirst) {
  const width = meanGap(outer, inner);
  const drop = zTop - zBottom;
  const n = Math.max(1, Math.round(drop / benchM));
  const run = (drop / n) / Math.tan((faceAngle * Math.PI) / 180);
  const steps = [];
  if (floorFirst) {
    const face = Math.min(run / width, 0.6 / n);
    const berm = n > 1 ? Math.min(SAFETY_BERM_M / width, (1 - n * face) * 0.5 / (n - 1)) : 0;
    const floor = 1 - n * face - (n - 1) * berm;
    let t = floor;
    let z = zTop;
    steps.push([t, z]);
    for (let j = 0; j < n; j += 1) {
      t += face;
      z = zTop - (drop * (j + 1)) / n;
      steps.push([j === n - 1 ? 1 : t, z]);
      if (j < n - 1) { t += berm; steps.push([t, z]); }
    }
  } else {
    let face = Math.min(run / width, 1 / n);
    let berm = (1 - n * face) / n;
    // A berm narrower than a metre or two is no berm: the faces take the whole wall.
    if (berm * width < 1.5) { face = 1 / n; berm = 0; }
    let t = 0;
    for (let j = 0; j < n; j += 1) {
      t += face;
      const z = zTop - (drop * (j + 1)) / n;
      steps.push([j === n - 1 && berm === 0 ? 1 : t, z]);
      if (berm > 0) {
        t += berm;
        steps.push([j === n - 1 ? 1 : t, z]);
      }
    }
  }
  return steps;
}

// Outlines made by one generator (same count, same start, same direction)
// pair up as they are; `split` puts extra points on every edge.
function pairedAsGiven(outlines, centre) {
  const n = outlines[0].length;
  if (!outlines.every((pts) => pts.length === n)) return false;
  const angle = (p) => Math.atan2(p[1] - centre.y, p[0] - centre.x);
  const a0 = angle(outlines[0][0]);
  const sign = Math.sign(signedArea(outlines[0]));
  return outlines.every((pts) => Math.abs(Math.atan2(Math.sin(angle(pts[0]) - a0), Math.cos(angle(pts[0]) - a0))) < 0.08
    && Math.sign(signedArea(pts)) === sign);
}

function split(points, pieces) {
  if (pieces <= 1) return points.map((p) => [p[0], p[1]]);
  const out = [];
  for (let i = 0; i < points.length; i += 1) {
    const a = points[i];
    const b = points[(i + 1) % points.length];
    for (let k = 0; k < pieces; k += 1) out.push([a[0] + (b[0] - a[0]) * (k / pieces), a[1] + (b[1] - a[1]) * (k / pieces)]);
  }
  return out;
}

const cache = new Map();             // a few shapes: the plan and the 3D view ask with different spacing

// The terraced pit of the twins, or null when the pit has no outline.
export function buildPit({ spacing = 14, minSamples = 72, maxSamples = 300 } = {}) {
  const pit = first('pit');
  const rim = pit?.attributes?.geo?.polygon;
  if (!rim || rim.length < 3) return null;
  const benches = byKind('bench').filter((bench) => bench.attributes?.geo?.polygon?.length >= 3 && typeof bench.attributes.geo.z === 'number')
    .sort((a, b) => b.attributes.geo.z - a.attributes.geo.z);
  const key = `${pit.thingId}|${JSON.stringify(rim)}|${benches.map((b) => `${b.thingId}:${b.attributes.geo.z}:${b.attributes.geo.polygon.length}:${b.attributes.geo.polygon[0]}:${b.attributes.geo.crest?.length ?? 0}`).join(',')}|${spacing}|${minSamples}|${maxSamples}`;
  if (cache.has(key)) return cache.get(key);

  const spec = pit.attributes.spec ?? {};
  const rimZ = spec.rim_elevation_m ?? pit.attributes.geo.z ?? 0;
  const benchM = spec.bench_height_m ?? DEFAULT_BENCH_M;
  const faceAngle = spec.face_angle_deg ?? spec.bench_face_angle_deg ?? DEFAULT_FACE_ANGLE_DEG;
  const centre = areaCentroid(rim);
  const crests = benches.map((bench) => bench.attributes.geo.crest).filter((pts) => Array.isArray(pts) && pts.length >= 3);
  const given = [rim, ...benches.map((bench) => bench.attributes.geo.polygon), ...crests];
  let shape;
  let samples;
  if (pairedAsGiven(given, centre)) {
    // As the model made them, with points added on the edges down to `spacing`.
    const pieces = Math.max(1, Math.min(8, Math.round(perimeter(rim) / rim.length / spacing), Math.floor(maxSamples / rim.length)));
    const flip = signedArea(rim) < 0;
    shape = (pts) => { const out = split(pts, pieces); return flip ? out.reverse() : out; };
    samples = rim.length * pieces;
  } else {
    samples = Math.round(Math.min(maxSamples, Math.max(minSamples, perimeter(rim) / spacing)));
    shape = (pts) => resample(pts, centre, samples);
  }
  const rimLoop = shape(rim);
  const rings = benches.map((bench) => {
    const crest = bench.attributes.geo.crest;
    return { id: bench.thingId, z: bench.attributes.geo.z, pts: shape(bench.attributes.geo.polygon),
      crest: Array.isArray(crest) && crest.length >= 3 ? shape(crest) : null };
  });

  const loops = [{ pts: rimLoop, z: rimZ, bench: null }];
  const bands = [];
  const floors = new Map();
  const push = (pts, z, bench, type) => {
    loops.push({ pts, z, bench });
    bands.push({ outer: loops.length - 2, inner: loops.length - 1, type, bench });
  };
  if (rings.length) {
    // The wall from the rim down to the first working bench.
    if (rimZ - rings[0].z > 0.5) {
      const steps = wallSteps(rimLoop, rimZ, rings[0].pts, rings[0].z, benchM, faceAngle, false);
      let lastZ = rimZ;
      for (const [t, z] of steps) {
        push(t >= 1 ? rings[0].pts : lerpLoop(rimLoop, rings[0].pts, t), z, t >= 1 ? rings[0].id : null, z < lastZ ? 'face' : 'flat');
        lastZ = z;
      }
    } else {
      push(rings[0].pts, rings[0].z, rings[0].id, 'flat');
    }
    rings.forEach((ring, i) => {
      const next = rings[i + 1];
      if (!next) { floors.set(ring.id, { outer: ring.pts, inner: null }); return; }
      if (ring.crest && ring.z - next.z <= benchM * 1.5) {
        // The model names the inner edge of the floor: floor to it, then one face down.
        floors.set(ring.id, { outer: ring.pts, inner: ring.crest });
        push(ring.crest, ring.z, ring.id, 'flat');
        push(next.pts, next.z, next.id, 'face');
        return;
      }
      const steps = wallSteps(ring.pts, ring.z, next.pts, next.z, benchM, faceAngle, true);
      let lastZ = ring.z;
      steps.forEach(([t, z], k) => {
        const pts = t >= 1 ? next.pts : lerpLoop(ring.pts, next.pts, t);
        if (k === 0) floors.set(ring.id, { outer: ring.pts, inner: pts });
        push(pts, z, z < lastZ ? next.id : ring.id, z < lastZ ? 'face' : 'flat');
        lastZ = z;
      });
    });
  }
  const floorZ = loops[loops.length - 1].z;
  const span = Math.max(1, rimZ - floorZ);
  for (const band of bands) band.depth = (rimZ - loops[band.inner].z) / span;
  const value = { centre, samples, rimZ, floorZ, benchM, loops, bands, floors, rings };
  if (cache.size > 3) cache.clear();
  cache.set(key, value);
  return value;
}
