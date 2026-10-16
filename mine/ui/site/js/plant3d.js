// The plant and the surface buildings of the 3D view, where the twins stand:
// the gyratory crusher with its dump pocket, the 3.7 km conveyor on its
// gantry, the stockpile under its stacker, the mill hall with the SAG mill
// and the ball mills under a glass roof, the flotation cells, the thickeners,
// the concentrate shed, the tailings pond, the substation, the workshop and
// the fuel station. Opaque parts are merged into one mesh (one draw call),
// each triangle naming the twin it belongs to, for picking.

import * as THREE from 'three';
import { byKind, first, twins, resolveRef } from './store.js';
import { Builder, ribbon, densify, rgb, scale, mix, clamp, convexHull, centroid } from './geom3d.js';
import { TEXTURE_MEAN } from './textures3d.js';
import { plantLayout } from './plant.js';
import { areaCentroid } from './pitgeom.js';

const K = 1 / TEXTURE_MEAN;
const P = {
  concrete: rgb('#aba59a'),
  clad: rgb('#c9cfd0'),
  cladWarm: rgb('#c4beb1'),
  roof: rgb('#5f7280'),
  trim: rgb('#2f5272'),
  steel: rgb('#5b6064'),
  dark: rgb('#34383b'),
  shell: rgb('#a2aca2'),
  motor: rgb('#3d4246'),
  tank: rgb('#b9bdb6'),
  froth: rgb('#b8a666'),
  frothDark: rgb('#7e7347'),
  crane: rgb('#d6a21f'),
  belt: rgb('#2b2b2a'),
  water: rgb('#557a7a'),
  pond: rgb('#7c9a95'),
  beach: rgb('#c4bcaa'),
  dam: rgb('#a49a86'),
  apron: rgb('#9c968a'),
  conc: rgb('#3f4840'),
  ore: rgb('#806650'),
  white: [1, 1, 1],
};

// A building with cladding panels of alternating shade, a roof, and an
// optional band of windows (into `win`, lit at night).
function clad(b, cx, cy, z0, sx, sy, sz, wall, roof, { panel = 4, band = null, win = null, doors = 0, noRoof = false } = {}) {
  const x0 = cx - sx / 2; const x1 = cx + sx / 2; const y0 = cy - sy / 2; const y1 = cy + sy / 2; const z1 = z0 + sz;
  const sides = [[[x0, y0], [x1, y0]], [[x1, y0], [x1, y1]], [[x1, y1], [x0, y1]], [[x0, y1], [x0, y0]]];
  const sign = 1;           // (dy, -dx) of each side points out of the box
  sides.forEach(([a, c], index) => {
    const len = Math.hypot(c[0] - a[0], c[1] - a[1]);
    const count = Math.max(1, Math.round(len / panel));
    for (let i = 0; i < count; i += 1) {
      const f0 = i / count; const f1 = (i + 1) / count;
      const p0 = [a[0] + (c[0] - a[0]) * f0, a[1] + (c[1] - a[1]) * f0];
      const p1 = [a[0] + (c[0] - a[0]) * f1, a[1] + (c[1] - a[1]) * f1];
      const shade = i % 2 ? 0.93 : 1.0;
      const out = [(c[1] - a[1]) / len, -(c[0] - a[0]) / len, 0];
      b.quad([p0[0], p0[1], z0], [p1[0], p1[1], z0], [p1[0], p1[1], z1], [p0[0], p0[1], z1], scale(wall, shade), false,
        null, [scale(wall, shade * 0.82), scale(wall, shade * 0.82), scale(wall, shade), scale(wall, shade)], [out[0] * sign, out[1] * sign, 0]);
    }
    const nx = (c[1] - a[1]) / len; const ny = -(c[0] - a[0]) / len;
    if (band && win) {
      const o = 0.08;
      win.quad([a[0] + nx * o, a[1] + ny * o, z0 + band[0]], [c[0] + nx * o, c[1] + ny * o, z0 + band[0]],
        [c[0] + nx * o, c[1] + ny * o, z0 + band[1]], [a[0] + nx * o, a[1] + ny * o, z0 + band[1]], P.white, false);
    }
    if (doors && index === 0) {
      for (let d = 0; d < doors; d += 1) {
        const f = (d + 0.5) / doors;
        const mx = a[0] + (c[0] - a[0]) * f; const my = a[1] + (c[1] - a[1]) * f;
        const w = Math.min(7, len / doors * 0.6) / 2;
        const ux = (c[0] - a[0]) / len; const uy = (c[1] - a[1]) / len;
        b.quad([mx - ux * w + nx * 0.06, my - uy * w + ny * 0.06, z0], [mx + ux * w + nx * 0.06, my + uy * w + ny * 0.06, z0],
          [mx + ux * w + nx * 0.06, my + uy * w + ny * 0.06, z0 + Math.min(8, sz * 0.6)], [mx - ux * w + nx * 0.06, my - uy * w + ny * 0.06, z0 + Math.min(8, sz * 0.6)], P.dark, false);
      }
    }
  });
  if (!noRoof) {
    b.quad([x0, y0, z1], [x1, y0, z1], [x1, y1, z1], [x0, y1, z1], roof, false, null, null, [0, 0, 1]);
    // A trim along the top of the walls (the parapet's flashing).
    if (sz > 4) b.box(cx, cy, z1 - 0.5, sx + 0.3, sy + 0.3, 0.6, P.trim, roof);
  }
}

// A state band round the top of a building: a frame of four strips (sx by sy, centre cx, cy, at z).
function frame(b, cx, cy, z, sx, sy, w = 1.4) {
  b.box(cx, cy - sy / 2 + w / 2, z, sx, w, 0.8, P.white);
  b.box(cx, cy + sy / 2 - w / 2, z, sx, w, 0.8, P.white);
  b.box(cx - sx / 2 + w / 2, cy, z, w, sy - 2 * w, 0.8, P.white);
  b.box(cx + sx / 2 - w / 2, cy, z, w, sy - 2 * w, 0.8, P.white);
}

// A lattice pylon or lighting mast: a tapered beam with a head.
function mast(b, x, y, z0, h, color = P.steel) {
  b.beam([x, y, z0], [x, y, z0 + h], 0.6, 0.6, color);
  b.box(x, y, z0 + h, 2.4, 0.6, 0.5, P.dark);
}

// A plant model seen from afar (more than a kilometre and a half away, where
// it is a few dozen pixels): its silhouette in a few hundred triangles, in
// the model's own axes (metres, Z up, origin as the model's). One draw call
// instead of a dozen, lit like the rest of the plant.
export function modelProxy(model) {
  const b = new Builder();
  const wall = rgb('#d3d6d2');
  if (model === 'crusher') {
    b.box(-15, 0, 0, 30, 34, 4.4, P.concrete);
    b.quad([-8.2, -10.2, 4.45], [0.2, -10.2, 4.45], [0.2, 10.2, 4.45], [-8.2, 10.2, 4.45], scale(P.dark, 0.35), false, null, null, [0, 0, 1]);
    clad(b, 16.4, 0, 0, 31.2, 24, 34, wall, P.roof, { panel: 6 });
    b.box(-4.5, 0, 13.5, 10.5, 25, 2.7, P.roof);
    b.box(22, -8, 0, 6, 4, 18, P.tank);
  } else if (model === 'mill_hall') {
    clad(b, 0, 0, 0, 160, 46, 30, wall, null, { panel: 8, noRoof: true });
    for (const x of [-80, 80]) b.tri([x, -23, 30], [x, 23, 30], [x, 0, 37], wall, wall, wall, false, null, null, [Math.sign(x), 0, 0]);
    b.quad([-81, -24.2, 29.6], [81, -24.2, 29.6], [81, 0, 38], [-81, 0, 38], P.roof, false, null, null, [0, -0.6, 1]);
    b.quad([-81, 0, 38], [81, 0, 38], [81, 24.2, 29.6], [-81, 24.2, 29.6], P.roof, false, null, null, [0, 0.6, 1]);
  } else if (model === 'flotation') {
    b.box(0, 0, 0, 70, 22.4, 1.2, P.concrete);
    for (const y of [-5.6, 5.6]) for (let i = 0; i < 7; i += 1) b.cylinder([(i - 3) * 9.43, y, 1.2], [(i - 3) * 9.43, y, 8.1], 3.6, P.tank, 10, P.froth);
    b.box(0, 0, 8.6, 76, 2.6, 0.8, P.steel);
  } else if (model === 'thickener_tailings' || model === 'thickener_concentrate') {
    const r = model === 'thickener_tailings' ? 38 : 13;
    b.cylinder([0, 0, 0], [0, 0, 5], r + 1.4, P.concrete, 32, P.water);
    b.cylinder([0, 0, 0], [0, 0, 11], 2.5, P.clad, 8, P.roof);
    b.box(0, 0, 5.3, 2 * r, 2, 1.2, P.steel);
    b.box(r + 6, -4, 0, 6, 5, 4, P.cladWarm);
  } else if (model === 'clay_plant') {
    b.box(4, 2, 0, 110, 64, 0.3, scale(P.concrete, 0.85));
    clad(b, 0, 0, 0.3, 64, 30, 20, P.cladWarm, P.roof, { panel: 8 });
    clad(b, -41, -4, 0.3, 18, 18, 10, P.clad, rgb('#2f5878'), { panel: 6 });
    b.box(42, -2, 0, 9, 9, 19, P.steel);
    for (let i = 0; i < 5; i += 1) b.cylinder([-18 + i * 6.6, 22, 0], [-18 + i * 6.6, 22, 6], 2.5, P.tank, 8, P.froth);
    for (const x of [-38, -20]) b.cylinder([x, 20, 0], [x, 20, 10], 4.5, P.tank, 10, P.tank);
    b.beam([0, -15, 18], [0, -62, 2], 3.2, 3, P.clad);
  }
  return b;
}

// `models`: the ids of the Blender models that are loaded (gltf3d.js). Where
// one stands for a piece of the plant (the crusher, the mill hall, the
// flotation, the thickeners, the conveyor, the clay-ore plant), that piece is
// not built here: its placement is returned in `placements` for the scene to
// draw the model there, and the units take the model's anchors.
export function buildPlant({ L, terrain, detail, models = null }) {
  const has = (id) => Boolean(models?.has(id));
  const opaque = new Builder();
  const glass = new Builder();
  const win = new Builder();
  const water = new Builder();
  const units = new Map();
  const lamps = [];
  const bands = [];               // [{id, builder}] state bands, one mesh each
  const groundZ = (x, y) => terrain.ground.at(x, y).z;
  const layout = plantLayout();
  const pos = layout.pos;
  const placeOf = (twin) => (twin ? pos.get(twin.thingId) ?? (typeof twin.attributes?.geo?.x === 'number' ? twin.attributes.geo : null) : null);
  const put = (local, x, y, angle, tag, z = groundZ(x, y)) => opaque.append(local, angle, L(x, y, z), tag);
  const out = { units, lamps, opaque, glass, win, water, bands, stockpile: null, belt: null, hallAnchor: null, places: [], placements: [], conveyorDeck: null, crusherBays: null };
  // A point of a model placed at (x, y) and turned by `angle`: its local (lx, ly) in the mine grid.
  const at = (x, y, angle, lx, ly) => [x + Math.cos(angle) * lx - Math.sin(angle) * ly, y + Math.sin(angle) * lx + Math.cos(angle) * ly];

  // -- the crusher --------------------------------------------------------------
  const crusher = first('crusher');
  const atCrusher = placeOf(crusher);
  if (atCrusher) {
    // The dump pocket faces the road the trucks come by: its access node, else the nearest point of a road.
    let toward = { x: atCrusher.x - 60, y: atCrusher.y };
    let best = Infinity;
    for (const segment of byKind('road-segment')) {
      for (const p of segment.attributes?.geo?.path ?? []) {
        const d = Math.hypot(p[0] - atCrusher.x, p[1] - atCrusher.y);
        if (d < best && d > 15) { best = d; toward = { x: p[0], y: p[1] }; }
      }
    }
    const access = twins.get(resolveRef(crusher.attributes?.links?.access_node))?.attributes?.geo;
    if (typeof access?.x === 'number' && Math.hypot(access.x - atCrusher.x, access.y - atCrusher.y) > 15) toward = { x: access.x, y: access.y };
    const angle = Math.atan2(toward.y - atCrusher.y, toward.x - atCrusher.x) + Math.PI;  // local -x to the road
    if (has('crusher')) {
      // The model's dump pocket faces the road the trucks come by. Its two bays are at local
      // x = -15.5, y = -5 and +5 (a truck's centre, its tail over the hopper): the model stands
      // with the bays at the end of the road and the building behind them.
      const ox = toward.x + Math.cos(angle) * 15.5;
      const oy = toward.y + Math.sin(angle) * 15.5;
      const z = groundZ(ox, oy);
      out.placements.push({ model: 'crusher', id: crusher.thingId, x: ox, y: oy, z, angle, owners: {} });
      out.crusherBays = [-5, 5].map((ly) => {
        const [bx, by] = at(ox, oy, angle, -15.5, ly);
        return { x: bx, y: by, z, angle };
      });
      // The scene lifts the models 0.3 m off the ground; the hopper, sunk below it, shows as a dark void.
      const hole = new Builder();
      hole.quad([-8.2, -10.2, 0.22], [0.2, -10.2, 0.22], [0.2, 10.2, 0.22], [-8.2, 10.2, 0.22], scale(P.dark, 0.35), false, null, null, [0, 0, 1]);
      put(hole, ox, oy, angle, crusher.thingId, z);
      const [cx, cy] = at(ox, oy, angle, 16, 0);
      units.set(crusher.thingId, { at: { x: cx, y: cy, z: z + 38 }, r: 30, base: z });
      const band = new Builder();
      frame(band, 16.4, 0, 34.6, 31.8, 24.8);
      bands.push({ id: crusher.thingId, builder: band, angle, at: L(ox, oy, z) });
      lamps.push([...at(ox, oy, angle, -20, 0), z + 18], [...at(ox, oy, angle, 20, 14), z + 34]);
      out.places.push([ox, oy]);
    } else {
    const b = new Builder();
    const w = new Builder();
    clad(b, 6, 0, 0, 22, 20, 12, P.concrete, P.concrete, { panel: 6 });             // concrete base
    clad(b, 8, 0, 12, 18, 18, 20, P.clad, P.roof, { panel: 3, band: [3, 4.5], win: w });
    b.box(8, 0, 32, 8, 6, 3, P.clad, P.roof);                                       // roof housing
    // Dump pocket: retaining walls round a dark hopper at truck level.
    b.box(-10, -12, 0, 18, 2, 3.5, P.concrete);
    b.box(-10, 12, 0, 18, 2, 3.5, P.concrete);
    b.box(-4, 0, 0, 6, 24, 1.2, P.concrete);
    b.quad([-16, -10, 0.15], [-6, -10, 0.15], [-6, 10, 0.15], [-16, 10, 0.15], P.dark, false);
    b.box(-17.5, 0, 0, 1.6, 22, 1.0, rgb('#c9b23a'));                               // wheel stop
    // Rock breaker on its pedestal.
    b.box(-6, 13, 0, 2.5, 2.5, 5, P.steel);
    b.beam([-6, 13, 5], [-11, 8, 8], 0.9, 0.9, P.crane);
    b.beam([-11, 8, 8], [-12, 4, 3], 0.7, 0.7, P.crane);
    put(b, atCrusher.x, atCrusher.y, angle, crusher.thingId);
    const z = groundZ(atCrusher.x, atCrusher.y);
    win.append(w, angle, L(atCrusher.x, atCrusher.y, z), crusher.thingId);
    units.set(crusher.thingId, { at: { x: atCrusher.x, y: atCrusher.y, z: z + 36 }, r: 26, base: z });
    const band = new Builder();
    band.box(8, 0, 31.2, 18.6, 18.6, 0.9, P.white);
    bands.push({ id: crusher.thingId, builder: band, angle, at: L(atCrusher.x, atCrusher.y, z) });
    lamps.push([atCrusher.x - 20, atCrusher.y, z + 18], [atCrusher.x + 20, atCrusher.y + 14, z + 34]);
    out.places.push([atCrusher.x, atCrusher.y]);
    }
  }

  // -- the conveyor --------------------------------------------------------------
  const belt = first('conveyor');
  const stockpile = first('stockpile');
  const atPile = placeOf(stockpile);
  const pileR = stockpile?.attributes?.spec?.radius_m ?? 60;
  const pileH = pileR * Math.tan((37 * Math.PI) / 180);
  const path = belt?.attributes?.geo?.path;
  if (path && path.length >= 2) {
    const points = densify(path.map((p) => [p[0], p[1], p[2] ?? 0]), detail.beltStep);
    // Height of the belt above the ground: out of the crusher's tunnel, on
    // low trestles across the steppe, then up the stacker to the top of the pile.
    let s = 0;
    const deck = points.map((p, i) => {
      if (i) s += Math.hypot(p[0] - points[i - 1][0], p[1] - points[i - 1][1]);
      let h = 4.5 * clamp(s / 70, 0.15, 1);
      if (atPile) {
        const d = Math.hypot(p[0] - atPile.x, p[1] - atPile.y);
        const rise = clamp(1 - (d - 0) / (pileR + 170), 0, 1);
        h = Math.max(h, rise * (pileH + 6));
      }
      return [p[0], p[1], groundZ(p[0], p[1]) + h, h, s];
    });
    const g = new Builder();
    const topRibbon = [];
    // With the model of the truss the scene tiles it along the deck; only the ore stripe is built here.
    if (has('conveyor')) out.conveyorDeck = deck;
    for (let i = 1; i < deck.length; i += 1) {
      const a = deck[i - 1]; const c = deck[i];
      if (out.conveyorDeck) { topRibbon.push([a[0], a[1], a[2] + 0.45]); continue; }
      const dx = c[0] - a[0]; const dy = c[1] - a[1];
      const len = Math.hypot(dx, dy) || 1;
      const nx = (-dy / len) * 1.25; const ny = (dx / len) * 1.25;
      // Side stringers of the truss and the walkway.
      g.beam(L(a[0] + nx, a[1] + ny, a[2]), L(c[0] + nx, c[1] + ny, c[2]), 0.35, 1.3, P.steel);
      g.beam(L(a[0] - nx, a[1] - ny, a[2]), L(c[0] - nx, c[1] - ny, c[2]), 0.35, 1.3, P.steel);
      g.quad(L(a[0] + nx * 1.6, a[1] + ny * 1.6, a[2] + 0.1), L(c[0] + nx * 1.6, c[1] + ny * 1.6, c[2] + 0.1),
        L(c[0] - nx * 1.6, c[1] - ny * 1.6, c[2] + 0.1), L(a[0] - nx * 1.6, a[1] - ny * 1.6, a[2] + 0.1), scale(P.belt, 1.2), false);
      // Trestles every other piece (A-frames).
      if (i % 2 === 0 && a[3] > 1.2) {
        const zg = groundZ(a[0], a[1]);
        g.beam(L(a[0] + nx * 1.3, a[1] + ny * 1.3, zg), L(a[0] + nx, a[1] + ny, a[2]), 0.3, 0.3, P.dark);
        g.beam(L(a[0] - nx * 1.3, a[1] - ny * 1.3, zg), L(a[0] - nx, a[1] - ny, a[2]), 0.3, 0.3, P.dark);
      }
      // Weather hoods over the belt on every other piece (the ore shows between them).
      if (i % 2 === 1 && detail.beltStep < 100) {
        const hood = rgb('#b9bdb9');
        const mid = (p) => [p[0], p[1], p[2] + 0.6];
        const A = mid(a); const Cc = mid(c);
        const r = 1.15;
        for (let s = 0; s < 4; s += 1) {
          const t0 = (s / 4) * Math.PI; const t1 = ((s + 1) / 4) * Math.PI;
          const off = (t, p) => L(p[0] + (nx / 1.25) * Math.cos(t) * r, p[1] + (ny / 1.25) * Math.cos(t) * r, p[2] + Math.sin(t) * r);
          const up = [(nx / 1.25) * Math.cos((t0 + t1) / 2), (ny / 1.25) * Math.cos((t0 + t1) / 2), Math.sin((t0 + t1) / 2)];
          g.quad(off(t0, A), off(t0, Cc), off(t1, Cc), off(t1, A), hood, false, null, null, up);
        }
      }
      topRibbon.push([a[0], a[1], a[2] + 0.45]);
    }
    const last = deck[deck.length - 1];
    topRibbon.push([last[0], last[1], last[2] + 0.45]);
    if (g.triangles) opaque.append(g, 0, [0, 0, 0], belt.thingId);
    // The ore on the belt: a stripe in the colour of ore while it runs.
    const stripe = new Builder();
    ribbon(stripe, topRibbon.map(([x, y, z]) => L(x, y, z)), 1.5, P.white, 0, false);
    out.belt = { builder: stripe, twin: belt };
    // Transfer tower over the pile.
    if (atPile) {
      const zg = groundZ(atPile.x, atPile.y);
      const t = new Builder();
      t.beam([0, 0, 0], [0, 0, pileH + 8], 3, 3, P.steel);
      clad(t, 0, 0, pileH + 5, 7, 7, 6, P.clad, P.roof, { panel: 3.5 });
      put(t, atPile.x, atPile.y, 0, stockpile.thingId, zg);
      lamps.push([atPile.x, atPile.y, zg + pileH + 12]);
    }
    const mid = deck[Math.floor(deck.length / 2)];
    units.set(belt.thingId, { at: { x: mid[0], y: mid[1], z: mid[2] + 6 }, r: 12 });
  }

  // -- the stockpile (rebuilt by the scene as its level changes) -------------------
  if (atPile) {
    const zg = groundZ(atPile.x, atPile.y);
    out.stockpile = { twin: stockpile, x: atPile.x, y: atPile.y, z: zg, r: pileR, h: pileH };
    units.set(stockpile.thingId, { at: { x: atPile.x, y: atPile.y, z: zg + pileH * 0.8 }, r: pileR, base: zg });
    // The reclaim gallery from under the pile to the mill hall.
    out.places.push([atPile.x, atPile.y]);
  }

  // -- the mill hall ---------------------------------------------------------------
  const sag = first('sag-mill');
  const ball = first('ball-mill');
  const circuit = first('mill');
  const atSag = placeOf(sag);
  const atBall = placeOf(ball);
  if (atSag || atBall || placeOf(circuit)) {
    const a = atSag ?? placeOf(circuit);
    const c = atBall ?? { x: a.x + 90, y: a.y + 40 };
    const angle = Math.atan2(c.y - a.y, c.x - a.x);
    const ux = Math.cos(angle); const uy = Math.sin(angle);
    const span = Math.hypot(c.x - a.x, c.y - a.y);
    const cx = (a.x + c.x) / 2; const cy = (a.y + c.y) / 2;
    const length = span + 70; const width = 46; const height = 30;
    const z = groundZ(cx, cy);
    if (has('mill_hall')) {
      // The model's SAG mill stands at local x = -45, its ball mills at 15 and 42: the middle
      // of the SAG and the ball mills goes to the middle of their twins.
      const ox = cx + ux * 8.25; const oy = cy + uy * 8.25;
      const zo = groundZ(ox, oy);
      const circuitId = circuit?.thingId ?? sag?.thingId ?? ball?.thingId;
      const sagId = sag?.thingId ?? circuitId; const ballId = ball?.thingId ?? circuitId;
      out.placements.push({ model: 'mill_hall', id: circuitId, x: ox, y: oy, z: zo, angle,
        owners: { hall: circuitId, roof: circuitId, crane: circuitId, sag_mill: sagId, sag_shell: sagId, ball_mill_1: ballId, ball_shell_1: ballId, ball_mill_2: ballId, ball_shell_2: ballId },
        turning: { sag_shell: sag?.thingId ?? circuitId, ball_shell_1: ball?.thingId ?? circuitId, ball_shell_2: ball?.thingId ?? circuitId } });
      // The mills' captions and panels stay where their twins are (within the hall, a few metres
      // from the model's mills), so they are laid out as before.
      const millAt = (twin, where, lx, dia, len, offsets) => {
        if (!twin) return;
        const [mx, my] = at(ox, oy, angle, lx, 0);
        const band = new Builder();
        for (const off of offsets) band.box(off, 0, 38.6, dia + 4, 2.9, 0.7, P.white);
        bands.push({ id: twin.thingId, builder: band, angle, at: L(mx, my, zo), roof: true });
        const p = where ?? { x: mx, y: my };
        units.set(twin.thingId, { at: { x: p.x, y: p.y, z: zo + dia + 8 }, r: Math.max(dia, len) / 2 + 6, base: zo, rotating: true });
      };
      millAt(sag, atSag, -45, 12.2, 7.9, [0]);
      millAt(ball, atBall, 28.5, 8.5, 13.4, [-13.5, 13.5]);
      if (circuit) units.set(circuit.thingId, { at: { x: ox, y: oy, z: zo + 44 }, r: 82, base: zo });
      out.hallAnchor = { x: ox, y: oy, z: zo + 36, corners: [[-1, -1], [1, -1], [1, 1], [-1, 1]].map(([i, j]) => [ox + ux * i * 80 - uy * j * 23, oy + uy * i * 80 + ux * j * 23]) };
      for (const i of [-1, 1]) lamps.push([ox + ux * i * 80, oy + uy * i * 80, zo + 32]);
      out.places.push([ox, oy]);
      out.hallAngle = angle;
    } else {
    // Walls: a concrete plinth, light profiled cladding with two bands of
    // windows, gable ends; a pitched metal roof with eaves, a ridge vent and
    // strips of roof lights (they glow at night, as the hall is lit inside).
    const hall = new Builder();
    const hallWin = new Builder();
    const wall = rgb('#d3d6d2');
    clad(hall, 0, 0, 0, length, width, 7, P.concrete, P.concrete, { panel: 7, noRoof: true, doors: 2 });
    clad(hall, 0, 0, 7, length, width, height - 7, wall, null, { panel: 3, band: [5, 7.5], win: hallWin, noRoof: true });
    clad(hall, 0, 0, 7, length, width, height - 7, wall, null, { panel: 3, band: [15, 16.5], win: hallWin, noRoof: true });
    for (const x of [-length / 2, length / 2]) {
      hall.tri([x, -width / 2, height], [x, width / 2, height], [x, 0, height + 7], wall, wall, wall, false, null, null, [Math.sign(x), 0, 0]);
    }
    const eave = 1.2;
    const L2 = length / 2 + eave;
    const roofC = P.roof;
    hall.quad([-L2, -width / 2 - eave, height - 0.4], [L2, -width / 2 - eave, height - 0.4], [L2, 0, height + 7], [-L2, 0, height + 7], roofC, false, null, null, [0, -0.6, 1]);
    hall.quad([-L2, 0, height + 7], [L2, 0, height + 7], [L2, width / 2 + eave, height - 0.4], [-L2, width / 2 + eave, height - 0.4], roofC, false, null, null, [0, 0.6, 1]);
    // Fascia boards under the eaves (they close the roof seen from below).
    for (const s of [-1, 1]) hall.box(0, s * (width / 2 + eave / 2), height - 1.2, 2 * L2, eave, 0.8, P.trim, P.trim);
    hall.box(0, 0, height + 6.6, length * 0.92, 2.6, 1.6, rgb('#8a979e'), rgb('#7b8890'));        // ridge vent
    // Columns of the frame showing on the walls.
    for (let x = -length / 2; x <= length / 2 + 0.1; x += 12) for (const y of [-width / 2, width / 2]) hall.box(x, y, 7, 0.7, 0.7, height - 7.5, P.trim);
    // Roof lights: strips of glass along each slope.
    const sky = new Builder();
    for (const s of [-1, 1]) {
      for (const f of [0.3, 0.62]) {
        const y0 = s * (width / 2) * f; const y1 = s * (width / 2) * (f + 0.12);
        const zr = (y) => height + 7 - (Math.abs(y) / (width / 2)) * 7.4 + 0.12;
        sky.quad([-length * 0.42, y0, zr(y0)], [length * 0.42, y0, zr(y0)], [length * 0.42, y1, zr(y1)], [-length * 0.42, y1, zr(y1)], [1, 1, 1], false, null, null, [0, s * 0.6, 1]);
      }
    }
    put(hall, cx, cy, angle, circuit?.thingId ?? sag?.thingId, z);
    win.append(hallWin, angle, L(cx, cy, z), circuit?.thingId ?? sag?.thingId);
    win.append(sky, angle, L(cx, cy, z), circuit?.thingId ?? sag?.thingId);

    // The mills, their axes across the hall.
    const mill = (twin, at, dia, len, count) => {
      if (!twin || !at) return;
      const m = new Builder();
      const band = new Builder();
      const r = dia / 2;
      for (let i = 0; i < count; i += 1) {
        const off = (i - (count - 1) / 2) * (dia + 7);
        const zc = r + 3.2;
        m.box(off, -len / 2 - 2.2, 0, r * 1.1, 3, zc - 1, P.concrete);
        m.box(off, len / 2 + 2.2, 0, r * 1.1, 3, zc - 1, P.concrete);
        m.cylinder([off, -len / 2, zc], [off, len / 2, zc], r, P.shell, 18, P.shell, false);
        m.cylinder([off, -len / 2, zc], [off, -len / 2 - 1.8, zc], r, P.shell, 18, P.steel, true, r * 0.42);
        m.cylinder([off, len / 2, zc], [off, len / 2 + 1.8, zc], r, P.shell, 18, P.steel, true, r * 0.42);
        m.cylinder([off, -len * 0.1, zc], [off, len * 0.1, zc], r + 0.9, P.motor, 18, P.motor);          // gearless motor
        // Its state shows on the hall's ridge vent, over the mill.
        band.box(off, 0, height + 8.2, dia + 4, 2.9, 0.7, P.white);
      }
      // Local x of the mill builder runs along the hall; its y across.
      const zg = groundZ(at.x, at.y);
      opaque.append(m, angle, L(at.x, at.y, zg), twin.thingId);
      bands.push({ id: twin.thingId, builder: band, angle, at: L(at.x, at.y, zg) });
      units.set(twin.thingId, { at: { x: at.x, y: at.y, z: zg + dia + 8 }, r: Math.max(dia, len) / 2 + 6, base: zg, rotating: true });
    };
    mill(sag, atSag, 12.2, 7.9, 1);
    mill(ball, atBall, 8.5, 13.4, Math.max(1, Math.min(4, ball?.attributes?.spec?.units ?? 2)));
    if (circuit) units.set(circuit.thingId, { at: { x: cx, y: cy, z: z + height + 8 }, r: length / 2, base: z });
    out.hallAnchor = { x: cx, y: cy, z: z + height + 6, corners: [[-1, -1], [1, -1], [1, 1], [-1, 1]].map(([i, j]) => [cx + ux * i * length / 2 - uy * j * width / 2, cy + uy * i * length / 2 + ux * j * width / 2]) };
    for (const i of [-1, 1]) lamps.push([cx + ux * i * length / 2, cy + uy * i * length / 2, z + height + 2]);
    out.places.push([cx, cy]);
    out.hallAngle = angle;
    }
  }

  // -- flotation ---------------------------------------------------------------------
  const flotation = first('flotation');
  const atFlot = placeOf(flotation);
  const angle = out.hallAngle ?? 0;
  if (atFlot && has('flotation')) {
    const z = groundZ(atFlot.x, atFlot.y);
    out.placements.push({ model: 'flotation', id: flotation.thingId, x: atFlot.x, y: atFlot.y, z, angle, owners: {} });
    // The state band: a ring round the top of every cell (two rows of seven, 9.43 m apart).
    const band = new Builder();
    for (const y of [-5.6, 5.6]) for (let i = 0; i < 7; i += 1) band.cylinder([(i - 3) * 9.43, y, 7.45], [(i - 3) * 9.43, y, 8.05], 3.78, P.white, 16, P.white, false);
    bands.push({ id: flotation.thingId, builder: band, angle, at: L(atFlot.x, atFlot.y, z) });
    units.set(flotation.thingId, { at: { x: atFlot.x, y: atFlot.y, z: z + 20 }, r: 40, base: z });
    lamps.push([atFlot.x, atFlot.y, z + 17]);
    out.places.push([atFlot.x, atFlot.y]);
  } else if (atFlot) {
    const z = groundZ(atFlot.x, atFlot.y);
    const f = new Builder();
    const band = new Builder();
    const rows = 2; const cells = 7; const r = 3.6;
    clad(f, 0, 0, 0, cells * 9 + 12, 34, 1.2, P.concrete, scale(P.concrete, 0.9), { panel: 9 });
    for (let row = 0; row < rows; row += 1) {
      for (let i = 0; i < cells; i += 1) {
        const x = (i - (cells - 1) / 2) * 9;
        const y = row ? 7.5 : -7.5;
        f.cylinder([x, y, 1.2], [x, y, 7.3], r, P.tank, 16, P.tank, false, r, true);
        f.cylinder([x, y, 7.3], [x, y, 7.75], r + 0.15, P.steel, 16, P.steel, false);           // rim
        // Froth: the copper-laden bubbles, bronze-grey, darker towards the middle.
        for (let s = 0; s < 16; s += 1) {
          const a0 = (s / 16) * Math.PI * 2; const a1 = ((s + 1) / 16) * Math.PI * 2;
          f.tri([x, y, 7.45], [x + Math.cos(a0) * r, y + Math.sin(a0) * r, 7.6], [x + Math.cos(a1) * r, y + Math.sin(a1) * r, 7.6], P.frothDark, P.froth, P.froth, true);
        }
        f.beam([x - r, y, 8.1], [x + r, y, 8.1], 0.5, 0.5, P.steel);                  // bridge with the drive
        f.box(x, y, 8.3, 1.2, 1.2, 1.4, P.motor);
        band.cylinder([x, y, 6.4], [x, y, 7.0], r + 0.12, P.white, 16, P.white, false);
      }
      f.box(0, row ? 12 : -12, 6.8, cells * 9, 1.2, 0.6, P.steel);                    // launder
    }
    f.box(0, 0, 8.2, cells * 9 + 4, 3, 0.4, P.steel);                                 // walkway between rows
    // Stairs up to the walkway, handrails along it.
    for (const s of [-1, 1]) f.beam([s * ((cells * 9) / 2 + 2), -1, 8.6], [s * ((cells * 9) / 2 + 2), 1, 8.6], 0.1, 0.1, P.crane);
    f.beam([-(cells * 9) / 2 - 2, -1.4, 9.4], [(cells * 9) / 2 + 2, -1.4, 9.4], 0.08, 0.08, P.crane);
    f.beam([-(cells * 9) / 2 - 2, 1.4, 9.4], [(cells * 9) / 2 + 2, 1.4, 9.4], 0.08, 0.08, P.crane);
    f.beam([(cells * 9) / 2 + 2, 0, 1.2], [(cells * 9) / 2 + 9, 0, 8.4], 1.0, 0.15, P.steel);
    opaque.append(f, angle, L(atFlot.x, atFlot.y, z), flotation.thingId);
    bands.push({ id: flotation.thingId, builder: band, angle, at: L(atFlot.x, atFlot.y, z) });
    units.set(flotation.thingId, { at: { x: atFlot.x, y: atFlot.y, z: z + 20 }, r: 40, base: z });
    lamps.push([atFlot.x, atFlot.y, z + 17]);
    out.places.push([atFlot.x, atFlot.y]);
  }
  if (atFlot) {
    // The tailings thickener, between the flotation and the pond.
    const pond = first('tailings')?.attributes?.geo;
    const pc = pond?.polygon ? areaCentroid(pond.polygon) : { x: atFlot.x, y: atFlot.y + 400 };
    const dir = Math.atan2(pc.y - atFlot.y, pc.x - atFlot.x);
    const tx = atFlot.x + Math.cos(dir) * 120 - Math.sin(dir) * 30;
    const ty = atFlot.y + Math.sin(dir) * 120 + Math.cos(dir) * 30;
    thickener(tx, ty, 38, first('tailings')?.thingId ?? flotation.thingId);
    out.thickener = { x: tx, y: ty };
  }

  function thickener(x, y, r, tag) {
    const z = groundZ(x, y);
    const model = r > 20 ? 'thickener_tailings' : 'thickener_concentrate';
    if (has(model)) {
      out.placements.push({ model, id: tag, x, y, z, angle: 0.6, owners: {}, turning: { rake: tag } });
      lamps.push([x, y, z + 12]);
      out.places.push([x, y]);
      return;
    }
    const t = new Builder();
    t.cylinder([0, 0, 0], [0, 0, 5], r, P.concrete, 40, P.concrete, false, r, true);
    t.cylinder([0, 0, 4.2], [0, 0, 5.4], r + 1.4, scale(P.concrete, 1.08), 40, P.concrete, false, r + 1.4, true);   // overflow launder
    t.cylinder([0, 0, 0], [0, 0, 9], 2.2, P.concrete, 12, P.concrete);                                 // centre column
    t.cylinder([0, 0, 3.4], [0, 0, 6.0], r * 0.16, P.steel, 16, P.dark, false);                        // feedwell
    t.beam([-r - 1.5, 0, 6.6], [0, 0, 8.6], 1.8, 1.2, P.steel);                                         // access bridge
    for (const s of [-1, 1]) t.beam([-r - 1.5, s * 0.9, 7.6], [0, s * 0.9, 9.6], 0.1, 0.1, P.crane);    // its handrails
    t.beam([0, 0, 4.75], [r * 0.92, r * 0.25, 4.75], 0.8, 0.35, P.dark);                               // rake arm
    t.beam([0, 0, 4.75], [-r * 0.6, -r * 0.7, 4.75], 0.8, 0.35, P.dark);
    t.box(0, 0, 9, 5, 5, 3, P.clad, P.roof);                                                           // drive house
    opaque.append(t, 0.6, L(x, y, z), tag);
    const w = new Builder();
    const n = 40;
    // Clear water at the rim, muddier towards the feedwell.
    const clear = scale(P.water, 1.0); const mud = rgb('#6d6a55');
    for (let i = 0; i < n; i += 1) {
      const a0 = (i / n) * Math.PI * 2; const a1 = ((i + 1) / n) * Math.PI * 2;
      const mid = 0.45;
      w.quad([Math.cos(a0) * r * mid, Math.sin(a0) * r * mid, 4.6], [Math.cos(a0) * r, Math.sin(a0) * r, 4.6], [Math.cos(a1) * r, Math.sin(a1) * r, 4.6], [Math.cos(a1) * r * mid, Math.sin(a1) * r * mid, 4.6],
        clear, true, null, [mix(mud, clear, 0.5), clear, clear, mix(mud, clear, 0.5)]);
      w.tri([0, 0, 4.6], [Math.cos(a0) * r * mid, Math.sin(a0) * r * mid, 4.6], [Math.cos(a1) * r * mid, Math.sin(a1) * r * mid, 4.6], mud, mix(mud, clear, 0.5), mix(mud, clear, 0.5));
    }
    water.append(w, 0, L(x, y, z), tag);
    lamps.push([x, y, z + 12]);
    out.places.push([x, y]);
  }

  // -- concentrate ---------------------------------------------------------------
  const concentrate = first('concentrate');
  const atConc = placeOf(concentrate);
  if (atConc) {
    const z = groundZ(atConc.x, atConc.y);
    const b = new Builder();
    const w = new Builder();
    clad(b, -22, 0, 0, 34, 22, 15, P.cladWarm, P.roof, { panel: 3.4, band: [9, 11], win: w });     // filter plant
    win.append(w, angle, L(atConc.x, atConc.y, z), concentrate.thingId);
    // Concentrate shed: a long barrel-vaulted store.
    const L2 = 56; const R = 13;
    for (let i = 0; i < 8; i += 1) {
      const a0 = (i / 8) * Math.PI; const a1 = ((i + 1) / 8) * Math.PI;
      b.quad([14, Math.cos(a0) * R, Math.sin(a0) * R + 3], [14 + L2, Math.cos(a0) * R, Math.sin(a0) * R + 3], [14 + L2, Math.cos(a1) * R, Math.sin(a1) * R + 3], [14, Math.cos(a1) * R, Math.sin(a1) * R + 3], i % 2 ? P.clad : scale(P.clad, 0.94), false);
    }
    b.box(14 + L2 / 2, -R, 0, L2, 0.6, 3, P.concrete);
    b.box(14 + L2 / 2, R, 0, L2, 0.6, 3, P.concrete);
    b.box(14, 0, 0, 0.6, 2 * R, 3, P.concrete);
    opaque.append(b, angle, L(atConc.x, atConc.y, z), concentrate.thingId);
    units.set(concentrate.thingId, { at: { x: atConc.x, y: atConc.y, z: z + 20 }, r: 30, base: z });
    // Concentrate thickener beside it.
    thickener(atConc.x + Math.cos(angle) * 10 - Math.sin(angle) * 40, atConc.y + Math.sin(angle) * 10 + Math.cos(angle) * 40, 13, concentrate.thingId);
    out.places.push([atConc.x, atConc.y]);
  }

  // -- the tailings pond ------------------------------------------------------------
  const tailings = first('tailings');
  const pond = tailings?.attributes?.geo;
  if (pond?.polygon) {
    const zPond = pond.z ?? groundZ(...Object.values(areaCentroid(pond.polygon)));
    const c = areaCentroid(pond.polygon);
    const shape = pond.polygon.map(([x, y]) => new THREE.Vector2(x, y));
    const faces = THREE.ShapeUtils.triangulateShape(shape, []);
    const spec = tailings.attributes?.spec ?? {};
    const dx = spec.discharge_x ?? c.x; const dy = spec.discharge_y ?? c.y;
    const w = new Builder();
    // Water lighter near the beach where the slurry comes in, darker away from it.
    const tone = (x, y) => scale(mix(P.beach, P.pond, clamp(Math.hypot(x - dx, y - dy) / 260, 0, 1) ** 0.6), 1);
    // Subdivide the pond so the tone can vary across it.
    const cut = (a, b2, c2, depth) => {
      if (depth === 0) { w.tri(L(a[0], a[1], zPond - 1.5), L(b2[0], b2[1], zPond - 1.5), L(c2[0], c2[1], zPond - 1.5), tone(...a), tone(...b2), tone(...c2)); return; }
      const m = (p, q) => [(p[0] + q[0]) / 2, (p[1] + q[1]) / 2];
      const ab = m(a, b2); const bc = m(b2, c2); const ca = m(c2, a);
      cut(a, ab, ca, depth - 1); cut(ab, b2, bc, depth - 1); cut(ca, bc, c2, depth - 1); cut(ab, bc, ca, depth - 1);
    };
    for (const [i, j, k] of faces) cut(pond.polygon[i], pond.polygon[j], pond.polygon[k], 4);
    water.append(w, 0, [0, 0, 0], tailings.thingId);
    // The embankment: a crest road and an outer slope down to the ground.
    const dam = new Builder();
    const ring = densify([...pond.polygon, pond.polygon[0]].map(([x, y]) => [x, y, 0]), 40).slice(0, -1);
    for (let i = 0; i < ring.length; i += 1) {
      const p = ring[i]; const q = ring[(i + 1) % ring.length];
      const op = [p[0] + (p[0] - c.x) / Math.hypot(p[0] - c.x, p[1] - c.y) * 14, p[1] + (p[1] - c.y) / Math.hypot(p[0] - c.x, p[1] - c.y) * 14];
      const oq = [q[0] + (q[0] - c.x) / Math.hypot(q[0] - c.x, q[1] - c.y) * 14, q[1] + (q[1] - c.y) / Math.hypot(q[0] - c.x, q[1] - c.y) * 14];
      const fp = [p[0] + (p[0] - c.x) / Math.hypot(p[0] - c.x, p[1] - c.y) * 40, p[1] + (p[1] - c.y) / Math.hypot(p[0] - c.x, p[1] - c.y) * 40];
      const fq = [q[0] + (q[0] - c.x) / Math.hypot(q[0] - c.x, q[1] - c.y) * 40, q[1] + (q[1] - c.y) / Math.hypot(q[0] - c.x, q[1] - c.y) * 40];
      dam.quad(L(p[0], p[1], zPond + 1), L(q[0], q[1], zPond + 1), L(oq[0], oq[1], zPond + 1), L(op[0], op[1], zPond + 1), scale(P.dam, K * 1.05), true);
      dam.quad(L(op[0], op[1], zPond + 1), L(oq[0], oq[1], zPond + 1), L(fq[0], fq[1], groundZ(fq[0], fq[1]) - 0.5), L(fp[0], fp[1], groundZ(fp[0], fp[1]) - 0.5), scale(P.dam, K * 0.95), true);
      dam.quad(L(p[0], p[1], zPond - 2), L(q[0], q[1], zPond - 2), L(q[0], q[1], zPond + 1), L(p[0], p[1], zPond + 1), scale(P.dam, K * 0.8), false);
    }
    opaque.append(dam, 0, [0, 0, 0], tailings.thingId);
    // The pipeline from the thickener to the discharge point.
    if (out.thickener) {
      const pipe = new Builder();
      const from = out.thickener;
      const pts = densify([[from.x, from.y, 0], [dx, dy, 0]], 30);
      for (let i = 1; i < pts.length; i += 1) {
        const a = pts[i - 1]; const b2 = pts[i];
        pipe.cylinder(L(a[0], a[1], groundZ(a[0], a[1]) + 1.2), L(b2[0], b2[1], groundZ(b2[0], b2[1]) + 1.2), 0.55, P.dark, 6, P.dark, false);
      }
      opaque.append(pipe, 0, [0, 0, 0], tailings.thingId);
    }
    units.set(tailings.thingId, { at: { x: c.x, y: c.y, z: zPond + 4 }, r: 40, base: zPond });
  }

  // -- the clay-ore plant (ОФ-2) -------------------------------------------------------
  // Its building with the desliming cyclones and the flotation cells, the feed
  // conveyor gallery turned towards the feed stockpile where the trucks tip.
  const clay = first('clay-plant');
  const clayGeo = clay?.attributes?.geo;
  if (clay && typeof clayGeo?.x === 'number') {
    const z = groundZ(clayGeo.x, clayGeo.y);
    const spec = clay.attributes?.spec ?? {};
    const feed = clayGeo.polygon?.length >= 3 ? centroid(clayGeo.polygon) : { x: spec.tip_x ?? clayGeo.x, y: spec.tip_y ?? clayGeo.y - 100 };
    const dx = feed.x - clayGeo.x; const dy = feed.y - clayGeo.y;
    const angle = Math.hypot(dx, dy) > 1 ? Math.atan2(dx, -dy) : 0;            // local -y towards the feed stockpile
    const desliming = first('desliming');
    const cells = first('clay-flotation');
    if (has('clay_plant')) {
      out.placements.push({ model: 'clay_plant', id: clay.thingId, x: clayGeo.x, y: clayGeo.y, z, angle, owners: {} });
    } else {
      const b = new Builder();
      const w = new Builder();
      clad(b, 0, 0, 0, 64, 30, 20, P.cladWarm, P.roof, { panel: 3.4, band: [6, 8], win: w });
      clad(b, -41, -4, 0, 18, 18, 10, P.clad, P.roof, { panel: 3, band: [4, 5.5], win: w });
      // The cyclone tower with its ten cyclones.
      b.box(42, -2, 0, 9, 9, 16, P.steel);
      for (let i = 0; i < 10; i += 1) {
        const a = (i / 10) * Math.PI * 2;
        const cx = 42 + Math.cos(a) * 2.6; const cy = -2 + Math.sin(a) * 2.6;
        b.cylinder([cx, cy, 16], [cx, cy, 19.5], 0.5, P.white, 8, P.white);
      }
      // Five flotation cells in front of the building, the feed gallery to the stockpile.
      for (let i = 0; i < 5; i += 1) b.cylinder([-18 + i * 6.6, 22, 0], [-18 + i * 6.6, 22, 6], 2.5, P.tank, 14, P.froth, false, 2.5, true);
      b.beam([0, -15, 18], [0, -62, 2], 3.2, 3, P.clad);
      opaque.append(b, angle, L(clayGeo.x, clayGeo.y, z), clay.thingId);
      win.append(w, angle, L(clayGeo.x, clayGeo.y, z), clay.thingId);
    }
    const anchor = (twin, lx, ly, top, r) => {
      if (!twin) return;
      const [ax, ay] = at(clayGeo.x, clayGeo.y, angle, lx, ly);
      units.set(twin.thingId, { at: { x: ax, y: ay, z: z + top }, r, base: z });
    };
    anchor(clay, 0, 0, 28, 40);
    anchor(desliming, 42, -2, 24, 12);
    anchor(cells, -6, 22, 12, 18);
    const band = new Builder();
    frame(band, 0, 0, 20.1, 64.4, 30.4);
    bands.push({ id: clay.thingId, builder: band, angle, at: L(clayGeo.x, clayGeo.y, z) });
    lamps.push([clayGeo.x, clayGeo.y, z + 26]);
    // (Not in out.places: the gravel apron is round the units of ОФ-1; the model brings its own slab.)
  }
  // The clay-ore stockpile (drawn by the scene like a ROM pad): its captions and panel over its middle.
  for (const pile of byKind('clay-stockpile')) {
    const polygon = pile.attributes?.geo?.polygon;
    if (!polygon?.length) continue;
    const c = centroid(polygon);
    const r = Math.max(...polygon.map(([x, y]) => Math.hypot(x - c.x, y - c.y)));
    const z = groundZ(c.x, c.y);
    units.set(pile.thingId, { at: { x: c.x, y: c.y, z: z + 14 }, r: Math.min(60, r * 0.4), base: z });
  }

  // -- services -----------------------------------------------------------------------
  const service = (twin, build) => {
    const at = twin?.attributes?.geo;
    if (typeof at?.x !== 'number') return;
    const z = groundZ(at.x, at.y);
    const b = new Builder();
    const w = new Builder();
    const top = build(b, w);
    opaque.append(b, 0, L(at.x, at.y, z), twin.thingId);
    win.append(w, 0, L(at.x, at.y, z), twin.thingId);
    units.set(twin.thingId, { at: { x: at.x, y: at.y, z: z + top + 4 }, r: 24, base: z });
    lamps.push([at.x, at.y, z + top + 3]);
  };
  service(first('substation'), (b, win) => {
    clad(b, 0, -14, 0, 22, 9, 5, P.cladWarm, P.roof, { panel: 3, band: [2.5, 3.6], win });
    for (let i = -1; i <= 1; i += 1) {
      b.box(i * 9, 4, 0, 5, 3.6, 4.5, P.steel);
      for (let f = -2; f <= 2; f += 1) b.box(i * 9 + f * 0.9, 6.1, 0.6, 0.25, 0.7, 3.4, P.dark);  // radiator fins
      b.cylinder([i * 9, 4, 4.5], [i * 9, 4, 7.2], 0.35, P.white, 6);                             // bushings
    }
    mast(b, -16, 12, 0, 22);
    mast(b, 16, 12, 0, 22);
    b.beam([-16, 12, 22], [16, 12, 22], 0.8, 0.8, P.steel);
    return 9;
  });
  service(first('workshop'), (b, win) => {
    clad(b, 0, 0, 0, 64, 30, 16, P.clad, P.roof, { panel: 4, band: [12, 13.6], win, doors: 4 });
    b.quad([-40, -26, 0.2], [40, -26, 0.2], [40, -15, 0.2], [-40, -15, 0.2], scale(P.apron, K), true);
    return 16;
  });
  service(first('fuel-station'), (b) => {
    b.box(0, 0, 6, 18, 12, 0.7, P.cladWarm);                                          // canopy
    for (const x of [-7, 7]) for (const y of [-4, 4]) b.box(x, y, 0, 0.5, 0.5, 6, P.steel);
    for (const y of [-14, -20]) b.cylinder([-10, y, 2], [10, y, 2], 1.9, P.white, 10, P.tank);
    b.box(14, 0, 0, 8, 6, 3.5, P.cladWarm, P.roof);
    return 7;
  });

  // -- what joins the units: the reclaim conveyor, a pipe rack, water tanks,
  //    reagents, offices, the power line --------------------------------------------------
  const gallery = (from, to, tag) => {
    // An enclosed conveyor gallery on trestles between two points [x, y, height above ground].
    const g = new Builder();
    const n = Math.max(1, Math.round(Math.hypot(to[0] - from[0], to[1] - from[1]) / 25));
    for (let i = 0; i < n; i += 1) {
      const p = (f) => [from[0] + (to[0] - from[0]) * f, from[1] + (to[1] - from[1]) * f, from[2] + (to[2] - from[2]) * f];
      const a = p(i / n); const c = p((i + 1) / n);
      const za = groundZ(a[0], a[1]) + a[2]; const zc = groundZ(c[0], c[1]) + c[2];
      // A covered gallery: clad box with the side trusses showing, on trestles.
      g.beam(L(a[0], a[1], za), L(c[0], c[1], zc), 3.6, 3.0, P.clad);
      g.beam(L(a[0], a[1], za + 1.6), L(c[0], c[1], zc + 1.6), 4.0, 0.25, P.roof);
      const dx = c[0] - a[0]; const dy = c[1] - a[1]; const dl = Math.hypot(dx, dy) || 1;
      for (const s of [-1, 1]) {
        const ox = (-dy / dl) * 1.85 * s; const oy = (dx / dl) * 1.85 * s;
        const pa = [a[0] + ox, a[1] + oy]; const pc = [c[0] + ox, c[1] + oy];
        g.beam(L(pa[0], pa[1], za - 1.4), L(pc[0], pc[1], zc - 1.4), 0.12, 0.25, P.steel);
        g.beam(L(pa[0], pa[1], za + 1.4), L(pc[0], pc[1], zc + 1.4), 0.12, 0.25, P.steel);
        g.beam(L(pa[0], pa[1], za - 1.4), L((pa[0] + pc[0]) / 2, (pa[1] + pc[1]) / 2, (za + zc) / 2 + 1.4), 0.12, 0.2, P.steel);
        g.beam(L((pa[0] + pc[0]) / 2, (pa[1] + pc[1]) / 2, (za + zc) / 2 + 1.4), L(pc[0], pc[1], zc - 1.4), 0.12, 0.2, P.steel);
      }
      if (a[2] > 1.5) {
        const zg = groundZ(a[0], a[1]);
        for (const s of [-1, 1]) g.beam(L(a[0] + (-dy / dl) * 1.6 * s, a[1] + (dx / dl) * 1.6 * s, zg), L(a[0] + (-dy / dl) * 1.2 * s, a[1] + (dx / dl) * 1.2 * s, za - 1.5), 0.45, 0.45, P.steel);
      }
    }
    opaque.append(g, 0, [0, 0, 0], tag);
  };
  if (atPile && atSag) {
    const d = Math.atan2(atSag.y - atPile.y, atSag.x - atPile.x);
    const start = [atPile.x + Math.cos(d) * (pileR * 0.35), atPile.y + Math.sin(d) * (pileR * 0.35), 1];
    const hallEdge = [atSag.x - Math.cos(d) * 28, atSag.y - Math.sin(d) * 28, 18];
    gallery(start, hallEdge, stockpile.thingId);
  }
  if (atFlot && (atBall || atSag)) {
    // A pipe rack from the mill hall to the flotation.
    const from = atBall ?? atSag;
    const pr = new Builder();
    const n = Math.max(1, Math.round(Math.hypot(atFlot.x - from.x, atFlot.y - from.y) / 12));
    for (let i = 0; i <= n; i += 1) {
      const x = from.x + (atFlot.x - from.x) * (i / n); const y = from.y + (atFlot.y - from.y) * (i / n);
      const z = groundZ(x, y);
      pr.beam(L(x, y, z), L(x, y, z + 7), 0.5, 0.5, P.steel);
    }
    const z0 = groundZ(from.x, from.y) + 7; const z1 = groundZ(atFlot.x, atFlot.y) + 7;
    for (const off of [-0.6, 0.6]) {
      const ox = -Math.sin(angle) * off; const oy = Math.cos(angle) * off;
      pr.cylinder(L(from.x + ox, from.y + oy, z0 + 0.5), L(atFlot.x + ox, atFlot.y + oy, z1 + 0.5), 0.35, P.white, 6, P.white, false);
    }
    opaque.append(pr, 0, [0, 0, 0], flotation?.thingId ?? null);
  }
  if (atFlot) {
    // Process water tanks and the reagent building beside the flotation.
    const side = (along, across) => [atFlot.x + Math.cos(angle) * along - Math.sin(angle) * across, atFlot.y + Math.sin(angle) * along + Math.cos(angle) * across];
    const t = new Builder();
    t.cylinder([0, 0, 0], [0, 0, 13], 9, rgb('#d6d8d3'), 20, rgb('#b9bcb7'));
    t.cylinder([22, 0, 0], [22, 0, 13], 9, rgb('#d6d8d3'), 20, rgb('#b9bcb7'));
    const [wx, wy] = side(-20, -48);
    opaque.append(t, angle, L(wx, wy, groundZ(wx, wy)), flotation.thingId);
    const r = new Builder();
    const rw = new Builder();
    clad(r, 0, 0, 0, 34, 20, 12, rgb('#b8bdbf'), rgb('#5f7480'), { panel: 3.4, band: [7, 8.6], win: rw });
    const [rx, ry] = side(62, -6);
    opaque.append(r, angle, L(rx, ry, groundZ(rx, ry)), flotation.thingId);
    win.append(rw, angle, L(rx, ry, groundZ(rx, ry)), flotation.thingId);
    out.places.push([wx, wy], [rx, ry]);
  }
  const sub = first('substation')?.attributes?.geo;
  if (sub && typeof sub.x === 'number') {
    // Offices and the laboratory near the substation; the power line to the mill hall.
    const o = new Builder();
    const ow = new Builder();
    clad(o, 0, 0, 0, 44, 16, 10, rgb('#c9c6bd'), rgb('#5f7480'), { panel: 3, band: [2.6, 4.2], win: ow });
    clad(o, 0, 0, 0, 44, 16, 10, rgb('#c9c6bd'), rgb('#5f7480'), { panel: 3, band: [6.4, 8], win: ow, noRoof: true });
    const ox = sub.x - 70; const oy = sub.y + 20;
    opaque.append(o, 0.1, L(ox, oy, groundZ(ox, oy)), first('substation').thingId);
    win.append(ow, 0.1, L(ox, oy, groundZ(ox, oy)), first('substation').thingId);
    out.places.push([ox, oy]);
    const hall = out.hallAnchor;
    if (hall) {
      const line = new Builder();
      const n = 3;
      let last = null;
      for (let i = 0; i <= n; i += 1) {
        const f = i / n;
        const x = sub.x + (hall.x - sub.x) * f * 0.92; const y = sub.y + 12 + (hall.y - sub.y) * f * 0.92;
        const z = groundZ(x, y);
        line.beam(L(x, y, z), L(x, y, z + 26), 0.9, 0.9, P.steel);
        line.beam(L(x - 4, y, z + 25), L(x + 4, y, z + 25), 0.4, 0.4, P.steel);
        if (last) for (const dx of [-4, 4]) line.beam(L(last[0] + dx, last[1], last[2] + 24.6), L(x + dx, y, z + 24.6), 0.08, 0.08, P.dark);
        last = [x, y, z];
      }
      opaque.append(line, 0, [0, 0, 0], first('substation').thingId);
    }
  }

  // -- the plant's ground and lighting masts ---------------------------------------------
  const unitPlaces = [...out.places];
  if (unitPlaces.length >= 3) {
    // A gravel apron round the units of the plant proper (not the crusher far away).
    const plantPts = unitPlaces.filter(([x, y]) => !atCrusher || Math.hypot(x - atCrusher.x, y - atCrusher.y) > 800);
    if (plantPts.length >= 3) {
      const hull = convexHull(plantPts.flatMap(([x, y]) => [[x - 45, y - 45], [x + 45, y - 45], [x + 45, y + 45], [x - 45, y + 45]]));
      out.apron = hull;
      const c = centroid(hull);
      for (const [x, y] of hull.filter((_, i) => i % 2 === 0)) {
        const mx = x + (c.x - x) * 0.08; const my = y + (c.y - y) * 0.08;
        const z = groundZ(mx, my);
        const m = new Builder();
        mast(m, 0, 0, 0, 24);
        opaque.append(m, 0, L(mx, my, z), null);
        lamps.push([mx, my, z + 24.5]);
      }
    }
  }
  return out;
}
