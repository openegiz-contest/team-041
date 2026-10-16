// The ground of the 3D view: the steppe to the horizon with gentle relief,
// the pit cut into it bench by bench (pitgeom.js) with rock that changes
// colour with depth, haul roads as compacted gravel with windrows, the waste
// dump as terraced lifts with fresh tip heads, and the ROM pads with their
// stockpiles. Everything here is static between reloads of the twins; the
// scene lights it again when the sun moves.

import * as THREE from 'three';
import { byKind, first, feature } from './store.js';
import { buildPit, areaCentroid } from './pitgeom.js';
import { Builder, ribbon, densify, rgb, mix, scale, clamp, pointInPolygon, nearestOnLoop, distanceToPolyline, centroid } from './geom3d.js';
import { fbm, noise2, TEXTURE_MEAN } from './textures3d.js';

const K = 1 / TEXTURE_MEAN;          // colours are brightened by the texture's mean
// Albedo of the ground: the dry steppe of eastern Kazakhstan (straw grass,
// sage, ochre soil, pale salt flats), the rock of a porphyry copper pit
// (red-brown oxide cap, pale leached zone, grey porphyry, green-grey depth),
// dusty berms, pale compacted haul roads and grey waste rock.
const C = {
  grass: rgb('#9e8c56'),
  sage: rgb('#787e59'),
  soil: rgb('#946f4b'),
  salt: rgb('#c2b597'),
  valley: rgb('#6f7651'),
  worked: rgb('#8d7c66'),
  dusty: rgb('#a08d74'),
  oxide: rgb('#9a5f3d'),
  leached: rgb('#b9a17e'),
  transition: rgb('#94765a'),
  porphyry: rgb('#7d7872'),
  green: rgb('#68715f'),
  dyke: rgb('#c4b9a5'),
  berm: rgb('#96826a'),
  hill: rgb('#8a7f6c'),
  floor: rgb('#7a6c58'),
  water: rgb('#4c6d6a'),
  road: rgb('#b9b2a4'),
  track: rgb('#8e877b'),
  roadEdge: rgb('#a09682'),
  windrow: rgb('#7f6d58'),
  waste: rgb('#a3927d'),
  wasteTop: rgb('#958673'),
  pad: rgb('#8c7d6b'),
  oreRich: rgb('#8c5e40'),
  orePoor: rgb('#857868'),
};

const smooth = (e0, e1, x) => { const t = clamp((x - e0) / (e1 - e0), 0, 1); return t * t * (3 - 2 * t); };

// -- the elevation of the ground ------------------------------------------------

// Elevation outside the pit from the heights the twins give (pit rim, surface
// roads, plant, pads, buildings), blended smoothly; away from the mine the
// steppe rises into low hills.
export function createGround(rimZ, pitLoop) {
  const points = [];
  const add = (x, y, z) => { if (Number.isFinite(x) && Number.isFinite(y) && Number.isFinite(z)) points.push([x, y, z]); };
  if (pitLoop) pitLoop.forEach(([x, y], i) => { if (i % 4 === 0) add(x, y, rimZ); });
  for (const segment of byKind('road-segment')) {
    if (segment.attributes?.spec?.road_class !== 'surface') continue;
    const path = densify(segment.attributes.geo.path, 160);
    for (const [x, y, z] of path) add(x, y, z);
  }
  for (const kind of ['crusher', 'stockpile', 'mill', 'sag-mill', 'ball-mill', 'flotation', 'concentrate', 'substation', 'workshop', 'fuel-station']) {
    for (const twin of byKind(kind)) { const g = twin.attributes?.geo; if (g && typeof g.x === 'number') add(g.x, g.y, g.z ?? rimZ); }
  }
  for (const pad of byKind('rom-pad')) { const g = pad.attributes?.geo; if (g?.polygon) { const c = centroid(g.polygon); add(c.x, c.y, g.z ?? rimZ); } }
  const tailings = first('tailings')?.attributes?.geo;
  if (tailings?.polygon) for (const [x, y] of tailings.polygon) add(x, y, (tailings.z ?? rimZ) - 3);
  for (const dump of byKind('waste-dump')) { const g = dump.attributes?.geo; if (g?.polygon) for (const [x, y] of g.polygon) add(x, y, rimZ); }
  const base = points.length ? points.reduce((s, p) => s + p[2], 0) / points.length : rimZ;

  // Elevation and how far the nearest known height is.
  function direct(x, y) {
    let wsum = 0;
    let zsum = 0;
    let nearest = Infinity;
    for (const p of points) {
      const d2 = (x - p[0]) ** 2 + (y - p[1]) ** 2;
      if (d2 < nearest) nearest = d2;
      const w = 1 / (d2 + 90000);
      wsum += w * w;
      zsum += w * w * p[2];
    }
    const near = Math.sqrt(nearest);
    const flat = wsum ? zsum / wsum : base;
    // Low hills far out, a gentle swell nearer, nothing where the mine stands.
    const away = smooth(250, 900, near);
    const far = smooth(1500, 9000, near);
    const swell = (fbm(x / 1400, y / 1400, 5, 3) - 0.5) * 18 * away;
    const hills = (Math.pow(fbm(x / 4200 + 3, y / 4200 - 7, 11, 4), 1.5) * 220 - 20) * far;
    // Low mountains on the horizon: ridged noise, far out.
    const ridge = 1 - Math.abs(2 * fbm(x / 7000 - 2, y / 7000 + 5, 19, 4) - 1);
    const mountains = Math.pow(ridge, 2.2) * 900 * smooth(9000, 17000, near);
    return { z: flat * (1 - far) + base * far + swell + hills + mountains, near };
  }
  // Over the mine the elevation is kept in a grid and interpolated: trucks and
  // roads ask for it often.
  const CELL = 40;
  let minX = Infinity; let minY = Infinity; let maxX = -Infinity; let maxY = -Infinity;
  for (const [x, y] of points) { minX = Math.min(minX, x); maxX = Math.max(maxX, x); minY = Math.min(minY, y); maxY = Math.max(maxY, y); }
  minX -= 600; minY -= 600; maxX += 600; maxY += 600;
  const gx = Math.max(2, Math.ceil((maxX - minX) / CELL) + 1);
  const gy = Math.max(2, Math.ceil((maxY - minY) / CELL) + 1);
  const gridZ = new Float32Array(gx * gy);
  const gridNear = new Float32Array(gx * gy);
  if (points.length) {
    for (let j = 0; j < gy; j += 1) {
      for (let i = 0; i < gx; i += 1) {
        const h = direct(minX + i * CELL, minY + j * CELL);
        gridZ[j * gx + i] = h.z;
        gridNear[j * gx + i] = h.near;
      }
    }
  }
  function at(x, y) {
    const fx = (x - minX) / CELL;
    const fy = (y - minY) / CELL;
    if (!points.length || fx < 0 || fy < 0 || fx >= gx - 1 || fy >= gy - 1) return points.length ? direct(x, y) : { z: base, near: Infinity };
    const i = Math.floor(fx); const j = Math.floor(fy);
    const tx = fx - i; const ty = fy - j;
    const k = j * gx + i;
    const lerp2 = (a) => (a[k] * (1 - tx) + a[k + 1] * tx) * (1 - ty) + (a[k + gx] * (1 - tx) + a[k + gx + 1] * tx) * ty;
    return { z: lerp2(gridZ), near: lerp2(gridNear) };
  }
  return { at, base };
}

// -- colour of the rock by depth --------------------------------------------------

function rockColor(rimZ, z, s) {
  const d = rimZ - z + (fbm(s / 380, z / 60, 3, 3) - 0.5) * 26;
  let c;
  if (d < 30) c = mix(C.oxide, C.leached, smooth(8, 24, d));
  else if (d < 70) c = mix(C.leached, C.transition, smooth(40, 70, d));
  else if (d < 150) c = mix(C.transition, C.porphyry, smooth(95, 150, d));
  else c = mix(C.porphyry, C.green, smooth(150, 230, d) * fbm(s / 500, 1, 9, 2));
  // Pale dykes cut across here and there.
  const dyke = smooth(0.8, 0.86, noise2(s / 160, z / 40, 17));
  c = mix(c, C.dyke, dyke * 0.35);
  // Strata: thin layers of lighter and darker rock, gently wavy along the wall.
  const layer = Math.sin(z / 3.3 + 2.4 * fbm(s / 260, z / 50, 29, 2)) * 0.5 + Math.sin(z / 1.4 + s / 180) * 0.2;
  return scale(c, (0.8 + 0.34 * fbm(s / 140, z / 45, 23, 3)) * (1 + 0.09 * layer) * K);
}

// -- the world ------------------------------------------------------------------------

// Build the ground, pit, roads, dump and pads. `L(x, y, z)` moves mine
// coordinates to the scene's origin; `detail` sets how fine the meshes are.
export function buildTerrain({ L, O, surfaceZ, detail }) {
  const pit = buildPit({ spacing: detail.pitSpacing, maxSamples: detail.pitSamples });
  const rimZ = pit ? pit.rimZ : surfaceZ;
  const rimLoop = pit ? pit.loops[0].pts : null;
  const ground = createGround(rimZ, rimLoop);
  const out = { pit, ground, meshes: [], roadMeshes: new Map(), heights: null, rimZ };

  // -- ground mesh: a grid fine over the mine, coarser to the horizon ------------
  const lease = first('mine')?.attributes?.geo?.polygon ?? [[-1700, -1800], [6600, -1800], [6600, 1900], [-1700, 1900]];
  const box = { minX: Math.min(...lease.map((p) => p[0])), maxX: Math.max(...lease.map((p) => p[0])),
    minY: Math.min(...lease.map((p) => p[1])), maxY: Math.max(...lease.map((p) => p[1])) };
  const axis = (lo, hi, cell, reach) => {
    const values = [];
    for (let v = lo; v <= hi + 1e-6; v += cell) values.push(v);
    let step = cell;
    let v = lo;
    while (v > lo - reach) { step *= 1.28; v -= step; values.unshift(v); }
    step = cell; v = values[values.length - 1];
    while (v < hi + reach) { step *= 1.28; v += step; values.push(v); }
    return values;
  };
  const xs = axis(box.minX, box.maxX, detail.groundCell, detail.horizon);
  const ys = axis(box.minY, box.maxY, detail.groundCell, detail.horizon);
  const nx = xs.length;
  const ny = ys.length;
  const gpos = new Float32Array(nx * ny * 3);
  const galb = new Float32Array(nx * ny * 3);
  const guv = new Float32Array(nx * ny * 2);
  const inside = new Uint8Array(nx * ny);
  for (let j = 0; j < ny; j += 1) {
    for (let i = 0; i < nx; i += 1) {
      let x = xs[i];
      let y = ys[j];
      const k = j * nx + i;
      let z;
      let near;
      if (rimLoop && pointInPolygon(x, y, rimLoop)) {
        const p = nearestOnLoop(x, y, rimLoop);
        x = p.x; y = p.y; z = rimZ; near = 0;
        inside[k] = 1;
      } else {
        const h = ground.at(x, y);
        z = h.z; near = h.near;
      }
      gpos.set(L(x, y, z), k * 3);
      guv[k * 2] = x / 320; guv[k * 2 + 1] = y / 320;
      // Steppe colours in patches; worked, dusty ground where the mine stands.
      const n1 = fbm(x / 900, y / 900, 2, 3);
      const n2 = fbm(x / 260, y / 260, 8, 2);
      let c = mix(C.grass, C.sage, smooth(0.38, 0.64, n1));
      c = mix(c, C.soil, smooth(0.6, 0.76, n2) * 0.65);
      c = mix(c, C.salt, smooth(0.66, 0.78, fbm(x / 1700 + 4, y / 1700 - 2, 13, 3)) * 0.7);
      c = mix(c, C.valley, smooth(0.55, 0.3, fbm(x / 3000, y / 3000, 4, 2)) * 0.5);
      const worked = 1 - smooth(60, 420, near);
      c = mix(c, C.worked, worked * 0.85);
      const rimDust = rimLoop ? 1 - smooth(0, 160, nearestOnLoop(x, y, rimLoop).d) : 0;
      c = mix(c, C.dusty, rimDust * 0.7);
      // Bare rock on the high ground far out.
      c = mix(c, C.hill, smooth(ground.base + 60, ground.base + 260, z) * 0.75);
      galb.set(scale(c, K * (0.92 + 0.16 * n2)), k * 3);
    }
  }
  const index = [];
  for (let j = 0; j < ny - 1; j += 1) {
    for (let i = 0; i < nx - 1; i += 1) {
      const a = j * nx + i; const b = a + 1; const c = a + nx; const d = c + 1;
      if (inside[a] && inside[b] && inside[d]) { /* in the pit */ } else index.push(a, b, d);
      if (inside[a] && inside[d] && inside[c]) { /* in the pit */ } else index.push(a, d, c);
    }
  }
  const groundGeometry = new THREE.BufferGeometry();
  groundGeometry.setAttribute('position', new THREE.BufferAttribute(gpos, 3));
  groundGeometry.setAttribute('uv', new THREE.BufferAttribute(guv, 2));
  groundGeometry.setAttribute('color', new THREE.BufferAttribute(new Float32Array(nx * ny * 3), 3));
  groundGeometry.setIndex(index);
  groundGeometry.computeVertexNormals();
  groundGeometry.userData.albedo = galb;
  groundGeometry.userData.normals = Float32Array.from(groundGeometry.attributes.normal.array);
  groundGeometry.deleteAttribute('normal');
  groundGeometry.computeBoundingSphere();
  out.groundGeometry = groundGeometry;

  // -- the pit -----------------------------------------------------------------
  const pitRoads = [];
  for (const segment of byKind('road-segment')) {
    const spec = segment.attributes?.spec ?? {};
    const path = segment.attributes?.geo?.path;
    if (!path || path.length < 2 || (spec.road_class !== 'ramp' && spec.road_class !== 'bench')) continue;
    pitRoads.push({ path: densify(path.map((p) => [p[0], p[1], p[2] ?? surfaceZ]), 10), half: (spec.width_m ?? 25) / 2 });
  }
  out.pitRoads = pitRoads;
  if (pit) {
    const { loops, bands } = pit;
    const reachZ = pit.benchM * 1.6;
    // Each loop's points take the height of a ramp they lie on, so ramps cut the walls.
    const zs = loops.map((loop) => loop.pts.map(([x, y]) => {
      let zHere = loop.z;
      for (const road of pitRoads) {
        const near = distanceToPolyline(x, y, road.path);
        if (Math.abs(near.z - loop.z) > reachZ) continue;
        if (near.d < road.half + 1) { zHere = near.z; break; }
        if (near.d < road.half + 7) { zHere = near.z + (zHere - near.z) * ((near.d - road.half - 1) / 6); break; }
      }
      return zHere;
    }));
    // Where the roads run: no windrow across them.
    const onRoad = roadMask(pitRoads, rimLoop);
    // Distance along each loop, for the texture and the colour of the rock.
    const along = loops.map((loop) => {
      const s = [0];
      for (let k = 1; k < loop.pts.length; k += 1) s.push(s[k - 1] + Math.hypot(loop.pts[k][0] - loop.pts[k - 1][0], loop.pts[k][1] - loop.pts[k - 1][1]));
      return s;
    });
    const faces = new Builder({ uv: true });
    const flats = new Builder({ uv: true });
    const n = pit.samples;
    // Across a face: the broken crest (light, it catches the sun), the face,
    // the scree at its toe (flatter, darker). Across a berm: rubble at the toe
    // of the face above, the berm, the safety windrow along the crest, the crest.
    const FACE_ROWS = [[0, 0, 1.14], [0.1, 0.07, 1.0], [0.8, 0.86, 0.8], [1, 1, 0.6]];
    const FLAT_ROWS = [[0, 0, 0.66], [0.18, 0, 0.9], [0.74, 0, 1.0], [0.86, 0, 1.12], [1, 0, 1.04]];
    bands.forEach((band, index) => {
      const outer = loops[band.outer].pts;
      const inner = loops[band.inner].pts;
      const zo = zs[band.outer];
      const zi = zs[band.inner];
      const so = along[band.outer];
      const si = along[band.inner];
      const face = band.type === 'face';
      const builder = face ? faces : flats;
      const rows = face ? FACE_ROWS : FLAT_ROWS;
      builder.tag = index;
      const sAt = (k) => (k === n ? so[n - 1] + Math.hypot(outer[0][0] - outer[n - 1][0], outer[0][1] - outer[n - 1][1]) : so[k]);
      const siAt = (k) => (k === n ? si[n - 1] + Math.hypot(inner[0][0] - inner[n - 1][0], inner[0][1] - inner[n - 1][1]) : si[k]);
      // A point of the band at sample k (0..n), row r: [mine x, y, z, s along].
      const point = (k, r) => {
        const kk = k % n;
        const [t, tz] = rows[r];
        const x = outer[kk][0] + (inner[kk][0] - outer[kk][0]) * t;
        const y = outer[kk][1] + (inner[kk][1] - outer[kk][1]) * t;
        let z = zo[kk] + (zi[kk] - zo[kk]) * (face ? tz : t);
        if (!face && r === 3 && Math.abs(zo[kk] - zi[kk]) < 0.5 && !onRoad(x, y)) z += 1.5;
        const s = sAt(k) + (siAt(k) - sAt(k)) * t;
        return [x, y, z, s];
      };
      for (let k = 0; k < n; k += 1) {
        for (let r = 0; r < rows.length - 1; r += 1) {
          const p = [point(k, r), point(k + 1, r), point(k + 1, r + 1), point(k, r + 1)];
          const shade = [rows[r][2], rows[r][2], rows[r + 1][2], rows[r + 1][2]];
          const color = p.map(([x, y, z, s], i) => {
            const rock = rockColor(rimZ, z, s);
            if (face) return scale(i < 2 || r === 0 ? rock : mix(rock, scale(C.berm, K), r === 2 ? 0.25 : 0), shade[i]);
            return scale(mix(rock, scale(C.berm, K), 0.5), shade[i] * (0.97 + 0.06 * noise2(x / 23, y / 23, 5)));
          });
          const uv = face ? p.map(([, , z, s]) => [s / 90, z / 30]) : p.map(([x, y]) => [x / 120, y / 120]);
          builder.quad(L(p[0][0], p[0][1], p[0][2]), L(p[1][0], p[1][1], p[1][2]), L(p[2][0], p[2][1], p[2][2]), L(p[3][0], p[3][1], p[3][2]),
            null, true, uv, color);
        }
      }
    });
    // The pit floor, cut fine enough for the light of the floodlights, with a
    // sump of water in its lowest corner.
    const last = loops[loops.length - 1];
    const lastZ = zs[loops.length - 1];
    flats.tag = bands.length;
    const floorFaces = THREE.ShapeUtils.triangulateShape(last.pts.map(([x, y]) => new THREE.Vector2(x, y)), []);
    const floorZ = lastZ.reduce((a, b) => a + b, 0) / lastZ.length;
    const col = (p) => scale(mix(C.floor, C.berm, 0.3 + 0.4 * fbm(p[0] / 120, p[1] / 120, 4, 2)), K);
    const cut = (a, b, c, depth) => {
      const longest = Math.max(Math.hypot(a[0] - b[0], a[1] - b[1]), Math.hypot(b[0] - c[0], b[1] - c[1]), Math.hypot(c[0] - a[0], c[1] - a[1]));
      if (depth === 0 || longest < 40) {
        flats.tri(L(a[0], a[1], a[2]), L(b[0], b[1], b[2]), L(c[0], c[1], c[2]), col(a), col(b), col(c), true, [a, b, c].map((p) => [p[0] / 120, p[1] / 120]));
        return;
      }
      const m = (p, q) => [(p[0] + q[0]) / 2, (p[1] + q[1]) / 2, (p[2] + q[2]) / 2];
      const ab = m(a, b); const bc = m(b, c); const ca = m(c, a);
      cut(a, ab, ca, depth - 1); cut(ab, b, bc, depth - 1); cut(ca, bc, c, depth - 1); cut(ab, bc, ca, depth - 1);
    };
    for (const [i, j, k] of floorFaces) {
      const P = [i, j, k].map((v) => [last.pts[v][0], last.pts[v][1], lastZ[v] ?? floorZ]);
      cut(P[0], P[1], P[2], detail.pitSpacing > 30 ? 1 : 4);
    }
    out.faceGeometry = faces.geometry();
    out.flatGeometry = flats.geometry();
    out.faceOwners = faces.owner;
    out.flatOwners = flats.owner;
    out.pitZs = zs;
    out.heights = rasterise([faces, flats], rimLoop, O);
    // The sump: an oval of water on the floor, away from the roads.
    out.sump = findSump(last.pts, lastZ[0], pitRoads);
  }

  out.tracks = detail.roadStep >= 30 ? null : steppeTracks(ground, L, box);

  out.heightAt = (x, y) => {
    const h = out.heights;
    if (h) {
      const i = Math.floor((x - h.x0) / h.cell);
      const j = Math.floor((y - h.y0) / h.cell);
      if (i >= 0 && j >= 0 && i < h.nx && j < h.ny) {
        const z = h.z[j * h.nx + i];
        if (!Number.isNaN(z)) return z;
      }
    }
    return ground.at(x, y).z;
  };
  return out;
}

// Dirt tracks across the steppe: light ruts that wander in from the horizon
// to the mine's roads (service vehicles, the old roads of the herders).
function steppeTracks(ground, L, box) {
  const ends = [];
  for (const segment of byKind('road-segment')) {
    if (segment.attributes?.spec?.road_class !== 'surface') continue;
    for (const p of segment.attributes.geo.path) ends.push(p);
  }
  if (!ends.length) return null;
  const b = new Builder({ uv: true });
  const cx = (box.minX + box.maxX) / 2; const cy = (box.minY + box.maxY) / 2;
  const rut = scale(C.soil, K * 0.92);
  const mid = scale(C.dusty, K * 1.05);
  for (let i = 0; i < 9; i += 1) {
    const a = (i / 9) * Math.PI * 2 + (noise2(i, 3, 91) - 0.5) * 0.6;
    const r = 6500 + noise2(i, 7, 93) * 4000;
    let x = cx + Math.cos(a) * r; let y = cy + Math.sin(a) * r;
    const end = ends[Math.floor(noise2(i, 11, 97) * ends.length) % ends.length];
    const pts = [];
    for (let step = 0; step < 400; step += 1) {
      pts.push([x, y]);
      const dx = end[0] - x; const dy = end[1] - y;
      const d = Math.hypot(dx, dy);
      // They fade into the worked ground before the pit, the dump and the plant.
      if (d < 60 || ground.at(x, y).near < 420) break;
      const wander = (fbm(x / 900, y / 900, 37 + i, 2) - 0.5) * 2.2;
      const heading = Math.atan2(dy, dx) + wander * Math.min(1, d / 1500);
      x += Math.cos(heading) * 40; y += Math.sin(heading) * 40;
    }
    if (pts.length < 3) continue;
    ribbon(b, pts.filter((_, k) => k % 2 === 0 || k === pts.length - 1).map(([px, py]) => L(px, py, ground.at(px, py).z + 0.5)), 5.5, null, 0, false, [rut, mid, rut], 12);
  }
  return b;
}

function findSump(floor, z, roads) {
  const c = areaCentroid(floor);
  let best = null;
  for (let a = 0; a < 16; a += 1) {
    for (const f of [0.55, 0.4]) {
      const angle = (a / 16) * Math.PI * 2;
      const far = floor.reduce((m, p) => Math.max(m, Math.hypot(p[0] - c.x, p[1] - c.y)), 0);
      const x = c.x + Math.cos(angle) * far * f;
      const y = c.y + Math.sin(angle) * far * f * 0.6;
      if (!pointInPolygon(x, y, floor)) continue;
      const clear = Math.min(...roads.map((road) => distanceToPolyline(x, y, road.path).d), nearestOnLoop(x, y, floor).d);
      if (!best || clear > best.clear) best = { x, y, z, clear };
    }
  }
  return best && best.clear > 50 ? { ...best, rx: Math.min(80, best.clear * 0.8), ry: Math.min(45, best.clear * 0.5) } : null;
}

// A test for a point near a pit road (ramps and bench roads), from a grid.
function roadMask(roads, rimLoop, cell = 6) {
  let minX = Infinity; let minY = Infinity; let maxX = -Infinity; let maxY = -Infinity;
  for (const [x, y] of rimLoop) {
    minX = Math.min(minX, x); maxX = Math.max(maxX, x); minY = Math.min(minY, y); maxY = Math.max(maxY, y);
  }
  const nx = Math.ceil((maxX - minX) / cell) + 3;
  const ny = Math.ceil((maxY - minY) / cell) + 3;
  const grid = new Uint8Array(nx * ny);
  const x0 = minX - cell; const y0 = minY - cell;
  for (const road of roads) {
    const reach = road.half + 9;
    const r = Math.ceil(reach / cell);
    for (const [px, py] of densify(road.path, cell)) {
      const ci = Math.floor((px - x0) / cell); const cj = Math.floor((py - y0) / cell);
      for (let i = ci - r; i <= ci + r; i += 1) {
        for (let j = cj - r; j <= cj + r; j += 1) {
          if (i < 0 || j < 0 || i >= nx || j >= ny) continue;
          if (Math.hypot(x0 + (i + 0.5) * cell - px, y0 + (j + 0.5) * cell - py) <= reach) grid[j * nx + i] = 1;
        }
      }
    }
  }
  return (x, y) => {
    const i = Math.floor((x - x0) / cell); const j = Math.floor((y - y0) / cell);
    return i >= 0 && j >= 0 && i < nx && j < ny && grid[j * nx + i] === 1;
  };
}

// Highest surface of the pit in each cell of a grid, for draping and placing.
function rasterise(builders, rimLoop, O, cell = 6) {
  let minX = Infinity; let minY = Infinity; let maxX = -Infinity; let maxY = -Infinity;
  for (const [x, y] of rimLoop) {
    minX = Math.min(minX, x); maxX = Math.max(maxX, x); minY = Math.min(minY, y); maxY = Math.max(maxY, y);
  }
  const nx = Math.ceil((maxX - minX) / cell) + 2;
  const ny = Math.ceil((maxY - minY) / cell) + 2;
  const z = new Float32Array(nx * ny).fill(NaN);
  const x0 = minX - cell;
  const y0 = minY - cell;
  for (const builder of builders) {
    const pos = builder.pos;
    for (let t = 0; t < pos.length; t += 9) {
      const ax = pos[t] + O.x; const ay = pos[t + 1] + O.y; const az = pos[t + 2] + O.z;
      const bx = pos[t + 3] + O.x; const by = pos[t + 4] + O.y; const bz = pos[t + 5] + O.z;
      const cx = pos[t + 6] + O.x; const cy = pos[t + 7] + O.y; const cz = pos[t + 8] + O.z;
      const i0 = Math.max(0, Math.floor((Math.min(ax, bx, cx) - x0) / cell));
      const i1 = Math.min(nx - 1, Math.floor((Math.max(ax, bx, cx) - x0) / cell));
      const j0 = Math.max(0, Math.floor((Math.min(ay, by, cy) - y0) / cell));
      const j1 = Math.min(ny - 1, Math.floor((Math.max(ay, by, cy) - y0) / cell));
      const det = (by - cy) * (ax - cx) + (cx - bx) * (ay - cy);
      if (Math.abs(det) < 1e-9) continue;
      for (let j = j0; j <= j1; j += 1) {
        const py = y0 + (j + 0.5) * cell;
        for (let i = i0; i <= i1; i += 1) {
          const px = x0 + (i + 0.5) * cell;
          const l1 = ((by - cy) * (px - cx) + (cx - bx) * (py - cy)) / det;
          const l2 = ((cy - ay) * (px - cx) + (ax - cx) * (py - cy)) / det;
          const l3 = 1 - l1 - l2;
          if (l1 < -0.02 || l2 < -0.02 || l3 < -0.02) continue;
          const value = l1 * az + l2 * bz + l3 * cz;
          const k = j * nx + i;
          if (Number.isNaN(z[k]) || value > z[k]) z[k] = value;
        }
      }
    }
  }
  return { x0, y0, cell, nx, ny, z };
}

// -- roads ------------------------------------------------------------------------

// One road segment: compacted gravel with darker wheel tracks and a windrow
// on each side that drops away (both sides on the surface). Returns the
// builder and the draped centre line.
export function roadGeometry(segment, terrain, L, detail) {
  const spec = segment.attributes?.spec ?? {};
  const path = segment.attributes.geo.path;
  const width = spec.width_m ?? 25;
  const inPit = spec.road_class === 'ramp' || spec.road_class === 'bench';
  // Ramps bend: short steps; surface roads are straight between their points.
  const points = densify(path.map((p) => [p[0], p[1], p[2] ?? terrain.rimZ]), inPit ? detail.roadStep : Math.max(detail.roadStep, 30))
    .map(([x, y, z]) => [x, y, inPit ? Math.max(z, terrain.heightAt(x, y)) : terrain.heightAt(x, y)]);
  const b = new Builder({ uv: true });
  const edge = scale(C.roadEdge, K);
  const trackC = scale(C.track, K);
  const mid = scale(C.road, K);
  ribbon(b, points.map(([x, y, z]) => L(x, y, z)), width, null, 0.35, false, [edge, trackC, mid, trackC, edge], 30);
  // Windrows: a ridge of rock along the open edge.
  const n = points.length;
  const side = (s) => {
    const ridge = [];
    const foot = [];
    for (let i = 0; i < n; i += 1) {
      const p = points[i];
      const q = points[Math.min(n - 1, i + 1)];
      const o = points[Math.max(0, i - 1)];
      let dx = q[0] - o[0]; let dy = q[1] - o[1];
      const len = Math.hypot(dx, dy) || 1;
      dx /= len; dy /= len;
      const nxv = -dy * s; const nyv = dx * s;
      const e = width / 2;
      ridge.push([p[0] + nxv * (e + 1.6), p[1] + nyv * (e + 1.6), p[2] + 1.7]);
      foot.push([[p[0] + nxv * (e - 0.2), p[1] + nyv * (e - 0.2), p[2] + 0.3], [p[0] + nxv * (e + 3.6), p[1] + nyv * (e + 3.6), p[2] + 0.1]]);
    }
    return { ridge, foot };
  };
  const wall = scale(C.windrow, K);
  for (const s of detail.roadStep >= 30 ? [] : [-1, 1]) {
    if (inPit) {
      // Only on the side that falls away into the pit.
      const mid_ = points[Math.floor(n / 2)];
      const q = points[Math.min(n - 1, Math.floor(n / 2) + 1)];
      const dx = q[0] - mid_[0]; const dy = q[1] - mid_[1];
      const len = Math.hypot(dx, dy) || 1;
      const px = mid_[0] + (-dy / len) * s * (width / 2 + 14);
      const py = mid_[1] + (dx / len) * s * (width / 2 + 14);
      if (terrain.heightAt(px, py) > mid_[2] - 3) continue;
    }
    const { ridge, foot } = side(s);
    for (let i = 0; i < n - 1; i += 1) {
      b.quad(L(...foot[i][0]), L(...foot[i + 1][0]), L(...ridge[i + 1]), L(...ridge[i]), wall, false, null, [scale(wall, 0.85), scale(wall, 0.85), wall, wall]);
      b.quad(L(...ridge[i]), L(...ridge[i + 1]), L(...foot[i + 1][1]), L(...foot[i][1]), wall, false);
    }
  }
  return { builder: b, points, width, inPit };
}

// -- the waste dump ----------------------------------------------------------------

// One lift of a dump: its toe wanders a little, its crest is set back by the
// slope of loose rock (more here, less there, as the trucks tipped).
function liftOf(b, polygon, z0, h, inset, side, top, L, seed = 1) {
  const c = areaCentroid(polygon);
  const ring = densify([...polygon, polygon[0]].map(([x, y]) => [x, y, 0]), 35).slice(0, -1);
  const toe = ring.map(([x, y], i) => {
    const d = Math.hypot(x - c.x, y - c.y) || 1;
    const w = (noise2(i * 0.41, seed, 29) - 0.5) * 34;
    return [x + ((x - c.x) / d) * w, y + ((y - c.y) / d) * w];
  });
  const upper = toe.map(([x, y], i) => {
    const d = Math.hypot(x - c.x, y - c.y) || 1;
    const k = Math.min(inset * (0.75 + 0.7 * noise2(i * 0.27, seed, 31)), d * 0.8);
    return [x - ((x - c.x) / d) * k, y - ((y - c.y) / d) * k];
  });
  let s = 0;
  const n = toe.length;
  for (let i = 0; i < n; i += 1) {
    const j = (i + 1) % n;
    const len = Math.hypot(toe[j][0] - toe[i][0], toe[j][1] - toe[i][1]);
    const uv = [[s / 40, 0], [(s + len) / 40, 0], [(s + len) / 40, h / 14], [s / 40, h / 14]];
    const shade = 0.92 + 0.16 * noise2(i * 0.6, seed, 37);
    b.quad(L(toe[i][0], toe[i][1], z0), L(toe[j][0], toe[j][1], z0), L(upper[j][0], upper[j][1], z0 + h), L(upper[i][0], upper[i][1], z0 + h),
      side, false, uv, [scale(side, 0.84 * shade), scale(side, 0.84 * shade), scale(side, shade), scale(side, shade)]);
    s += len;
  }
  return upper;
}

function topOf(b, polygon, z, color, L) {
  const faces = THREE.ShapeUtils.triangulateShape(polygon.map(([x, y]) => new THREE.Vector2(x, y)), []);
  for (const [i, j, m] of faces) b.tri(L(polygon[i][0], polygon[i][1], z), L(polygon[j][0], polygon[j][1], z), L(polygon[m][0], polygon[m][1], z), color);
}

// The dump in lifts: the lift the trucks tip on (up to the twin's elevation),
// an older lift behind it, and tip heads along the crest by the tipping point.
export function dumpGeometry(dump, terrain, L) {
  const g = dump.attributes.geo;
  const polygon = g.polygon;
  const c = areaCentroid(polygon);
  const z0 = terrain.ground.at(c.x, c.y).z;
  const h = Math.max(8, (g.z ?? z0 + 20) - z0);
  const sides = new Builder({ uv: true });
  const tops = new Builder({ uv: true });
  const slope = h / Math.tan((37 * Math.PI) / 180);
  const upper = liftOf(sides, polygon, z0, h, slope, scale(C.waste, K), null, L);
  topOf(tops, upper, z0 + h, scale(C.wasteTop, K), L);
  // The older lift: towards the side away from the tipping point.
  const tip = dump.attributes.spec ?? {};
  const tx = tip.tip_x ?? c.x;
  const ty = tip.tip_y ?? c.y;
  const awayX = c.x - (tx - c.x) * 0.5;
  const awayY = c.y - (ty - c.y) * 0.5;
  // Two older lifts, each set back towards the side away from the tipping point.
  let below = upper;
  let top = z0 + h;
  for (const [share, lift, seed] of [[0.66, 15, 7], [0.6, 13, 11]]) {
    const cb = areaCentroid(below);
    const next = below.map(([x, y]) => [awayX + (cb.x - c.x) * 0.3 + (x - cb.x) * share, awayY + (cb.y - c.y) * 0.3 + (y - cb.y) * share])
      .filter(([x, y]) => pointInPolygon(x, y, below));
    if (next.length < 3) break;
    const crest = liftOf(sides, next, top - 0.2, lift, lift / Math.tan((37 * Math.PI) / 180), scale(C.waste, K * (seed === 7 ? 0.97 : 0.94)), null, L, seed);
    top += lift - 0.2;
    topOf(tops, crest, top, scale(C.wasteTop, K * (seed === 7 ? 1.02 : 1.04)), L);
    below = crest;
  }
  // Tip heads: the last loads, end-dumped in a row along the crest near the tipping point.
  const crest = upper;
  const near = nearestOnLoop(tx, ty, crest);
  for (let i = -7; i <= 7; i += 1) {
    const along = i * 14;
    let best = null;
    for (let k = 0; k < crest.length; k += 1) {
      const a = crest[k]; const bb = crest[(k + 1) % crest.length];
      const len = Math.hypot(bb[0] - a[0], bb[1] - a[1]) || 1;
      const ux = (bb[0] - a[0]) / len; const uy = (bb[1] - a[1]) / len;
      const t = (near.x - a[0]) * ux + (near.y - a[1]) * uy;
      if (t >= -1 && t <= len + 1) best = { x: near.x + ux * along, y: near.y + uy * along, ux, uy };
    }
    if (!best) continue;
    const inward = [c.x - best.x, c.y - best.y];
    const il = Math.hypot(...inward) || 1;
    const px = best.x + (inward[0] / il) * (5 + (i % 2) * 3);
    const py = best.y + (inward[1] / il) * (5 + (i % 2) * 3);
    const shade = 0.9 + 0.2 * noise2(i, 3, 5);
    const [lx, ly, lz] = L(px, py, z0 + h - 0.1);
    tops.cone(lx, ly, lz, 5.5, 3.2, scale(C.waste, K * shade), 7);
  }
  return { sides, tops, top: { ...c, z: z0 + h }, base: z0, height: h };
}

// -- ROM pads -----------------------------------------------------------------------

// A pad's ore: a row of truck-tipped piles along its long side, as high as the stock.
export function padGeometry(pad, terrain, L, level) {
  const g = pad.attributes.geo;
  const polygon = g.polygon;
  const z0 = Math.max(g.z ?? terrain.rimZ, terrain.ground.at(...Object.values(centroid(polygon))).z);
  const slab = new Builder({ uv: true });
  topOf(slab, polygon, z0 + 0.25, scale(C.pad, K), L);
  const piles = new Builder({ uv: true });
  const rich = feature(pad, 'role') === 'high-grade';
  const color = scale(rich ? C.oreRich : C.orePoor, K);
  // The long axis of the pad.
  const xs = polygon.map((p) => p[0]); const ys = polygon.map((p) => p[1]);
  const wide = Math.max(...xs) - Math.min(...xs) >= Math.max(...ys) - Math.min(...ys);
  const lo = wide ? Math.min(...xs) : Math.min(...ys);
  const hi = wide ? Math.max(...xs) : Math.max(...ys);
  const across = wide ? (Math.min(...ys) + Math.max(...ys)) / 2 : (Math.min(...xs) + Math.max(...xs)) / 2;
  const depth = wide ? Math.max(...ys) - Math.min(...ys) : Math.max(...xs) - Math.min(...xs);
  const stack = (pad.attributes?.spec?.stack_height_m ?? 8) * 1.6;
  const h = Math.max(0, stack * clamp(level, 0, 1));
  if (h > 0.4) {
    const r = Math.min(depth * 0.42, h / Math.tan((37 * Math.PI) / 180) + 6);
    const count = Math.max(1, Math.floor((hi - lo - 2 * r) / (r * 1.1)) + 1);
    // As the stock grows the row grows along the pad.
    const used = Math.max(1, Math.round(count * clamp(level * 1.4, 0.15, 1)));
    for (let i = 0; i < used; i += 1) {
      const t = lo + r + i * r * 1.1 + 8;
      const x = wide ? t : across + (i % 2 ? 6 : -6);
      const y = wide ? across + (i % 2 ? 6 : -6) : t;
      const [lx, ly, lz] = L(x, y, z0 + 0.25);
      piles.cone(lx, ly, lz, r, h, scale(color, 0.94 + 0.12 * noise2(i, 1, 3)), 12, r * 0.18);
    }
  }
  return { slab, piles, z0 };
}
