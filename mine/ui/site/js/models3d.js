// Models of the mine's machines, built from boxes, beams, prisms and lathed
// shapes in their real sizes (metres), facing +y with the origin on the
// ground under the machine's centre. Each machine is split into the parts
// that move on their own, so that the scene can draw every part of every
// machine of a kind as one instanced mesh; each comes in two levels of
// detail: `near` (a few thousand triangles, for the camera close by) and
// `far` (a couple of hundred, for the overview), and `simple` for the
// 2D-canvas stand-in of WebGL.
//
//   truck  Caterpillar 785C (CAT yellow): frame with the cab on its deck,
//          the diagonal stair, six wheels on 33.00R51 tyres; the dump body
//          with its canopy and ribs, which tips about its rear hinge; the load.
//   shovel Hitachi EX3600 front shovel (Hitachi orange): crawlers with shoes
//          and rollers, car body; the upper structure with house,
//          counterweight, cab, boom, stick and bucket, which slews.
//   drill  Sandvik D55SP blasthole drill: crawlers, deck, power pack and cab;
//          the lattice mast, raised to drill and lowered to tram.
//
// `lit`: geometry for a material that lights itself (colours are the
// albedo, with normals); otherwise lit once by a fixed light.

import * as THREE from 'three';
import { Builder, rgb, clamp } from './geom3d.js';

const CAT = rgb('#eab51d');
const CAT_DARK = rgb('#b78a14');
const CAT_WORN = rgb('#76603f');
const HITACHI = rgb('#f06b17');
const HITACHI_DARK = rgb('#b9500f');
const SANDVIK = rgb('#e8b324');
const SANDVIK_GREY = rgb('#5f6366');
const STEEL = rgb('#3a3c3e');
const STEEL_MID = rgb('#5d6062');
const STEEL_LIGHT = rgb('#9a9c98');
const TYRE = rgb('#1e1e1f');
const TREAD = rgb('#2d2a27');
const RIM = rgb('#b1ab9e');
const HUB = rgb('#6f6b63');
const GLASS = rgb('#1c2834');
const CHROME = rgb('#d4d7d7');
const LAMP = rgb('#fff4d6');
const RED = rgb('#b81d12');
const BLACK = rgb('#161616');
const DIRT = rgb('#6d5a45');
const BUCKET = rgb('#8c5a34');

const geometryOf = (b, lit) => (lit ? b.litGeometry() : b.machineGeometry());

// -- shared parts ---------------------------------------------------------------

// A box with its vertical edges and the edges of its top chamfered by `c`.
function bevelBox(b, x, y, z0, sx, sy, sz, color, top = color, c = 0.2) {
  const hx = sx / 2; const hy = sy / 2;
  const plan = (k) => [[x - hx + k, y - hy], [x + hx - k, y - hy], [x + hx, y - hy + k], [x + hx, y + hy - k], [x + hx - k, y + hy], [x - hx + k, y + hy], [x - hx, y + hy - k], [x - hx, y - hy + k]];
  const lower = plan(c);
  b.prismZ(lower, z0, z0 + sz - c, color, color);
  // The chamfer round the top, then the lid.
  const inner = lower.map(([px, py]) => [px + Math.sign(x - px) * c * 0.7 * (Math.abs(px - x) > hx - c - 1e-6 ? 1 : 0), py + Math.sign(y - py) * c * 0.7 * (Math.abs(py - y) > hy - c - 1e-6 ? 1 : 0)]);
  const zt = z0 + sz - c;
  const centre = [x, y, z0 + sz - c * 2];
  for (let i = 0; i < 8; i += 1) {
    const j = (i + 1) % 8;
    b.quadOut([...lower[i], zt], [...lower[j], zt], [...inner[j], z0 + sz], [...inner[i], z0 + sz], color, centre);
  }
  for (let i = 1; i < 7; i += 1) b.tri([...inner[0], z0 + sz], [...inner[i], z0 + sz], [...inner[i + 1], z0 + sz], top, top, top, false, null, null, [0, 0, 1]);
}

// A handrail along a path [[x, y]] at height z: posts, a top rail and a knee rail.
function rail(b, path, z, h, color, post = 1.4) {
  for (let i = 1; i < path.length; i += 1) {
    const [x0, y0] = path[i - 1]; const [x1, y1] = path[i];
    b.beam([x0, y0, z + h], [x1, y1, z + h], 0.07, 0.07, color);
    b.beam([x0, y0, z + h * 0.5], [x1, y1, z + h * 0.5], 0.05, 0.05, color);
    const n = Math.max(1, Math.round(Math.hypot(x1 - x0, y1 - y0) / post));
    for (let k = i === 1 ? 0 : 1; k <= n; k += 1) {
      const f = k / n;
      b.box(x0 + (x1 - x0) * f, y0 + (y1 - y0) * f, z, 0.07, 0.07, h, color);
    }
  }
}

// A stair from p (bottom) to q (top), `w` wide: two stringers and the treads.
function stair(b, p, q, w, color, rails = true) {
  const d = [q[0] - p[0], q[1] - p[1]];
  const len = Math.hypot(...d) || 1;
  const sx = -d[1] / len * w / 2; const sy = d[0] / len * w / 2;
  b.beam([p[0] + sx, p[1] + sy, p[2]], [q[0] + sx, q[1] + sy, q[2]], 0.08, 0.22, color);
  b.beam([p[0] - sx, p[1] - sy, p[2]], [q[0] - sx, q[1] - sy, q[2]], 0.08, 0.22, color);
  const steps = Math.max(2, Math.round((q[2] - p[2]) / 0.3));
  for (let i = 1; i < steps; i += 1) {
    const f = i / steps;
    const c = [p[0] + (q[0] - p[0]) * f, p[1] + (q[1] - p[1]) * f, p[2] + (q[2] - p[2]) * f];
    b.beam([c[0] - sx, c[1] - sy, c[2]], [c[0] + sx, c[1] + sy, c[2]], 0.24, 0.05, STEEL_MID);
  }
  if (rails) {
    b.beam([p[0] + sx * 1.1, p[1] + sy * 1.1, p[2] + 1.0], [q[0] + sx * 1.1, q[1] + sy * 1.1, q[2] + 1.0], 0.06, 0.06, color);
    b.box(p[0] + sx * 1.1, p[1] + sy * 1.1, p[2], 0.06, 0.06, 1.0, color);
  }
}

// An operator's cab: a lower panel, a band of glass on all sides with
// corner pillars, a roof with a visor; centre (x, y), floor z0.
function cab(b, x, y, z0, sx, sy, sz, color, { glassFrom = 0.38, front = 1 } = {}) {
  const zg = z0 + sz * glassFrom;
  const zr = z0 + sz - 0.18;
  b.box(x, y, z0, sx, sy, zg - z0, color);
  b.box(x, y, zg, sx - 0.06, sy - 0.06, zr - zg, GLASS, GLASS);
  for (const i of [-1, 1]) for (const j of [-1, 1]) b.box(x + i * (sx / 2 - 0.07), y + j * (sy / 2 - 0.07), zg, 0.14, 0.14, zr - zg, color);
  b.box(x, y + front * (sy / 2 - 0.07), zg + (zr - zg) * 0.48, sx, 0.08, 0.08, color);            // window bar
  b.box(x, y + front * 0.08, zr, sx + 0.16, sy + 0.32, 0.18, color);                             // roof with a visor
}

// A tyre on the axle along x: a lathed profile with lugs on the tread, and
// (`outer`) the rim and hub on its outside face.
function wheel(b, cx, cy, R, w, { outer = true, sides = 24, lugs = true } = {}) {
  const cz = R;
  const side = cx >= 0 ? 1 : -1;
  const rimR = R * 0.6;
  const prof = [[-w / 2, rimR], [-w / 2 - 0.02, R * 0.86], [-w / 2 + 0.13, R], [w / 2 - 0.13, R], [w / 2 + 0.02, R * 0.86], [w / 2, rimR]];
  const at = (t, r, a) => [cx + t, cy + r * Math.cos(a), cz + r * Math.sin(a)];
  for (let k = 0; k < prof.length - 1; k += 1) {
    const [t0, r0] = prof[k]; const [t1, r1] = prof[k + 1];
    const nl = Math.hypot(r1 - r0, t1 - t0) || 1;
    const nt = -(r1 - r0) / nl; const nr = (t1 - t0) / nl;
    const tread = k === 2;
    const color = k === 1 || k === 3 || tread ? TREAD : TYRE;
    for (let i = 0; i < sides; i += 1) {
      const a0 = (i / sides) * Math.PI * 2; const a1 = ((i + 1) / sides) * Math.PI * 2;
      // Lugs: the tread stands out on two segments of three.
      const lug = (j, r) => (lugs && r >= R - 1e-6 && j % 3 === 0 ? r - 0.07 : r);
      const A = at(t0, lug(i, r0), a0); const B = at(t1, lug(i, r1), a0);
      const Cc = at(t1, lug(i + 1, r1), a1); const D = at(t0, lug(i + 1, r0), a1);
      const n0 = [nt, nr * Math.cos(a0), nr * Math.sin(a0)];
      const n1 = [nt, nr * Math.cos(a1), nr * Math.sin(a1)];
      b.tri(A, B, Cc, color, color, color, false, null, [n0, n0, n1]);
      b.tri(A, Cc, D, color, color, color, false, null, [n0, n1, n1]);
    }
  }
  if (!outer) return;
  // The rim: a flange, a recessed disc and the hub with its cap.
  const n = 16;
  const face = side * (w / 2);
  const annulus = (t, r0, r1, color, facing) => {
    for (let i = 0; i < n; i += 1) {
      const a0 = (i / n) * Math.PI * 2; const a1 = ((i + 1) / n) * Math.PI * 2;
      b.quad(at(t, r0, a0), at(t, r1, a0), at(t, r1, a1), at(t, r0, a1), color, false, null, null, [facing, 0, 0]);
    }
  };
  annulus(face + side * 0.02, rimR * 0.82, rimR, RIM, side);
  const deep = face - side * 0.2;
  for (let i = 0; i < n; i += 1) {
    const a0 = (i / n) * Math.PI * 2; const a1 = ((i + 1) / n) * Math.PI * 2;
    const am = (a0 + a1) / 2;
    b.quad(at(face + side * 0.02, rimR * 0.82, a0), at(deep, rimR * 0.82, a0), at(deep, rimR * 0.82, a1), at(face + side * 0.02, rimR * 0.82, a1), HUB, false, null, null,
      [0, -Math.cos(am), -Math.sin(am)]);
  }
  annulus(deep, 0.42, rimR * 0.82, RIM, side);
  b.cylinder([cx + deep, cy, cz], [cx + face + side * 0.16, cy, cz], 0.42, HUB, 10, STEEL_MID);
}

// A crawler track along y centred at x: the loop of shoes with grousers,
// the track frame inside it, the idler and sprocket at the ends and rollers.
function crawler(b, x, width, length, height, { plate = 0.75, rollers = 6 } = {}) {
  const rr = height / 2;
  const half = length / 2 - rr;
  const loop = [];
  const straight = Math.max(2, Math.round((2 * half) / plate));
  for (let i = 0; i < straight; i += 1) loop.push([-half + (2 * half * i) / straight, 0]);
  for (let i = 0; i < 6; i += 1) { const a = -Math.PI / 2 + (Math.PI * i) / 6; loop.push([half + Math.cos(a) * rr, rr + Math.sin(a) * rr]); }
  for (let i = 0; i < straight; i += 1) loop.push([half - (2 * half * i) / straight, height]);
  for (let i = 0; i < 6; i += 1) { const a = Math.PI / 2 + (Math.PI * i) / 6; loop.push([-half + Math.cos(a) * rr, rr + Math.sin(a) * rr]); }
  const n = loop.length;
  const x0 = x - width / 2; const x1 = x + width / 2;
  const thick = 0.32;
  const inset = loop.map(([y, z]) => {
    const dy = y - (y > half ? half : y < -half ? -half : y);
    const dz = z - rr;
    const l = Math.hypot(dy, dz) || 1;
    return Math.abs(y) <= half ? [y, z + (z < rr ? thick : -thick)] : [y - (dy / l) * thick, z - (dz / l) * thick];
  });
  for (let i = 0; i < n; i += 1) {
    const j = (i + 1) % n;
    const [ya, za] = loop[i]; const [yb, zb] = loop[j];
    // Outwards from the loop: down under it, up over it, round its ends.
    const my = (ya + yb) / 2; const mz = (za + zb) / 2;
    const oy = my - clamp(my, -half, half); const oz = mz - rr;
    const ol = Math.hypot(oy, oz) || 1;
    const out = [0, oy / ol, oz / ol];
    // A shoe: its outer face, its edges at both sides, a grouser across it.
    b.quad([x0, ya, za], [x1, ya, za], [x1, yb, zb], [x0, yb, zb], STEEL, false, null, null, out);
    for (const [xs, facing] of [[x0, -1], [x1, 1]]) {
      b.quad([xs, ya, za], [xs, yb, zb], [xs, inset[j][0], inset[j][1]], [xs, inset[i][0], inset[i][1]], STEEL_MID, false, null, null, [facing, 0, 0]);
    }
    const g = 0.11;
    const ridge = (xx) => [xx, ya + out[1] * g, za + out[2] * g];
    b.quad([x0, ya, za], [x1, ya, za], ridge(x1), ridge(x0), STEEL, false, null, null, [0, (ya - yb), (za - zb)]);
    b.quad(ridge(x0), ridge(x1), [x1, yb, zb], [x0, yb, zb], STEEL, false, null, null, out);
  }
  // The track frame in the loop, the idler (front), the sprocket (rear) and rollers.
  b.box(x, 0, rr - (height - 2 * thick) * 0.32, width * 0.72, 2 * half, (height - 2 * thick) * 0.64, STEEL);
  for (const end of [-1, 1]) b.cylinder([x0 + 0.08, end * half, rr], [x1 - 0.08, end * half, rr], rr - thick - 0.04, STEEL_MID, 12, STEEL_LIGHT);
  for (let i = 0; i < rollers; i += 1) {
    const y = -half + ((i + 0.5) * 2 * half) / rollers;
    b.cylinder([x0 + 0.1, y, thick + 0.2], [x1 - 0.1, y, thick + 0.2], 0.2, STEEL_LIGHT, 8, STEEL_LIGHT);
  }
}

// A hydraulic cylinder from p to q: a barrel, then the chrome rod.
function ram(b, p, q, r, barrel) {
  const m = [p[0] + (q[0] - p[0]) * 0.56, p[1] + (q[1] - p[1]) * 0.56, p[2] + (q[2] - p[2]) * 0.56];
  b.cylinder(p, m, r, barrel, 8, STEEL);
  b.cylinder(m, q, r * 0.6, CHROME, 8, CHROME);
}

// -- Caterpillar 785C ---------------------------------------------------------

export const TRUCK = { hinge: [0, -4.7, 2.1], length: 11.5, width: 6.6, height: 5.75, radius: 6.5 };
const TYRE_R = 1.47;

export function truckParts({ lod = 'near', lit = true } = {}) {
  if (lod === 'far') return truckFar(lit);
  const simple = lod === 'simple';
  const frame = new Builder();
  const W = 0.9;
  for (const [x, y, outer] of [[-2.45, 3.3, true], [2.45, 3.3, true], [-2.5, -1.9, true], [2.5, -1.9, true], [-1.55, -1.9, false], [1.55, -1.9, false]]) {
    if (simple) frame.cylinder([x - W / 2, y, TYRE_R], [x + W / 2, y, TYRE_R], TYRE_R, TYRE, 7, TYRE);
    else wheel(frame, x, y, TYRE_R, W, { outer });
  }
  // Frame rails and cross members, the rear axle and its differential.
  frame.box(-0.75, -0.4, 1.05, 0.45, 10.6, 0.95, STEEL);
  frame.box(0.75, -0.4, 1.05, 0.45, 10.6, 0.95, STEEL);
  for (const y of [4.7, 0.7, -4.6]) frame.box(0, y, 1.15, 1.95, 0.4, 0.7, STEEL);
  frame.cylinder([-2.0, -1.9, TYRE_R], [2.0, -1.9, TYRE_R], 0.52, STEEL, simple ? 6 : 12, STEEL_MID);
  frame.box(0, -1.9, 0.85, 1.7, 1.5, 1.3, STEEL);
  if (!simple) {
    // Front suspension struts, the steering, the hoist cylinders (stowed).
    for (const s of [-1, 1]) {
      frame.cylinder([s * 1.75, 3.3, 1.5], [s * 1.55, 3.3, 3.3], 0.24, CHROME, 8, STEEL);
      frame.cylinder([s * 1.75, 3.3, 1.2], [s * 1.75, 3.3, 1.9], 0.36, STEEL, 8, STEEL);
      ram(frame, [s * 1.05, 1.5, 1.7], [s * 1.05, -1.4, 2.0], 0.3, CAT_DARK);
    }
    frame.beam([-1.6, 3.9, 1.3], [1.6, 3.9, 1.3], 0.18, 0.18, STEEL_MID);
  }
  // The power module under the deck, the radiator grille and the bumper.
  bevelBox(frame, 0, 4.35, 1.5, 2.7, 2.5, 1.86, CAT, CAT, 0.16);
  frame.box(0, 5.63, 1.62, 2.4, 0.08, 1.62, BLACK);
  if (!simple) for (let z = 1.75; z < 3.2; z += 0.26) frame.box(0, 5.68, z, 2.3, 0.05, 0.07, STEEL_MID);
  frame.box(0, 5.88, 0.95, 5.3, 0.5, 0.8, CAT_DARK);
  for (const x of [-2.1, -1.6, 1.6, 2.1]) frame.box(x, 6.14, 1.32, 0.34, 0.04, 0.26, LAMP);
  // The deck over the front wheels, with its supports and handrails.
  frame.box(0, 4.2, 3.33, 6.5, 2.66, 0.22, CAT);
  for (const s of [-1, 1]) frame.beam([s * 1.3, 5.2, 2.4], [s * 2.9, 5.2, 3.33], 0.22, 0.22, CAT_DARK);
  if (!simple) {
    rail(frame, [[-3.2, 3.0], [-3.2, 5.48], [-0.6, 5.48]], 3.55, 1.0, CAT);
    rail(frame, [[0.6, 5.48], [3.2, 5.48], [3.2, 3.0]], 3.55, 1.0, CAT);
    // The diagonal stair across the front, from the bumper to the deck.
    stair(frame, [-2.45, 6.0, 1.75], [-0.25, 5.75, 3.55], 0.72, CAT_DARK);
    // Air cleaners on the right of the deck, mirrors, beacon.
    for (const x of [1.55, 2.5]) frame.cylinder([x, 4.75, 3.55], [x, 4.75, 4.45], 0.36, CAT, 10, STEEL_MID);
    frame.beam([-3.15, 5.3, 4.6], [-3.6, 5.6, 4.9], 0.06, 0.06, BLACK);
    frame.box(-3.6, 5.6, 4.6, 0.12, 0.3, 0.5, BLACK);
  }
  // The cab on the left of the deck.
  if (simple) frame.box(-2.05, 4.2, 3.55, 2.0, 2.3, 1.9, CAT);
  else cab(frame, -2.05, 4.15, 3.55, 2.0, 2.3, 1.9, CAT);
  // Fuel tank (left) and hydraulic tank (right) between the wheels; tail lights.
  bevelBox(frame, -2.05, 0.7, 1.15, 1.15, 2.1, 1.5, CAT, CAT, 0.18);
  bevelBox(frame, 2.05, 0.7, 1.25, 1.1, 1.9, 1.4, CAT, CAT, 0.18);
  for (const s of [-1, 1]) frame.box(s * 0.75, -5.72, 1.4, 0.3, 0.06, 0.2, RED);
  frame.grime(0.3, 2.2, DIRT, 0.6);

  const body = new Builder();
  // Side walls: a profile in (y, z), the floor rising to the tail.
  const side = [[2.75, 2.05], [-3.0, 2.05], [-5.75, 3.05], [-5.88, 3.48], [-5.1, 5.05], [2.75, 5.05]];
  body.prismX(side, -3.22, -3.0, CAT);
  body.prismX(side, 3.0, 3.22, CAT);
  // The floor, thick under the load.
  body.prismX([[2.75, 2.05], [-3.0, 2.05], [-5.75, 3.05], [-5.75, 3.32], [-3.0, 2.32], [2.75, 2.32]], -3.0, 3.0, CAT_DARK);
  // Worn steel inside, where the rock slides.
  body.quad([-2.99, 2.62, 2.33], [2.99, 2.62, 2.33], [2.99, -3.0, 2.33], [-2.99, -3.0, 2.33], CAT_WORN, true);
  body.quad([-2.99, -3.0, 2.33], [2.99, -3.0, 2.33], [2.99, -5.74, 3.33], [-2.99, -5.74, 3.33], CAT_WORN, true);
  for (const s of [-1, 1]) {
    body.prismX([[2.6, 2.33], [-3.0, 2.33], [-5.6, 3.3], [-5.0, 4.95], [2.6, 4.95]], s * 2.985, s * 2.99, CAT_WORN);
  }
  // The front wall, up to the canopy over the cab.
  body.box(0, 2.75, 2.05, 6.44, 0.3, 3.5, CAT);
  body.quad([-2.99, 2.59, 2.33], [2.99, 2.59, 2.33], [2.99, 2.59, 5.0], [-2.99, 2.59, 5.0], CAT_WORN, false, null, null, [0, -1, 0]);
  body.box(0, 4.3, 5.48, 6.5, 3.3, 0.24, CAT, CAT);
  if (!simple) {
    body.box(0, 5.9, 5.22, 6.5, 0.12, 0.5, CAT_DARK);                                        // canopy lip
    for (const x of [-2.2, 0, 2.2]) body.box(x, 4.3, 5.24, 0.2, 3.1, 0.24, CAT_DARK);         // canopy ribs
    // Ribs and rails on the outside of the walls.
    for (const s of [-1, 1]) {
      for (const y of [2.2, 0.4, -1.4, -3.2]) body.box(s * 3.31, y, 2.05, 0.18, 0.32, 3.0, CAT_DARK);
      body.box(s * 3.3, -1.18, 4.82, 0.3, 7.86, 0.3, CAT);
      body.box(s * 3.3, -0.12, 2.05, 0.26, 5.74, 0.28, CAT_DARK);
      body.beam([s * 3.3, -3.0, 2.15], [s * 3.3, -5.7, 3.15], 0.26, 0.28, CAT_DARK);
      body.box(s * 1.0, -4.7, 1.75, 0.4, 0.7, 0.6, STEEL);                                     // hinge brackets
    }
    body.box(0, -5.88, 3.0, 6.2, 0.2, 0.55, CAT_DARK);                                        // tail lip
  }

  // The load: a heap of broken rock in the body, highest over the rear axle.
  const load = new Builder();
  const NX = simple ? 3 : 7; const NY = simple ? 4 : 9;
  const height = (x, y) => {
    const fx = clamp(1 - (x / 3.0) ** 2, 0, 1) ** 0.7;
    const fy = Math.sin(Math.PI * clamp((y + 5.4) / 8.0, 0, 1)) ** 0.6;
    const lumps = simple ? 0 : 0.18 * Math.sin(x * 3.1 + y * 1.7) * Math.cos(y * 2.3 - x);
    return 4.55 + 1.55 * fx * fy + lumps * fx;
  };
  const white = [1, 1, 1];
  for (let i = 0; i < NX; i += 1) {
    for (let j = 0; j < NY; j += 1) {
      const xa = -2.98 + (5.96 * i) / NX; const xb = -2.98 + (5.96 * (i + 1)) / NX;
      const ya = -5.4 + (7.9 * j) / NY; const yb = -5.4 + (7.9 * (j + 1)) / NY;
      load.quad([xa, ya, height(xa, ya)], [xb, ya, height(xb, ya)], [xb, yb, height(xb, yb)], [xa, yb, height(xa, yb)], white, true);
    }
  }
  return { frame: geometryOf(frame, lit), body: geometryOf(body, lit), load: geometryOf(load, lit) };
}

// The same truck from afar, where it is a few pixels: blocks in the right proportions.
function truckFar(lit) {
  const frame = new Builder();
  for (const y of [3.3, -1.9]) {
    const hw = y < 0 ? 0.95 : 0.45;
    for (const x of [-2.45, 2.45]) frame.cylinder([x - hw, y, TYRE_R], [x + hw, y, TYRE_R], TYRE_R, TYRE, 6, TYRE);
  }
  frame.box(0, -0.4, 1.05, 1.9, 10.6, 0.95, STEEL);
  frame.box(0, 4.35, 1.5, 2.7, 2.5, 1.86, CAT);
  frame.box(0, 4.2, 3.33, 6.5, 2.66, 0.22, CAT);
  frame.box(-2.05, 4.2, 3.55, 2.0, 2.3, 1.9, CAT);
  frame.box(0, 5.88, 0.95, 5.3, 0.5, 0.8, CAT_DARK);
  frame.grime(0.3, 2.2, DIRT, 0.5);
  const body = new Builder();
  body.prismX([[2.75, 2.05], [-3.0, 2.05], [-5.75, 3.05], [-5.88, 3.48], [-5.1, 5.05], [2.75, 5.05]], -3.3, 3.3, CAT, CAT_DARK);
  body.box(0, 4.3, 5.48, 6.5, 3.3, 0.24, CAT);
  const load = new Builder();
  const white = [1, 1, 1];
  load.quad([-2.9, -5.2, 4.9], [2.9, -5.2, 4.9], [1.8, -1.5, 6.0], [-1.8, -1.5, 6.0], white, true);
  load.quad([-1.8, -1.5, 6.0], [1.8, -1.5, 6.0], [2.9, 2.4, 4.9], [-2.9, 2.4, 4.9], white, true);
  return { frame: geometryOf(frame, lit), body: geometryOf(body, lit), load: geometryOf(load, lit) };
}

// -- Hitachi EX3600 front shovel ------------------------------------------------

export const SHOVEL = { radius: 9, height: 12, bucketReach: 12 };

// The counterweight's back: an arc about the slewing centre (tail swing 6.5 m).
const tail = (r, from = -58, to = -122, n = 8) => Array.from({ length: n + 1 }, (_, i) => {
  const a = ((from + ((to - from) * i) / n) * Math.PI) / 180;
  return [Math.cos(a) * r, Math.sin(a) * r];
});

export function shovelParts({ lod = 'near', lit = true } = {}) {
  if (lod === 'far') return shovelFar(lit);
  const simple = lod === 'simple';
  const base = new Builder();
  if (simple) {
    for (const s of [-1, 1]) base.box(s * 2.95, 0, 0, 1.1, 8.4, 2.1, STEEL);
  } else {
    crawler(base, -2.95, 1.1, 8.4, 2.1);
    crawler(base, 2.95, 1.1, 8.4, 2.1);
  }
  bevelBox(base, 0, 0, 0.75, 4.0, 4.6, 1.5, STEEL, STEEL, 0.3);
  for (const s of [-1, 1]) base.box(s * 2.2, 0, 0.9, 0.6, 3.2, 1.0, STEEL);
  base.cylinder([0, 0, 2.2], [0, 0, 2.62], 2.35, STEEL_LIGHT, simple ? 10 : 20, STEEL);
  base.grime(0.0, 2.4, DIRT, 0.5);

  const upper = new Builder();
  // The walkway deck round the house, with the counterweight's curve at the back.
  upper.prismZ([[-3.45, 3.3], [-3.45, -3.9], ...tail(6.55).reverse(), [3.45, -3.9], [3.45, 3.3]], 2.62, 2.9, STEEL_MID, STEEL_MID);
  // Counterweight: massive, rounded at the back.
  upper.prismZ([[-3.42, -3.9], ...tail(6.45).reverse(), [3.42, -3.9]], 2.9, 6.0, HITACHI, HITACHI);
  upper.prismZ([[-3.44, -4.6], ...tail(6.47, -60, -120, 6).reverse(), [3.44, -4.6]], 2.9, 3.3, HITACHI_DARK, HITACHI_DARK);
  // The machinery house, the engine hood with its grilles, the exhaust stacks.
  bevelBox(upper, 0.35, -1.4, 2.9, 5.9, 5.2, 3.7, HITACHI, HITACHI, 0.28);
  bevelBox(upper, 0.35, -2.5, 6.6, 5.3, 3.0, 0.8, HITACHI, HITACHI, 0.2);
  upper.box(0.35, -2.5, 7.4, 4.5, 2.4, 0.04, STEEL_MID);
  if (!simple) for (let i = 0; i < 7; i += 1) upper.box(0.35, -3.5 + i * 0.33, 7.44, 4.4, 0.12, 0.08, STEEL);   // radiator grille
  if (!simple) {
    for (const x of [1.4, 2.3]) upper.cylinder([x, -3.5, 7.2], [x, -3.5, 8.6], 0.22, STEEL, 8, BLACK);
    // Louvres along the right side of the house; door seams on the left.
    for (let i = 0; i < 6; i += 1) upper.box(3.33, -3.4 + i * 0.42, 3.8, 0.06, 0.2, 1.9, BLACK);
    for (const y of [-3.2, -1.6, 0.0]) upper.box(-2.58, y, 3.3, 0.04, 0.06, 2.8, HITACHI_DARK);
    // Handrails along both walkways and a stair down the left front.
    rail(upper, [[-3.4, -3.8], [-3.4, 1.0]], 2.9, 1.05, CAT_DARK);
    rail(upper, [[3.4, -3.8], [3.4, 3.2], [1.6, 3.2]], 2.9, 1.05, CAT_DARK);
    stair(upper, [-3.75, 3.8, 0.35], [-3.6, 1.2, 2.9], 0.8, CAT_DARK);
  }
  // The cab, high at the front left, on its pedestal.
  upper.box(-2.4, 2.2, 2.9, 1.9, 2.1, 2.2, HITACHI);
  if (simple) upper.box(-2.4, 2.25, 5.1, 2.1, 2.3, 2.5, HITACHI);
  else cab(upper, -2.4, 2.25, 5.1, 2.1, 2.3, 2.5, HITACHI, { glassFrom: 0.22 });
  // Boom foot, boom (tapering), stick and bucket.
  upper.box(0.35, 1.1, 2.9, 2.6, 1.6, 2.4, HITACHI_DARK);
  upper.beam([0.35, 1.2, 4.3], [0.35, 6.7, 10.5], 2.1, 1.9, HITACHI, HITACHI, 1.4, 1.15);
  upper.beam([0.35, 6.6, 10.6], [0.35, 9.5, 6.0], 1.6, 1.3, HITACHI, HITACHI, 1.4, 1.1);
  upper.prismX([[9.05, 6.5], [8.9, 3.2], [9.5, 2.05], [12.1, 1.95], [12.4, 2.45], [11.2, 4.7], [10.4, 6.5]], -2.15, 2.85, BUCKET, BUCKET);
  if (!simple) {
    upper.box(0.35, 12.15, 1.95, 5.1, 0.55, 0.3, STEEL_MID);                                 // lip
    for (let i = 0; i < 6; i += 1) upper.beam([-1.75 + i * 0.84, 12.35, 2.12], [-1.75 + i * 0.84, 12.95, 2.05], 0.34, 0.28, STEEL_LIGHT, STEEL_LIGHT, 0.16, 0.12);
    for (const s of [-1, 1]) {
      upper.prismX([[9.0, 6.55], [8.85, 3.15], [9.45, 2.0], [11.0, 1.95], [10.4, 4.6]], 0.35 + s * 2.5, 0.35 + s * 2.6, STEEL_MID);  // side plates
      ram(upper, [0.35 + s * 1.15, 1.9, 3.3], [0.35 + s * 1.0, 4.6, 7.7], 0.32, HITACHI_DARK);      // boom cylinders
      ram(upper, [0.35 + s * 0.75, 3.0, 6.0], [0.35 + s * 0.75, 8.0, 8.3], 0.28, HITACHI_DARK);     // arm cylinders
      ram(upper, [0.35 + s * 0.9, 6.0, 9.6], [0.35 + s * 0.9, 9.3, 7.3], 0.26, HITACHI_DARK);       // bucket cylinders
    }
    // Work lamps on the boom and the cab roof.
    for (const s of [-1, 1]) upper.box(0.35 + s * 0.9, 5.2, 8.6, 0.4, 0.2, 0.3, LAMP);
    upper.box(-2.4, 3.45, 7.5, 1.2, 0.1, 0.22, LAMP);
  }
  upper.grime(1.5, 3.4, DIRT, 0.35);
  return { base: geometryOf(base, lit), upper: geometryOf(upper, lit) };
}

function shovelFar(lit) {
  const base = new Builder();
  for (const s of [-1, 1]) base.box(s * 2.95, 0, 0, 1.1, 8.4, 2.1, STEEL);
  base.box(0, 0, 0.75, 4.0, 4.6, 1.9, STEEL);
  const upper = new Builder();
  upper.prismZ([[-3.42, -3.9], ...tail(6.45, -58, -122, 4).reverse(), [3.42, -3.9]], 2.62, 6.0, HITACHI);
  upper.box(0.35, -1.4, 2.62, 5.9, 5.2, 4.0, HITACHI);
  upper.box(-2.4, 2.2, 2.62, 2.0, 2.2, 5.0, HITACHI);
  upper.beam([0.35, 1.2, 4.3], [0.35, 6.7, 10.5], 2.1, 1.9, HITACHI, HITACHI, 1.4, 1.15);
  upper.beam([0.35, 6.6, 10.6], [0.35, 9.5, 6.0], 1.6, 1.3, HITACHI);
  upper.prismX([[9.05, 6.5], [8.9, 3.2], [9.5, 2.05], [12.1, 1.95], [12.4, 2.45], [11.2, 4.7], [10.4, 6.5]], -2.15, 2.85, BUCKET);
  return { base: geometryOf(base, lit), upper: geometryOf(upper, lit) };
}

// -- Sandvik D55SP blasthole drill ---------------------------------------------

export const DRILL = { pivot: [0.6, 4.1, 2.3], mast: 18, radius: 7, height: 21 };

export function drillParts({ lod = 'near', lit = true } = {}) {
  const simple = lod !== 'near';
  const base = new Builder();
  if (simple) for (const s of [-1, 1]) base.box(s * 2.1, 0, 0, 1.0, 7.4, 1.35, STEEL);
  else {
    crawler(base, -2.1, 0.95, 7.4, 1.35, { plate: 0.8, rollers: 5 });
    crawler(base, 2.1, 0.95, 7.4, 1.35, { plate: 0.8, rollers: 5 });
  }
  base.box(0, 0, 1.3, 4.9, 9.8, 0.6, SANDVIK_GREY);                                       // main frame
  base.box(0, 0, 1.9, 5.0, 9.9, 0.12, STEEL_MID);                                         // deck plate
  bevelBox(base, 0.3, -2.9, 2.02, 4.3, 3.6, 2.5, SANDVIK, SANDVIK, 0.22);                  // power pack
  base.box(0.3, -4.72, 2.25, 3.7, 0.06, 2.0, STEEL);                                      // radiator
  if (!simple) for (let i = 0; i < 6; i += 1) base.box(0.3, -4.77, 2.4 + i * 0.3, 3.5, 0.05, 0.1, BLACK);
  bevelBox(base, 0.7, 0.35, 2.02, 3.0, 2.7, 1.9, SANDVIK, SANDVIK, 0.18);                  // compressor
  if (simple) base.box(-1.55, 2.6, 2.02, 1.6, 2.2, 2.4, SANDVIK);
  else cab(base, -1.55, 2.6, 2.02, 1.6, 2.2, 2.4, SANDVIK, { glassFrom: 0.3 });
  for (const x of [-2.15, 2.15]) for (const y of [-4.4, 4.4]) base.cylinder([x, y, 0], [x, y, 1.6], 0.24, STEEL_LIGHT, 8, STEEL);   // levelling jacks
  if (!simple) {
    for (let i = 0; i < 5; i += 1) base.box(2.47, -4.2 + i * 0.5, 2.5, 0.06, 0.24, 1.6, BLACK);   // louvres
    base.cylinder([2.0, 2.3, 2.02], [2.0, 2.3, 3.8], 0.55, SANDVIK_GREY, 10, STEEL);        // dust collector
    rail(base, [[-2.45, -4.8], [-2.45, 1.4]], 1.96, 1.0, SANDVIK);
    rail(base, [[2.45, -4.8], [2.45, 1.0]], 1.96, 1.0, SANDVIK);
    stair(base, [-2.85, -3.4, 0.3], [-2.55, -1.6, 1.96], 0.6, SANDVIK);
    base.box(-1.55, 3.75, 4.3, 1.0, 0.1, 0.2, LAMP);
  }
  base.grime(0.0, 1.9, DIRT, 0.55);

  // The mast, built standing up from its pivot (origin), lowered by the scene.
  const mast = new Builder();
  const H = DRILL.mast;
  const rails = [[-0.55, -0.5], [0.55, -0.5], [0.55, 0.5], [-0.55, 0.5]];
  for (const [x, y] of rails) mast.beam([x, y, 0], [x, y, H], 0.26, 0.26, SANDVIK);
  if (!simple) {
    for (let z = 1.2; z < H - 1; z += 2.4) {
      mast.beam([-0.55, -0.5, z], [0.55, -0.5, z + 1.2], 0.1, 0.1, SANDVIK);
      mast.beam([0.55, 0.5, z], [-0.55, 0.5, z + 1.2], 0.1, 0.1, SANDVIK);
      mast.beam([-0.55, 0.5, z + 1.2], [-0.55, -0.5, z + 2.4], 0.1, 0.1, SANDVIK);
      mast.beam([0.55, -0.5, z + 1.2], [0.55, 0.5, z + 2.4], 0.1, 0.1, SANDVIK);
    }
    mast.box(0, 0.75, H * 0.55, 1.0, 0.9, 1.3, SANDVIK_GREY);                             // rotary head
    mast.cylinder([0, 0.75, 0.3], [0, 0.75, H - 1], 0.17, CHROME, 8, CHROME);              // drill pipe
    mast.cylinder([-0.9, 0, 0.5], [-0.9, 0, H - 2], 0.2, STEEL_MID, 8, STEEL);             // pipe in the carousel
    mast.box(0, 0, H - 0.1, 1.5, 1.4, 0.9, SANDVIK);                                       // crown
    mast.box(0, 0.6, H + 0.8, 0.3, 0.2, 0.3, LAMP);
  } else {
    mast.box(0, 0, H - 0.1, 1.5, 1.4, 0.9, SANDVIK);
  }
  mast.box(0, 0.1, 0, 1.7, 1.5, 1.2, STEEL);                                               // table and dust hood
  return { base: geometryOf(base, lit), mast: geometryOf(mast, lit) };
}

// -- marks under and over machines --------------------------------------------

// A flat ring of unit radius on the ground, for the state halo.
export function haloGeometry(segments = 36) {
  const geometry = new THREE.RingGeometry(0.88, 1, segments, 1);
  return geometry;
}

// A flat unit disc for the soft shadow under a machine.
export function blobGeometry() {
  return new THREE.PlaneGeometry(2, 2);
}

// How many triangles a geometry holds.
export const trianglesOf = (geometry) => (geometry.index ? geometry.index.count : geometry.attributes.position.count) / 3;
