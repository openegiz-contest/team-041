// The Blender-made models of the 3D view (models/*.glb, built by
// tools/blender, described by models/models.json): loaded once, after the
// first frame of the procedural scene, with the vendored GLTFLoader and
// DRACOLoader, and turned into what the scene draws.
//
// Every model becomes a rig: one part per node that moves on its own (dump
// body, wheel, upper, boom, stick, bucket, mast, cylinder, mill shell, rake,
// roof), each part a geometry in the scene's axes (Z up, a machine facing
// +Y, metres) with its origin on the part's pivot. The colour of each
// material and the grime of the vertex colours are baked into the vertex
// colours, its metalness, roughness and glow into one more attribute, so a
// part is one draw call, instanced for every machine of a kind; the plant
// keeps its cladding and concrete textures (one call per texture). The
// geometry is not indexed, so the hologram's shader can light its edges.
//
// poseRig() places the parts of one machine from its pose (the angle of
// every moving part); hydraulic cylinders follow their pins.

import * as THREE from 'three';

const BASE = 'models/';
const DECODER = 'vendor/three/addons/libs/draco/gltf/';

let loader = null;
async function gltfLoader() {
  if (loader) return loader;
  const [{ GLTFLoader }, { DRACOLoader }] = await Promise.all([
    import('../vendor/three/addons/loaders/GLTFLoader.js'),
    import('../vendor/three/addons/loaders/DRACOLoader.js'),
  ]);
  const draco = new DRACOLoader().setDecoderPath(DECODER);
  draco.setWorkerLimit(2);
  loader = new GLTFLoader().setDRACOLoader(draco);
  return loader;
}

let manifestPromise = null;
export function manifest() {
  if (!manifestPromise) {
    manifestPromise = fetch(`${BASE}models.json`).then((r) => {
      if (!r.ok) throw new Error(`models.json: HTTP ${r.status}`);
      return r.json();
    });
  }
  return manifestPromise;
}

const nextFrame = () => new Promise((resolve) => { requestAnimationFrame(() => setTimeout(resolve, 0)); });

// -- materials -----------------------------------------------------------------------

// One physically based material for every model part: the colour from the
// vertices, metalness, roughness and glow from the `pbr` attribute, and a
// cheap reflection of the sky and the ground (two colours by the direction
// of the reflected ray) so that steel and glass read without an environment map.
export function modelMaterial({ map = null, side = THREE.FrontSide } = {}) {
  const material = new THREE.MeshStandardMaterial({ vertexColors: true, map, side, metalness: 1, roughness: 1 });
  const uniforms = {
    uEnvSky: { value: new THREE.Color(0.5, 0.58, 0.74) },
    uEnvGround: { value: new THREE.Color(0.25, 0.21, 0.15) },
    uLamp: { value: 0.25 },
  };
  material.userData.env = uniforms;
  material.onBeforeCompile = (shader) => {
    Object.assign(shader.uniforms, uniforms);
    shader.vertexShader = shader.vertexShader
      .replace('#include <common>', '#include <common>\nattribute vec3 pbr;\nvarying vec3 vPbr;')
      .replace('#include <begin_vertex>', '#include <begin_vertex>\nvPbr = pbr;');
    shader.fragmentShader = shader.fragmentShader
      .replace('#include <common>', '#include <common>\nvarying vec3 vPbr;\nuniform vec3 uEnvSky;\nuniform vec3 uEnvGround;\nuniform float uLamp;')
      .replace('#include <roughnessmap_fragment>', 'float roughnessFactor = max(vPbr.y, 0.04);')
      .replace('#include <metalnessmap_fragment>', 'float metalnessFactor = vPbr.x;')
      .replace('#include <emissivemap_fragment>', '#include <emissivemap_fragment>\ntotalEmissiveRadiance += diffuseColor.rgb * vPbr.z * uLamp;')
      .replace('#include <lights_fragment_maps>', `#include <lights_fragment_maps>
  #if defined( RE_IndirectSpecular )
    vec3 envUp = normalize( ( viewMatrix * vec4( 0.0, 0.0, 1.0, 0.0 ) ).xyz );
    vec3 envR = reflect( - geometryViewDir, normal );
    float envT = smoothstep( -0.25, 0.45, dot( envR, envUp ) );
    radiance += mix( uEnvGround, uEnvSky, envT );
  #endif`);
  };
  material.customProgramCacheKey = () => 'model-pbr';
  return material;
}

// -- rigs --------------------------------------------------------------------------

const TO_SCENE_X = Math.PI / 2;     // glTF (+Y up, facing -Z) to the scene (+Z up, facing +Y)

// The meshes of a node itself (its primitives), not those of the nodes under it.
function ownMeshes(node) {
  const out = [];
  const walk = (o) => {
    if (o.isMesh) out.push(o);
    for (const child of o.children) if (!child.userData.name) walk(child);
  };
  walk(node);
  return out;
}

// Bake meshes into geometries, one per texture (and side), in the frame `inverse`.
function bake(meshes, inverse, materials, textures) {
  const groups = new Map();
  const m = new THREE.Matrix4();
  const n = new THREE.Matrix3();
  const v = new THREE.Vector3();
  const nv = new THREE.Vector3();
  for (const mesh of meshes) {
    const material = mesh.material;
    const info = materials[material.name] ?? {};
    const textureKind = material.map ? (info.texture ?? 'map') : '';
    if (textureKind && material.map && !textures.has(textureKind)) textures.set(textureKind, material.map);
    const double = material.side === THREE.DoubleSide || info.double === true;
    const key = `${textureKind}|${double ? 2 : 1}`;
    let g = groups.get(key);
    if (!g) {
      g = { texture: textureKind, double, pos: [], nrm: [], col: [], uv: [], pbr: [] };
      groups.set(key, g);
    }
    const geometry = mesh.geometry;
    const position = geometry.attributes.position;
    const normal = geometry.attributes.normal;
    const color = geometry.attributes.color;
    const uv = geometry.attributes.uv;
    const index = geometry.index;
    m.multiplyMatrices(inverse, mesh.matrixWorld);
    n.getNormalMatrix(m);
    const base = material.color;
    const metal = info.metallic ?? material.metalness ?? 0;
    const rough = info.roughness ?? material.roughness ?? 0.6;
    const glow = info.emissive ? (info.strength ?? 1) : 0;
    const count = index ? index.count : position.count;
    for (let i = 0; i < count; i += 1) {
      const k = index ? index.getX(i) : i;
      v.fromBufferAttribute(position, k).applyMatrix4(m);
      g.pos.push(v.x, v.y, v.z);
      if (normal) {
        nv.fromBufferAttribute(normal, k).applyMatrix3(n).normalize();
        g.nrm.push(nv.x, nv.y, nv.z);
      } else g.nrm.push(0, 0, 1);
      const r = color ? color.getX(k) : 1;
      const gg = color ? color.getY(k) : 1;
      const b = color ? color.getZ(k) : 1;
      g.col.push(base.r * r, base.g * gg, base.b * b);
      if (textureKind) g.uv.push(uv ? uv.getX(k) : 0, uv ? uv.getY(k) : 0);
      g.pbr.push(metal, rough, glow);
    }
  }
  const layers = [];
  for (const g of groups.values()) {
    if (!g.pos.length) continue;
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute('position', new THREE.Float32BufferAttribute(g.pos, 3));
    geometry.setAttribute('normal', new THREE.Float32BufferAttribute(g.nrm, 3));
    geometry.setAttribute('color', new THREE.Float32BufferAttribute(g.col, 3));
    geometry.setAttribute('pbr', new THREE.Float32BufferAttribute(g.pbr, 3));
    if (g.texture) geometry.setAttribute('uv', new THREE.Float32BufferAttribute(g.uv, 2));
    geometry.computeBoundingSphere();
    geometry.computeBoundingBox();
    geometry.userData.keep = true;           // shared by every placement: never disposed with a mesh
    layers.push({ geometry, texture: g.texture, double: g.double, triangles: g.pos.length / 9 });
  }
  return layers;
}

// From afar (LOD1) some moving parts are not worth a draw call of their own:
// they are merged into the part that carries them, in their rest pose.
const MERGE_FAR = {
  cat785c: { wheel_FL: 'frame', wheel_FR: 'frame', wheel_RL: 'frame', wheel_RR: 'frame' },
  ex3600: { boom: 'upper', stick: 'upper', bucket: 'upper' },
};

// The rig of one level of detail of a model, from its loaded glTF scene.
function buildRig(id, lod, scene, materials, textures) {
  scene.rotation.x = TO_SCENE_X;
  scene.updateMatrixWorld(true);
  const named = new Map();
  scene.traverse((o) => { if (o.userData.name && !named.has(o.userData.name)) named.set(o.userData.name, o); });
  const parts = [];
  const byName = new Map();
  const pins = {};
  const frames = new Map();
  const infos = lod.nodes.filter((info) => named.has(info.name));
  // Parents before children.
  const ordered = [];
  const seen = new Set();
  const visit = (info) => {
    if (seen.has(info.name)) return;
    seen.add(info.name);
    const parent = infos.find((other) => other.name === info.parent);
    if (parent) visit(parent);
    ordered.push(info);
  };
  infos.forEach(visit);
  const merge = lod.lod > 0 ? MERGE_FAR[id] ?? {} : {};
  const extra = new Map();
  for (const [child, target] of Object.entries(merge)) {
    if (!named.has(child) || !named.has(target)) continue;
    extra.set(target, [...(extra.get(target) ?? []), ...ownMeshes(named.get(child))]);
  }
  const worldPos = new THREE.Vector3();
  for (const info of ordered) {
    const node = named.get(info.name);
    if (merge[info.name] && named.has(merge[info.name])) continue;
    node.getWorldPosition(worldPos);
    const frame = info.aim ? node.matrixWorld.clone() : new THREE.Matrix4().makeTranslation(worldPos.x, worldPos.y, worldPos.z);
    frames.set(info.name, frame);
    const parentFrame = frames.get(info.parent) ?? new THREE.Matrix4();
    const parentIndex = byName.has(info.parent) ? byName.get(info.parent) : -1;
    if (info.empty) {
      const local = worldPos.clone().applyMatrix4(parentFrame.clone().invert());
      pins[info.name] = { part: parentIndex, local };
      continue;
    }
    const rest = parentFrame.clone().invert().multiply(frame);
    const restT = new THREE.Matrix4().copyPosition(rest);
    const layers = bake([...ownMeshes(node), ...(extra.get(info.name) ?? [])], frame.clone().invert(), materials, textures);
    const part = { name: info.name, parent: parentIndex, rest, restT, axis: info.axis ?? null, aim: info.aim ?? null,
      restLength: info.rest_length_m ?? 1, layers, triangles: layers.reduce((sum, l) => sum + l.triangles, 0),
      pivot: worldPos.clone() };
    byName.set(info.name, parts.length);
    parts.push(part);
  }
  return { id, lod: lod.lod, parts, byName, pins, triangles: parts.reduce((sum, p) => sum + p.triangles, 0),
    bounds: lod.bounds_m, size: lod.size_m };
}

// Load models: `ids` of models.json, both levels of detail when there are two.
// Resolves to {manifest, models: {id: {info, lods: [rig, rig]}}, textures: Map(kind -> texture)}.
// `onModel(id)` is told as each model is ready.
export async function loadModels(ids, { library = null, onModel = null } = {}) {
  const [json, gltf] = await Promise.all([manifest(), gltfLoader()]);
  const lib = library ?? { manifest: json, models: {}, textures: new Map(), failed: [] };
  const wanted = json.models.filter((m) => ids.includes(m.id) && !lib.models[m.id]);
  const jobs = wanted.flatMap((m) => m.lods.map((l) => ({ m, l, promise: gltf.loadAsync(`${BASE}${l.file}`) })));
  // A file that fails is reported once, by its model below (not left as an unhandled rejection).
  for (const job of jobs) job.promise.catch(() => {});
  // Turn each file into its rig as it arrives, a frame apart, so the page stays responsive.
  for (const m of wanted) {
    try {
      const lods = [];
      for (const job of jobs.filter((j) => j.m === m)) {
        const loaded = await job.promise;
        await nextFrame();
        lods[job.l.lod] = buildRig(m.id, job.l, loaded.scene, json.materials, lib.textures);
      }
      lib.models[m.id] = { info: m, lods };
      if (onModel) onModel(m.id);
    } catch (error) {
      lib.failed.push(m.id);
      console.warn(`3D: the model ${m.id} could not be loaded (${error?.message ?? error}); its procedural model stays`);
    }
  }
  return lib;
}

// -- poses ---------------------------------------------------------------------------

const turn = new THREE.Matrix4();
const pa = new THREE.Vector3();
const pb = new THREE.Vector3();
const px = new THREE.Vector3();
const py = new THREE.Vector3();
const pz = new THREE.Vector3();

// Matrices (world) of every part of a rig placed by `base`, with the moving
// parts turned by `angles[name]` (radians about the part's axis). Cylinders
// stretch from their pin to the pin they aim at.
export function poseRig(rig, base, angles, out) {
  const parts = rig.parts;
  for (let i = 0; i < parts.length; i += 1) {
    const p = parts[i];
    if (p.aim) continue;
    out[i].multiplyMatrices(p.parent < 0 ? base : out[p.parent], p.rest);
    const a = angles ? angles[p.name] : 0;
    if (a && p.axis) {
      if (p.axis === 'x') turn.makeRotationX(a);
      else if (p.axis === 'y') turn.makeRotationY(a);
      else turn.makeRotationZ(a);
      out[i].multiply(turn);
    }
  }
  for (let i = 0; i < parts.length; i += 1) {
    const p = parts[i];
    if (!p.aim) continue;
    const parent = p.parent < 0 ? base : out[p.parent];
    const pin = rig.pins[p.aim];
    if (!pin) { out[i].multiplyMatrices(parent, p.rest); continue; }
    out[i].multiplyMatrices(parent, p.restT);
    pa.setFromMatrixPosition(out[i]);
    pb.copy(pin.local).applyMatrix4(pin.part < 0 ? base : out[pin.part]);
    pz.subVectors(pb, pa);
    const length = pz.length() || 1;
    const stretch = 1 / p.restLength;          // the z column: the whole way to the pin, over the rest length
    const zx = pz.x * stretch; const zy = pz.y * stretch; const zz = pz.z * stretch;
    pz.divideScalar(length);
    px.setFromMatrixColumn(parent, 0);
    const k = px.length() || 1;
    px.divideScalar(k);
    px.addScaledVector(pz, -px.dot(pz)).normalize();
    py.crossVectors(pz, px);
    out[i].set(px.x * k, py.x * k, zx, pa.x,
      px.y * k, py.y * k, zy, pa.y,
      px.z * k, py.z * k, zz, pa.z,
      0, 0, 0, 1);
  }
  return out;
}

// The pivot of a part in the model (scene axes, metres), or null.
export function pivotOf(rig, name) {
  const i = rig?.byName.get(name);
  return i === undefined ? null : rig.parts[i].pivot;
}
