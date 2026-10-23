// «Разрез»: the site as a block cut out of the ground. The 3D view clips the
// world to a box round the pit and both plants (scene3d.js sets the clipping
// planes); this module builds the four vertical faces of that box, painted
// with the geology under the surface, and the lines along their edges. The
// front face runs along the pit, south of its axis, so the pit is seen in
// section.
//
// The geology is the simulator's own rule (mine/model/geology.py, params.yaml
// group `clay`): kaolinisation fades with depth below the pit's rim as
// exp(−depth / 60 m), grows towards the west end of the pit and follows zones
// of a smooth random field; ore with at least 15 % clay is clay ore. The page
// does not know the simulator's random field, so it uses one of the same kind
// (plane waves 500–1500 m long) and pulls it towards the clay content the
// block twins report where they lie. The copper ore body is a schematic lens
// under the pit (the pit is cut round it); the blocks' classes check it.
//
// Built once, lazily, when «Разрез» is first pressed.

import * as THREE from 'three';
import { byKind, first, feature } from './store.js';
import { fbm, noise2 } from './textures3d.js';

// params.yaml, group `clay` (the values the simulator uses).
const CLAY = { weatheringDepthM: 60, westBias: 0.15, zoneAmp: 0.25, kaolinFrom: 0.4, kaolinFull: 0.7,
  backgroundPct: 3, kaolinPct: 40, clayOreMinPct: 15 };
// params.yaml, group `geology`: correlation length 250 m, waves 2–6 of it.
const WAVE_MIN_M = 500;
const WAVE_MAX_M = 1500;

// Colours of the faces and of the legend (sRGB).
export const CUT_COLORS = {
  clayOre: '#e8b64c',
  kaolin: '#ddd2b4',
  ore: '#b8623a',
  waste: '#7a736b',
  wasteDeep: '#5f5955',
};

const hex = (value) => [1, 3, 5].map((i) => parseInt(value.slice(i, i + 2), 16) / 255);   // sRGB components 0..1
const smooth = (e0, e1, x) => { const t = Math.min(1, Math.max(0, (x - e0) / (e1 - e0))); return t * t * (3 - 2 * t); };
const mixc = (a, b, t) => [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t];

// A smooth field of unit variance: plane waves of random direction, length and phase (as geology.py's Field).
function waveField(seed) {
  let s = seed >>> 0;
  const rand = () => { s = (s + 0x6d2b79f5) >>> 0; let t = s; t = Math.imul(t ^ (t >>> 15), t | 1); t ^= t + Math.imul(t ^ (t >>> 7), t | 61); return ((t ^ (t >>> 14)) >>> 0) / 4294967296; };
  const waves = [];
  for (let i = 0; i < 24; i += 1) {
    const direction = rand() * Math.PI;
    const k = (2 * Math.PI) / (WAVE_MIN_M + rand() * (WAVE_MAX_M - WAVE_MIN_M));
    waves.push([k * Math.cos(direction), k * Math.sin(direction), rand() * 2 * Math.PI]);
  }
  const scale = Math.sqrt(2 / waves.length);
  return (x, y) => { let v = 0; for (const [kx, ky, p] of waves) v += Math.cos(kx * x + ky * y + p); return v * scale; };
}

// The geology of the mine grid, from the pit twin and the block twins.
export function createGeology() {
  const pit = first('pit');
  const rim = pit?.attributes?.geo?.polygon ?? [[-2000, -400], [2000, -400], [2000, 400], [-2000, 400]];
  const spec = pit?.attributes?.spec ?? {};
  const xs = rim.map((p) => p[0]);
  const ys = rim.map((p) => p[1]);
  const cx = (Math.min(...xs) + Math.max(...xs)) / 2;
  const cy = (Math.min(...ys) + Math.max(...ys)) / 2;
  const halfLength = (Math.max(...xs) - Math.min(...xs)) / 2;
  const halfWidth = (Math.max(...ys) - Math.min(...ys)) / 2;
  const rimZ = spec.rim_elevation_m ?? pit?.attributes?.geo?.z ?? 230;
  const floorZ = spec.floor_elevation_m ?? rimZ - 225;
  const field = waveField(77);

  const westOf = (x) => Math.min(1, Math.max(0, (cx - x) / halfLength));
  const index0 = (x, z) => Math.exp(-(rimZ - z) / CLAY.weatheringDepthM) + CLAY.westBias * westOf(x);

  // What each block twin says of the zone field where it lies (its clay share read back through the rule).
  const samples = [];
  const blocks = byKind('block').map((block) => {
    const s = block.attributes?.spec ?? {};
    const x = s.centre_x ?? block.attributes?.geo?.x;
    const y = s.centre_y ?? block.attributes?.geo?.y;
    const z = block.attributes?.geo?.z;
    const clay = Number(feature(block, 'clay_pct'));
    return { x, y, z, clay, material: feature(block, 'material') };
  }).filter((b) => Number.isFinite(b.x) && Number.isFinite(b.y) && Number.isFinite(b.z) && Number.isFinite(b.clay));
  for (const b of blocks) {
    const synthetic = field(b.x, b.y);
    const k = (b.clay - CLAY.backgroundPct) / (CLAY.kaolinPct - CLAY.backgroundPct);
    const zoneAt = (kk) => (CLAY.kaolinFrom + (CLAY.kaolinFull - CLAY.kaolinFrom) * kk - index0(b.x, b.z)) / CLAY.zoneAmp;
    let observed;
    if (b.clay >= CLAY.kaolinPct - 0.5) observed = Math.max(synthetic, zoneAt(1));          // fully kaolinised: at least this
    else if (b.clay <= 8) observed = Math.min(synthetic, zoneAt(0.05));                    // background clay: at most this
    else observed = zoneAt(k);
    samples.push({ x: b.x, y: b.y, r: observed - synthetic });
  }
  const R2 = 2 * 250 * 250;
  const zone = (x, y) => {
    let w = 0;
    let r = 0;
    for (const s of samples) {
      const d2 = (x - s.x) ** 2 + (y - s.y) ** 2;
      if (d2 > 9 * R2) continue;
      const k = Math.exp(-d2 / R2);
      w += k;
      r += k * s.r;
    }
    return field(x, y) + (w > 0 ? r / Math.max(1, w) : 0);
  };

  // Clay share (%) at a point, given the zone value of its column. The model has no blocks below the pit floor:
  // there the zones fade out over 100 m and only the fading with depth is left.
  const clayPct = (x, z, zoneValue) => {
    const index = index0(x, z) + CLAY.zoneAmp * zoneValue * smooth(floorZ - 100, floorZ, z);
    const k = Math.min(1, Math.max(0, (index - CLAY.kaolinFrom) / (CLAY.kaolinFull - CLAY.kaolinFrom)));
    return CLAY.backgroundPct + (CLAY.kaolinPct - CLAY.backgroundPct) * k;
  };

  // The ore body: a lens along the pit, its root below the floor; 1 inside its edge, 0 outside. It is drawn to
  // agree with the block twins: it reaches past the west end of the pit (clay ore on the west benches) and stops
  // short of the east end (waste there), and its axis runs from the south-west to the north-east (waste on the
  // south-west wall, ore on the north wall).
  const depthPit = Math.max(60, rimZ - floorZ);
  const rootM = depthPit * 1.7;
  const bodyX = cx - 0.2 * halfLength;
  const bodyWest = 1.08 * halfLength;
  const bodyEast = 1.03 * halfLength;
  const bodyB = 0.5 * halfWidth;
  const bodyAxisY = (x) => cy + 0.12 * (x - bodyX);
  const oreBody = (x, y, z) => {
    const d = rimZ - z;
    if (d > rootM) return 0;
    const taper = Math.sqrt(Math.max(0, 1 - (Math.max(0, d) / rootM) ** 2));
    const u = (x - bodyX) / ((x < bodyX ? bodyWest : bodyEast) * taper + 1e-6);
    const v = (y - bodyAxisY(x)) / (bodyB * (1 + 0.35 * Math.max(0, -u)) * taper + 1e-6);
    const wobble = 1 + 0.22 * (fbm(x / 420 + 7, z / 160, 41, 3) - 0.5) + 0.1 * (fbm(y / 300, z / 90, 43, 2) - 0.5);
    const r = (u * u + v * v) * wobble;
    return smooth(1.06, 0.94, r);
  };

  // How well the schematic agrees with the block twins: ore (sulphide, clay or low-grade) inside, waste outside.
  let bodyAgree = 0;
  for (const b of blocks) {
    const inside = oreBody(b.x, b.y, b.z - 7.5) > 0.5;
    if (inside === (b.material !== 'waste')) bodyAgree += 1;
  }

  return { cx, cy, halfLength, halfWidth, rimZ, floorZ, rootM, zone, clayPct, oreBody, bodyAxisY, blocks: blocks.length, bodyAgree };
}

// Paint one face: s along it (mine metres), z from `bottom` to `top`; `at(s)` gives the mine x, y of s.
function paintFace(geo, { s0, s1, bottom, top, at, pxPerM = 0.4, maxW = 2048 }) {
  const w = Math.max(64, Math.min(maxW, Math.round((s1 - s0) * pxPerM)));
  const h = Math.max(32, Math.min(512, Math.round((top - bottom) * pxPerM)));
  const canvas = document.createElement('canvas');
  canvas.width = w;
  canvas.height = h;
  const ctx = canvas.getContext('2d');
  const image = ctx.createImageData(w, h);
  const data = image.data;
  const C = Object.fromEntries(Object.entries(CUT_COLORS).map(([k, v]) => [k, hex(v)]));
  for (let i = 0; i < w; i += 1) {
    const s = s0 + ((i + 0.5) / w) * (s1 - s0);
    const [x, y] = at(s);
    const zoneValue = geo.zone(x, y);
    for (let j = 0; j < h; j += 1) {
      const z = top - ((j + 0.5) / h) * (top - bottom);
      const clay = geo.clayPct(x, z, zoneValue);
      const kaolin = smooth(CLAY.clayOreMinPct - 2.5, CLAY.clayOreMinPct + 2.5, clay);
      const body = geo.oreBody(x, y, z);
      // Waste: grey in gently dipping layers, darker with depth.
      const dip = z + s * 0.035 + 9 * (fbm(s / 700, 3, 51, 2) - 0.5);
      const band = 0.5 + 0.5 * Math.sin(dip / 23 + 2.2 * fbm(s / 380, z / 90, 53, 2));
      let waste = mixc(C.waste, C.wasteDeep, smooth(geo.rimZ - 140, geo.rimZ - 420, z) * 0.8 + band * 0.25);
      waste = mixc(waste, C.kaolin, kaolin);
      // Ore: copper sulphides, paler towards the edge of the body (low grade); kaolinised near the surface.
      let ore = mixc(C.ore, C.waste, (1 - smooth(0.5, 1, body)) * 0.35);
      ore = mixc(ore, C.clayOre, kaolin);
      let c = mixc(waste, ore, smooth(0.35, 0.65, body));
      // Grain: speckle and faint streaks, so the face reads as rock.
      const grain = 0.9 + 0.14 * fbm(s / 40, z / 14, 61, 3) + 0.05 * (noise2(s / 6, z / 4, 67) - 0.5);
      // A darker seam where one layer meets another (the base of the kaolinised zone, the edge of the ore body).
      const step = (top - bottom) / h * 2;
      const below = smooth(CLAY.clayOreMinPct - 2.5, CLAY.clayOreMinPct + 2.5, geo.clayPct(x, z - step, zoneValue));
      const seam = 1 - 0.3 * Math.min(1, Math.abs(kaolin - below) * 1.5 + Math.abs(smooth(0.35, 0.65, body) - smooth(0.35, 0.65, geo.oreBody(x, y, z - step))) * 1.5);
      c = [c[0] * grain * seam, c[1] * grain * seam, c[2] * grain * seam];
      const k = (j * w + i) * 4;
      data[k] = Math.min(255, c[0] * 255);
      data[k + 1] = Math.min(255, c[1] * 255);
      data[k + 2] = Math.min(255, c[2] * 255);
      data[k + 3] = 255;
    }
  }
  ctx.putImageData(image, 0, 0);
  const texture = new THREE.CanvasTexture(canvas);
  texture.colorSpace = THREE.SRGBColorSpace;
  texture.anisotropy = 4;
  texture.generateMipmaps = true;
  texture.minFilter = THREE.LinearMipmapLinearFilter;
  return texture;
}

// The upper edge of the world's surfaces where a vertical plane cuts them: the plane is `axis` = `value`
// (scene coordinates); returns [[s, z], ...] along the other horizontal axis from s0 to s1.
function profile(geometries, axis, value, s0, s1) {
  const a = axis === 'x' ? 0 : 1;
  const b = 1 - a;
  const segments = [];
  for (const geometry of geometries) {
    if (!geometry) continue;
    const pos = geometry.attributes.position.array;
    const index = geometry.index ? geometry.index.array : null;
    const count = index ? index.length : pos.length / 3;
    for (let t = 0; t < count; t += 3) {
      const ids = index ? [index[t], index[t + 1], index[t + 2]] : [t, t + 1, t + 2];
      const p = ids.map((i) => [pos[i * 3], pos[i * 3 + 1], pos[i * 3 + 2]]);
      const d = p.map((q) => q[a] - value);
      if ((d[0] > 0 && d[1] > 0 && d[2] > 0) || (d[0] < 0 && d[1] < 0 && d[2] < 0)) continue;
      const hits = [];
      for (let e = 0; e < 3; e += 1) {
        const i = e; const j = (e + 1) % 3;
        if ((d[i] <= 0 && d[j] > 0) || (d[i] > 0 && d[j] <= 0)) {
          const f = d[i] / (d[i] - d[j]);
          hits.push([p[i][b] + (p[j][b] - p[i][b]) * f, p[i][2] + (p[j][2] - p[i][2]) * f]);
        }
      }
      if (hits.length < 2) continue;
      const [h0, h1] = hits[0][0] <= hits[1][0] ? [hits[0], hits[1]] : [hits[1], hits[0]];
      if (h1[0] < s0 || h0[0] > s1 || h1[0] - h0[0] < 1e-6) continue;
      segments.push([h0[0], h0[1], h1[0], h1[1]]);
    }
  }
  // The upper envelope, at every end of a segment.
  const BUCKET = 25;
  const n = Math.ceil((s1 - s0) / BUCKET) + 1;
  const buckets = Array.from({ length: n }, () => []);
  for (const seg of segments) {
    const i0 = Math.max(0, Math.floor((seg[0] - s0) / BUCKET));
    const i1 = Math.min(n - 1, Math.floor((seg[2] - s0) / BUCKET));
    for (let i = i0; i <= i1; i += 1) buckets[i].push(seg);
  }
  const zAt = (s) => {
    const list = buckets[Math.min(n - 1, Math.max(0, Math.floor((s - s0) / BUCKET)))];
    let z = -Infinity;
    for (const seg of list) {
      if (s < seg[0] - 1e-6 || s > seg[2] + 1e-6) continue;
      const f = (s - seg[0]) / (seg[2] - seg[0]);
      z = Math.max(z, seg[1] + (seg[3] - seg[1]) * f);
    }
    return z;
  };
  const stops = new Set([s0, s1]);
  for (const seg of segments) {
    if (seg[0] > s0 && seg[0] < s1) stops.add(seg[0]);
    if (seg[2] > s0 && seg[2] < s1) stops.add(seg[2]);
  }
  for (let s = s0; s < s1; s += 50) stops.add(s);
  const out = [];
  let last = null;
  for (const s of [...stops].sort((p, q) => p - q)) {
    if (last !== null && s - last < 0.05) continue;
    const z = zAt(s);
    if (Number.isFinite(z)) out.push([s, z]);
    last = s;
  }
  // Drop points on a straight line.
  const kept = [];
  for (let i = 0; i < out.length; i += 1) {
    if (i > 0 && i < out.length - 1) {
      const [pa, pb, pc] = [kept[kept.length - 1], out[i], out[i + 1]];
      const f = (pb[0] - pa[0]) / ((pc[0] - pa[0]) || 1);
      if (Math.abs(pa[1] + (pc[1] - pa[1]) * f - pb[1]) < 0.02) continue;
    }
    kept.push(out[i]);
  }
  return kept;
}

const FACE_VERTEX = `
  varying vec2 vUv;
  void main() { vUv = uv; gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); }`;
const FACE_FRAGMENT = `
  uniform sampler2D uMap; uniform vec3 uTint;
  varying vec2 vUv;
  void main() {
    gl_FragColor = vec4(texture2D(uMap, vUv).rgb * uTint, 1.0);
    #include <colorspace_fragment>
  }`;
const LINE_VERTEX = `void main() { gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); }`;
const LINE_FRAGMENT = `uniform vec3 uColor; uniform float uOpacity; void main() { gl_FragColor = vec4(uColor, uOpacity);
  #include <colorspace_fragment>
  }`;

// Build the block: `box` in mine metres {minX, maxX, minY, maxY, bottom}; `surfaces` the geometries whose
// upper edge the faces follow (scene coordinates); `O` the scene's origin in the mine grid.
export function buildCutaway({ box, surfaces, O }) {
  const geo = createGeology();
  const group = new THREE.Group();
  group.name = 'cutaway';
  const faces = [];
  const textures = [];
  const materials = [];
  const edge = [];
  // Each face: the plane, the direction along it (so that it faces outwards), the light it gets.
  const FACES = [
    { name: 'south', axis: 'y', value: box.minY, from: box.minX, to: box.maxX, out: [0, -1], tint: 1.0 },
    { name: 'east', axis: 'x', value: box.maxX, from: box.minY, to: box.maxY, out: [1, 0], tint: 0.8 },
    { name: 'north', axis: 'y', value: box.maxY, from: box.maxX, to: box.minX, out: [0, 1], tint: 0.72 },
    { name: 'west', axis: 'x', value: box.minX, from: box.maxY, to: box.minY, out: [-1, 0], tint: 0.86 },
  ];
  let topMax = -Infinity;
  const profiles = FACES.map((f) => {
    const local = f.axis === 'x' ? f.value - O.x : f.value - O.y;
    const lo = Math.min(f.from, f.to) - (f.axis === 'x' ? O.y : O.x);
    const hi = Math.max(f.from, f.to) - (f.axis === 'x' ? O.y : O.x);
    const pts = profile(surfaces, f.axis, local, lo, hi).map(([s, z]) => [s + (f.axis === 'x' ? O.y : O.x), z + O.z]);
    for (const [, z] of pts) topMax = Math.max(topMax, z);
    return pts;
  });
  const top = topMax + 4;
  const bottom = box.bottom;
  const lineGeometry = [];
  FACES.forEach((f, n) => {
    let pts = profiles[n];
    if (pts.length < 2) pts = [[Math.min(f.from, f.to), geo.rimZ], [Math.max(f.from, f.to), geo.rimZ]];
    const s0 = Math.min(f.from, f.to);
    const s1 = Math.max(f.from, f.to);
    const at = (s) => (f.axis === 'x' ? [f.value, s] : [s, f.value]);
    const texture = paintFace(geo, { s0, s1, bottom, top, at });
    textures.push(texture);
    const position = [];
    const uv = [];
    const P = (s, z) => { const [x, y] = at(s); return [x - O.x, y - O.y, z - O.z]; };
    const U = (s, z) => [(s - s0) / (s1 - s0), (z - bottom) / (top - bottom)];
    // Wound so that the face looks outwards.
    const reverse = (f.axis === 'y' && f.out[1] > 0) || (f.axis === 'x' && f.out[0] < 0);
    for (let i = 0; i < pts.length - 1; i += 1) {
      const [sa, za] = pts[i];
      const [sb, zb] = pts[i + 1];
      const quad = [[sa, bottom], [sb, bottom], [sb, zb], [sa, za]];
      const tris = reverse ? [[0, 2, 1], [0, 3, 2]] : [[0, 1, 2], [0, 2, 3]];
      for (const tri of tris) for (const k of tri) { position.push(...P(...quad[k])); uv.push(...U(...quad[k])); }
    }
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute('position', new THREE.Float32BufferAttribute(position, 3));
    geometry.setAttribute('uv', new THREE.Float32BufferAttribute(uv, 2));
    geometry.computeBoundingSphere();
    const material = new THREE.ShaderMaterial({
      uniforms: { uMap: { value: texture }, uTint: { value: new THREE.Color(f.tint, f.tint, f.tint) } },
      vertexShader: FACE_VERTEX,
      fragmentShader: FACE_FRAGMENT,
      side: THREE.FrontSide,
      polygonOffset: true,
      polygonOffsetFactor: 1,
      polygonOffsetUnits: 2,
    });
    material.userData.tint = f.tint;
    materials.push(material);
    const mesh = new THREE.Mesh(geometry, material);
    mesh.userData.cutFace = f.name;
    group.add(mesh);
    faces.push(mesh);
    // Edges: the upper edge along the surface, the bottom, the corner.
    for (let i = 0; i < pts.length - 1; i += 1) lineGeometry.push(...P(...pts[i]), ...P(...pts[i + 1]));
    lineGeometry.push(...P(s0, bottom), ...P(s1, bottom));
    const corner = f.to > f.from ? pts[pts.length - 1] : pts[0];
    const cornerS = f.to > f.from ? s1 : s0;
    lineGeometry.push(...P(cornerS, bottom), ...P(cornerS, corner[1]));
  });
  const lines = new THREE.BufferGeometry();
  lines.setAttribute('position', new THREE.Float32BufferAttribute(lineGeometry, 3));
  const lineMaterial = new THREE.ShaderMaterial({
    uniforms: { uColor: { value: new THREE.Color('#f4efe6') }, uOpacity: { value: 0.75 } },
    vertexShader: LINE_VERTEX,
    fragmentShader: LINE_FRAGMENT,
    transparent: true,
    depthWrite: false,
  });
  materials.push(lineMaterial);
  const edges = new THREE.LineSegments(lines, lineMaterial);
  edges.renderOrder = 2;
  group.add(edges);
  edge.push(edges);

  // Planes that keep what lies inside the box (scene coordinates).
  const planes = [
    new THREE.Plane(new THREE.Vector3(1, 0, 0), -(box.minX - O.x)),
    new THREE.Plane(new THREE.Vector3(-1, 0, 0), box.maxX - O.x),
    new THREE.Plane(new THREE.Vector3(0, 1, 0), -(box.minY - O.y)),
    new THREE.Plane(new THREE.Vector3(0, -1, 0), box.maxY - O.y),
  ];

  // The look: `hologram` true or false.
  function setLook(hologram) {
    for (const material of materials) {
      if (material === lineMaterial) {
        material.uniforms.uColor.value.set(hologram ? '#55e6ff' : '#f4efe6');
        material.uniforms.uOpacity.value = hologram ? 0.95 : 0.7;
        material.blending = hologram ? THREE.AdditiveBlending : THREE.NormalBlending;
        continue;
      }
      const t = material.userData.tint * (hologram ? 0.92 : 1);
      material.uniforms.uTint.value.setRGB(t, t * (hologram ? 1.02 : 1), t * (hologram ? 1.06 : 1));
    }
  }

  function dispose() {
    group.traverse((node) => { node.geometry?.dispose(); });
    materials.forEach((m) => m.dispose());
    textures.forEach((t) => t.dispose());
  }

  // Where the layers are named on the front face (mine metres): the middle of the thickest run of each down a
  // column of the face, from the geology itself.
  const labels = [];
  {
    const front = profiles[0];
    const surfaceAt = (x) => {
      for (let i = 0; i < front.length - 1; i += 1) if (x >= front[i][0] && x <= front[i + 1][0]) {
        const f = (x - front[i][0]) / ((front[i + 1][0] - front[i][0]) || 1);
        return front[i][1] + (front[i + 1][1] - front[i][1]) * f;
      }
      return geo.rimZ;
    };
    const y = box.minY;
    const best = {};
    const span = box.maxX - box.minX;
    for (let x = box.minX + span * 0.04; x < box.maxX - span * 0.04; x += 30) {
      const zone = geo.zone(x, y);
      const zs = surfaceAt(x);
      let run = null;
      const close = (z) => {
        if (run && (!best[run.key] || run.top - z > best[run.key].thick)) best[run.key] = { x, z: (run.top + z) / 2, thick: run.top - z };
        run = null;
      };
      for (let z = zs - 2; z > bottom + 10; z -= 4) {
        const ore = geo.oreBody(x, y, z) > 0.6;
        const clay = geo.clayPct(x, z, zone) >= CLAY.clayOreMinPct;
        const key = ore ? (clay ? 'clay' : 'ore') : (clay ? 'kaolin' : 'waste');
        if (run?.key !== key) { close(z); if (key) run = { key, top: z }; }
      }
      close(bottom + 10);
    }
    for (const key of ['clay', 'kaolin', 'ore', 'waste']) if (best[key] && best[key].thick > 12) labels.push({ key, x: best[key].x, y, z: best[key].z });
  }

  return { group, faces, planes, setLook, dispose, geo, labels, top, bottom };
}
