// Sky, light and air of the 3D view: where the sun stands at the mine's
// simulated time (Bozshakol, 51.847° N 74.279° E, UTC+5), the colours of the sky,
// of the sunlight and of the haze for that sun, a sky dome with the sun or
// the moon and stars, rain and snow when the weather twin says so, and dust:
// behind trucks on the haul roads and over a blast.

import * as THREE from 'three';
import { clamp, mix, scale } from './geom3d.js';
import { textures } from './textures3d.js';

const LAT = (51.847 * Math.PI) / 180;
const LON = 74.279;
const UTC_OFFSET = 5;

// Direction to the sun (x east, y north, z up) and its elevation in degrees,
// for a simulated time given as local clock time "YYYY-MM-DDTHH:MM:SS".
export function sunAt(iso) {
  const match = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})/.exec(String(iso ?? ''));
  if (!match) return { dir: new THREE.Vector3(-0.45, -0.62, 0.64).normalize(), elevation: 40 };
  const [, y, m, d, hh, mm] = match.map(Number);
  const start = Date.UTC(y, 0, 1);
  const day = Math.floor((Date.UTC(y, m - 1, d) - start) / 86400000) + 1;
  const hours = hh + mm / 60;
  const decl = ((23.44 * Math.PI) / 180) * Math.sin((2 * Math.PI * (284 + day)) / 365);
  const B = (2 * Math.PI * (day - 81)) / 364;
  const eot = 9.87 * Math.sin(2 * B) - 7.53 * Math.cos(B) - 1.5 * Math.sin(B);
  const solar = hours - UTC_OFFSET + LON / 15 + eot / 60;
  const ha = ((solar - 12) * 15 * Math.PI) / 180;
  const sinEl = Math.sin(LAT) * Math.sin(decl) + Math.cos(LAT) * Math.cos(decl) * Math.cos(ha);
  const el = Math.asin(clamp(sinEl, -1, 1));
  // Azimuth from the south, positive towards the west.
  const az = Math.atan2(Math.sin(ha), Math.cos(ha) * Math.sin(LAT) - Math.tan(decl) * Math.cos(LAT));
  const fromNorth = Math.PI + az;
  const dir = new THREE.Vector3(Math.sin(fromNorth) * Math.cos(el), Math.cos(fromNorth) * Math.cos(el), Math.sin(el));
  return { dir, elevation: (el * 180) / Math.PI, hours };
}

// Colours for a sun elevation (degrees), linear RGB in the scene's own
// units (the renderer maps them to the screen with ACES and an exposure that
// follows the light, as a camera would). Keyframes from deep night through
// twilight and sunset to the midday steppe. `sun` is the key light: the sun
// by day, the moon by night (cool and weak); `sky` lights from above,
// `ground` is bounced back from below; `exposure` adapts to the light.
const KEYS = [
  { e: -18, zenith: '#0a1430', horizon: '#20325a', haze: '#1b2945', sun: [0.15, 0.2, 0.33], sky: [0.085, 0.11, 0.19], ground: [0.035, 0.035, 0.045], exposure: 1.85 },
  { e: -9, zenith: '#10214a', horizon: '#33466f', haze: '#2a3a5a', sun: [0.15, 0.2, 0.32], sky: [0.1, 0.125, 0.2], ground: [0.04, 0.04, 0.05], exposure: 1.75 },
  { e: -3, zenith: '#24406f', horizon: '#b27f70', haze: '#6f6678', sun: [0.06, 0.07, 0.1], sky: [0.22, 0.23, 0.31], ground: [0.07, 0.06, 0.07], exposure: 1.45 },
  { e: 2, zenith: '#3b6299', horizon: '#f2ac72', haze: '#cfa688', sun: [1.7, 0.98, 0.52], sky: [0.36, 0.37, 0.46], ground: [0.13, 0.1, 0.07], exposure: 1.02 },
  { e: 10, zenith: '#4479b8', horizon: '#ecd3ab', haze: '#d8c9b0', sun: [2.25, 1.85, 1.35], sky: [0.44, 0.49, 0.62], ground: [0.21, 0.17, 0.12], exposure: 0.82 },
  { e: 25, zenith: '#3d79c2', horizon: '#c9dae6', haze: '#cdd5d6', sun: [2.45, 2.25, 1.95], sky: [0.5, 0.58, 0.74], ground: [0.25, 0.21, 0.15], exposure: 0.66 },
  { e: 60, zenith: '#3672bf', horizon: '#c2d6e4', haze: '#c8d2d6', sun: [2.55, 2.4, 2.15], sky: [0.52, 0.6, 0.76], ground: [0.26, 0.22, 0.16], exposure: 0.62 },
];

const hexLinear = (hex) => { const c = new THREE.Color(hex); return [c.r, c.g, c.b]; };
const KEYS_LINEAR = KEYS.map((key) => ({ ...key, zenith: hexLinear(key.zenith), horizon: hexLinear(key.horizon), haze: hexLinear(key.haze) }));

export function palette(elevation, weather = 'dry') {
  let i = 0;
  while (i < KEYS_LINEAR.length - 2 && elevation > KEYS_LINEAR[i + 1].e) i += 1;
  const a = KEYS_LINEAR[i];
  const b = KEYS_LINEAR[i + 1];
  const t = clamp((elevation - a.e) / (b.e - a.e), 0, 1);
  const out = {};
  for (const key of ['zenith', 'horizon', 'haze', 'sun', 'sky', 'ground']) out[key] = mix(a[key], b[key], t);
  out.exposure = a.exposure + (b.exposure - a.exposure) * t;
  // Overcast: no direct sun, a grey sky, more light from all of it.
  if (weather === 'rain' || weather === 'snow') {
    const grey = weather === 'snow' ? 0.62 : 0.5;
    const level = clamp((elevation + 6) / 30, 0.05, 1);
    const overcast = [grey * level, (grey + 0.02) * level, (grey + 0.05) * level];
    out.zenith = mix(out.zenith, scale(overcast, 0.8), 0.85);
    out.horizon = mix(out.horizon, overcast, 0.85);
    out.haze = mix(out.haze, overcast, 0.9);
    out.sky = mix(out.sky, scale(out.sky, 1.5), 0.6);
    out.sun = scale(out.sun, 0.15);
    out.exposure *= 1.1;
  }
  out.night = clamp((-elevation - 2) / 10, 0, 1);
  out.dusk = clamp(1 - Math.abs(elevation - 1) / 9, 0, 1);
  // The site's lights: floodlights, windows, headlights; on from late dusk.
  out.lamps = clamp((4 - elevation) / 8, 0, 1);
  return out;
}

// Direction to the key light: the sun while it is up, the moon (opposite
// the sun, high in the south-ish sky) once it is down.
export function keyDirection(sunDir, elevation) {
  if (elevation > -1.5) return sunDir.z > 0.03 ? sunDir.clone() : new THREE.Vector3(sunDir.x, sunDir.y, 0.03).normalize();
  const h = Math.hypot(sunDir.x, sunDir.y) || 1;
  return new THREE.Vector3((-sunDir.x / h) * 0.72, (-sunDir.y / h) * 0.72, 0.69).normalize();
}

// -- sky dome -------------------------------------------------------------------

export function createSky() {
  const geometry = new THREE.SphereGeometry(1, 32, 16);
  const count = geometry.attributes.position.count;
  geometry.setAttribute('color', new THREE.Float32BufferAttribute(new Float32Array(count * 3), 3));
  // Drawn after the opaque world, where nothing else was drawn (cheaper on a software renderer).
  const material = new THREE.MeshBasicMaterial({ vertexColors: true, side: THREE.BackSide, depthWrite: false, depthTest: true, fog: false });
  const dome = new THREE.Mesh(geometry, material);
  dome.renderOrder = 10;
  dome.frustumCulled = false;
  dome.userData.layer = -1;

  // Stars: fixed points on the dome, shown at night.
  const starPositions = [];
  let seed = 7;
  const rand = () => { seed = (seed * 16807) % 2147483647; return seed / 2147483647; };
  for (let i = 0; i < 420; i += 1) {
    const z = 0.08 + rand() * 0.92;
    const a = rand() * Math.PI * 2;
    const r = Math.sqrt(1 - z * z);
    starPositions.push(Math.cos(a) * r * 0.98, Math.sin(a) * r * 0.98, z * 0.98);
  }
  const starGeometry = new THREE.BufferGeometry();
  starGeometry.setAttribute('position', new THREE.Float32BufferAttribute(starPositions, 3));
  const stars = new THREE.Points(starGeometry, new THREE.PointsMaterial({ color: 0xdfe6ff, size: 2, sizeAttenuation: false, transparent: true, opacity: 0, depthTest: true, depthWrite: false, fog: false }));
  stars.renderOrder = 11;
  stars.frustumCulled = false;
  dome.add(stars);

  // The sun's (or the moon's) glow.
  const glowGeometry = new THREE.BufferGeometry();
  glowGeometry.setAttribute('position', new THREE.Float32BufferAttribute([0, 0, 1], 3));
  const glow = new THREE.Points(glowGeometry, new THREE.PointsMaterial({ color: 0xffffff, size: 90, sizeAttenuation: false, map: textures().spot, transparent: true, opacity: 0.9, depthTest: true, depthWrite: false, fog: false, blending: THREE.AdditiveBlending }));
  glow.renderOrder = 12;
  glow.frustumCulled = false;
  dome.add(glow);

  const pos = geometry.attributes.position.array;
  const col = geometry.attributes.color.array;
  let last = '';
  function update(colors, sunDir) {
    const key = `${colors.zenith.map((v) => v.toFixed(3))}|${colors.horizon.map((v) => v.toFixed(3))}|${sunDir.x.toFixed(2)}|${sunDir.y.toFixed(2)}|${sunDir.z.toFixed(2)}`;
    if (key === last) return;
    last = key;
    const sunUp = sunDir.z > -0.05;
    for (let i = 0; i < count; i += 1) {
      const x = pos[i * 3]; const y = pos[i * 3 + 1]; const z = pos[i * 3 + 2];
      let c;
      if (z <= 0) c = colors.haze;
      else {
        // Horizon colour low down, zenith colour high up.
        const t = Math.pow(clamp(z, 0, 1), 0.5);
        c = mix(colors.horizon, colors.zenith, t);
        // Brighter towards the sun near the horizon (sunrise, sunset glow).
        const toward = Math.max(0, (x * sunDir.x + y * sunDir.y) / (Math.hypot(sunDir.x, sunDir.y) || 1));
        const glowK = (sunUp ? 0.35 : 0.18) * Math.pow(toward, 3) * (1 - t) * (0.4 + colors.dusk);
        c = [c[0] + glowK * 1.0, c[1] + glowK * 0.62, c[2] + glowK * 0.35];
        // A band of haze just over the horizon.
        const band = clamp(1 - z / 0.06, 0, 1);
        c = mix(c, colors.haze, band * 0.8);
      }
      col[i * 3] = c[0]; col[i * 3 + 1] = c[1]; col[i * 3 + 2] = c[2];
    }
    geometry.attributes.color.needsUpdate = true;
    stars.material.opacity = clamp(colors.night * 1.2, 0, 0.85);
    if (sunDir.z > -0.04) {
      glow.position.copy(sunDir);
      glow.material.color.setRGB(1.0, 0.8 + 0.2 * (1 - colors.dusk), 0.55 + 0.4 * (1 - colors.dusk));
      glow.material.size = 70 + 60 * colors.dusk;
      glow.material.opacity = 0.95;
    } else {
      // The moon, opposite the sun, small and pale.
      glow.position.set(-sunDir.x, -sunDir.y, Math.max(0.25, -sunDir.z)).normalize();
      glow.material.color.setRGB(0.72, 0.78, 0.92);
      glow.material.size = 26;
      glow.material.opacity = 0.75 * colors.night;
    }
  }
  return { dome, update };
}

// -- particles ------------------------------------------------------------------

// Dust: soft spots that rise, spread and fade. `emit` places one; the system
// moves them in `step` and says whether any is still in the air.
export function createDust(maxCount = 700) {
  const positions = new Float32Array(maxCount * 3);
  const colors = new Float32Array(maxCount * 4);
  const sizes = new Float32Array(maxCount);
  const life = new Float32Array(maxCount);
  const span = new Float32Array(maxCount);
  const vel = new Float32Array(maxCount * 3);
  const grow = new Float32Array(maxCount);
  const base = new Float32Array(maxCount);
  const alpha = new Float32Array(maxCount);
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute('position', new THREE.BufferAttribute(positions, 3).setUsage(THREE.DynamicDrawUsage));
  geometry.setAttribute('color', new THREE.BufferAttribute(colors, 4).setUsage(THREE.DynamicDrawUsage));
  geometry.setAttribute('size', new THREE.BufferAttribute(sizes, 1).setUsage(THREE.DynamicDrawUsage));
  // Points whose size is in metres (the stock material takes one size for all).
  const material = new THREE.ShaderMaterial({
    uniforms: { map: { value: textures().spot }, scale: { value: 400 }, tint: { value: new THREE.Color(1, 1, 1) },
      fogColor: { value: new THREE.Color() }, fogNear: { value: 1 }, fogFar: { value: 2 } },
    vertexShader: `attribute float size; attribute vec4 color; varying vec4 vColor; varying float vFog;
      uniform float scale; uniform float fogNear; uniform float fogFar;
      void main() { vColor = color; vec4 mv = modelViewMatrix * vec4(position, 1.0);
        gl_PointSize = clamp(size * scale / -mv.z, 1.0, 96.0); gl_Position = projectionMatrix * mv;
        vFog = smoothstep(fogNear, fogFar, -mv.z); }`,
    fragmentShader: `uniform sampler2D map; uniform vec3 tint; uniform vec3 fogColor; varying vec4 vColor; varying float vFog;
      void main() { vec4 t = texture2D(map, gl_PointCoord); gl_FragColor = vec4(mix(vColor.rgb * tint, fogColor, vFog), vColor.a * t.a);
        #include <colorspace_fragment>
      }`,
    transparent: true, depthWrite: false,
  });
  const points = new THREE.Points(geometry, material);
  points.frustumCulled = false;
  points.renderOrder = 3;
  let next = 0;
  let alive = 0;
  geometry.setDrawRange(0, 0);

  function emit(x, y, z, { vx = 0, vy = 0, vz = 0.6, size = 6, growth = 3, seconds = 4, color = [0.62, 0.52, 0.4], opacity = 0.35 } = {}) {
    const i = next;
    next = (next + 1) % maxCount;
    positions[i * 3] = x; positions[i * 3 + 1] = y; positions[i * 3 + 2] = z;
    vel[i * 3] = vx; vel[i * 3 + 1] = vy; vel[i * 3 + 2] = vz;
    colors[i * 4] = color[0]; colors[i * 4 + 1] = color[1]; colors[i * 4 + 2] = color[2]; colors[i * 4 + 3] = 0;
    base[i] = size; grow[i] = growth; life[i] = 0; span[i] = seconds; alpha[i] = opacity;
    sizes[i] = size;
  }

  // Advance by dt seconds; returns whether any particle is alive.
  function step(dt) {
    let count = 0;
    let highest = -1;
    for (let i = 0; i < maxCount; i += 1) {
      if (span[i] <= 0) continue;
      life[i] += dt;
      const f = life[i] / span[i];
      if (f >= 1) { span[i] = 0; colors[i * 4 + 3] = 0; sizes[i] = 0; continue; }
      positions[i * 3] += vel[i * 3] * dt;
      positions[i * 3 + 1] += vel[i * 3 + 1] * dt;
      positions[i * 3 + 2] += vel[i * 3 + 2] * dt;
      vel[i * 3] *= 0.985; vel[i * 3 + 1] *= 0.985; vel[i * 3 + 2] *= 0.99;
      sizes[i] = base[i] + grow[i] * life[i];
      // Fade in quickly, out slowly.
      colors[i * 4 + 3] = alpha[i] * Math.min(1, f * 8) * (1 - f) * (1 - f);
      count += 1;
      highest = i;
    }
    alive = count;
    geometry.setDrawRange(0, highest + 1);
    geometry.attributes.position.needsUpdate = true;
    geometry.attributes.color.needsUpdate = true;
    geometry.attributes.size.needsUpdate = true;
    return count > 0;
  }

  return { points, emit, step, get alive() { return alive; }, material };
}

// Rain (short streaks) or snow (flakes) in a box that follows the camera's target.
export function createWeather() {
  const COUNT = 1400;
  const rainPositions = new Float32Array(COUNT * 6);
  const rainGeometry = new THREE.BufferGeometry();
  rainGeometry.setAttribute('position', new THREE.BufferAttribute(rainPositions, 3).setUsage(THREE.DynamicDrawUsage));
  const rain = new THREE.LineSegments(rainGeometry, new THREE.LineBasicMaterial({ color: 0xc7d3dd, transparent: true, opacity: 0.38, fog: false, depthWrite: false }));
  rain.frustumCulled = false;
  rain.visible = false;
  const snowPositions = new Float32Array(COUNT * 3);
  const snowGeometry = new THREE.BufferGeometry();
  snowGeometry.setAttribute('position', new THREE.BufferAttribute(snowPositions, 3).setUsage(THREE.DynamicDrawUsage));
  const snow = new THREE.Points(snowGeometry, new THREE.PointsMaterial({ color: 0xf2f5fa, size: 3, sizeAttenuation: false, map: textures().spot, transparent: true, opacity: 0.85, depthWrite: false, fog: false }));
  snow.frustumCulled = false;
  snow.visible = false;
  const seeds = new Float32Array(COUNT * 3).map(() => Math.random());
  let kind = 'dry';
  let time = 0;

  function set(next) { kind = next; rain.visible = next === 'rain'; snow.visible = next === 'snow'; }

  // The drops fill a box of `size` metres around (cx, cy, cz).
  function step(dt, cx, cy, cz, size) {
    if (kind === 'dry') return false;
    time += dt;
    const half = size / 2;
    const fall = kind === 'rain' ? size * 0.9 : size * 0.12;
    const streak = size * 0.03;
    for (let i = 0; i < COUNT; i += 1) {
      const sx = seeds[i * 3]; const sy = seeds[i * 3 + 1]; const sz = seeds[i * 3 + 2];
      const x = cx - half + ((sx * size + time * (kind === 'snow' ? size * 0.02 : size * 0.06)) % size);
      const y = cy - half + ((sy * size + (kind === 'snow' ? Math.sin(time + i) * 2 : 0)) % size + size) % size;
      const z = cz + size * 0.6 - ((sz * size + time * fall) % size);
      if (kind === 'rain') {
        rainPositions.set([x, y, z, x + streak * 0.15, y + streak * 0.1, z + streak], i * 6);
      } else {
        snowPositions.set([x, y, z], i * 3);
      }
    }
    if (kind === 'rain') rainGeometry.attributes.position.needsUpdate = true;
    else snowGeometry.attributes.position.needsUpdate = true;
    return true;
  }

  return { rain, snow, set, step, get kind() { return kind; } };
}

// Lights that only show at night: glows over lamps and headlights and pools of
// light on the ground, both as additive spots.
export function createGlows(maxCount = 400) {
  const positions = new Float32Array(maxCount * 3);
  const colors = new Float32Array(maxCount * 3);
  const sizes = new Float32Array(maxCount);
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute('position', new THREE.BufferAttribute(positions, 3).setUsage(THREE.DynamicDrawUsage));
  geometry.setAttribute('color', new THREE.BufferAttribute(colors, 3).setUsage(THREE.DynamicDrawUsage));
  geometry.setAttribute('size', new THREE.BufferAttribute(sizes, 1).setUsage(THREE.DynamicDrawUsage));
  const material = new THREE.ShaderMaterial({
    uniforms: { map: { value: textures().spot }, strength: { value: 0 }, scale: { value: 400 } },
    vertexShader: `attribute float size; attribute vec3 color; varying vec3 vColor; uniform float scale;
      void main() { vColor = color; vec4 mv = modelViewMatrix * vec4(position, 1.0);
        gl_PointSize = clamp(size * scale / -mv.z, 3.0, 44.0); gl_Position = projectionMatrix * mv; }`,
    fragmentShader: `uniform sampler2D map; uniform float strength; varying vec3 vColor;
      void main() { vec4 t = texture2D(map, gl_PointCoord); gl_FragColor = vec4(vColor * strength * t.a, 1.0);
        #include <colorspace_fragment>
      }`,
    transparent: true, depthWrite: false, blending: THREE.AdditiveBlending,
  });
  const points = new THREE.Points(geometry, material);
  points.frustumCulled = false;
  points.renderOrder = 4;
  let count = 0;
  return {
    points,
    material,
    begin() { count = 0; },
    add(x, y, z, size, color) {
      if (count >= maxCount) return;
      positions[count * 3] = x; positions[count * 3 + 1] = y; positions[count * 3 + 2] = z;
      colors[count * 3] = color[0]; colors[count * 3 + 1] = color[1]; colors[count * 3 + 2] = color[2];
      sizes[count] = size;
      count += 1;
    },
    end() {
      geometry.setDrawRange(0, count);
      geometry.attributes.position.needsUpdate = true;
      geometry.attributes.color.needsUpdate = true;
      geometry.attributes.size.needsUpdate = true;
    },
  };
}
