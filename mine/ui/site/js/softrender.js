// A stand-in for THREE.WebGLRenderer when the browser has no WebGL at all:
// it draws the same three.js scene on a 2D canvas, triangles from the back
// to the front (painter's algorithm). It knows the few kinds of objects the
// 3D view uses — meshes coloured per vertex or by their material, instanced
// meshes of the equipment, and lines — and no lighting, which the scene does
// not need (its colours are lit when they are built). It is much slower than
// WebGL, so the 3D view built on it uses less detail and draws less often.
//
// Objects are drawn in layers (userData.layer, inherited from the parent):
// the ground, then the pit, then what lies on them, then what stands on
// them; within a layer the farthest triangle first.

import * as THREE from 'three';

export function createSoftRenderer(canvas) {
  const ctx = canvas.getContext('2d', { alpha: false });
  const info = { render: { triangles: 0, calls: 0 } };
  let width = 0;
  let height = 0;

  const viewProjection = new THREE.Matrix4();
  const world = new THREE.Matrix4();
  const full = new THREE.Matrix4();
  const instance = new THREE.Matrix4();
  const v = new THREE.Vector4();
  const color = new THREE.Color();
  const tint = new THREE.Color();
  const styles = new Map();

  // CSS colour of a linear colour, cached by its 6-bit channels in sRGB (so
  // dark colours keep their hue: steps in linear values are coarse there).
  const srgb = { r: 0, g: 0, b: 0 };
  const shade = new THREE.Color();
  // The scene's light is in its own units: mapped to the screen with the
  // same ACES curve and exposure as WebGL draws it.
  const fit = (v) => (v * (v + 0.0245786) - 0.000090537) / (v * (0.983729 * v + 0.432951) + 0.238081);
  function toneMap(c) {
    const k = (api.toneMappingExposure ?? 1) / 0.6;
    const r = c.r * k; const g = c.g * k; const b = c.b * k;
    const r1 = fit(0.59719 * r + 0.35458 * g + 0.04823 * b);
    const g1 = fit(0.076 * r + 0.90834 * g + 0.01566 * b);
    const b1 = fit(0.0284 * r + 0.13383 * g + 0.83777 * b);
    shade.setRGB(1.60475 * r1 - 0.53108 * g1 - 0.07367 * b1, -0.10208 * r1 + 1.10813 * g1 - 0.00605 * b1, -0.00327 * r1 - 0.07276 * g1 + 1.07602 * b1);
  }
  function style(c) {
    if (api.toneMapping) toneMap(c); else shade.copy(c);
    shade.setRGB(THREE.MathUtils.clamp(shade.r, 0, 1), THREE.MathUtils.clamp(shade.g, 0, 1), THREE.MathUtils.clamp(shade.b, 0, 1));
    shade.getRGB(srgb, THREE.SRGBColorSpace);
    const r = Math.round(srgb.r * 63);
    const g = Math.round(srgb.g * 63);
    const b = Math.round(srgb.b * 63);
    const key = (r << 12) | (g << 6) | b;
    let value = styles.get(key);
    if (!value) {
      value = `rgb(${Math.round((r * 255) / 63)},${Math.round((g * 255) / 63)},${Math.round((b * 255) / 63)})`;
      styles.set(key, value);
    }
    return value;
  }

  // Clip-space position of vertex i of an attribute under matrix m, into out (x, y screen; z depth; w).
  function project(attribute, i, m, out) {
    v.set(attribute.getX(i), attribute.getY(i), attribute.getZ(i), 1).applyMatrix4(m);
    out.w = v.w;
    out.x = (v.x / v.w + 1) * 0.5 * width;
    out.y = (1 - v.y / v.w) * 0.5 * height;
    out.z = v.z / v.w;
  }

  const a = {}; const b = {}; const c = {};

  function collectMesh(object, layer, list, matrix, instanceTint) {
    const geometry = object.geometry;
    const position = geometry.attributes.position;
    if (!position) return;
    const colors = geometry.attributes.color;
    const index = geometry.index;
    const materials = [].concat(object.material);
    const groups = geometry.groups.length ? geometry.groups : [{ start: 0, count: index ? index.count : position.count, materialIndex: 0 }];
    for (const group of groups) {
      const material = materials[group.materialIndex ?? 0] ?? materials[0];
      if (!material || material.visible === false) continue;
      const opacity = material.transparent ? material.opacity : 1;
      for (let k = group.start; k + 2 < group.start + group.count; k += 3) {
        const i0 = index ? index.getX(k) : k;
        const i1 = index ? index.getX(k + 1) : k + 1;
        const i2 = index ? index.getX(k + 2) : k + 2;
        project(position, i0, matrix, a);
        project(position, i1, matrix, b);
        project(position, i2, matrix, c);
        if (a.w <= 0 || b.w <= 0 || c.w <= 0) continue;
        if ((a.x < 0 && b.x < 0 && c.x < 0) || (a.x > width && b.x > width && c.x > width)
          || (a.y < 0 && b.y < 0 && c.y < 0) || (a.y > height && b.y > height && c.y > height)) continue;
        tint.copy(material.color);
        // The mean of the corners' colours: a triangle is filled with one colour.
        if (material.vertexColors && colors) {
          tint.multiply(color.setRGB((colors.getX(i0) + colors.getX(i1) + colors.getX(i2)) / 3,
            (colors.getY(i0) + colors.getY(i1) + colors.getY(i2)) / 3, (colors.getZ(i0) + colors.getZ(i1) + colors.getZ(i2)) / 3));
        }
        if (instanceTint) tint.multiply(instanceTint);
        list.push({ layer, sub: group.materialIndex ?? 0, depth: (a.z + b.z + c.z) / 3, x0: a.x, y0: a.y, x1: b.x, y1: b.y, x2: c.x, y2: c.y, fill: style(tint), alpha: opacity });
      }
    }
  }

  function collectLines(object, layer, list, matrix) {
    const position = object.geometry.attributes.position;
    if (!position) return;
    const material = object.material;
    const loop = object.isLineLoop;
    const segments = object.isLineSegments;
    const count = position.count;
    const step = segments ? 2 : 1;
    const last = segments ? count - 1 : loop ? count : count - 1;
    const path = [];
    for (let i = 0; i < last; i += step) {
      project(position, i, matrix, a);
      project(position, (i + 1) % count, matrix, b);
      if (a.w <= 0 || b.w <= 0) continue;
      path.push(a.x, a.y, b.x, b.y);
    }
    if (path.length) list.push({ layer, sub: 9, depth: -2, lines: path, stroke: style(material.color), alpha: material.transparent ? material.opacity : 1 });
  }

  const api = {
    domElement: canvas,
    toneMapping: 0,
    toneMappingExposure: 1,
    info,
    soft: true,
    setPixelRatio() {},
    setSize(w, h) {
      width = w;
      height = h;
      canvas.width = w;
      canvas.height = h;
    },
    render(scene, camera) {
      camera.updateMatrixWorld();
      scene.updateMatrixWorld();
      viewProjection.multiplyMatrices(camera.projectionMatrix, camera.matrixWorldInverse);
      ctx.setTransform(1, 0, 0, 1, 0, 0);
      ctx.globalAlpha = 1;
      ctx.fillStyle = scene.background?.isColor ? style(scene.background) : '#000';
      ctx.fillRect(0, 0, width, height);
      const list = [];
      let calls = 0;
      const visit = (object, layer) => {
        if (!object.visible) return;
        const here = object.userData.layer ?? layer;
        if (object.isInstancedMesh) {
          for (let i = 0; i < object.count; i += 1) {
            object.getMatrixAt(i, instance);
            world.multiplyMatrices(object.matrixWorld, instance);
            full.multiplyMatrices(viewProjection, world);
            let instanceTint = null;
            if (object.instanceColor) { object.getColorAt(i, color); instanceTint = color.clone(); }
            collectMesh(object, here, list, full, instanceTint);
          }
          calls += 1;
        } else if (object.isMesh) {
          full.multiplyMatrices(viewProjection, object.matrixWorld);
          collectMesh(object, here, list, full, null);
          calls += 1;
        } else if (object.isLine) {
          full.multiplyMatrices(viewProjection, object.matrixWorld);
          collectLines(object, here, list, full);
          calls += 1;
        }
        for (const child of object.children) visit(child, here);
      };
      visit(scene, 2);
      // Within a layer: by material of a mesh (a road's edge before its surface),
      // lines after the triangles they lie on, then the farthest triangle first.
      list.sort((p, q) => p.layer - q.layer || p.sub - q.sub || q.depth - p.depth);
      ctx.lineJoin = 'round';
      let triangles = 0;
      for (const item of list) {
        ctx.globalAlpha = item.alpha;
        if (item.lines) {
          ctx.beginPath();
          for (let i = 0; i < item.lines.length; i += 4) {
            ctx.moveTo(item.lines[i], item.lines[i + 1]);
            ctx.lineTo(item.lines[i + 2], item.lines[i + 3]);
          }
          ctx.strokeStyle = item.stroke;
          ctx.lineWidth = 1;
          ctx.stroke();
          continue;
        }
        ctx.beginPath();
        ctx.moveTo(item.x0, item.y0);
        ctx.lineTo(item.x1, item.y1);
        ctx.lineTo(item.x2, item.y2);
        ctx.closePath();
        ctx.fillStyle = item.fill;
        ctx.fill();
        // A hairline of the same colour hides the seams between neighbours.
        if (item.alpha === 1) {
          ctx.strokeStyle = item.fill;
          ctx.lineWidth = 0.7;
          ctx.stroke();
        }
        triangles += 1;
      }
      ctx.globalAlpha = 1;
      info.render.triangles = triangles;
      info.render.calls = calls;
    },
  };
  return api;
}
