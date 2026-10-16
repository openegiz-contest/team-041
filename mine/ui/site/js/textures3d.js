// Small procedural textures for the 3D view, drawn once on canvases at start:
// nothing is downloaded. The surface textures are grey and tile seamlessly;
// they only modulate the colours the geometry carries in its vertices (rock,
// soil, gravel), so one texture serves every hue. Their mean is about 0.86,
// which the scene allows for in its colours.

import * as THREE from 'three';

// -- seeded, tileable value noise ---------------------------------------------

function hash(x, y, seed) {
  let h = (x * 374761393 + y * 668265263 + seed * 2147483647) | 0;
  h = Math.imul(h ^ (h >>> 13), 1274126177);
  h ^= h >>> 16;
  return (h >>> 0) / 4294967295;
}

const fade = (t) => t * t * (3 - 2 * t);

// Value noise on a lattice that wraps every `period` cells (so a texture tiles).
export function noise2(x, y, seed = 1, period = 0) {
  const xi = Math.floor(x);
  const yi = Math.floor(y);
  const fx = fade(x - xi);
  const fy = fade(y - yi);
  const w = (v) => (period ? ((v % period) + period) % period : v);
  const a = hash(w(xi), w(yi), seed);
  const b = hash(w(xi + 1), w(yi), seed);
  const c = hash(w(xi), w(yi + 1), seed);
  const d = hash(w(xi + 1), w(yi + 1), seed);
  return a + (b - a) * fx + (c - a) * fy + (a - b - c + d) * fx * fy;
}

// Fractal sum of octaves, 0..1.
export function fbm(x, y, seed = 1, octaves = 4, period = 0) {
  let sum = 0;
  let amp = 0.5;
  let norm = 0;
  let f = 1;
  for (let i = 0; i < octaves; i += 1) {
    sum += amp * noise2(x * f, y * f, seed + i * 17, period ? period * f : 0);
    norm += amp;
    amp *= 0.5;
    f *= 2;
  }
  return sum / norm;
}

// -- textures -------------------------------------------------------------------

function canvasOf(size) {
  const canvas = document.createElement('canvas');
  canvas.width = size;
  canvas.height = size;
  return canvas;
}

function surface(size, value) {
  const canvas = canvasOf(size);
  const ctx = canvas.getContext('2d');
  const image = ctx.createImageData(size, size);
  for (let y = 0; y < size; y += 1) {
    for (let x = 0; x < size; x += 1) {
      const v = Math.max(0, Math.min(1, value(x / size, y / size, x, y)));
      const k = (y * size + x) * 4;
      const g = Math.round(v * 255);
      image.data[k] = g;
      image.data[k + 1] = g;
      image.data[k + 2] = g;
      image.data[k + 3] = 255;
    }
  }
  ctx.putImageData(image, 0, 0);
  return canvas;
}

function tiled(canvas, soft = false) {
  const texture = new THREE.CanvasTexture(canvas);
  texture.wrapS = THREE.RepeatWrapping;
  texture.wrapT = THREE.RepeatWrapping;
  texture.colorSpace = THREE.NoColorSpace;
  texture.minFilter = THREE.LinearMipmapNearestFilter;
  texture.magFilter = THREE.LinearFilter;
  texture.anisotropy = 1;
  texture.userData.soft = soft;
  return texture;
}

// Steppe and soil: patches of dry grass and bare ground with tufts and pebbles.
// One repeat covers a few hundred metres: the patches still read from afar.
function grain() {
  const P = 4;
  return tiled(surface(256, (u, v, x, y) => {
    const patches = fbm(u * P, v * P, 3, 4, P);
    const mid = fbm(u * 16, v * 16, 7, 3, 16);
    const fine = noise2(u * 64, v * 64, 9, 64);
    let value = 0.62 + 0.34 * patches + 0.14 * (mid - 0.5) + 0.08 * (fine - 0.5);
    const r = hash(x, y, 77);
    if (r < 0.03) value -= 0.14;                  // dark tufts of grass
    else if (r > 0.988) value += 0.1;             // light pebbles
    return value;
  }));
}

// Rock faces: strata with a little dip, runnels down the face, blocky
// joints and cracks. u runs along the face, v down it; one repeat is about
// two benches high and a hundred metres long.
function strata() {
  return tiled(surface(256, (u, v) => {
    // Layers: bands across the face, a little wavy, of differing thickness.
    const wave = fbm(u * 2, v * 2, 19, 2, 2) * 0.35;
    const layer = fbm(u * 1, (v + wave) * 18, 5, 2, 1) * 0.6 + fbm(u * 2, (v + wave) * 7, 21, 3, 2) * 0.4;
    const runnels = fbm(u * 40, v * 2, 31, 3, 40);
    const crack = Math.abs(noise2(u * 14, v * 5, 41, 14) - 0.5) < 0.014 ? -0.1 : 0;
    const blocks = noise2(u * 20, v * 8, 57, 20);
    const broad = fbm(u * 3, v * 3, 63, 2, 3);
    return 0.3 + 0.75 * layer + 0.2 * (runnels - 0.5) + 0.14 * (blocks - 0.5) + 0.16 * (broad - 0.5) + crack;
  }));
}

// Haul road: compacted gravel polished along the way by the tyres: fine
// streaks along the road, a few loose stones, patches of darker grit.
function road() {
  return tiled(surface(256, (u, v, x, y) => {
    const streaks = fbm(u * 2, v * 64, 71, 3, 2);
    const patches = fbm(u * 4, v * 4, 73, 3, 4);
    const grit = hash(x, y, 17);
    let value = 0.6 + 0.3 * streaks + 0.18 * (patches - 0.5) + 0.08 * (grit - 0.5);
    if (grit > 0.994) value += 0.15;
    return value;
  }));
}

// Gravel of berms, roads and floors: fine grit, scattered stones and broad
// patches of darker and lighter ground.
function gravel() {
  return tiled(surface(256, (u, v, x, y) => {
    const broad = fbm(u * 4, v * 4, 67, 3, 4);
    const base = fbm(u * 16, v * 16, 61, 3, 16);
    const grit = hash(x, y, 13);
    let value = 0.66 + 0.26 * broad + 0.14 * (base - 0.5) + 0.12 * (grit - 0.5);
    if (grit > 0.992) value += 0.12;
    if (grit < 0.01) value -= 0.18;
    return value;
  }));
}

// A soft round spot (dust, glows, rain drops, light pools): white with alpha.
function spot() {
  const size = 64;
  const canvas = canvasOf(size);
  const ctx = canvas.getContext('2d');
  const g = ctx.createRadialGradient(size / 2, size / 2, 0, size / 2, size / 2, size / 2);
  g.addColorStop(0, 'rgba(255,255,255,1)');
  g.addColorStop(0.35, 'rgba(255,255,255,0.55)');
  g.addColorStop(1, 'rgba(255,255,255,0)');
  ctx.fillStyle = g;
  ctx.fillRect(0, 0, size, size);
  const texture = new THREE.CanvasTexture(canvas);
  texture.colorSpace = THREE.SRGBColorSpace;
  return texture;
}

// Road signs, drawn as a disc on a post: no entry and a speed limit warning.
function sign(kind) {
  const size = 64;
  const canvas = canvasOf(size);
  const ctx = canvas.getContext('2d');
  const c = size / 2;
  if (kind === 'closed') {
    ctx.beginPath(); ctx.arc(c, c, 30, 0, Math.PI * 2); ctx.fillStyle = '#ffffff'; ctx.fill();
    ctx.beginPath(); ctx.arc(c, c, 27, 0, Math.PI * 2); ctx.fillStyle = '#d42a2a'; ctx.fill();
    ctx.fillStyle = '#ffffff'; ctx.fillRect(c - 18, c - 5, 36, 10);
  } else {
    ctx.beginPath(); ctx.moveTo(c, 4); ctx.lineTo(size - 3, size - 8); ctx.lineTo(3, size - 8); ctx.closePath();
    ctx.fillStyle = '#ffffff'; ctx.fill();
    ctx.beginPath(); ctx.moveTo(c, 12); ctx.lineTo(size - 11, size - 12); ctx.lineTo(11, size - 12); ctx.closePath();
    ctx.fillStyle = '#f2b51c'; ctx.fill();
    ctx.fillStyle = '#1c1a14'; ctx.fillRect(c - 3, 24, 6, 16); ctx.fillRect(c - 3, 44, 6, 6);
  }
  const texture = new THREE.CanvasTexture(canvas);
  texture.colorSpace = THREE.SRGBColorSpace;
  return texture;
}

let made = null;
export function textures() {
  if (!made) made = { grain: grain(), strata: strata(), gravel: gravel(), road: road(), spot: spot(), closed: sign('closed'), restricted: sign('restricted') };
  return made;
}

// The mean value of the surface textures: colours are divided by it.
export const TEXTURE_MEAN = 0.8;
