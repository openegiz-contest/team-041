// Geometry helpers of the 3D view. Every surface is built from triangles with
// an albedo (its own colour, ambient occlusion included) and a normal per
// vertex; its displayed colour is the albedo lit by the sun (or the moon),
// the sky, a back light and the site's lamps, computed here on the CPU. The
// world's materials then do no lighting of their own, which keeps a frame
// cheap in a browser that renders WebGL in software, and the static world is
// lit again (relight) when the simulated sun has moved. The machines, which
// turn, take litGeometry() and a material that lights itself. Faces are
// wound to face out (front faces seen from outside).

import * as THREE from 'three';

export const clamp = (value, low, high) => Math.min(Math.max(value, low), high);

// Linear colour from an sRGB hex string, as [r, g, b].
const cache = new Map();
export function rgb(hex) {
  let value = cache.get(hex);
  if (!value) {
    const c = new THREE.Color(hex);
    value = [c.r, c.g, c.b];
    cache.set(hex, value);
  }
  return value;
}
export const mix = (a, b, t) => [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t];
export const scale = (a, k) => [a[0] * k, a[1] * k, a[2] * k];

// The light of the moment: direction to the key light (the sun, or the moon
// at night; unit, z up) and its colour, sky light from above, light bounced
// from the ground, a cool back light from the side away from the key (it
// keeps the shaded faces of the pit apart), and the site's own lamps:
// floodlight towers and lamps that light pools around them once it is dark
// (`locals`, scene coordinates, switched on by `localGain`).
export const LIGHT = {
  sun: new THREE.Vector3(-0.45, -0.62, 0.64).normalize(),
  sunColor: [2.4, 2.2, 1.9],
  sky: [0.5, 0.58, 0.74],
  ground: [0.25, 0.21, 0.15],
  rim: [0.12, 0.14, 0.18],
  locals: [],
  localsVersion: 0,
  localGain: 0,
};

// A fixed light for the machines drawn without lighting of their own (the
// 2D-canvas stand-in): from above and a little in front, the same from every side.
const MACHINE_LIGHT = { sun: new THREE.Vector3(0.35, 0.5, 0.79).normalize(), sunColor: [0.78, 0.76, 0.72], sky: [0.5, 0.52, 0.56], ground: [0.22, 0.2, 0.17], rim: [0, 0, 0], locals: [], localGain: 0 };

function shadeInto(out, o, r, g, b, nx, ny, nz, light, sx, sy, sz, hx, hy) {
  // Direct light, plus key light thrown back by the lit walls opposite onto
  // the faces turned away from it (in a pit they are never black).
  const ndl = nx * sx + ny * sy + nz * sz;
  const away = Math.max(0, -(nx * hx + ny * hy)) * (1 - Math.abs(nz));
  const d = Math.max(0, ndl) + 0.16 * away;
  // Sky light reaches steep faces too (open pit, light sky); the ground bounces a little.
  const up = 0.66 + 0.34 * nz;
  const down = (0.5 - 0.5 * nz) * 0.9;
  out[o] = r * (light.sky[0] * up + light.ground[0] * down + light.sunColor[0] * d + light.rim[0] * away);
  out[o + 1] = g * (light.sky[1] * up + light.ground[1] * down + light.sunColor[1] * d + light.rim[1] * away);
  out[o + 2] = b * (light.sky[2] * up + light.ground[2] * down + light.sunColor[2] * d + light.rim[2] * away);
}

// Light of the site's lamps at each vertex of a geometry (rgb), computed once
// for a set of lamps: lamps on towers light the ground round their foot and
// the faces turned to them, falling off with distance.
function localLight(geometry, locals) {
  const pos = geometry.attributes.position.array;
  const normals = geometry.userData.normals;
  const out = new Float32Array(pos.length);
  if (!locals.length) return out;
  // Lamps binned in a grid, so each vertex looks only at the lamps near it.
  const CELL = 160;
  const bins = new Map();
  for (const lamp of locals) {
    const r = Math.ceil(lamp.range / CELL);
    const ci = Math.floor(lamp.x / CELL); const cj = Math.floor(lamp.y / CELL);
    for (let i = ci - r; i <= ci + r; i += 1) {
      for (let j = cj - r; j <= cj + r; j += 1) {
        const key = i * 100003 + j;
        let list = bins.get(key);
        if (!list) { list = []; bins.set(key, list); }
        list.push(lamp);
      }
    }
  }
  for (let v = 0; v < pos.length; v += 3) {
    const px = pos[v]; const py = pos[v + 1]; const pz = pos[v + 2];
    const list = bins.get(Math.floor(px / CELL) * 100003 + Math.floor(py / CELL));
    if (!list) continue;
    let lr = 0; let lg = 0; let lb = 0;
    for (const lamp of list) {
      const dx = lamp.x - px; const dy = lamp.y - py; const dz = lamp.z - pz;
      const d2 = dx * dx + dy * dy + dz * dz;
      const r2 = lamp.range * lamp.range;
      if (d2 >= r2) continue;
      const d = Math.sqrt(d2) || 1;
      const ndl = (normals[v] * dx + normals[v + 1] * dy + normals[v + 2] * dz) / d;
      const fall = (1 - d2 / r2) ** 2 / (1 + d2 / (lamp.core * lamp.core));
      const w = fall * (0.3 + 0.7 * Math.max(0, ndl));
      lr += lamp.color[0] * w; lg += lamp.color[1] * w; lb += lamp.color[2] * w;
    }
    out[v] = lr; out[v + 1] = lg; out[v + 2] = lb;
  }
  return out;
}

// Colours of a geometry from its albedo and normals under `light`; `turn` is
// the rotation of its mesh about z, so the sun is taken in the mesh's frame.
export function relight(geometry, light = LIGHT, turn = 0) {
  const albedo = geometry.userData.albedo;
  const normals = geometry.userData.normals;
  if (!albedo) return;
  const colors = geometry.attributes.color.array;
  const c = Math.cos(-turn);
  const s = Math.sin(-turn);
  const sx = light.sun.x * c - light.sun.y * s;
  const sy = light.sun.x * s + light.sun.y * c;
  const sz = light.sun.z;
  const h = Math.hypot(sx, sy) || 1;
  const hx = sx / h; const hy = sy / h;
  for (let i = 0; i < albedo.length; i += 3) {
    shadeInto(colors, i, albedo[i], albedo[i + 1], albedo[i + 2], normals[i], normals[i + 1], normals[i + 2], light, sx, sy, sz, hx, hy);
  }
  const gain = light.localGain ?? 0;
  if (gain > 0.001 && light.locals?.length && !turn) {
    if (geometry.userData.localVersion !== light.localsVersion) {
      geometry.userData.local = localLight(geometry, light.locals);
      geometry.userData.localVersion = light.localsVersion;
    }
    const local = geometry.userData.local;
    for (let i = 0; i < albedo.length; i += 1) colors[i] += albedo[i] * local[i] * gain;
  }
  geometry.attributes.color.needsUpdate = true;
}

// Collects triangles into one non-indexed BufferGeometry. Colours are given
// per triangle or per vertex; `up` turns a normal upwards (ground surfaces
// whose winding is not known); `uv` adds texture coordinates per vertex.
export class Builder {
  constructor({ uv = false } = {}) {
    this.pos = [];
    this.alb = [];
    this.nrm = [];
    this.uvs = uv ? [] : null;
    this.owner = [];
    this.tag = null;
  }

  get triangles() { return this.pos.length / 9; }

  // `ns`: a normal per corner (smooth surfaces); otherwise the face's own.
  // The winding follows the normal: towards `ns`, towards `toward` when
  // given, else upwards with `up`; so faces seen from outside are front faces.
  tri(a, b, c, ca, cb = ca, cc = ca, up = true, uv = null, ns = null, toward = null) {
    const ux = b[0] - a[0]; const uy = b[1] - a[1]; const uz = b[2] - a[2];
    const vx = c[0] - a[0]; const vy = c[1] - a[1]; const vz = c[2] - a[2];
    let nx = uy * vz - uz * vy;
    let ny = uz * vx - ux * vz;
    let nz = ux * vy - uy * vx;
    const len = Math.hypot(nx, ny, nz) || 1;
    nx /= len; ny /= len; nz /= len;
    let flip;
    if (ns) flip = nx * (ns[0][0] + ns[1][0] + ns[2][0]) + ny * (ns[0][1] + ns[1][1] + ns[2][1]) + nz * (ns[0][2] + ns[1][2] + ns[2][2]) < 0;
    else if (toward) flip = nx * toward[0] + ny * toward[1] + nz * toward[2] < 0;
    else flip = up && nz < 0;
    if (flip) {
      [b, c] = [c, b]; [cb, cc] = [cc, cb];
      if (uv) uv = [uv[0], uv[2], uv[1]];
      if (ns) ns = [ns[0], ns[2], ns[1]];
      nx = -nx; ny = -ny; nz = -nz;
    }
    this.pos.push(a[0], a[1], a[2], b[0], b[1], b[2], c[0], c[1], c[2]);
    this.alb.push(ca[0], ca[1], ca[2], cb[0], cb[1], cb[2], cc[0], cc[1], cc[2]);
    if (ns) this.nrm.push(...ns[0], ...ns[1], ...ns[2]);
    else this.nrm.push(nx, ny, nz, nx, ny, nz, nx, ny, nz);
    if (this.uvs) {
      if (uv) this.uvs.push(uv[0][0], uv[0][1], uv[1][0], uv[1][1], uv[2][0], uv[2][1]);
      else this.uvs.push(a[0] / 60, a[1] / 60, b[0] / 60, b[1] / 60, c[0] / 60, c[1] / 60);
    }
    this.owner.push(this.tag);
  }

  // Quad a-b-c-d; colours per corner when `cs` is an array of four colours.
  quad(a, b, c, d, color, up = true, uv = null, cs = null, toward = null) {
    const [c0, c1, c2, c3] = cs ?? [color, color, color, color];
    this.tri(a, b, c, c0, c1, c2, up, uv ? [uv[0], uv[1], uv[2]] : null, null, toward);
    this.tri(a, c, d, c0, c2, c3, up, uv ? [uv[0], uv[2], uv[3]] : null, null, toward);
  }

  // A quad that faces away from point `o` (closed solids: its outside).
  quadOut(a, b, c, d, color, o, cs = null) {
    const m = [(a[0] + b[0] + c[0] + d[0]) / 4 - o[0], (a[1] + b[1] + c[1] + d[1]) / 4 - o[1], (a[2] + b[2] + c[2] + d[2]) / 4 - o[2]];
    this.quad(a, b, c, d, color, false, null, cs, m);
  }

  // Axis-aligned box: centre (x, y) on the floor z0, sizes sx, sy, sz.
  box(x, y, z0, sx, sy, sz, color, top = color, bottom = false) {
    const x0 = x - sx / 2; const x1 = x + sx / 2;
    const y0 = y - sy / 2; const y1 = y + sy / 2;
    const z1 = z0 + sz;
    this.quad([x0, y0, z1], [x1, y0, z1], [x1, y1, z1], [x0, y1, z1], top, false, null, null, [0, 0, 1]);
    this.quad([x0, y0, z0], [x1, y0, z0], [x1, y0, z1], [x0, y0, z1], color, false, null, null, [0, -1, 0]);
    this.quad([x1, y0, z0], [x1, y1, z0], [x1, y1, z1], [x1, y0, z1], color, false, null, null, [1, 0, 0]);
    this.quad([x1, y1, z0], [x0, y1, z0], [x0, y1, z1], [x1, y1, z1], color, false, null, null, [0, 1, 0]);
    this.quad([x0, y1, z0], [x0, y0, z0], [x0, y0, z1], [x0, y1, z1], color, false, null, null, [-1, 0, 0]);
    if (bottom) this.quad([x0, y1, z0], [x1, y1, z0], [x1, y0, z0], [x0, y0, z0], color, false, null, null, [0, 0, -1]);
  }

  // A box between two points with a cross-section w (across, horizontal) by
  // h (across, the other way): booms, sticks, beams, struts. With w2, h2 the
  // section changes along it (a tapered boom).
  beam(p, q, w, h, color, end = color, w2 = w, h2 = h) {
    const d = [q[0] - p[0], q[1] - p[1], q[2] - p[2]];
    const len = Math.hypot(...d) || 1;
    const f = [d[0] / len, d[1] / len, d[2] / len];
    // A side vector: horizontal, across the beam (or x for a vertical beam).
    let sx = -f[1]; let sy = f[0]; let sz = 0;
    const sl = Math.hypot(sx, sy);
    if (sl < 1e-4) { sx = 1; sy = 0; } else { sx /= sl; sy /= sl; }
    const u = [f[1] * sz - f[2] * sy, f[2] * sx - f[0] * sz, f[0] * sy - f[1] * sx];
    const corner = (o, i, j, ww, hh) => [o[0] + sx * i * ww / 2 + u[0] * j * hh / 2, o[1] + sy * i * ww / 2 + u[1] * j * hh / 2, o[2] + sz * i * ww / 2 + u[2] * j * hh / 2];
    const P = [corner(p, -1, -1, w, h), corner(p, 1, -1, w, h), corner(p, 1, 1, w, h), corner(p, -1, 1, w, h)];
    const Q = [corner(q, -1, -1, w2, h2), corner(q, 1, -1, w2, h2), corner(q, 1, 1, w2, h2), corner(q, -1, 1, w2, h2)];
    const mid = [(p[0] + q[0]) / 2, (p[1] + q[1]) / 2, (p[2] + q[2]) / 2];
    for (let i = 0; i < 4; i += 1) {
      const j = (i + 1) % 4;
      this.quadOut(P[i], P[j], Q[j], Q[i], color, mid);
    }
    this.quad(P[3], P[2], P[1], P[0], end, false, null, null, [-f[0], -f[1], -f[2]]);
    this.quad(Q[0], Q[1], Q[2], Q[3], end, false, null, null, f);
  }

  // A cylinder between two points, radius r: wheels, mills, tanks, pipes.
  // `smooth`: its side shaded round rather than in facets.
  cylinder(p, q, r, color, sides = 12, cap = color, caps = true, r2 = r, smooth = false) {
    const d = [q[0] - p[0], q[1] - p[1], q[2] - p[2]];
    const len = Math.hypot(...d) || 1;
    const f = [d[0] / len, d[1] / len, d[2] / len];
    let a = Math.abs(f[2]) < 0.9 ? [0, 0, 1] : [1, 0, 0];
    let s = [f[1] * a[2] - f[2] * a[1], f[2] * a[0] - f[0] * a[2], f[0] * a[1] - f[1] * a[0]];
    const sl = Math.hypot(...s);
    s = s.map((v) => v / sl);
    a = [f[1] * s[2] - f[2] * s[1], f[2] * s[0] - f[0] * s[2], f[0] * s[1] - f[1] * s[0]];
    const dir = (i) => {
      const t = (i / sides) * Math.PI * 2;
      return [s[0] * Math.cos(t) + a[0] * Math.sin(t), s[1] * Math.cos(t) + a[1] * Math.sin(t), s[2] * Math.cos(t) + a[2] * Math.sin(t)];
    };
    const ring = (o, radius) => Array.from({ length: sides }, (_, i) => {
      const t = (i / sides) * Math.PI * 2;
      const cx = Math.cos(t) * radius; const cy = Math.sin(t) * radius;
      return [o[0] + s[0] * cx + a[0] * cy, o[1] + s[1] * cx + a[1] * cy, o[2] + s[2] * cx + a[2] * cy];
    });
    const P = ring(p, r);
    const Q = ring(q, r2);
    const back = [-f[0], -f[1], -f[2]];
    for (let i = 0; i < sides; i += 1) {
      const j = (i + 1) % sides;
      const ni = dir(i); const nj = dir(j);
      if (smooth) {
        this.tri(P[i], P[j], Q[j], color, color, color, false, null, [ni, nj, nj]);
        this.tri(P[i], Q[j], Q[i], color, color, color, false, null, [ni, nj, ni]);
      } else this.quad(P[i], P[j], Q[j], Q[i], color, false, null, null, [ni[0] + nj[0], ni[1] + nj[1], ni[2] + nj[2]]);
      if (caps) {
        this.tri(q, Q[i], Q[j], cap, cap, cap, false, null, null, f);
        this.tri(p, P[j], P[i], cap, cap, cap, false, null, null, back);
      }
    }
  }

  // A cone standing on z0 with its apex `h` above, or a frustum with top radius `top`.
  cone(x, y, z0, r, h, color, sides = 16, top = 0, topColor = color) {
    for (let i = 0; i < sides; i += 1) {
      const a0 = (i / sides) * Math.PI * 2;
      const a1 = ((i + 1) / sides) * Math.PI * 2;
      const b0 = [x + Math.cos(a0) * r, y + Math.sin(a0) * r, z0];
      const b1 = [x + Math.cos(a1) * r, y + Math.sin(a1) * r, z0];
      const out = [Math.cos((a0 + a1) / 2), Math.sin((a0 + a1) / 2), 0.3];
      if (top > 0) {
        const t0 = [x + Math.cos(a0) * top, y + Math.sin(a0) * top, z0 + h];
        const t1 = [x + Math.cos(a1) * top, y + Math.sin(a1) * top, z0 + h];
        this.quad(b0, b1, t1, t0, color, false, null, null, out);
        this.tri([x, y, z0 + h], t0, t1, topColor, topColor, topColor, false, null, null, [0, 0, 1]);
      } else {
        this.tri(b0, b1, [x, y, z0 + h], color, color, color, false, null, null, out);
      }
    }
  }

  // A prism: a closed outline in the (y, z) plane extruded along x from x0 to x1.
  prismX(profile, x0, x1, color, side = color) {
    const n = profile.length;
    let area = 0;
    for (let i = 0; i < n; i += 1) {
      const [y0, z0] = profile[i];
      const [y1, z1] = profile[(i + 1) % n];
      area += y0 * z1 - y1 * z0;
    }
    const sign = area >= 0 ? 1 : -1;
    for (let i = 0; i < n; i += 1) {
      const [y0, z0] = profile[i];
      const [y1, z1] = profile[(i + 1) % n];
      // Outward normal of an edge of a counter-clockwise outline: (dz, -dy).
      this.quad([x0, y0, z0], [x0, y1, z1], [x1, y1, z1], [x1, y0, z0], color, false, null, null, [0, (z1 - z0) * sign, -(y1 - y0) * sign]);
    }
    const lo = x0 <= x1 ? -1 : 1;
    const capA = THREE.ShapeUtils.triangulateShape(profile.map(([y, z]) => new THREE.Vector2(y, z)), []);
    for (const [i, j, k] of capA) {
      this.tri([x0, ...profile[i]], [x0, ...profile[j]], [x0, ...profile[k]], side, side, side, false, null, null, [lo, 0, 0]);
      this.tri([x1, ...profile[i]], [x1, ...profile[k]], [x1, ...profile[j]], side, side, side, false, null, null, [-lo, 0, 0]);
    }
  }

  // A prism: a closed outline in the (x, y) plane from z0 up to z1 (a
  // counterweight, a house with a rounded back); `top` colours its lid.
  prismZ(outline, z0, z1, color, top = color, bottom = false) {
    const n = outline.length;
    let area = 0;
    for (let i = 0; i < n; i += 1) {
      const [xa, ya] = outline[i];
      const [xb, yb] = outline[(i + 1) % n];
      area += xa * yb - xb * ya;
    }
    const sign = area >= 0 ? 1 : -1;
    for (let i = 0; i < n; i += 1) {
      const [xa, ya] = outline[i];
      const [xb, yb] = outline[(i + 1) % n];
      this.quad([xa, ya, z0], [xb, yb, z0], [xb, yb, z1], [xa, ya, z1], color, false, null, null, [(yb - ya) * sign, -(xb - xa) * sign, 0]);
    }
    const faces = THREE.ShapeUtils.triangulateShape(outline.map(([x, y]) => new THREE.Vector2(x, y)), []);
    for (const [i, j, k] of faces) {
      this.tri([...outline[i], z1], [...outline[j], z1], [...outline[k], z1], top, top, top, false, null, null, [0, 0, 1]);
      if (bottom) this.tri([...outline[i], z0], [...outline[j], z0], [...outline[k], z0], color, color, color, false, null, null, [0, 0, -1]);
    }
  }

  // Add another builder's triangles turned by `angle` about z and moved to
  // `at` ([x, y, z] in this builder's frame); `tag` names their owner.
  append(other, angle = 0, at = [0, 0, 0], tag = other.tag) {
    const c = Math.cos(angle);
    const s = Math.sin(angle);
    const p = other.pos;
    const n = other.nrm;
    for (let i = 0; i < p.length; i += 3) {
      this.pos.push(p[i] * c - p[i + 1] * s + at[0], p[i] * s + p[i + 1] * c + at[1], p[i + 2] + at[2]);
      this.nrm.push(n[i] * c - n[i + 1] * s, n[i] * s + n[i + 1] * c, n[i + 2]);
    }
    for (const value of other.alb) this.alb.push(value);
    if (this.uvs) {
      if (other.uvs) for (const value of other.uvs) this.uvs.push(value);
      else for (let i = 0; i < p.length; i += 3) this.uvs.push(p[i] / 60, p[i + 1] / 60);
    }
    for (let i = 0; i < other.owner.length; i += 1) this.owner.push(tag ?? other.owner[i]);
  }

  geometry(light = LIGHT) {
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute('position', new THREE.Float32BufferAttribute(this.pos, 3));
    geometry.setAttribute('color', new THREE.Float32BufferAttribute(new Float32Array(this.pos.length), 3));
    if (this.uvs) geometry.setAttribute('uv', new THREE.Float32BufferAttribute(this.uvs, 2));
    geometry.userData.albedo = Float32Array.from(this.alb);
    geometry.userData.normals = Float32Array.from(this.nrm);
    relight(geometry, light);
    geometry.computeBoundingSphere();
    return geometry;
  }

  // A geometry lit once by the fixed light of the machines; it keeps no albedo.
  machineGeometry() {
    const geometry = this.geometry(MACHINE_LIGHT);
    delete geometry.userData.albedo;
    delete geometry.userData.normals;
    return geometry;
  }

  // A geometry for a material that lights itself (the machines in WebGL):
  // its colours are the albedo, with a normal per vertex.
  litGeometry() {
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute('position', new THREE.Float32BufferAttribute(this.pos, 3));
    geometry.setAttribute('normal', new THREE.Float32BufferAttribute(this.nrm, 3));
    geometry.setAttribute('color', new THREE.Float32BufferAttribute(this.alb, 3));
    geometry.computeBoundingSphere();
    return geometry;
  }

  // Darken and brown the colours below `z1` (dust and mud on the lower parts of a machine).
  grime(z0, z1, dirt, amount = 0.55) {
    for (let i = 0; i < this.pos.length; i += 3) {
      const t = clamp((z1 - this.pos[i + 2]) / (z1 - z0), 0, 1) * amount;
      if (t <= 0) continue;
      this.alb[i] += (dirt[0] - this.alb[i]) * t;
      this.alb[i + 1] += (dirt[1] - this.alb[i + 1]) * t;
      this.alb[i + 2] += (dirt[2] - this.alb[i + 2]) * t;
    }
    return this;
  }
}

// A flat band of `width` along a polyline [[x, y, z]], lifted by `lift`.
// `cols` splits it across into strips with a colour each ([colour] from the
// left edge to the right), for tyre tracks and edges; `uvScale` maps the
// texture along (distance) and across (metres).
export function ribbon(builder, points, width, color, lift = 0.4, closed = false, cols = null, uvScale = 0) {
  const n = points.length;
  if (n < 2) return;
  const half = width / 2;
  const normals = [];
  const along = [0];
  for (let i = 0; i < n; i += 1) {
    const prev = points[closed ? (i - 1 + n) % n : Math.max(0, i - 1)];
    const next = points[closed ? (i + 1) % n : Math.min(n - 1, i + 1)];
    const here = points[i];
    if (i) along.push(along[i - 1] + Math.hypot(here[0] - points[i - 1][0], here[1] - points[i - 1][1]));
    let ax = here[0] - prev[0]; let ay = here[1] - prev[1];
    let bx = next[0] - here[0]; let by = next[1] - here[1];
    const la = Math.hypot(ax, ay) || 1; const lb = Math.hypot(bx, by) || 1;
    ax /= la; ay /= la; bx /= lb; by /= lb;
    let tx = ax + bx; let ty = ay + by;
    const lt = Math.hypot(tx, ty);
    if (lt < 1e-6) { tx = bx; ty = by; } else { tx /= lt; ty /= lt; }
    const cos = Math.max(0.35, tx * bx + ty * by);
    normals.push([-ty / cos, tx / cos]);
  }
  const strips = cols ?? [color];
  const k = strips.length;
  const edge = (i, f) => [points[i][0] + normals[i][0] * half * f, points[i][1] + normals[i][1] * half * f, points[i][2] + lift];
  const last = closed ? n : n - 1;
  for (let i = 0; i < last; i += 1) {
    const j = (i + 1) % n;
    const sj = closed && j === 0 ? along[i] + Math.hypot(points[0][0] - points[i][0], points[0][1] - points[i][1]) : along[j];
    for (let s = 0; s < k; s += 1) {
      const f0 = -1 + (2 * s) / k;
      const f1 = -1 + (2 * (s + 1)) / k;
      const uv = uvScale ? [[along[i] / uvScale, (f0 * half) / uvScale], [sj / uvScale, (f0 * half) / uvScale],
        [sj / uvScale, (f1 * half) / uvScale], [along[i] / uvScale, (f1 * half) / uvScale]] : null;
      builder.quad(edge(i, f0), edge(j, f0), edge(j, f1), edge(i, f1), strips[s], true, uv);
    }
  }
}

// Points along a polyline every `step` metres at most.
export function densify(points, step) {
  const out = [points[0]];
  for (let i = 1; i < points.length; i += 1) {
    const a = points[i - 1];
    const b = points[i];
    const length = Math.hypot(b[0] - a[0], b[1] - a[1]);
    const pieces = Math.max(1, Math.ceil(length / step));
    for (let k = 1; k <= pieces; k += 1) {
      const f = k / pieces;
      out.push([a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f, (a[2] ?? 0) + ((b[2] ?? 0) - (a[2] ?? 0)) * f]);
    }
  }
  return out;
}

export function convexHull(points) {
  const pts = [...points].sort((a, b) => a[0] - b[0] || a[1] - b[1]);
  if (pts.length < 3) return pts;
  const cross = (o, a, b) => (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0]);
  const lower = [];
  for (const p of pts) {
    while (lower.length >= 2 && cross(lower[lower.length - 2], lower[lower.length - 1], p) <= 0) lower.pop();
    lower.push(p);
  }
  const upper = [];
  for (const p of pts.reverse()) {
    while (upper.length >= 2 && cross(upper[upper.length - 2], upper[upper.length - 1], p) <= 0) upper.pop();
    upper.push(p);
  }
  return lower.slice(0, -1).concat(upper.slice(0, -1));
}

export function centroid(points) {
  let x = 0;
  let y = 0;
  for (const p of points) { x += p[0]; y += p[1]; }
  return { x: x / points.length, y: y / points.length };
}

export function pointInPolygon(x, y, poly) {
  let inside = false;
  for (let i = 0, j = poly.length - 1; i < poly.length; j = i, i += 1) {
    const [xi, yi] = poly[i];
    const [xj, yj] = poly[j];
    if ((yi > y) !== (yj > y) && x < ((xj - xi) * (y - yi)) / ((yj - yi) || 1e-9) + xi) inside = !inside;
  }
  return inside;
}

// Nearest point of a closed polyline to (x, y): {x, y, d}.
export function nearestOnLoop(x, y, loop) {
  let best = { x: loop[0][0], y: loop[0][1], d: Infinity };
  for (let i = 0; i < loop.length; i += 1) {
    const [ax, ay] = loop[i];
    const [bx, by] = loop[(i + 1) % loop.length];
    const lengthSq = (bx - ax) ** 2 + (by - ay) ** 2 || 1;
    const f = clamp(((x - ax) * (bx - ax) + (y - ay) * (by - ay)) / lengthSq, 0, 1);
    const px = ax + (bx - ax) * f;
    const py = ay + (by - ay) * f;
    const d = Math.hypot(x - px, y - py);
    if (d < best.d) best = { x: px, y: py, d };
  }
  return best;
}

export function distanceToPolyline(x, y, path) {
  let best = { d: Infinity, z: 0, f: 0, i: 0 };
  for (let i = 1; i < path.length; i += 1) {
    const [ax, ay, az] = path[i - 1];
    const [bx, by, bz] = path[i];
    const lengthSq = (bx - ax) ** 2 + (by - ay) ** 2 || 1;
    const f = clamp(((x - ax) * (bx - ax) + (y - ay) * (by - ay)) / lengthSq, 0, 1);
    const d = Math.hypot(x - (ax + (bx - ax) * f), y - (ay + (by - ay) * f));
    if (d < best.d) best = { d, z: (az ?? 0) + ((bz ?? 0) - (az ?? 0)) * f, f, i };
  }
  return best;
}
