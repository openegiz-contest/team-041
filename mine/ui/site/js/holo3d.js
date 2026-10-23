// The hologram look of the 3D view: the mine as a holographic twin on a dark
// navy-to-teal stage. The ground is a dark floor with a faint cyan grid that
// melts into a glow on the horizon; the pit's terraces are dark teal with a
// glowing line along every crest and toe; roads are dark lanes with lit
// edges; machines and the plant are translucent cyan glass with a bright rim
// where their faces turn away from the eye and lit edges, tinted by their
// state (cyan normal, amber attention, red alarm). No sun, sky or weather
// light: weather shows only as particles.
//
// Everything here is cheap in a browser that renders WebGL in software: no
// post-processing pass; the glow is the rim term of the shaders, additive
// lines and soft spots on the floor. The edges of the glass are drawn by the
// same shader from a per-vertex attribute (`bary`), so they cost no draw call.
// Without WebGL (the 2D-canvas stand-in) the same materials are plain colours.

import * as THREE from 'three';

// Colours of the look (sRGB).
export const HOLO = {
  top: '#03070d',
  horizon: '#0d3c4d',
  below: '#04121a',
  fog: '#08222e',
  floor: '#06141e',
  wall: '#0f475d',
  flat: '#08222f',
  rim: '#41d9ff',
  grid: '#1cb7e2',
  crest: '#55e6ff',
  toe: '#167d9b',
  work: '#c3faff',
  lane: '#27c4e4',
  glass: '#3cd6ff',
  amber: '#ffae3a',
  red: '#ff4658',
  dim: '#4d8494',
  select: '#f0fdff',
  link: '#9ff0ff',
  ok: '#57ff95',
  load: '#a8fbff',
};

const lin = (hex) => new THREE.Color(hex);

// The hologram's colour of a machine or a unit in a state.
export function holoTone(state) {
  switch (state) {
    case 'down': case 'stopped': return 'bad';
    case 'queue_load': case 'queue_dump': case 'blocked': case 'starved': case 'overload': case 'starting': return 'warn';
    case 'idle': case 'standby': case 'service': case 'fuel': case 'fuelling': case 'paused': case undefined: case null: case '': return 'dim';
    default: return 'ok';
  }
}

// -- shaders ---------------------------------------------------------------------------------------

const FOG_UNIFORMS = () => ({ uFog: { value: lin(HOLO.fog) }, uFogNear: { value: 4000 }, uFogFar: { value: 20000 } });

// Opaque ground-like surfaces: a colour for walls, one for flats, a soft key
// from the south-west, a rim glow at grazing angles and, on the floor, a grid.
const SURFACE_VERTEX = `
  #include <clipping_planes_pars_vertex>
  varying vec3 vW; varying vec3 vN; varying vec3 vV;
  void main() {
    vec4 w = modelMatrix * vec4(position, 1.0);
    vW = w.xyz;
  #if NUM_CLIPPING_PLANES > 0
    vClipPosition = -(viewMatrix * w).xyz;
  #endif
    vN = normalize(mat3(modelMatrix) * normal);
    vV = cameraPosition - w.xyz;
    gl_Position = projectionMatrix * viewMatrix * w;
  }`;
const SURFACE_FRAGMENT = `
  uniform vec3 uFlat; uniform vec3 uWall; uniform vec3 uRim; uniform float uRimK;
  uniform vec3 uGrid; uniform float uGridK; uniform float uGridStep; uniform vec3 uTint;
  uniform vec3 uFog; uniform float uFogNear; uniform float uFogFar;
  varying vec3 vW; varying vec3 vN; varying vec3 vV;
  #include <clipping_planes_pars_fragment>
  void main() {
    #include <clipping_planes_fragment>
    vec3 N = normalize(vN);
    if (!gl_FrontFacing) N = -N;
    float dist = length(vV);
    vec3 V = vV / max(dist, 0.001);
    float up = clamp(N.z, 0.0, 1.0);
    vec3 c = mix(uWall, uFlat, smoothstep(0.55, 0.93, up));
    float key = max(dot(N, vec3(-0.42, -0.55, 0.72)), 0.0);
    c *= 0.6 + 0.6 * key;
    float f = 1.0 - abs(dot(N, V));
    c += uRim * (f * f * f) * uRimK;
    // Faint scan lines across the steep faces, every 3 m.
    float steep = 1.0 - smoothstep(0.35, 0.6, up);
    float zf = fract(vW.z / 3.0);
    float scan = 1.0 - smoothstep(0.0, max(fwidth(vW.z / 3.0) * 1.2, 0.02), min(zf, 1.0 - zf));
    c += uRim * scan * steep * 0.05 * (1.0 - smoothstep(300.0, 900.0, dist));
  #ifdef GRID
    vec2 g = vW.xy / uGridStep;
    vec2 w = fwidth(g);
    vec2 l = abs(fract(g - 0.5) - 0.5) / max(w, vec2(1e-4));
    float minor = (1.0 - min(min(l.x, l.y), 1.0)) * (1.0 - smoothstep(0.06, 0.3, max(w.x, w.y)));
    vec2 g5 = g * 0.2; vec2 w5 = w * 0.2;
    vec2 l5 = abs(fract(g5 - 0.5) - 0.5) / max(w5, vec2(1e-4));
    float major = (1.0 - min(min(l5.x, l5.y), 1.0)) * (1.0 - smoothstep(0.08, 0.4, max(w5.x, w5.y)));
    float near = 1.0 - smoothstep(uGridStep * 30.0, uGridStep * 90.0, dist);
    c += uGrid * (minor * 0.1 * near + major * 0.3 * (1.0 - smoothstep(uGridStep * 60.0, uGridStep * 160.0, dist))) * uGridK * up;
  #endif
    c *= uTint;
    c = mix(c, uFog, smoothstep(uFogNear, uFogFar, dist));
    gl_FragColor = vec4(c, 1.0);
    #include <colorspace_fragment>
  }`;

// Translucent glass: shaded a little by its facing, a bright rim where it
// turns away, faint scan lines, lit edges (EDGES: from the `bary` attribute),
// tinted per instance (machines) or per vertex (`tint`: the plant's units).
const GLASS_VERTEX = `
  #ifdef EDGES
  attribute vec3 bary;
  varying vec3 vBary;
  #endif
  #ifdef USE_TINT
  attribute vec3 tint;
  #endif
  #ifdef USE_ALB
  attribute vec3 albedo;
  #endif
  varying vec3 vW; varying vec3 vN; varying vec3 vV; varying vec3 vTint; varying float vLum;
  #include <clipping_planes_pars_vertex>
  void main() {
    vec4 p = vec4(position, 1.0);
    vec3 n = normal;
  #ifdef USE_INSTANCING
    p = instanceMatrix * p;
    n = mat3(instanceMatrix) * n;
  #endif
    vec4 w = modelMatrix * p;
    vW = w.xyz;
  #if NUM_CLIPPING_PLANES > 0
    vClipPosition = -(viewMatrix * w).xyz;
  #endif
    vN = normalize(mat3(modelMatrix) * n);
    vV = cameraPosition - w.xyz;
    vTint = vec3(1.0);
  #ifdef USE_INSTANCING_COLOR
    vTint = instanceColor;
  #endif
  #ifdef USE_TINT
    vTint *= tint;
  #endif
    vLum = 0.5;
  #ifdef USE_ALB
    vLum = dot(albedo, vec3(0.299, 0.587, 0.114));
  #endif
  #ifdef EDGES
    vBary = bary;
  #endif
    gl_Position = projectionMatrix * viewMatrix * w;
  }`;
const GLASS_FRAGMENT = `
  uniform vec3 uColor; uniform float uOpacity; uniform float uEdgeK; uniform float uFresK; uniform float uGain;
  uniform vec3 uFog; uniform float uFogNear; uniform float uFogFar;
  #ifdef EDGES
  varying vec3 vBary;
  #endif
  varying vec3 vW; varying vec3 vN; varying vec3 vV; varying vec3 vTint; varying float vLum;
  #include <clipping_planes_pars_fragment>
  void main() {
    #include <clipping_planes_fragment>
    vec3 N = normalize(vN);
    if (!gl_FrontFacing) N = -N;
    float dist = length(vV);
    vec3 V = vV / max(dist, 0.001);
    float f = 1.0 - abs(dot(N, V));
    float fres = f * f;
    vec3 base = uColor * vTint;
    vec3 bright = mix(base, vec3(1.0), 0.3);
    float shade = 0.42 + 0.38 * clamp(N.z, 0.0, 1.0) + 0.25 * max(dot(N, vec3(-0.42, -0.55, 0.72)), 0.0);
    float lum = mix(0.3, 1.0, sqrt(clamp(vLum * 1.7, 0.0, 1.0)));
    vec3 c = base * shade * lum * uGain + bright * fres * uFresK;
    float a = uOpacity * (0.6 + 0.4 * lum) + fres * 0.42;
    c *= 0.88 + 0.12 * step(0.5, fract(vW.z * 0.6));
  #ifdef EDGES
    vec3 d = fwidth(vBary);
    vec3 e = smoothstep(vec3(0.0), d * 1.4, vBary);
    float edge = (1.0 - min(min(e.x, e.y), e.z)) * uEdgeK;
    c = mix(c, mix(base, vec3(1.0), 0.5) * 1.25, edge);
    a = max(a, edge * 0.95);
  #endif
    float fog = smoothstep(uFogNear, uFogFar, dist);
    c = mix(c, uFog, fog);
    gl_FragColor = vec4(c, clamp(a, 0.0, 1.0) * (1.0 - 0.7 * fog));
    #include <colorspace_fragment>
  }`;

const DOME_VERTEX = `
  varying vec3 vDir;
  void main() { vDir = position; gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); }`;
const DOME_FRAGMENT = `
  uniform vec3 uTop; uniform vec3 uHorizon; uniform vec3 uBelow;
  varying vec3 vDir;
  void main() {
    vec3 d = normalize(vDir);
    float z = d.z;
    vec3 c = z > 0.0 ? mix(uHorizon, uTop, pow(clamp(z * 2.2, 0.0, 1.0), 0.55)) : mix(uHorizon, uBelow, clamp(-z * 5.0, 0.0, 1.0));
    gl_FragColor = vec4(c, 1.0);
    #include <colorspace_fragment>
  }`;

function surface({ flat, wall, rim = HOLO.rim, rimK = 0.5, grid = false, gridK = 1, step = 50 }) {
  const material = new THREE.ShaderMaterial({
    uniforms: { uFlat: { value: lin(flat) }, uWall: { value: lin(wall) }, uRim: { value: lin(rim) }, uRimK: { value: rimK },
      uGrid: { value: lin(HOLO.grid) }, uGridK: { value: gridK }, uGridStep: { value: step }, uTint: { value: new THREE.Color(1, 1, 1) }, ...FOG_UNIFORMS() },
    vertexShader: SURFACE_VERTEX,
    fragmentShader: SURFACE_FRAGMENT,
    defines: grid ? { GRID: '' } : {},
    side: THREE.DoubleSide,
    clipping: true,          // clipped only when the renderer has clipping planes (the «Разрез» view)
  });
  material.userData.holo = 'surface';
  return material;
}

function glass({ color = HOLO.glass, opacity = 0.28, edges = false, tint = false, albedo = false, fres = 1.1, edgeK = 0.85, gain = 0.85, side = THREE.FrontSide, blending = THREE.NormalBlending, depthWrite = false }) {
  const defines = {};
  if (edges) defines.EDGES = '';
  if (tint) defines.USE_TINT = '';
  if (albedo) defines.USE_ALB = '';
  const material = new THREE.ShaderMaterial({
    uniforms: { uColor: { value: lin(color) }, uOpacity: { value: opacity }, uEdgeK: { value: edgeK }, uFresK: { value: fres }, uGain: { value: gain }, ...FOG_UNIFORMS() },
    vertexShader: GLASS_VERTEX,
    fragmentShader: GLASS_FRAGMENT,
    defines,
    clipping: true,
    transparent: true,
    depthWrite,
    side,
    blending,
  });
  material.userData.holo = 'glass';
  material.userData.needs = { edges, tint, albedo };
  return material;
}

// -- materials ------------------------------------------------------------------------------------

// The hologram's materials, by the role of the realistic material they stand
// in for. In the 2D-canvas stand-in they are plain colours (it knows no shaders).
export function createHoloMaterials(soft) {
  const hidden = new THREE.MeshBasicMaterial({ visible: false });
  if (soft) {
    const basic = (color, extra = {}) => new THREE.MeshBasicMaterial({ color: lin(color), vertexColors: true, side: THREE.DoubleSide, ...extra });
    const flatSee = (color, opacity) => new THREE.MeshBasicMaterial({ color: lin(color), transparent: true, opacity, depthWrite: false, side: THREE.DoubleSide });
    const see = (color, opacity) => new THREE.MeshBasicMaterial({ color: lin(color), vertexColors: true, transparent: true, opacity, depthWrite: false, side: THREE.DoubleSide });
    return {
      soft: true,
      hidden,
      ground: basic('#ffffff'), face: basic('#ffffff'), flat: basic('#ffffff'),
      dump: basic('#ffffff'), dumpTop: basic('#ffffff'), pad: basic('#ffffff'), muck: basic('#ffffff'),
      road: basic('#ffffff'), water: basic('#1d6a85', { vertexColors: false }),
      piles: see('#4ccff2', 0.55), plant: flatSee('#53dcff', 0.6), plantPlain: flatSee('#53dcff', 0.6), plantGlass: flatSee('#53dcff', 0.25),
      belt: new THREE.MeshBasicMaterial({ color: lin(HOLO.lane) }),
      machine: new THREE.MeshBasicMaterial({ color: lin('#ffffff') }),
      load: new THREE.MeshBasicMaterial({ color: lin(HOLO.load) }),
      barrier: new THREE.MeshBasicMaterial({ color: new THREE.Color(1.2, 1.2, 1.2) }),
      dome: null,
      all: [],
    };
  }
  const H = {
    soft: false,
    hidden,
    ground: surface({ flat: HOLO.floor, wall: '#0a2735', grid: true, gridK: 0.8, step: 50, rimK: 0.03 }),
    face: surface({ flat: HOLO.flat, wall: HOLO.wall, rimK: 0.28 }),
    flat: surface({ flat: HOLO.flat, wall: HOLO.wall, rimK: 0.07, grid: true, gridK: 0.7, step: 25 }),
    dump: surface({ flat: '#0c2e3d', wall: '#16566c', rimK: 0.4 }),
    dumpTop: surface({ flat: '#0c2e3d', wall: '#16566c', rimK: 0.25, grid: true, gridK: 0.5, step: 25 }),
    pad: surface({ flat: '#09222e', wall: '#0e3a4a', rimK: 0.05, grid: true, gridK: 0.5, step: 20 }),
    muck: surface({ flat: '#163f4c', wall: '#1a5363', rimK: 0.6 }),
    road: surface({ flat: '#0b3042', wall: '#0e3a4c', rimK: 0.05 }),
    water: surface({ flat: '#072a3d', wall: '#072a3d', rim: '#6fe2ff', rimK: 0.3 }),
    piles: glass({ color: '#4fd2f0', opacity: 0.3, fres: 1.0 }),
    plant: glass({ edges: true, tint: true, albedo: true, opacity: 0.4, color: '#ffffff', gain: 1.35 }),
    plantPlain: glass({ edges: true, albedo: true, opacity: 0.34, gain: 1.1 }),
    plantGlass: glass({ opacity: 0.1, fres: 0.8, side: THREE.DoubleSide }),
    belt: glass({ color: HOLO.lane, opacity: 0.85, fres: 0.3, gain: 1.2 }),
    machine: glass({ edges: true, albedo: true, opacity: 0.42, color: '#ffffff', fres: 1.0, gain: 1.15 }),
    load: glass({ color: '#ffffff', opacity: 0.62, fres: 0.9, gain: 1.35 }),
    barrier: new THREE.MeshBasicMaterial({ color: new THREE.Color(1.4, 1.4, 1.4), toneMapped: false }),
  };
  H.all = Object.values(H).filter((m) => m && m.isMaterial);
  return H;
}

// A road's own copy of the lane material (its tint follows the road's state).
export function roadMaterial(H) {
  if (H.soft) return H.road.clone();
  const material = H.road.clone();
  material.userData.holo = 'surface';
  return material;
}

// Glass for one-off marks (the blast dome).
export function markGlass(color, opacity) {
  return glass({ color, opacity, fres: 1.6, side: THREE.DoubleSide, blending: THREE.AdditiveBlending, gain: 0.6 });
}

// Keep the fade of every hologram material in step with the camera.
export function setFog(materials, near, far) {
  for (const material of materials) {
    const u = material.uniforms;
    if (!u?.uFogNear) continue;
    u.uFogNear.value = near;
    u.uFogFar.value = far;
  }
}

// The stage: dark at the zenith, a teal glow at the horizon.
export function createDome() {
  const material = new THREE.ShaderMaterial({
    uniforms: { uTop: { value: lin(HOLO.top) }, uHorizon: { value: lin(HOLO.horizon) }, uBelow: { value: lin(HOLO.below) } },
    vertexShader: DOME_VERTEX,
    fragmentShader: DOME_FRAGMENT,
    side: THREE.BackSide,
    depthWrite: false,
  });
  const dome = new THREE.Mesh(new THREE.SphereGeometry(1, 32, 16), material);
  dome.renderOrder = 10;
  dome.frustumCulled = false;
  dome.userData.layer = -1;
  return dome;
}

// -- geometry the shaders need ----------------------------------------------------------------------

// Normals (from the builder's own), lit edges and the albedo, added once to a
// geometry the first time a hologram material is put on it.
export function prepare(geometry, material) {
  const needs = material?.userData?.needs ?? {};
  if (material?.isShaderMaterial && !geometry.attributes.normal) {
    if (geometry.userData.normals) geometry.setAttribute('normal', new THREE.BufferAttribute(geometry.userData.normals, 3));
    else geometry.computeVertexNormals();
  }
  if (needs.edges && !geometry.attributes.bary) geometry.setAttribute('bary', edgeAttribute(geometry));
  if (needs.albedo && !geometry.attributes.albedo) {
    if (geometry.userData.albedo) geometry.setAttribute('albedo', new THREE.BufferAttribute(geometry.userData.albedo, 3));
    else if (geometry.attributes.color) geometry.setAttribute('albedo', geometry.attributes.color);
    else geometry.setAttribute('albedo', new THREE.BufferAttribute(new Float32Array(geometry.attributes.position.count * 3).fill(0.5), 3));
  }
  if (needs.tint && !geometry.attributes.tint) {
    geometry.setAttribute('tint', new THREE.BufferAttribute(new Float32Array(geometry.attributes.position.count * 3).fill(1), 3));
  }
}

// Barycentric corners of each triangle, with the edges inside a flat face
// (the diagonal of a quad, the facets of a round tank) pushed out of reach,
// so the shader lights only the real edges of the shape: where the surface
// folds by more than `degrees`, or ends.
export function edgeAttribute(geometry, degrees = 32) {
  const position = geometry.attributes.position.array;
  const count = geometry.attributes.position.count;
  const out = new Float32Array(count * 3);
  if (geometry.index || count % 3) {
    for (let i = 0; i < count; i += 1) out[i * 3 + (i % 3)] = 1;
    return new THREE.BufferAttribute(out, 3);
  }
  const faces = count / 3;
  // One id per distinct corner (to 1 cm).
  const ids = new Map();
  const vid = new Int32Array(count);
  let next = 0;
  for (let i = 0; i < count; i += 1) {
    const key = `${Math.round(position[i * 3] * 100)},${Math.round(position[i * 3 + 1] * 100)},${Math.round(position[i * 3 + 2] * 100)}`;
    let id = ids.get(key);
    if (id === undefined) { id = next; next += 1; ids.set(key, id); }
    vid[i] = id;
  }
  const normals = new Float32Array(faces * 3);
  for (let f = 0; f < faces; f += 1) {
    const a = f * 9;
    const ux = position[a + 3] - position[a]; const uy = position[a + 4] - position[a + 1]; const uz = position[a + 5] - position[a + 2];
    const vx = position[a + 6] - position[a]; const vy = position[a + 7] - position[a + 1]; const vz = position[a + 8] - position[a + 2];
    let nx = uy * vz - uz * vy; let ny = uz * vx - ux * vz; let nz = ux * vy - uy * vx;
    const len = Math.hypot(nx, ny, nz) || 1;
    nx /= len; ny /= len; nz /= len;
    normals[f * 3] = nx; normals[f * 3 + 1] = ny; normals[f * 3 + 2] = nz;
  }
  const cos = Math.cos((degrees * Math.PI) / 180);
  const hidden = new Uint8Array(count);
  const seen = new Map();          // edge -> face * 3 + the corner opposite it
  const SPAN = 67108864;
  for (let f = 0; f < faces; f += 1) {
    for (let e = 0; e < 3; e += 1) {
      const p = vid[f * 3 + ((e + 1) % 3)];
      const q = vid[f * 3 + ((e + 2) % 3)];
      if (p === q) { hidden[f * 3 + e] = 1; continue; }
      const key = p < q ? p * SPAN + q : q * SPAN + p;
      const other = seen.get(key);
      if (other === undefined) { seen.set(key, f * 3 + e); continue; }
      if (other < 0) continue;
      const g = Math.floor(other / 3);
      // The builder winds every face outwards: neighbours in one plane have the same normal.
      const dot = normals[f * 3] * normals[g * 3] + normals[f * 3 + 1] * normals[g * 3 + 1] + normals[f * 3 + 2] * normals[g * 3 + 2];
      if (dot > cos) { hidden[f * 3 + e] = 1; hidden[other] = 1; }
      seen.set(key, -1);
    }
  }
  for (let f = 0; f < faces; f += 1) {
    for (let v = 0; v < 3; v += 1) {
      const i = f * 3 + v;
      for (let c = 0; c < 3; c += 1) out[i * 3 + c] = (v === c ? 1 : 0) + hidden[f * 3 + c];
    }
  }
  return new THREE.BufferAttribute(out, 3);
}

// -- lines: bench crests and toes, road lanes ----------------------------------------------------------

export function lineMaterial(opacity = 1) {
  return new THREE.LineBasicMaterial({ vertexColors: true, transparent: true, opacity, depthWrite: false, toneMapped: false, fog: false, blending: THREE.AdditiveBlending });
}

// Glowing contour lines of the pit: every crest (the top edge of a face)
// bright, every toe dim; the benches being worked brighter still.
// `loops` from the pit model, `zs` the heights of their points (ramps cut in),
// `isWorked(z)` whether a bench at that height has a machine at work.
export function contourLines(pit, zs, L, isWorked, lift = 0.7) {
  const crest = new Set();
  const toe = new Set();
  for (const band of pit.bands) {
    if (band.type !== 'face') continue;
    crest.add(band.outer);
    toe.add(band.inner);
  }
  const positions = [];
  const loopsOf = [];
  pit.loops.forEach((loop, i) => {
    if (!crest.has(i) && !toe.has(i)) return;
    const pts = loop.pts;
    const from = positions.length / 3;
    for (let k = 0; k < pts.length; k += 1) {
      const a = pts[k];
      const b = pts[(k + 1) % pts.length];
      const za = (zs?.[i]?.[k] ?? loop.z) + lift;
      const zb = (zs?.[i]?.[(k + 1) % pts.length] ?? loop.z) + lift;
      positions.push(...L(a[0], a[1], za), ...L(b[0], b[1], zb));
    }
    loopsOf.push({ from, count: positions.length / 3 - from, z: loop.z, crest: crest.has(i) });
  });
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3));
  geometry.setAttribute('color', new THREE.Float32BufferAttribute(new Float32Array(positions.length), 3));
  const lines = new THREE.LineSegments(geometry, lineMaterial(0.95));
  lines.userData.layer = 3;
  lines.renderOrder = 2;
  lines.frustumCulled = false;
  let key = '';
  const crestC = lin(HOLO.crest); const toeC = lin(HOLO.toe); const workC = lin(HOLO.work);
  // Colour the loops again when the worked benches change.
  lines.userData.recolour = () => {
    const worked = loopsOf.map((loop) => (isWorked(loop.z) ? 1 : 0));
    const next = worked.join('');
    if (next === key) return false;
    key = next;
    const colors = geometry.attributes.color.array;
    loopsOf.forEach((loop, i) => {
      const c = worked[i] ? workC : loop.crest ? crestC : toeC;
      const k = worked[i] ? 1.1 : loop.crest ? 0.6 : 0.4;
      for (let v = loop.from; v < loop.from + loop.count; v += 1) {
        colors[v * 3] = c.r * k; colors[v * 3 + 1] = c.g * k; colors[v * 3 + 2] = c.b * k;
      }
    });
    geometry.attributes.color.needsUpdate = true;
    return true;
  };
  lines.userData.recolour();
  return lines;
}

// Lit edges and a dashed middle line along every road; `roads` is
// [{id, points: [[x, y, z]], width}] in mine coordinates.
export function laneLines(roads, L, lift = 0.9) {
  const positions = [];
  const ranges = [];
  for (const road of roads) {
    const pts = road.points;
    if (pts.length < 2) continue;
    const from = positions.length / 3;
    const n = pts.length;
    const side = (i, s) => {
      const p = pts[i];
      const q = pts[Math.min(n - 1, i + 1)];
      const o = pts[Math.max(0, i - 1)];
      const dx = q[0] - o[0]; const dy = q[1] - o[1];
      const len = Math.hypot(dx, dy) || 1;
      return L(p[0] - (dy / len) * s, p[1] + (dx / len) * s, p[2] + lift);
    };
    const half = road.width / 2 - 0.6;
    for (let i = 0; i < n - 1; i += 1) {
      positions.push(...side(i, half), ...side(i + 1, half));
      positions.push(...side(i, -half), ...side(i + 1, -half));
    }
    const mid = from + (positions.length / 3 - from);
    // A dashed line down the middle: one dash every other step of the road.
    let run = 0;
    for (let i = 0; i < n - 1; i += 1) {
      const step = Math.hypot(pts[i + 1][0] - pts[i][0], pts[i + 1][1] - pts[i][1]);
      if (Math.floor(run / 18) % 2 === 0) positions.push(...side(i, 0), ...side(i + 1, 0));
      run += step;
    }
    ranges.push({ id: road.id, from, edges: mid - from, count: positions.length / 3 - from });
  }
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3));
  geometry.setAttribute('color', new THREE.Float32BufferAttribute(new Float32Array(positions.length), 3));
  const lines = new THREE.LineSegments(geometry, lineMaterial(0.9));
  lines.userData.layer = 3;
  lines.renderOrder = 2;
  lines.frustumCulled = false;
  // Colour each road by its state: {id: '#hex'}.
  lines.userData.recolour = (colorOf) => {
    const colors = geometry.attributes.color.array;
    const c = new THREE.Color();
    for (const range of ranges) {
      c.set(colorOf(range.id));
      for (let v = range.from; v < range.from + range.count; v += 1) {
        const k = v < range.from + range.edges ? 1 : 0.55;
        colors[v * 3] = c.r * k; colors[v * 3 + 1] = c.g * k; colors[v * 3 + 2] = c.b * k;
      }
    }
    geometry.attributes.color.needsUpdate = true;
  };
  return lines;
}

// -- the 2D-canvas stand-in: colours from the normals, no shaders ----------------------------------------

// Vertex colours of a lit geometry in the hologram palette (the stand-in
// multiplies them by its plain material colour).
export function holoRelight(geometry, kind = 'ground') {
  const normals = geometry.userData.normals;
  const colors = geometry.attributes.color?.array;
  if (!normals || !colors) return;
  const flat = lin(kind === 'water' ? '#0a3a55' : kind === 'dump' ? '#0e3444' : HOLO.flat);
  const floor = lin(HOLO.floor);
  const wall = lin(kind === 'dump' ? '#155066' : HOLO.wall);
  const upC = kind === 'ground' ? floor : flat;
  for (let i = 0; i < colors.length; i += 3) {
    const nz = Math.abs(normals[i + 2]);
    const key = Math.max(0, -0.42 * normals[i] - 0.55 * normals[i + 1] + 0.72 * nz);
    const t = Math.min(1, Math.max(0, (nz - 0.55) / 0.38));
    const k = 0.6 + 0.65 * key;
    colors[i] = (wall.r + (upC.r - wall.r) * t) * k;
    colors[i + 1] = (wall.g + (upC.g - wall.g) * t) * k;
    colors[i + 2] = (wall.b + (upC.b - wall.b) * t) * k;
  }
  geometry.attributes.color.needsUpdate = true;
}
