// The 3D view of the mine: the page's main view. It is built from the twins:
// the pit bench by bench from pitgeom.js, the steppe around it, the haul
// roads, the waste dump, the ROM pads, the plant from the crusher to the
// tailings pond, and the machines where they report, as models of the
// Caterpillar 785C trucks, the Hitachi EX3600 shovels and the Sandvik D55SP
// drills. Trucks drive along the roads between reports (motion.js), tip
// their bodies at the dump, shovels slew between the face and the truck,
// drills raise their masts. The sun stands where it stands at the simulated
// time; rain, snow and dust follow the twins.
//
// Everything is drawn with three.js (vendor/three, pinned). The static
// world carries its light in its vertex colours, computed on the CPU from the
// sun or the moon, the sky, a back light and, at night, the floodlight towers
// and lamps of the site, and recomputed when the sun moves; the machines,
// which turn, light themselves (Phong, a little sheen). The picture is in
// scene light units mapped to the screen by ACES with an exposure that
// follows the hour, so day, dusk and a lit night all read. ?time=HH:MM in
// the address previews the light of that hour (nothing else changes). Near
// the camera the machines are detailed models, afar simple ones. A frame is
// drawn only when something changed: the camera, the data, a truck or a
// particle in motion. Captions are drawn on a 2D canvas over the scene
// and never overlap. Without WebGL the same scene is drawn on a 2D canvas,
// simpler and less often (softrender.js).
//
// Once the first frame is on the screen the Blender models (models/*.glb,
// gltf3d.js) are loaded and take the place of the procedural ones: the
// trucks, shovels and drills (detailed near the camera, simple afar, one
// instanced mesh per moving part: dump body, wheels, upper, boom, stick,
// bucket, mast, cylinders), the crusher, the mill hall (its mill shells turn,
// its roof lifts off close up), the flotation, the thickeners (their rakes
// turn), the conveyor truss, the floodlight towers and the clay-ore plant.
// Plant models afar are drawn as their silhouettes. If the models cannot be
// loaded (or without WebGL, or with ?models=0) the procedural models stay.
//
// That is the realistic look. The default look is the hologram (holo3d.js):
// the same meshes wear hologram materials (dark teal ground and benches with
// glowing contour lines, roads as lit lanes, machines and plant as tinted
// glass), there is no sun, and floating panels (panels3d.js) carry the live
// figures of the machines and units over them. setStyle() switches the look.

import * as THREE from 'three';
import { twins, byKind, first, feature, kindOf, titleOf, relatedIds, activeScenarios, resolveRef } from './store.js';
import { C, LAYERS, DEPLETED, truckColor, stateColor } from './theme.js';
import { shortTitle, stateText, formatNumber } from './labels.js';
import { createMotion } from './motion.js';
import { areaCentroid } from './pitgeom.js';
import { plantLayout } from './plant.js';
import { createSoftRenderer } from './softrender.js';
import { Builder, ribbon, densify, relight, LIGHT, rgb, scale, mix, clamp, distanceToPolyline, centroid } from './geom3d.js';
import { textures, fbm, noise2, TEXTURE_MEAN } from './textures3d.js';
import { buildTerrain, roadGeometry, dumpGeometry, padGeometry } from './terrain3d.js';
import { buildPlant, modelProxy } from './plant3d.js';
import { truckParts, shovelParts, drillParts, haloGeometry, blobGeometry, trianglesOf, TRUCK, DRILL } from './models3d.js';
import { sunAt, palette, keyDirection, createSky, createDust, createWeather, createGlows } from './sky3d.js';
import { HOLO, holoTone, createHoloMaterials, roadMaterial, markGlass, setFog, createDome, prepare, contourLines, laneLines, holoRelight } from './holo3d.js';
import { createPanels, PANEL_KINDS } from './panels3d.js';

const FONT = 'system-ui, "Segoe UI", Roboto, "Helvetica Neue", sans-serif';
const FRAME_MS = 33;
const DATA_DELAY_MS = 300;
const FOV = 40;
const NEAR_M = 360;              // machines nearer the camera than this are drawn in detail
const POOLS = 96;                // pools of light on the ground at night, at most
const K = 1 / TEXTURE_MEAN;

const col = (hex) => new THREE.Color(hex);

export function createScene({ canvas, labelCanvas, panelLayer = null, onSelect, onHover, soft = false, style: startStyle = 'hologram' }) {
  const renderer = soft ? createSoftRenderer(canvas)
    : new THREE.WebGLRenderer({ canvas, antialias: true, powerPreference: 'low-power', alpha: false, stencil: false });
  renderer.setPixelRatio(1);
  // Scene light in its own units, mapped to the screen by ACES with an
  // exposure that follows the light of the hour (as a camera adapts).
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure = 1;
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  const gl = soft ? null : renderer.getContext();
  const DETAIL = soft
    ? { pitSpacing: 45, pitSamples: 140, groundCell: 320, horizon: 7000, roadStep: 30, beltStep: 160, blockDepth: 0, particles: false, minFrameMs: 160 }
    : { pitSpacing: 18, pitSamples: 250, groundCell: 70, horizon: 24000, roadStep: 12, beltStep: 30, blockDepth: null, particles: true, minFrameMs: 0 };
  const tex = textures();
  const lctx = labelCanvas.getContext('2d');
  const scene = new THREE.Scene();
  scene.background = soft ? col('#9fb3c2') : null;
  scene.fog = new THREE.Fog(0xc4cdd1, 4000, 20000);
  const camera = new THREE.PerspectiveCamera(FOV, 1, 5, 60000);
  camera.up.set(0, 0, 1);
  const motion = createMotion();

  let width = 0;
  let height = 0;
  let insets = { top: 0, right: 0, bottom: 0, left: 0 };
  let active = false;
  let raf = 0;
  let dirty = true;             // draw at the next animation frame (camera, selection, layout)
  let dataDirty = false;        // draw within frameMs (data that changed)
  let dataDue = 0;
  let lastRender = 0;
  let layer = 'none';
  let selectedId = null;
  let related = new Set();
  let affected = new Map();
  let overlayRects = [];
  let marksKey = '';
  let notes = [];
  let timing = false;

  // The mine grid is moved so that its middle is at the origin: numbers stay small for the GPU.
  const O = { x: 0, y: 0, z: 0 };
  const L = (x, y, z) => [x - O.x, y - O.y, z - O.z];

  const groups = {};
  const LAYER = { sky: -1, ground: 0, pit: 1, areas: 2, roads: 2, blocks: 2, holo: 3, plant: 4, marks: 3, events: 5, blast: 6 };
  for (const name of Object.keys(LAYER)) {
    groups[name] = new THREE.Group();
    groups[name].userData.layer = LAYER[name];
    scene.add(groups[name]);
  }

  // -- materials ----------------------------------------------------------------
  const map = (texture) => (soft ? null : texture);
  const M = {
    ground: new THREE.MeshBasicMaterial({ vertexColors: true, map: map(tex.grain) }),
    face: new THREE.MeshBasicMaterial({ vertexColors: true, map: map(tex.strata), side: THREE.DoubleSide }),
    flat: new THREE.MeshBasicMaterial({ vertexColors: true, map: map(tex.gravel), side: THREE.DoubleSide }),
    dumpSide: new THREE.MeshBasicMaterial({ vertexColors: true, map: map(tex.strata), side: THREE.DoubleSide }),
    dumpTop: new THREE.MeshBasicMaterial({ vertexColors: true, map: map(tex.gravel), side: THREE.DoubleSide }),
    pad: new THREE.MeshBasicMaterial({ vertexColors: true, map: map(tex.gravel), polygonOffset: true, polygonOffsetFactor: -1, polygonOffsetUnits: -2 }),
    piles: new THREE.MeshBasicMaterial({ vertexColors: true, map: map(tex.gravel) }),
    tracks: new THREE.MeshBasicMaterial({ vertexColors: true, map: map(tex.road), side: THREE.DoubleSide, polygonOffset: true, polygonOffsetFactor: -2, polygonOffsetUnits: -4 }),
    plant: new THREE.MeshBasicMaterial({ vertexColors: true, side: THREE.DoubleSide }),
    glass: new THREE.MeshBasicMaterial({ vertexColors: true, transparent: true, opacity: 0.24, depthWrite: false, side: THREE.DoubleSide }),
    windows: new THREE.MeshBasicMaterial({ color: 0x2a3036, side: THREE.DoubleSide, polygonOffset: true, polygonOffsetFactor: -1, polygonOffsetUnits: -2 }),
    water: new THREE.MeshBasicMaterial({ vertexColors: true, side: THREE.DoubleSide }),
    muck: new THREE.MeshBasicMaterial({ vertexColors: true, map: map(tex.gravel), polygonOffset: true, polygonOffsetFactor: -1, polygonOffsetUnits: -2 }),
    blocks: new THREE.MeshBasicMaterial({ vertexColors: true, toneMapped: false, transparent: true, opacity: 0.62, depthWrite: false, polygonOffset: true, polygonOffsetFactor: -2, polygonOffsetUnits: -4 }),
    belt: new THREE.MeshBasicMaterial({ vertexColors: true, polygonOffset: true, polygonOffsetFactor: -2, polygonOffsetUnits: -4 }),
    // The machines turn, so they light themselves (sun or moon, sky, the site's
    // lamps at night) with a little sheen on the paint; the 2D stand-in has no
    // lighting and takes them lit once.
    machine: soft ? new THREE.MeshBasicMaterial({ vertexColors: true })
      : new THREE.MeshPhongMaterial({ vertexColors: true, specular: 0x303030, shininess: 42 }),
    load: soft ? new THREE.MeshBasicMaterial({ vertexColors: true })
      : new THREE.MeshLambertMaterial({ vertexColors: true }),
    halo: new THREE.MeshBasicMaterial({ toneMapped: false, transparent: true, opacity: 0.7, depthWrite: false, side: THREE.DoubleSide, polygonOffset: true, polygonOffsetFactor: -4, polygonOffsetUnits: -8 }),
    blob: new THREE.MeshBasicMaterial({ color: 0x000000, map: tex.spot, transparent: true, opacity: 0.6, depthWrite: false, polygonOffset: true, polygonOffsetFactor: -3, polygonOffsetUnits: -6 }),
    pool: new THREE.MeshBasicMaterial({ color: 0xffd9a0, map: tex.spot, transparent: true, opacity: 0, depthWrite: false, blending: THREE.AdditiveBlending, polygonOffset: true, polygonOffsetFactor: -3, polygonOffsetUnits: -6 }),
    barrier: new THREE.MeshBasicMaterial({ vertexColors: false }),
  };
  const litMaterials = [M.machine, M.load];

  // -- the hologram look ---------------------------------------------------------------
  // Each mesh keeps its realistic material (userData.real); in the hologram
  // look it wears the hologram material of the same role instead.
  let style = startStyle === 'realistic' ? 'realistic' : 'hologram';
  const holo = () => style === 'hologram';
  const H = createHoloMaterials(soft);
  H.blocks = M.blocks.clone();
  H.blocks.blending = THREE.AdditiveBlending;
  H.blocks.opacity = 0.5;
  H.halo = M.halo.clone();
  H.halo.blending = THREE.AdditiveBlending;
  H.halo.opacity = 0.32;
  H.pool = M.pool;
  const HOLO_OF = new Map([[M.ground, H.ground], [M.face, H.face], [M.flat, H.flat], [M.dumpSide, H.dump], [M.dumpTop, H.dumpTop], [M.pad, H.pad],
    [M.piles, H.piles], [M.tracks, H.hidden], [M.plant, H.plantPlain], [M.glass, H.plantGlass], [M.windows, H.hidden], [M.water, H.water], [M.muck, H.muck],
    [M.belt, H.belt], [M.blocks, H.blocks], [M.machine, H.machine], [M.load, H.load], [M.halo, H.halo], [M.blob, H.hidden], [M.barrier, H.barrier]]);
  const holoShared = new Set([...H.all, H.blocks, H.halo, H.hidden]);
  // The 2D stand-in lights the hologram's surfaces in their colours, by kind.
  const SOFT_KIND = new Map([[M.dumpSide, 'dump'], [M.dumpTop, 'dump'], [M.water, 'water']]);

  function holoMaterialOf(mesh) {
    const real = mesh.userData.real;
    if (mesh.userData.holo) return mesh.userData.holo;
    if (real === M.plant && mesh.userData.owners) return H.plant;
    return HOLO_OF.get(real) ?? null;
  }

  // Put on a mesh the material of the current look.
  function styleMesh(mesh) {
    if (!mesh.userData.real) mesh.userData.real = mesh.material;
    const wanted = holo() ? holoMaterialOf(mesh) ?? mesh.userData.real : mesh.userData.real;
    if (holo() && wanted !== mesh.userData.real) prepare(mesh.geometry, wanted);
    if (mesh.material !== wanted) mesh.material = wanted;
  }
  function styleAll() {
    scene.traverse((node) => { if ((node.isMesh || node.isInstancedMesh) && node.userData.real) styleMesh(node); });
  }
  const lit = [];                  // static geometries lit by the sun: relit when it moves
  // Lights for the machines (the rest of the world carries its light in its colours).
  const keyLight = new THREE.DirectionalLight(0xffffff, Math.PI);
  const skyLight = new THREE.HemisphereLight(0xffffff, 0x444444, Math.PI);
  skyLight.position.set(0, 0, 1);
  const siteLight = new THREE.AmbientLight(0xffc98a, 0);
  // A cool light from behind, opposite the key light: it draws the machines' outline against the ground.
  const backLight = new THREE.DirectionalLight(0xffffff, 0);
  if (!soft) scene.add(keyLight, skyLight, siteLight, backLight);

  // -- the sky ----------------------------------------------------------------------
  const sky = soft ? null : createSky();
  if (sky) groups.sky.add(sky.dome);
  const dust = DETAIL.particles ? createDust() : null;
  if (dust) groups.events.add(dust.points);
  const weather = DETAIL.particles ? createWeather() : null;
  if (weather) { groups.events.add(weather.rain); groups.events.add(weather.snow); }
  const glows = soft ? null : createGlows(500);
  if (glows) groups.events.add(glows.points);
  const beacons = soft ? null : createGlows(60);
  if (beacons) groups.events.add(beacons.points);
  // The hologram's stage: dark above, a teal glow on the horizon.
  const holoDome = soft ? null : createDome();
  if (holoDome) groups.sky.add(holoDome);
  const keep = new Set([sky?.dome, holoDome, dust?.points, weather?.rain, weather?.snow, glows?.points, beacons?.points].filter(Boolean));
  const panels = panelLayer ? createPanels(panelLayer, lctx) : null;
  let placedPanels = [];

  // -- what the scene holds, rebuilt by reload() --------------------------------------
  let terrain = null;
  let plant = null;
  let worldBox = null;
  let surfaceZ = 0;
  let pickables = [];
  const roadMeshes = new Map();
  let blockRanges = [];
  let blocksMesh = null;
  let muckMesh = null;
  let muckKey = '';
  const padMeshes = new Map();     // pad thingId -> {piles mesh, level}
  let stockpileMesh = null;
  let stockpileLevel = -1;
  let beltMesh = null;
  const bandMaterials = new Map(); // unit thingId -> material of its state band
  let units = new Map();
  let hallAnchor = null;
  const fleet = {};
  let lodTriangles = {};           // part -> triangles of one machine's part
  const fleetIds = { truck: [], loader: [], 'drill-rig': [] };
  const shown = new Map();         // thingId -> {x, y, z, r, top}: where a machine was drawn last
  const anim = new Map();          // thingId -> {tilt, mast, slew}: animated parts
  let followers = [];
  let blast = null;                // {x, y, z, radius, block}
  let lastBlastState = 'idle';
  let pools = null;
  let towers = [];                 // floodlight towers: [{x, y, z}] (mine grid, lamp height)
  let barriers = null;
  let signs = [];
  const skyState = { key: '', elevation: 40, night: 0, lamps: 0, colors: null, sun: LIGHT.sun.clone(), weather: 'dry', dustColor: [0.62, 0.53, 0.42] };

  const heightAt = (x, y) => (terrain ? terrain.heightAt(x, y) : surfaceZ);

  // -- the Blender models (gltf3d.js) ----------------------------------------------------
  // Loaded once after the first frame of the procedural scene; until they are
  // ready (or if they cannot be loaded, or without WebGL) the procedural
  // models stand in. Their geometries are shared by every placement.
  let G = null;                    // the module gltf3d.js, once imported
  let lib = null;                  // the loaded models: {models: {id: {info, lods}}, textures}
  // ?models=0 in the address keeps the procedural models (to compare, or to check the fallback).
  let libState = soft || new URLSearchParams(window.location.search).get('models') === '0' ? 'off' : 'idle';
  let libStarted = 0;
  let libReadyAt = 0;
  const MM = {};                   // materials of the models (realistic look)
  const modelHolo = new Map();     // twin thingId -> its hologram glass for the plant models
  let placed = [];                 // plant models drawn: [{placement, rig, base, mats, meshes, angles, turning, cluster}]
  let farClusters = [];            // their silhouettes for afar: [{mesh, entries, x, y, z, far}]
  let roofMeshes = [];
  let conveyorModel = null;        // the conveyor truss tiled along the deck
  let towerMeshes = [];
  const modelIds = () => (libState === 'ready' && lib ? new Set(Object.keys(lib.models)) : null);
  const MACHINE_MODELS = { truck: 'cat785c', loader: 'ex3600', 'drill-rig': 'd55sp' };
  const PLANT_MODELS = ['crusher', 'mill_hall', 'flotation', 'thickener_tailings', 'thickener_concentrate', 'floodlight', 'conveyor'];

  // -- building ---------------------------------------------------------------------

  const shared = new Set([...Object.values(M), ...holoShared]);
  function clearGroup(group) {
    for (const child of [...group.children]) {
      if (keep.has(child)) continue;
      group.remove(child);
      child.traverse?.((node) => {
        if (!node.geometry?.userData?.keep) node.geometry?.dispose();
        const materials = new Set([node.material, node.userData.real, node.userData.holo].flat().filter(Boolean));
        materials.forEach((material) => { if (!shared.has(material)) material.dispose(); });
      });
    }
  }

  function addMesh(group, geometry, material, data = {}) {
    const mesh = new THREE.Mesh(geometry, material);
    Object.assign(mesh.userData, data);
    group.add(mesh);
    if (geometry.userData.albedo) {
      lit.push(geometry);
      geometry.userData.holoKind = SOFT_KIND.get(material) ?? (material === M.ground ? 'ground' : 'flat');
      if (soft && holo()) holoRelight(geometry, geometry.userData.holoKind);
    }
    styleMesh(mesh);
    return mesh;
  }

  function dropMesh(group, mesh) {
    if (!mesh) return;
    group.remove(mesh);
    const i = lit.indexOf(mesh.geometry);
    if (i >= 0) lit.splice(i, 1);
    pickables = pickables.filter((m) => m !== mesh);
    if (!mesh.geometry.userData?.keep) mesh.geometry.dispose();
  }

  function boxOfTwins() {
    const box = { minX: Infinity, maxX: -Infinity, minY: Infinity, maxY: -Infinity };
    const add = (x, y) => {
      box.minX = Math.min(box.minX, x); box.maxX = Math.max(box.maxX, x);
      box.minY = Math.min(box.minY, y); box.maxY = Math.max(box.maxY, y);
    };
    for (const twin of twins.values()) {
      const kind = kindOf(twin);
      if (kind === 'mine' || kind === 'truck') continue;
      const geo = twin.attributes?.geo ?? {};
      if (geo.polygon) geo.polygon.forEach(([x, y]) => add(x, y));
      else if (geo.path) geo.path.forEach(([x, y]) => add(x, y));
      else if (typeof geo.x === 'number') add(geo.x, geo.y);
    }
    return Number.isFinite(box.minX) ? box : null;
  }

  function buildWorld() {
    for (const name of ['ground', 'pit', 'areas', 'roads', 'plant', 'holo']) clearGroup(groups[name]);
    lit.length = 0;
    LIGHT.locals = [];
    LIGHT.localsVersion += 1;
    roadMeshes.clear();
    padMeshes.clear();
    bandMaterials.clear();
    terrain = buildTerrain({ L, O, surfaceZ, detail: DETAIL });
    addMesh(groups.ground, terrain.groundGeometry, M.ground, { ground: true });
    if (terrain.tracks?.triangles) addMesh(groups.ground, terrain.tracks.geometry(), M.tracks);
    if (terrain.faceGeometry) {
      const faces = addMesh(groups.pit, terrain.faceGeometry, M.face, { pit: true, owners: terrain.faceOwners });
      const flats = addMesh(groups.pit, terrain.flatGeometry, M.flat, { pit: true, owners: terrain.flatOwners });
      pickables.push(faces, flats);
      if (terrain.sump) {
        const s = terrain.sump;
        const b = new Builder();
        const n = 20;
        for (let i = 0; i < n; i += 1) {
          const a0 = (i / n) * Math.PI * 2; const a1 = ((i + 1) / n) * Math.PI * 2;
          b.tri(L(s.x, s.y, s.z + 0.4), L(s.x + Math.cos(a0) * s.rx, s.y + Math.sin(a0) * s.ry, s.z + 0.4), L(s.x + Math.cos(a1) * s.rx, s.y + Math.sin(a1) * s.ry, s.z + 0.4),
            scale(rgb('#4f8790'), 1.1), rgb('#3f6f78'), rgb('#3f6f78'));
        }
        addMesh(groups.pit, b.geometry(), M.water);
      }
    }
    for (const segment of byKind('road-segment')) {
      const path = segment.attributes?.geo?.path;
      if (!path || path.length < 2) continue;
      const road = roadGeometry(segment, terrain, L, DETAIL);
      const material = new THREE.MeshBasicMaterial({ vertexColors: true, map: map(tex.road), side: THREE.DoubleSide, polygonOffset: true, polygonOffsetFactor: -2, polygonOffsetUnits: -6 });
      const mesh = addMesh(groups.roads, road.builder.geometry(), material, { id: segment.thingId, points: road.points, width: road.width, holo: roadMaterial(H) });
      roadMeshes.set(segment.thingId, mesh);
      pickables.push(mesh);
    }
    for (const dump of byKind('waste-dump')) {
      if (!dump.attributes?.geo?.polygon) continue;
      const d = dumpGeometry(dump, terrain, L);
      pickables.push(addMesh(groups.areas, d.sides.geometry(), M.dumpSide, { id: dump.thingId }));
      pickables.push(addMesh(groups.areas, d.tops.geometry(), M.dumpTop, { id: dump.thingId, top: d.top }));
    }
    for (const pad of [...byKind('rom-pad'), ...byKind('clay-stockpile')]) {
      if (!pad.attributes?.geo?.polygon) continue;
      const p = padGeometry(pad, terrain, L, 0);
      pickables.push(addMesh(groups.areas, p.slab.geometry(), M.pad, { id: pad.thingId }));
      padMeshes.set(pad.thingId, { piles: null, level: -1, z0: p.z0 });
    }
    buildPlantMeshes();
    muckKey = '';
    buildTowers();
    siteLamps();
    buildHoloLines();
  }

  // The plant: built procedurally, with the Blender models where they are loaded.
  // Run again (alone) when the models arrive.
  function buildPlantMeshes() {
    const old = new Set();
    groups.plant.traverse((node) => { old.add(node); const i = lit.indexOf(node.geometry); if (i >= 0) lit.splice(i, 1); });
    pickables = pickables.filter((mesh) => !old.has(mesh));
    clearGroup(groups.plant);
    bandMaterials.clear();
    plant = buildPlant({ L, terrain, detail: DETAIL, models: modelIds() });
    units = plant.units;
    hallAnchor = plant.hallAnchor;
    plantMesh = plant.opaque.triangles ? addMesh(groups.plant, plant.opaque.geometry(), M.plant, { owners: plant.opaque.owner }) : null;
    plantOwners = [...new Set(plant.opaque.owner)];
    plantTintKey = '';
    if (plantMesh) pickables.push(plantMesh);
    if (plant.glass.triangles) addMesh(groups.plant, plant.glass.geometry(), M.glass, { owners: plant.glass.owner });
    if (plant.win.triangles) addMesh(groups.plant, plant.win.machineGeometry(), M.windows);
    if (plant.water.triangles) pickables.push(addMesh(groups.plant, plant.water.geometry(), M.water, { owners: plant.water.owner }));
    for (const band of plant.bands) {
      const b = new Builder();
      b.append(band.builder, band.angle, band.at);
      const material = new THREE.MeshBasicMaterial({ color: 0xffffff, side: THREE.DoubleSide, toneMapped: false });
      const mesh = new THREE.Mesh(b.machineGeometry(), material);
      mesh.userData.id = band.id;
      mesh.userData.roof = Boolean(band.roof);
      groups.plant.add(mesh);
      bandMaterials.set(band.id, material);
    }
    beltMesh = null;
    if (plant.belt) {
      beltMesh = addMesh(groups.plant, plant.belt.builder.machineGeometry(), M.belt, { id: plant.belt.twin.thingId });
      beltMesh.userData.twin = plant.belt.twin;
    }
    if (plant.apron) {
      const b = new Builder({ uv: true });
      const faces = THREE.ShapeUtils.triangulateShape(plant.apron.map(([x, y]) => new THREE.Vector2(x, y)), []);
      const cut = (a, c1, c2, depth) => {
        if (depth === 0) {
          b.tri(L(a[0], a[1], heightAt(a[0], a[1]) + 0.2), L(c1[0], c1[1], heightAt(c1[0], c1[1]) + 0.2), L(c2[0], c2[1], heightAt(c2[0], c2[1]) + 0.2), scale(rgb('#878074'), K));
          return;
        }
        const m = (p, q) => [(p[0] + q[0]) / 2, (p[1] + q[1]) / 2];
        const ab = m(a, c1); const bc = m(c1, c2); const ca = m(c2, a);
        cut(a, ab, ca, depth - 1); cut(ab, c1, bc, depth - 1); cut(ca, bc, c2, depth - 1); cut(ab, bc, ca, depth - 1);
      };
      for (const [i, j, k] of faces) cut(plant.apron[i], plant.apron[j], plant.apron[k], soft ? 1 : 4);
      addMesh(groups.plant, b.geometry(), M.pad);
    }
    stockpileMesh = null;
    stockpileLevel = -1;
    placePlantModels();
  }

  // The lamps of the site light the ground round them once it is dark.
  function siteLamps() {
    LIGHT.locals = [
      ...towers.map((t) => ({ x: t.cx - O.x, y: t.cy - O.y, z: t.z - O.z, color: [1.5, 1.22, 0.85], range: 170, core: 70 })),
      ...plant.lamps.map(([x, y, z]) => ({ x: x - O.x, y: y - O.y, z: z - O.z, color: [1.0, 0.82, 0.58], range: 110, core: 50 })),
    ];
    LIGHT.localsVersion += 1;
  }

  // -- the Blender models: loading, materials, plant placements -----------------------------

  const LIFT = 0.3;                 // plant models stand this much over the ground (and over the gravel apron)

  function startModels() {
    if (libState !== 'idle') return;
    libState = 'loading';
    libStarted = performance.now();
    const ids = [...Object.values(MACHINE_MODELS), ...PLANT_MODELS];
    if (byKind('clay-plant').length) ids.push('clay_plant');
    import('./gltf3d.js')
      .then((module) => { G = module; return G.loadModels(ids); })
      .then((loaded) => {
        lib = loaded;
        if (!Object.keys(lib.models).length) throw new Error('no model could be loaded');
        createModelMaterials();
        libState = 'ready';
        libReadyAt = performance.now();
        applyModels();
      })
      .catch((error) => {
        libState = 'failed';
        console.warn(`3D: the Blender models could not be loaded (${error?.message ?? error}); the procedural models stay`);
      });
  }

  // A model needed later (the clay-ore plant, when its twins appear).
  function loadMore(ids) {
    const wanted = ids.filter((id) => !lib.models[id] && !lib.failed.includes(id) && !lib.pending?.has(id));
    if (!wanted.length) return;
    lib.pending = new Set([...(lib.pending ?? []), ...wanted]);
    G.loadModels(wanted, { library: lib }).then(() => {
      for (const id of wanted) lib.pending.delete(id);
      applyModels();
    }).catch(() => {});
  }

  function createModelMaterials() {
    if (MM.plain) return;
    MM.plain = G.modelMaterial();
    MM.double = G.modelMaterial({ side: THREE.DoubleSide });
    for (const [kind, texture] of lib.textures) {
      MM[kind] = G.modelMaterial({ map: texture });
      MM[`${kind}|2`] = G.modelMaterial({ map: texture, side: THREE.DoubleSide });
    }
    for (const material of Object.values(MM)) shared.add(material);
    applyModelLight();
  }
  const materialOf = (layer) => (layer.texture ? MM[layer.double ? `${layer.texture}|2` : layer.texture] ?? MM.plain : layer.double ? MM.double : MM.plain);

  // The light of the hour on the models: the sky and the ground they reflect, their lamps at night.
  function applyModelLight() {
    const p = skyState.colors;
    for (const material of Object.values(MM)) {
      const env = material.userData.env;
      if (!env || !p) continue;
      env.uEnvSky.value.setRGB(...p.sky);
      env.uEnvGround.value.setRGB(...p.ground);
      env.uLamp.value = 0.12 + 1.6 * (p.lamps ?? 0);
    }
  }

  // The hologram's glass of a unit of the plant, in the colour of its state.
  function holoOf(id) {
    if (soft || !H.plantPlain?.clone) return H.plantPlain;
    let material = modelHolo.get(id ?? '');
    if (!material) {
      material = H.plantPlain.clone();
      material.userData.needs = H.plantPlain.userData.needs;
      material.uniforms.uColor.value.set(unitTint(id));
      modelHolo.set(id ?? '', material);
      shared.add(material);
      setFog([material], scene.fog.near, scene.fog.far);
    }
    return material;
  }

  // The models arrived: the plant and the fleet are drawn with them.
  function applyModels() {
    if (!terrain || libState !== 'ready') return;
    buildPlantMeshes();
    for (const mesh of towerMeshes) dropMesh(groups.pit, mesh);
    towerMeshes = [];
    towerModel = null;
    buildTowers();
    siteLamps();
    buildFleet();
    updateAreas();
    roadsKey = '';
    updateRoads();
    plantTintKey = '';
    updatePlant();
    marksKey = '';
    dirty = true;
  }

  // The plant's models where buildPlant() left their placements.
  function placePlantModels() {
    placed = [];
    farClusters = [];
    roofMeshes = [];
    roofOpen = false;
    insideKey = '';
    conveyorModel = null;
    if (!lib || libState !== 'ready') return;
    for (const p of plant.placements ?? []) {
      const model = lib.models[p.model];
      const rig = model?.lods[0];
      if (!rig) continue;
      const base = new THREE.Matrix4().compose(new THREE.Vector3(...L(p.x, p.y, p.z + LIFT)), new THREE.Quaternion().setFromAxisAngle(zAxis, p.angle), new THREE.Vector3(1, 1, 1));
      const mats = rig.parts.map(() => new THREE.Matrix4());
      const angles = {};
      G.poseRig(rig, base, angles, mats);
      const entry = { placement: p, rig, base, mats, angles, meshes: rig.parts.map(() => []), turning: [], cluster: null };
      rig.parts.forEach((part, i) => {
        const owner = p.owners?.[part.name] ?? p.id;
        for (const layer of part.layers) {
          const material = materialOf(layer);
          const mesh = new THREE.Mesh(layer.geometry, material);
          mesh.matrixAutoUpdate = false;
          mesh.matrix.copy(mats[i]);
          mesh.userData.id = owner;
          mesh.userData.real = material;
          mesh.userData.holo = part.name === 'water' ? H.water : holoOf(owner);
          mesh.userData.layer = 4;
          groups.plant.add(mesh);
          styleMesh(mesh);
          if (part.name !== 'water') pickables.push(mesh);
          entry.meshes[i].push(mesh);
          if (part.name === 'roof') roofMeshes.push(mesh);
        }
        const twinId = p.turning?.[part.name];
        if (twinId !== undefined && part.axis) entry.turning.push({ index: i, name: part.name, twin: twinId, speed: part.name === 'rake' ? 0.05 : part.name === 'sag_shell' ? 1.0 : 1.4 });
      });
      placed.push(entry);
    }
    // Their silhouettes for afar, one mesh for each group of models standing together (the
    // crusher, the clay-ore plant, the rest of the plant), drawn instead of them beyond FAR_PLANT_M.
    farClusters = [];
    const byCluster = new Map();
    for (const entry of placed) {
      const key = entry.placement.model === 'crusher' ? 'crusher' : entry.placement.model === 'clay_plant' ? 'clay' : 'plant';
      if (!byCluster.has(key)) byCluster.set(key, []);
      byCluster.get(key).push(entry);
    }
    for (const entries of byCluster.values()) {
      const b = new Builder();
      for (const { placement: p } of entries) {
        const proxy = modelProxy(p.model);
        if (proxy.triangles) b.append(proxy, p.angle, L(p.x, p.y, p.z + LIFT), p.id);
        if (!plantOwners.includes(p.id)) plantOwners.push(p.id);
      }
      if (!b.triangles) continue;
      const mesh = addMesh(groups.plant, b.geometry(), M.plant, { owners: b.owner });
      mesh.visible = false;
      pickables.push(mesh);
      const cluster = { mesh, entries, x: entries.reduce((sum, e) => sum + e.placement.x, 0) / entries.length,
        y: entries.reduce((sum, e) => sum + e.placement.y, 0) / entries.length, z: entries[0].placement.z };
      for (const entry of entries) entry.cluster = cluster;
      farClusters.push(cluster);
    }
    if (plant.conveyorDeck && lib.models.conveyor) placeConveyor(plant.conveyorDeck, plant.belt?.twin);
  }

  // Turn the mill shells (while their mill runs) and the thickener rakes.
  let plantTurning = false;
  function stepPlantModels(dt) {
    plantTurning = false;
    if (!placed.length) return;
    const paused = feature(first('mine'), 'sim_state') === 'paused';
    const near = hallAnchor && Math.hypot(camera.position.x + O.x - hallAnchor.x, camera.position.y + O.y - hallAnchor.y) < 2400;
    for (const entry of placed) {
      if (!entry.turning.length) continue;
      let changed = false;
      for (const t of entry.turning) {
        const state = feature(twins.get(t.twin), 'state');
        const running = !paused && !['stopped', 'down', 'idle', 'standby', 'paused'].includes(state);
        if (!running) continue;
        if (t.speed > 0.5 && near) plantTurning = true;
        if (!dt) continue;
        entry.angles[t.name] = ((entry.angles[t.name] ?? 0) + t.speed * dt) % (Math.PI * 2);
        changed = true;
      }
      if (!changed) continue;
      G.poseRig(entry.rig, entry.base, entry.angles, entry.mats);
      for (const t of entry.turning) for (const mesh of entry.meshes[t.index]) mesh.matrix.copy(entry.mats[t.index]);
    }
  }

  // The roof of the mill hall lifts off when the camera comes close, to show the mills; the
  // machinery inside is drawn only when it can be seen (roof off, or through the hologram's glass, not too far).
  let roofOpen = false;
  let insideKey = '';
  const INSIDE = new Set(['sag_mill', 'sag_shell', 'ball_mill_1', 'ball_shell_1', 'ball_mill_2', 'ball_shell_2', 'crane']);
  // Beyond this a plant model is drawn as its silhouette (modelProxy).
  const FAR_PLANT_M = 1600;
  function stepRoof() {
    if (!placed.length) return;
    const p = camera.position;
    const dist = (x, y, z) => Math.hypot(p.x + O.x - x, p.y + O.y - y, p.z + O.z - z);
    const hallD = hallAnchor ? dist(hallAnchor.x, hallAnchor.y, hallAnchor.z) : Infinity;
    const open = roofMeshes.length > 0 && hallD < 1100;
    const inside = open || (holo() && hallD < 2600);
    for (const cluster of farClusters) cluster.far = dist(cluster.x, cluster.y, cluster.z) > FAR_PLANT_M;
    const key = `${open}|${inside}|${farClusters.map((cluster) => Number(cluster.far)).join('')}`;
    if (key === insideKey) return;
    insideKey = key;
    roofOpen = open;
    for (const cluster of farClusters) cluster.mesh.visible = cluster.far;
    placed.forEach((entry) => {
      const far = Boolean(entry.cluster?.far);
      entry.rig.parts.forEach((part, i) => {
        let visible = !far;
        if (part.name === 'roof') visible = visible && !open;
        if (entry.placement.model === 'mill_hall' && INSIDE.has(part.name)) visible = visible && inside;
        for (const mesh of entry.meshes[i]) mesh.visible = visible;
      });
    });
    for (const mesh of groups.plant.children) if (mesh.userData.roof) mesh.visible = !open;
  }

  // The floodlight towers: the detailed model near the camera, the simple one afar.
  let towerModel = null;
  const NEAR_TOWER_M = 300;
  function stepTowers(force = false) {
    const t = towerModel;
    if (!t) return;
    if (!force && t.at && t.at.distanceTo(camera.position) < 20) return;
    (t.at ??= new THREE.Vector3()).copy(camera.position);
    const cx = camera.position.x + O.x; const cy = camera.position.y + O.y; const cz = camera.position.z + O.z;
    const near = t.items.map((item) => Math.hypot(item.x - cx, item.y - cy, item.z - cz) < NEAR_TOWER_M);
    const key = near.map((n) => (n ? 1 : 0)).join('');
    if (!force && key === t.key) return;
    t.key = key;
    t.lods.forEach((meshes, li) => {
      const take = t.items.filter((_, k) => near[k] === (li === 0) || (t.lods.length === 1));
      for (const mesh of meshes) {
        take.forEach((item, n) => mesh.setMatrixAt(n, item.matrix));
        mesh.count = take.length;
        mesh.visible = take.length > 0;
        mesh.instanceMatrix.needsUpdate = true;
      }
    });
  }

  // The conveyor: the 24 m truss segment of the model tiled along the deck,
  // in detail near the camera, simplified afar, on trestles down to the ground.
  function placeConveyor(deck, beltTwin) {
    const model = lib.models.conveyor;
    const segments = [];
    let i = 1;
    const total = deck[deck.length - 1][4];
    const pointAt = (s) => {
      while (i < deck.length - 1 && deck[i][4] < s) i += 1;
      while (i > 1 && deck[i - 1][4] > s) i -= 1;
      const a = deck[i - 1]; const b = deck[i];
      const f = clamp((s - a[4]) / Math.max(1e-6, b[4] - a[4]), 0, 1);
      return [a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f, a[2] + (b[2] - a[2]) * f, a[3] + (b[3] - a[3]) * f];
    };
    for (let s = 0; s < total - 1; s += 24) {
      const p = pointAt(s);
      const q = pointAt(Math.min(total, s + 24));
      const dx = q[0] - p[0]; const dy = q[1] - p[1]; const dz = q[2] - p[2];
      const run = Math.hypot(dx, dy);
      if (run < 0.5) continue;
      const yaw = Math.atan2(dy, dx) - Math.PI / 2;
      const pitch = Math.atan2(dz, run);
      const length = Math.hypot(run, dz);
      const origin = new THREE.Vector3(...L(p[0], p[1], p[2] - 0.6));
      const rotation = new THREE.Quaternion().setFromAxisAngle(zAxis, yaw).multiply(new THREE.Quaternion().setFromAxisAngle(xAxis, pitch));
      const truss = new THREE.Matrix4().compose(origin, rotation, new THREE.Vector3(1, length / 24, 1));
      const h = p[2] - 0.6 - heightAt(p[0], p[1]);
      const trestle = h > 1.2 ? new THREE.Matrix4().compose(origin, new THREE.Quaternion().setFromAxisAngle(zAxis, yaw), new THREE.Vector3(1, 1, h)) : null;
      segments.push({ x: p[0], y: p[1], z: p[2], truss, trestle });
    }
    const lods = model.lods.map((rig) => {
      const parts = {};
      for (const part of rig.parts) {
        parts[part.name] = part.layers.map((layer) => {
          const mesh = new THREE.InstancedMesh(layer.geometry, materialOf(layer), Math.max(1, segments.length));
          mesh.count = 0;
          mesh.frustumCulled = false;
          mesh.userData.layer = 4;
          mesh.userData.real = mesh.material;
          mesh.userData.holo = part.name === 'ore' ? H.hidden : holoOf(beltTwin?.thingId);
          groups.plant.add(mesh);
          styleMesh(mesh);
          return mesh;
        });
      }
      return { rig, parts };
    });
    conveyorModel = { segments, lods, key: '', twin: beltTwin };
    stepConveyor(true);
    insideKey = '';
  }

  // Which segments are drawn in detail: those near the camera (again when the camera has moved).
  const NEAR_BELT_M = 260;
  function stepConveyor(force = false) {
    const c = conveyorModel;
    if (!c) return;
    const running = feature(c.twin, 'load_tph') > 0 && feature(c.twin, 'state') !== 'stopped';
    // Again only when the camera has moved some way, or the belt started or stopped.
    if (!force && c.at && c.at.distanceTo(camera.position) < 20 && c.running === running) return;
    (c.at ??= new THREE.Vector3()).copy(camera.position);
    c.running = running;
    const cx = camera.position.x + O.x; const cy = camera.position.y + O.y; const cz = camera.position.z + O.z;
    const near = c.segments.map((s) => Math.hypot(s.x - cx, s.y - cy, s.z - cz) < NEAR_BELT_M);
    const key = `${near.map((n) => (n ? 1 : 0)).join('')}|${running}|${style}`;
    if (!force && key === c.key) return;
    c.key = key;
    c.lods.forEach((lod, li) => {
      const take = c.segments.filter((_, k) => near[k] === (li === 0));
      for (const [name, meshes] of Object.entries(lod.parts)) {
        for (const mesh of meshes) {
          let n = 0;
          for (const s of take) {
            const m = name === 'trestle' ? s.trestle : s.truss;
            if (!m) continue;
            mesh.setMatrixAt(n, m);
            n += 1;
          }
          mesh.count = name === 'ore' && (!running || holo()) ? 0 : n;
          mesh.visible = mesh.count > 0;
          mesh.instanceMatrix.needsUpdate = true;
        }
      }
    });
    // The ore stripe of the procedural belt is the hologram's lit lane; the model's ore stands for it in the realistic look.
    if (beltMesh) beltMesh.visible = holo();
  }

  // The hologram's glowing lines: the crest and the toe of every bench (the
  // benches being worked brighter), and the lit edges of every road.
  let contours = null;
  let lanes = null;
  const workedZ = new Set();
  function worked() {
    const next = new Set();
    for (const kind of ['loader', 'drill-rig']) {
      for (const twin of byKind(kind)) {
        const z = twins.get(feature(twin, 'block'))?.attributes?.geo?.z;
        if (typeof z === 'number' && !['down'].includes(feature(twin, 'state'))) next.add(Math.round(z));
      }
    }
    return next;
  }
  function buildHoloLines() {
    contours = null;
    lanes = null;
    if (terrain?.pit) {
      workedZ.clear();
      for (const z of worked()) workedZ.add(z);
      contours = contourLines(terrain.pit, terrain.pitZs, L, (z) => [...workedZ].some((w) => Math.abs(w - z) < 1.5));
      groups.holo.add(contours);
      if (soft) contours.material.color.set(HOLO.crest);      // the 2D stand-in draws a line in one colour
    }
    const roads = [...roadMeshes.entries()].map(([id, mesh]) => ({ id, points: mesh.userData.points, width: mesh.userData.width }));
    if (roads.length) {
      lanes = laneLines(roads, L);
      groups.holo.add(lanes);
      if (soft) lanes.material.color.set(HOLO.lane);
    }
    groups.holo.visible = holo();
  }
  function updateHoloLines() {
    if (contours) {
      const next = worked();
      if ([...next].sort().join() !== [...workedZ].sort().join()) {
        workedZ.clear();
        for (const z of next) workedZ.add(z);
        contours.userData.recolour();
      }
    }
    if (lanes) {
      lanes.userData.recolour((id) => {
        const segment = twins.get(id);
        const status = feature(segment, 'status');
        if (status === 'closed') return HOLO.red;
        if (status === 'restricted') return HOLO.amber;
        return feature(segment, 'surface') === 'wet' ? '#4fb6ff' : HOLO.lane;
      });
    }
  }

  // Mobile floodlight towers: along the ramps, on the pit floor, by the
  // shovels, at the ROM pads and the dump's tipping point. A trailer, a mast
  // and a frame of four lamps (lit at night).
  function buildTowers() {
    towers = [];
    towerMeshes = [];
    const spots = [];
    const pitModel = terrain?.pit;
    const free = (x, y) => spots.every((p) => Math.hypot(p[0] - x, p[1] - y) > 140);
    for (const [id, mesh] of roadMeshes) {
      const segment = twins.get(id);
      const cls = segment?.attributes?.spec?.road_class;
      if (cls !== 'ramp' && cls !== 'bench') continue;
      const pts = mesh.userData.points;
      let run = 120;
      for (let i = 1; i < pts.length; i += 1) {
        run += Math.hypot(pts[i][0] - pts[i - 1][0], pts[i][1] - pts[i - 1][1]);
        if (run < 280) continue;
        const [x, y, z] = pts[i];
        const dx = pts[i][0] - pts[i - 1][0]; const dy = pts[i][1] - pts[i - 1][1];
        const len = Math.hypot(dx, dy) || 1;
        const off = mesh.userData.width / 2 + 7;
        // On the side against the wall, out of the way of the trucks.
        const sides = [1, -1].map((s) => [x - (dy / len) * off * s, y + (dx / len) * off * s]);
        const [px, py] = heightAt(...sides[0]) >= heightAt(...sides[1]) ? sides[0] : sides[1];
        if (!free(px, py)) continue;
        spots.push([px, py, Math.max(z, heightAt(px, py))]);
        run = 0;
      }
    }
    for (const loader of byKind('loader')) {
      const at = equipmentPlace(loader);
      if (!at || !pitCentre()) continue;
      const c = pitCentre();
      const d = Math.hypot(c.x - at.x, c.y - at.y) || 1;
      const px = at.x + ((c.x - at.x) / d) * 45; const py = at.y + ((c.y - at.y) / d) * 45;
      if (free(px, py)) spots.push([px, py, heightAt(px, py)]);
    }
    if (pitModel) {
      const floor = pitModel.loops[pitModel.loops.length - 1].pts;
      const fc = areaCentroid(floor);
      const far = floor.reduce((best, p) => (Math.hypot(p[0] - fc.x, p[1] - fc.y) > Math.hypot(best[0] - fc.x, best[1] - fc.y) ? p : best), floor[0]);
      for (const f of [-0.35, 0.35]) {
        const px = fc.x + (far[0] - fc.x) * f; const py = fc.y + (far[1] - fc.y) * f;
        if (free(px, py)) spots.push([px, py, heightAt(px, py)]);
      }
    }
    for (const pad of byKind('rom-pad')) {
      const polygon = pad.attributes?.geo?.polygon;
      if (!polygon) continue;
      const c = areaCentroid(polygon);
      const corner = polygon.reduce((best, p) => (p[1] < best[1] ? p : best), polygon[0]);
      const px = corner[0] + (c.x - corner[0]) * 0.1; const py = corner[1] + (c.y - corner[1]) * 0.1;
      if (free(px, py)) spots.push([px, py, heightAt(px, py)]);
    }
    for (const dump of byKind('waste-dump')) {
      const spec = dump.attributes?.spec ?? {};
      if (typeof spec.tip_x !== 'number') continue;
      const top = pickables.find((m) => m.userData.id === dump.thingId && m.userData.top)?.userData.top;
      const c = top ?? { x: spec.tip_x, y: spec.tip_y };
      const px = spec.tip_x + (c.x - spec.tip_x) * 0.25; const py = spec.tip_y + (c.y - spec.tip_y) * 0.25;
      if (free(px, py)) spots.push([px, py, top?.z ?? heightAt(px, py)]);
    }
    if (!spots.length) return;
    const tower = libState === 'ready' ? lib?.models.floodlight : null;
    towerModel = null;
    if (tower) {
      // The Blender tower (it faces local +y): one instanced mesh per part and level of detail
      // for all of them, the detailed one for the towers near the camera.
      const items = spots.map(([x, y, z]) => {
        const toward = pitCentre() ? Math.atan2(pitCentre().y - y, pitCentre().x - x) : 0;
        towers.push({ x: x + Math.cos(toward) * 1.2, y: y + Math.sin(toward) * 1.2, z: z + 9.5, cx: x + Math.cos(toward) * 6, cy: y + Math.sin(toward) * 6 });
        return { x, y, z, matrix: new THREE.Matrix4().compose(new THREE.Vector3(...L(x, y, z + 0.1)), new THREE.Quaternion().setFromAxisAngle(zAxis, toward - Math.PI / 2), new THREE.Vector3(1, 1, 1)) };
      });
      const lods = tower.lods.map((rig) => rig.parts.flatMap((part) => part.layers.map((layer) => {
        const lamp = part.name === 'lamps';
        const mesh = new THREE.InstancedMesh(layer.geometry, lamp ? M.windows : materialOf(layer), items.length);
        mesh.count = 0;
        mesh.frustumCulled = false;
        mesh.userData.layer = 4;
        mesh.userData.real = mesh.material;
        mesh.userData.holo = lamp ? H.hidden : H.plantPlain;
        groups.pit.add(mesh);
        styleMesh(mesh);
        towerMeshes.push(mesh);
        return mesh;
      })));
      towerModel = { items, lods, key: '' };
      stepTowers(true);
      return;
    }
    const b = new Builder();
    const lamps = new Builder();
    const MAST = 13;
    for (const [x, y, z] of spots) {
      const toward = pitCentre() ? Math.atan2(pitCentre().y - y, pitCentre().x - x) : 0;
      const t = new Builder();
      const tl = new Builder();
      t.box(0, 0, 0.4, 4.2, 1.8, 1.5, rgb('#d9d4c4'));                  // trailer with the generator
      t.box(0, 0, 0.0, 0.5, 1.6, 0.6, rgb('#3a3c3e'));
      t.box(-1.4, 0, 0, 0.3, 1.9, 0.9, rgb('#2a2a2a'));                 // wheels
      t.beam([0.6, 0, 1.9], [0.6, 0, MAST], 0.3, 0.3, rgb('#9a9c98'));   // mast
      t.box(0.6, 0, MAST, 0.2, 3.0, 1.4, rgb('#3a3c3e'));                // lamp frame
      for (const [dy, dz] of [[-0.75, 0.35], [0.75, 0.35], [-0.75, 1.05], [0.75, 1.05]]) {
        t.box(0.75, dy, MAST + dz - 0.3, 0.3, 0.62, 0.55, rgb('#2a2c2e'));
        tl.quad([0.92, dy - 0.26, MAST + dz - 0.26], [0.92, dy + 0.26, MAST + dz - 0.26], [0.92, dy + 0.26, MAST + dz + 0.2], [0.92, dy - 0.26, MAST + dz + 0.2], [1, 1, 1], false, null, null, [1, 0, 0]);
      }
      b.append(t, toward, L(x, y, z));
      lamps.append(tl, toward, L(x, y, z));
      towers.push({ x: x + Math.cos(toward) * 0.9, y: y + Math.sin(toward) * 0.9, z: z + MAST + 0.6, cx: x + Math.cos(toward) * 6, cy: y + Math.sin(toward) * 6 });
    }
    towerMeshes = [addMesh(groups.pit, b.geometry(), M.plant), addMesh(groups.pit, lamps.machineGeometry(), M.windows)];
  }

  // The stockpile and the ROM pads: rebuilt when their level has changed by a step.
  function updateAreas() {
    for (const [id, entry] of padMeshes) {
      const pad = twins.get(id);
      const level = Math.round(clamp((feature(pad, 'level_pct') ?? 0) / 100, 0, 1) * 20) / 20;
      if (level === entry.level) continue;
      entry.level = level;
      dropMesh(groups.areas, entry.piles);
      const p = padGeometry(pad, terrain, L, level);
      entry.piles = p.piles.triangles ? addMesh(groups.areas, p.piles.geometry(), M.piles, { id }) : null;
      if (entry.piles) pickables.push(entry.piles);
    }
    if (plant?.stockpile) {
      const s = plant.stockpile;
      const level = Math.round(clamp((feature(s.twin, 'level_pct') ?? 0) / 100, 0, 1) * 25) / 25;
      const belt = first('conveyor');
      const feeding = feature(belt, 'load_tph') > 0 && feature(belt, 'state') !== 'stopped';
      const key = `${level}|${feeding}`;
      if (key !== stockpileLevel) {
        stockpileLevel = key;
        dropMesh(groups.plant, stockpileMesh);
        const k = Math.cbrt(Math.max(0.03, level));
        const b = new Builder({ uv: true });
        const [x, y, z] = L(s.x, s.y, s.z);
        const r = s.r * k;
        const h = s.h * k;
        // A conical pile of crushed ore at its angle of repose: a little
        // irregular round its toe, runnels down its flanks, fresh ore
        // (lighter) near the top where the stacker drops it, damp at the toe.
        const n = soft ? 12 : 40;
        const rings = soft ? 1 : 8;
        const ore = rgb('#6e5a4a');
        const fresh = rgb('#8a7260');
        const damp = rgb('#4f4136');
        const at = (i, j) => {
          const a = (i / n) * Math.PI * 2;
          const f = j / rings;                        // 0 at the toe, 1 at the top
          const wobble = (noise2(Math.cos(a) * 2.2 + 5, Math.sin(a) * 2.2, 8) - 0.5) * 0.1 * (1 - f);
          const runnel = Math.sin(a * 23 + noise2(i * 0.3, j, 9) * 3) * 0.05 * (1 - f) * f;
          const rr = r * (1 - f) * (1 + wobble + runnel);
          // Slightly concave flanks, as tipped rock settles.
          const zz = h * f;
          return [x + Math.cos(a) * rr, y + Math.sin(a) * rr, z + zz];
        };
        const colorAt = (i, j) => {
          const f = j / rings;
          let c = mix(damp, ore, clamp(f * 3, 0, 1));
          c = mix(c, fresh, clamp((f - 0.55) * 2.2, 0, 1) * 0.8);
          // Streaks down the flanks where the ore ran (coarser and finer, drier and damper).
          const streak = noise2(i * 0.85, 0.5, 4) * 0.7 + noise2(i * 2.3, j * 0.2, 6) * 0.3;
          return scale(c, K * (0.7 + 0.5 * streak) * (0.95 + 0.1 * noise2(i * 0.9, j * 1.3, 7)));
        };
        const uv = (ii, jj) => [((ii / n) * Math.PI * 2 * r) / 14, ((1 - jj / rings) * Math.hypot(r, h)) / 14];
        for (let j = 0; j < rings; j += 1) {
          for (let i = 0; i < n; i += 1) {
            if (j === rings - 1) {
              b.tri(at(i, j), at(i + 1, j), [x, y, z + h], colorAt(i, j), colorAt(i + 1, j), colorAt(i, rings), true, [uv(i, j), uv(i + 1, j), uv(i + 0.5, rings)]);
            } else {
              b.quad(at(i, j), at(i + 1, j), at(i + 1, j + 1), at(i, j + 1), null, true, [uv(i, j), uv(i + 1, j), uv(i + 1, j + 1), uv(i, j + 1)],
                [colorAt(i, j), colorAt(i + 1, j), colorAt(i + 1, j + 1), colorAt(i, j + 1)]);
            }
          }
        }
        // The stream of ore falling from the stacker's head onto the top.
        if (!soft && level > 0.02 && feeding) {
          const top = z + s.h + 5;
          const c0 = scale(fresh, K * 0.9);
          for (let i = 0; i < 6; i += 1) {
            const a0 = (i / 6) * Math.PI * 2; const a1 = ((i + 1) / 6) * Math.PI * 2;
            b.quad([x + Math.cos(a0) * 1.1, y + Math.sin(a0) * 1.1, top], [x + Math.cos(a1) * 1.1, y + Math.sin(a1) * 1.1, top],
              [x + Math.cos(a1) * 2.2, y + Math.sin(a1) * 2.2, z + h], [x + Math.cos(a0) * 2.2, y + Math.sin(a0) * 2.2, z + h], c0, false, null, null,
              [Math.cos((a0 + a1) / 2), Math.sin((a0 + a1) / 2), 0]);
          }
        }
        stockpileMesh = addMesh(groups.plant, b.geometry(), M.piles, { id: s.twin.thingId });
        pickables.push(stockpileMesh);
      }
    }
    if (beltMesh) {
      const belt = beltMesh.userData.twin;
      const running = feature(belt, 'load_tph') > 0 && feature(belt, 'state') !== 'stopped';
      M.belt.color.copy(running ? col('#7a5f48') : col('#2c2c2b'));
      const beltColor = H.belt.uniforms?.uColor.value ?? H.belt.color;
      beltColor.set(running ? '#8ff6ff' : HOLO.red);
      if (H.belt.uniforms) H.belt.uniforms.uGain.value = running ? 1.4 : 0.6;
    }
  }

  let plantMesh = null;
  let plantOwners = [];
  let plantTintKey = '';
  function updatePlant() {
    for (const [id, material] of bandMaterials) material.color.set(stateColor(feature(twins.get(id), 'state') ?? 'running'));
    // The hologram of each unit takes the colour of its state (the plant's merged meshes per
    // triangle, the models by their own glass).
    for (const [id, material] of modelHolo) material.uniforms.uColor.value.set(unitTint(id || null));
    if (!holo()) return;
    const meshes = [plantMesh, ...farClusters.map((cluster) => cluster.mesh)].filter((mesh) => mesh?.geometry.attributes.tint);
    if (!meshes.length) return;
    const colourOf = new Map(plantOwners.map((id) => [id, unitTint(id)]));
    const key = [...colourOf.values()].join('|') + meshes.length;
    if (key === plantTintKey) return;
    plantTintKey = key;
    const c = new THREE.Color();
    for (const mesh of meshes) {
      const owners = mesh.userData.owners;
      const attribute = mesh.geometry.attributes.tint;
      const array = attribute.array;
      for (let t = 0; t < owners.length; t += 1) {
        c.set(colourOf.get(owners[t]) ?? unitTint(owners[t]));
        for (let v = 0; v < 3; v += 1) {
          const o = (t * 3 + v) * 3;
          array[o] = c.r; array[o + 1] = c.g; array[o + 2] = c.b;
        }
      }
      attribute.needsUpdate = true;
    }
  }
  function unitTint(id) {
    if (!id) return HOLO.glass;
    if (id === selectedId) return HOLO.select;
    const tone = holoTone(feature(twins.get(id), 'state') ?? 'running');
    if (tone === 'bad') return HOLO.red;
    if (tone === 'warn' || affected.has(id)) return HOLO.amber;
    return HOLO.glass;
  }

  // -- roads: state, barriers and signs ---------------------------------------------------

  function roadTint(segment) {
    const status = feature(segment, 'status');
    const wet = feature(segment, 'surface') === 'wet';
    const c = new THREE.Color(1, 1, 1);
    if (wet) c.setRGB(0.62, 0.66, 0.74);
    if (status === 'closed') c.multiply(new THREE.Color(1.0, 0.72, 0.68));
    else if (status === 'restricted') c.multiply(new THREE.Color(1.0, 0.93, 0.74));
    return c;
  }

  let roadsKey = '';
  function updateRoads() {
    const key = byKind('road-segment').map((s) => `${feature(s, 'status')}${feature(s, 'surface')}`).join('|');
    for (const [id, mesh] of roadMeshes) {
      const segment = twins.get(id);
      mesh.userData.real.color.copy(roadTint(segment));
      const lane = mesh.userData.holo;
      const status = feature(segment, 'status');
      const tint = status === 'closed' ? [1.6, 0.6, 0.65] : status === 'restricted' ? [1.15, 1.0, 0.82] : [1, 1, 1];
      if (lane?.uniforms) lane.uniforms.uTint.value.setRGB(...tint);
      else if (lane?.color) lane.color.setRGB(...tint);
    }
    updateHoloLines();
    if (key === roadsKey) return;
    roadsKey = key;
    // Barriers across both ends of a closed road with a no-entry sign; a
    // warning sign in the middle of a restricted one.
    if (barriers) { groups.events.remove(barriers); barriers.geometry.dispose(); barriers = null; }
    for (const sprite of signs) { groups.events.remove(sprite); sprite.material.dispose(); }
    signs = [];
    const blocks = [];
    for (const [id, mesh] of roadMeshes) {
      const status = feature(twins.get(id), 'status');
      const pts = mesh.userData.points;
      if (status === 'closed') {
        for (const end of [0, 1]) {
          const i = end ? Math.max(1, pts.length - 3) : Math.min(pts.length - 2, 2);
          const a = pts[i]; const b = pts[end ? i - 1 : i + 1];
          const dx = b[0] - a[0]; const dy = b[1] - a[1];
          const len = Math.hypot(dx, dy) || 1;
          const nx = -dy / len; const ny = dx / len;
          const w = mesh.userData.width;
          const count = Math.max(4, Math.round(w / 3));
          for (let k = 0; k < count; k += 1) {
            const f = (k + 0.5) / count - 0.5;
            blocks.push({ x: a[0] + nx * f * w, y: a[1] + ny * f * w, z: a[2], angle: Math.atan2(ny, nx), red: k % 2 === 0 });
          }
          signs.push(signSprite(tex.closed, a[0] + nx * (w / 2 + 3), a[1] + ny * (w / 2 + 3), a[2]));
        }
      } else if (status === 'restricted') {
        const a = pts[Math.floor(pts.length / 2)];
        signs.push(signSprite(tex.restricted, a[0], a[1], a[2]));
      }
    }
    if (blocks.length) {
      const b = new Builder();
      b.box(0, 0, 0, 2.6, 0.9, 1.15, [1, 1, 1]);
      barriers = new THREE.InstancedMesh(b.machineGeometry(), M.barrier, blocks.length);
      blocks.forEach((block, i) => barriers.setColorAt(i, col(block.red ? '#d63a32' : '#f2f0ea')));
      barriers.userData.layer = 5;
      barriers.userData.blocks = blocks;
      styleMesh(barriers);
      barriers.userData.k = 0;
      groups.events.add(barriers);
    }
    signScale = 0;
  }

  // Barriers and signs grow from afar like the machines, so a closure reads on the overview.
  let signScale = 0;
  function scaleEvents(k) {
    if (barriers && barriers.userData.k !== k) {
      barriers.userData.k = k;
      const m = new THREE.Matrix4();
      const qq = new THREE.Quaternion();
      barriers.userData.blocks.forEach((block, i) => {
        // Spread across the road as it is, each block taller and deeper from afar.
        m.compose(new THREE.Vector3(...L(block.x, block.y, block.z + 0.3)), qq.setFromAxisAngle(zAxis, block.angle), new THREE.Vector3(1, Math.min(k, 2.2), Math.min(k, 2.5)));
        barriers.setMatrixAt(i, m);
      });
      barriers.instanceMatrix.needsUpdate = true;
    }
    if (signScale !== k) {
      signScale = k;
      for (const sprite of signs) sprite.scale.set(8 * k, 8 * k, 1);
    }
  }

  function signSprite(texture, x, y, z) {
    const sprite = new THREE.Sprite(new THREE.SpriteMaterial({ map: texture, fog: true, toneMapped: false }));
    sprite.center.set(0.5, 0);
    sprite.position.set(...L(x, y, z + 3.5));
    sprite.scale.set(8, 8, 1);
    groups.events.add(sprite);
    return sprite;
  }

  // -- blocks: muck piles of blasted rock, and the colour layers -----------------------------

  function blockOutline(block) {
    const polygon = block.attributes?.geo?.polygon;
    return polygon && polygon.length >= 3 ? polygon : null;
  }

  function drape(builder, block, lift, colorAt, depth) {
    const polygon = blockOutline(block);
    const zBlock = block.attributes.geo.z;
    const at = (x, y) => {
      const zGround = heightAt(x, y);
      const z = typeof zBlock === 'number' && Math.abs(zGround - zBlock) > 40 ? zBlock : zGround;
      return L(x, y, z + lift(x, y));
    };
    const cut = (a, c1, c2, d) => {
      if (d === 0) { builder.tri(at(...a), at(...c1), at(...c2), colorAt(...a), colorAt(...c1), colorAt(...c2), true, [[a[0] / 30, a[1] / 30], [c1[0] / 30, c1[1] / 30], [c2[0] / 30, c2[1] / 30]]); return; }
      const m = (p, q) => [(p[0] + q[0]) / 2, (p[1] + q[1]) / 2];
      const ab = m(a, c1); const bc = m(c1, c2); const ca = m(c2, a);
      cut(a, ab, ca, d - 1); cut(ab, c1, bc, d - 1); cut(ca, bc, c2, d - 1); cut(ab, bc, ca, d - 1);
    };
    const faces = THREE.ShapeUtils.triangulateShape(polygon.map(([x, y]) => new THREE.Vector2(x, y)), []);
    for (const [i, j, k] of faces) cut(polygon[i], polygon[j], polygon[k], depth);
  }

  function blockDepth(block) {
    const polygon = blockOutline(block);
    const area = Math.abs(THREE.ShapeUtils.area(polygon.map(([x, y]) => new THREE.Vector2(x, y))));
    return DETAIL.blockDepth ?? (Math.sqrt(area) > 70 ? 3 : 2);
  }

  // Broken rock lies as a rough heap on the bench where the block was.
  function buildMuck() {
    const broken = byKind('block').filter((block) => blockOutline(block) && ['blasted', 'mining'].includes(feature(block, 'status')));
    const key = broken.map((block) => block.thingId).join(',');
    if (key === muckKey) return;
    muckKey = key;
    dropMesh(groups.blocks, muckMesh);
    muckMesh = null;
    if (!broken.length) return;
    const b = new Builder({ uv: true });
    const ranges = [];
    for (const block of broken) {
      const polygon = blockOutline(block);
      const c = areaCentroid(polygon);
      const r = Math.max(...polygon.map(([x, y]) => Math.hypot(x - c.x, y - c.y)));
      const from = b.triangles;
      drape(b, block, (x, y) => {
        const edge = clamp(1 - Math.hypot(x - c.x, y - c.y) / (r * 1.05), 0, 1);
        // A muck pile: highest in the middle, lumpy, a few metres over the bench.
        return 0.4 + Math.pow(edge, 0.7) * 7 + (fbm(x / 9, y / 9, 13, 2) - 0.5) * 2.4 * edge;
      }, (x, y) => scale(mix(rgb('#6f655a'), rgb('#8a7b69'), fbm(x / 14, y / 14, 7, 2)), K), blockDepth(block));
      ranges.push({ id: block.thingId, from, count: b.triangles - from });
    }
    b.owner = [];
    for (const range of ranges) for (let i = 0; i < range.count; i += 1) b.owner.push(range.id);
    muckMesh = addMesh(groups.blocks, b.geometry(), M.muck, { owners: b.owner });
    pickables.push(muckMesh);
  }

  function blockColor(block) {
    if (feature(block, 'status') === 'depleted') return col(DEPLETED);
    return col(LAYERS[layer].color(block));
  }

  // The colour layers (state, copper, hardness, destination) lie over the blocks.
  function buildBlocks() {
    clearGroup(groups.blocks);
    blocksMesh = null;
    muckMesh = null;
    muckKey = '';
    blockRanges = [];
    const b = new Builder();
    const white = [1, 1, 1];
    for (const block of byKind('block')) {
      if (!blockOutline(block)) continue;
      const from = b.pos.length / 3;
      drape(b, block, () => 1.1, () => white, Math.min(blockDepth(block), 2));
      blockRanges.push({ id: block.thingId, from, count: b.pos.length / 3 - from });
    }
    if (blockRanges.length) {
      blocksMesh = new THREE.Mesh(b.machineGeometry(), M.blocks);
      blocksMesh.userData.blocks = true;
      styleMesh(blocksMesh);
      groups.blocks.add(blocksMesh);
      pickables.push(blocksMesh);
    }
    buildMuck();
    colourBlocks();
  }

  function colourBlocks() {
    if (!blocksMesh) return;
    blocksMesh.visible = layer !== 'none';
    if (layer === 'none') return;
    const colors = blocksMesh.geometry.attributes.color;
    for (const range of blockRanges) {
      const block = twins.get(range.id);
      const c = block ? blockColor(block) : col(C.idle);
      for (let v = range.from; v < range.from + range.count; v += 1) {
        colors.array[v * 3] = c.r; colors.array[v * 3 + 1] = c.g; colors.array[v * 3 + 2] = c.b;
      }
    }
    colors.needsUpdate = true;
  }

  // -- machines --------------------------------------------------------------------------

  function instanced(geometry, count, material = M.machine, layerIndex = 4, holoMaterial = null) {
    const mesh = new THREE.InstancedMesh(geometry, material, Math.max(1, count));
    mesh.count = count;
    mesh.frustumCulled = false;
    mesh.instanceMatrix.setUsage(THREE.DynamicDrawUsage);
    mesh.userData.layer = layerIndex;
    if (holoMaterial) mesh.userData.holo = holoMaterial;
    scene.add(mesh);
    styleMesh(mesh);
    return mesh;
  }

  // The hologram's colour of a machine: cyan at work, amber when it waits or
  // a scenario touches it, red when it is broken, white when selected.
  function machineTint(id, state) {
    if (id === selectedId) return HOLO.select;
    const tone = holoTone(state);
    if (tone === 'bad') return HOLO.red;
    if (tone === 'warn' || affected.has(id)) return HOLO.amber;
    return tone === 'dim' ? HOLO.dim : HOLO.glass;
  }
  const LOAD_TINT = { ore: HOLO.load, clay: '#e9dcae', 'low-grade': '#7fdcff', waste: '#86aab5' };
  function whiteFleet() {
    for (const mesh of Object.values(fleet)) {
      if (!mesh.instanceColor || mesh === fleet.halo) continue;
      for (let i = 0; i < mesh.instanceMatrix.count; i += 1) mesh.setColorAt(i, tmpColor.set('#ffffff'));
      mesh.instanceColor.needsUpdate = true;
    }
  }

  // The machines drawn with the Blender models, by kind: {rigs: [LOD0, LOD1],
  // meshes[lod][part] = [instanced mesh per layer], mats[lod][part], used[lod]}.
  const gf = { truck: null, loader: null, 'drill-rig': null };
  function buildModelKind(kind, count) {
    const model = libState === 'ready' ? lib?.models[MACHINE_MODELS[kind]] : null;
    if (!model || !model.lods[0]) return null;
    const rigs = [model.lods[0], model.lods[1] ?? model.lods[0]];
    const meshes = rigs.map((rig, li) => rig.parts.map((part) => part.layers.map((layer, k) => {
      // instanced() records the model's own material as the realistic one before it puts on the hologram glass.
      const mesh = instanced(layer.geometry, count, materialOf(layer), 4, part.name === 'load' ? H.load : H.machine);
      fleet[`${kind}:${li}:${part.name}:${k}`] = mesh;
      return mesh;
    })));
    return { rigs, meshes, mats: rigs.map((rig) => rig.parts.map(() => new THREE.Matrix4())), used: [0, 0],
      load: rigs.map((rig) => rig.byName.get('load') ?? -1) };
  }

  // One machine of a kind drawn with its model: its parts posed, its tint (hologram), its load.
  function drawModel(entry, close, frame, angles, tint, loadColor) {
    const li = close ? 0 : 1;
    const rig = entry.rigs[li];
    const j = entry.used[li]++;
    G.poseRig(rig, frame, angles, entry.mats[li]);
    const loadIndex = entry.load[li];
    const parts = entry.meshes[li];
    for (let i = 0; i < parts.length; i += 1) {
      const isLoad = i === loadIndex;
      const matrix = isLoad && !loadColor ? HIDE : entry.mats[li][i];
      for (const mesh of parts[i]) {
        mesh.setMatrixAt(j, matrix);
        if (isLoad && loadColor) mesh.setColorAt(j, tmpColor.set(loadColor));
        else if (tint) mesh.setColorAt(j, tmpColor.set(tint));
      }
    }
  }

  function buildFleet() {
    for (const mesh of Object.values(fleet)) {
      scene.remove(mesh);
      if (!mesh.geometry.userData?.keep) mesh.geometry.dispose();
    }
    for (const key of Object.keys(fleet)) delete fleet[key];
    fleetIds.truck = byKind('truck').map((twin) => twin.thingId);
    fleetIds.loader = byKind('loader').map((twin) => twin.thingId);
    fleetIds['drill-rig'] = byKind('drill-rig').map((twin) => twin.thingId);
    const nt = fleetIds.truck.length;
    const nl = fleetIds.loader.length;
    const nd = fleetIds['drill-rig'].length;
    const all = nt + nl + nd;
    fleet.blob = instanced(blobGeometry(), all, M.blob, 3);
    fleet.halo = instanced(haloGeometry(soft ? 12 : 36), all, M.halo, 3);
    // Each machine is drawn detailed near the camera and as a few blocks
    // afar (a few pixels there); each level is one instanced mesh per part,
    // filled every frame with the machines it draws.
    const lit = !soft;
    const near = soft ? 'simple' : 'near';
    // The Blender models where they are loaded, the procedural ones otherwise.
    gf.truck = buildModelKind('truck', nt);
    gf.loader = buildModelKind('loader', nl);
    gf['drill-rig'] = buildModelKind('drill-rig', nd);
    if (!gf.truck) {
      const truck = truckParts({ lod: near, lit });
      fleet.truckFrame = instanced(truck.frame, nt);
      fleet.truckBody = instanced(truck.body, nt);
      fleet.truckLoad = instanced(truck.load, nt, M.load);
    }
    if (!gf.loader) {
      const shovel = shovelParts({ lod: near, lit });
      fleet.loaderBase = instanced(shovel.base, nl);
      fleet.loaderUpper = instanced(shovel.upper, nl);
    }
    if (!gf['drill-rig']) {
      const drill = drillParts({ lod: near, lit });
      fleet.rigBase = instanced(drill.base, nd);
      fleet.rigMast = instanced(drill.mast, nd);
    }
    if (!soft) {
      if (!gf.truck) {
        const truckFar = truckParts({ lod: 'far', lit });
        fleet.truckFarFrame = instanced(truckFar.frame, nt);
        fleet.truckFarBody = instanced(truckFar.body, nt);
        fleet.truckFarLoad = instanced(truckFar.load, nt, M.load);
      }
      if (!gf.loader) {
        const shovelFar = shovelParts({ lod: 'far', lit });
        fleet.loaderFarBase = instanced(shovelFar.base, nl);
        fleet.loaderFarUpper = instanced(shovelFar.upper, nl);
      }
      if (!gf['drill-rig']) {
        const drillFar = drillParts({ lod: 'far', lit });
        fleet.rigFarBase = instanced(drillFar.base, nd);
        fleet.rigFarMast = instanced(drillFar.mast, nd);
      }
    }
    lodTriangles = {};
    for (const [name, mesh] of Object.entries(fleet)) lodTriangles[name] = trianglesOf(mesh.geometry);
    for (const mesh of Object.values(fleet)) {
      for (let i = 0; i < mesh.count; i += 1) mesh.setColorAt(i, col('#ffffff'));
      if (mesh.instanceColor) mesh.instanceColor.needsUpdate = true;
    }
    anim.clear();
    motion.rebuild();
  }

  // Larger than life from afar, so the machines read on the overview; life
  // size up close. Each machine by its own distance from the camera, so one
  // in the foreground is not drawn as large as one across the pit.
  const equipmentScale = () => clamp(cam.distance / 900, 1, 3.2);
  const scaleAt = (x, y, z) => clamp(Math.hypot(x - O.x - camera.position.x, y - O.y - camera.position.y, z - O.z - camera.position.z) / 900, 1, 3.2);

  function truckZ(x, y, seg) {
    const mesh = seg ? roadMeshes.get(seg) : null;
    if (mesh) return distanceToPolyline(x, y, mesh.userData.points).z + 0.3;
    return heightAt(x, y);
  }

  const m4 = new THREE.Matrix4();
  const m5 = new THREE.Matrix4();
  const m6 = new THREE.Matrix4();
  const q = new THREE.Quaternion();
  const qPitch = new THREE.Quaternion();
  const v3 = new THREE.Vector3();
  const s3 = new THREE.Vector3();
  const xAxis = new THREE.Vector3(1, 0, 0);
  const zAxis = new THREE.Vector3(0, 0, 1);
  const tmpColor = new THREE.Color();
  const HIDE = new THREE.Matrix4().makeScale(0, 0, 0);

  // Matrix of a machine: at (x, y, z), turned by `angle` about z, pitched by `pitch`, scaled by k.
  function base(x, y, z, angle, pitch, k, out = m4) {
    q.setFromAxisAngle(zAxis, angle);
    if (pitch) q.multiply(qPitch.setFromAxisAngle(xAxis, pitch));
    v3.set(x - O.x, y - O.y, z - O.z);
    s3.set(k, k, k);
    return out.compose(v3, q, s3);
  }

  // A part turned about the x axis through a pivot of the machine.
  function hinged(parent, pivot, angle, out) {
    m5.makeTranslation(pivot[0], pivot[1], pivot[2]);
    m6.makeRotationX(angle);
    out.multiplyMatrices(parent, m5).multiply(m6);
    m5.makeTranslation(-pivot[0], -pivot[1], -pivot[2]);
    return out.multiply(m5);
  }

  const shadowScale = new THREE.Vector3();
  function markers(index, id, x, y, z, r, angle, color) {
    // The soft shadow, thrown away from the sun.
    const away = skyState.sun;
    const len = clamp((1 / Math.max(0.25, away.z)) * 0.35, 0.2, 1.2);
    base(x - away.x * r * len * 0.5, y - away.y * r * len * 0.5, z + 0.15, angle, 0, 1, m4);
    m4.scale(shadowScale.set(r * 0.62, r * 0.9, 1));
    fleet.blob.setMatrixAt(index, m4);
    const selected = id === selectedId;
    const radius = (selected ? 1.25 : 1) * r;
    base(x, y, z + 0.25, 0, 0, 1, m4);
    m4.scale(shadowScale.set(radius, radius, 1));
    fleet.halo.setMatrixAt(index, m4);
    fleet.halo.setColorAt(index, tmpColor.set(selected ? (holo() ? HOLO.select : C.select) : color));
  }

  function anyAnimating() {
    for (const value of anim.values()) if (value.moving) return true;
    return false;
  }

  const frameM = new THREE.Matrix4();
  const bodyM = new THREE.Matrix4();
  const mastM = new THREE.Matrix4();
  const pivotM = new THREE.Matrix4();
  const tiltM = new THREE.Matrix4();
  const poolScale = new THREE.Vector3();

  // The front of the shovel through one pass (phase 0..1): crowd into the face
  // and curl the full bucket, raise it and swing (the upper slews to the truck
  // meanwhile), open the bucket over the body, lower it swinging back. Angles
  // about each pin (models.json: boom, stick and bucket up with +).
  const SHOVEL_REST = { boom: -0.3, stick: 0.6, bucket: -0.2 };
  const DIG_KEYS = [
    [0.0, { boom: -0.32, stick: -0.2, bucket: -0.5 }],
    [0.22, { boom: -0.05, stick: 0.6, bucket: 0.45 }],
    [0.35, { boom: 0.3, stick: 0.75, bucket: 0.55 }],
    [0.52, { boom: 0.33, stick: 0.85, bucket: 0.5 }],
    [0.62, { boom: 0.33, stick: 0.85, bucket: -0.75 }],
    [0.8, { boom: 0.3, stick: 0.75, bucket: -0.6 }],
    [1.0, { boom: -0.32, stick: -0.2, bucket: -0.5 }],
  ];
  const digPoseOut = { boom: 0, stick: 0, bucket: 0 };
  function digPose(phase) {
    let i = 1;
    while (i < DIG_KEYS.length - 1 && phase > DIG_KEYS[i][0]) i += 1;
    const [t0, a] = DIG_KEYS[i - 1];
    const [t1, b] = DIG_KEYS[i];
    const f = clamp((phase - t0) / (t1 - t0), 0, 1);
    const e = f * f * (3 - 2 * f);
    for (const key of ['boom', 'stick', 'bucket']) digPoseOut[key] = a[key] + (b[key] - a[key]) * e;
    return digPoseOut;
  }

  function updateFleet(now, dt) {
    if (!fleet.blob) return false;      // the twins are not loaded yet
    const k = equipmentScale();
    scaleEvents(k);
    let moving = false;
    const hologram = holo();
    const night = hologram ? 0 : skyState.lamps ?? 0;
    const mine = first('mine');
    const speedup = Number(feature(mine, 'speedup')) || 20;
    const paused = feature(mine, 'sim_state') === 'paused';
    if (glows) glows.begin();
    if (beacons) beacons.begin();
    let index = 0;
    const poolsAt = [];
    // Level of detail of each machine by its own distance from the camera.
    const near = (x, y, z) => soft || Math.hypot(x - O.x - camera.position.x, y - O.y - camera.position.y, z - O.z - camera.position.z) < NEAR_M;
    const used = { truck: 0, truckFar: 0, load: 0, loadFar: 0, loader: 0, loaderFar: 0, rig: 0, rigFar: 0 };
    // The trucks queued at the crusher's model (reported at its access node): they wait in a line.
    const bays0 = gf.truck ? plant?.crusherBays : null;
    const crusherQueue = !bays0 ? [] : fleetIds.truck.filter((tid) => {
      const t = twins.get(tid);
      const px = feature(t, 'pos_x'); const py = feature(t, 'pos_y');
      return feature(t, 'state') === 'queue_dump' && typeof px === 'number' && Math.hypot(px - bays0[0].x, py - bays0[0].y) < 90;
    });
    fleetIds.truck.forEach((id) => {
      const twin = twins.get(id);
      const track = motion.shown(id, now);
      const at = track && { x: track.x, y: track.y, heading: track.heading, at: track.at, moving: track.moving };
      const slot = index;
      index += 1;
      if (!twin || !at) {
        fleet.blob.setMatrixAt(slot, HIDE); fleet.halo.setMatrixAt(slot, HIDE);
        shown.delete(id);
        return;
      }
      if (at.moving) moving = true;
      const state = feature(twin, 'state');
      // Under the shovel: the model puts the truck where the shovel stands;
      // it is drawn beside it, parallel, its body under the bucket's swing.
      const loader = state === 'loading' ? twins.get(feature(twin, 'assigned_loader')) : null;
      const shovelAt = loader && feature(loader, 'truck') === id ? equipmentPlace(loader) : null;
      if (shovelAt && Math.hypot(shovelAt.x - at.x, shovelAt.y - at.y) < 30) {
        const ks = scaleAt(shovelAt.x, shovelAt.y, shovelAt.z);
        at.x = shovelAt.x + Math.cos(shovelAt.angle) * 13 * ks - Math.sin(shovelAt.angle) * 4 * ks;
        at.y = shovelAt.y + Math.sin(shovelAt.angle) * 13 * ks + Math.cos(shovelAt.angle) * 4 * ks;
        at.heading = -shovelAt.angle;
        at.at = null;
      }
      // Tipping at the crusher's model: backed into one of its two bays, the tail over the hopper.
      const bays = gf.truck && state === 'dumping' ? plant?.crusherBays : null;
      if (bays) {
        const bay = bays.reduce((best, b) => (Math.hypot(b.x - at.x, b.y - at.y) < Math.hypot(best.x - at.x, best.y - at.y) ? b : best), bays[0]);
        if (Math.hypot(bay.x - at.x, bay.y - at.y) < 70) {
          const taken = [...shown.entries()].some(([other, s]) => other !== id && s.bay === bay);
          const use = taken ? bays.find((b) => b !== bay) ?? bay : bay;
          at.x = use.x; at.y = use.y;
          at.heading = -(use.angle + Math.PI / 2);
          at.at = null;
          at.bay = use;
        }
      }
      // Waiting to tip there: in a line on the road in front of the bays.
      const place = crusherQueue.indexOf(id);
      if (place >= 0 && state === 'queue_dump') {
        const [b0, b1] = plant.crusherBays;
        const d = 17 + 13 * place;
        at.x = (b0.x + b1.x) / 2 - Math.cos(b0.angle) * d;
        at.y = (b0.y + b1.y) / 2 - Math.sin(b0.angle) * d;
        at.heading = -(b0.angle + Math.PI / 2);
        at.at = null;
      }
      const z = truckZ(at.x, at.y, at.at);
      const k = scaleAt(at.x, at.y, z);
      const angle = -at.heading;
      const fx = Math.sin(at.heading); const fy = Math.cos(at.heading);
      const ahead = truckZ(at.x + fx * 5, at.y + fy * 5, at.at);
      const behind = truckZ(at.x - fx * 5, at.y - fy * 5, at.at);
      const pitch = clamp(Math.atan2(ahead - behind, 10), -0.2, 0.2);
      let a = anim.get(id);
      if (!a) { a = { tilt: 0, roll: 0, moving: false }; anim.set(id, a); }
      const wantTilt = state === 'dumping' ? (gf.truck ? 0.79 : 0.9) : 0;
      if (Math.abs(a.tilt - wantTilt) > 0.001) {
        a.tilt += Math.sign(wantTilt - a.tilt) * Math.min(Math.abs(wantTilt - a.tilt), dt * (wantTilt ? 0.9 : 1.4));
        a.moving = true;
      } else a.moving = false;
      const close = near(at.x, at.y, z);
      base(at.x, at.y, z, angle, pitch, k, frameM);
      const tint = hologram ? machineTint(id, state) : null;
      // The wheels roll with the way the truck went since the last frame (a jump is not rolled).
      if (a.lastX !== undefined) {
        const step = Math.hypot(at.x - a.lastX, at.y - a.lastY);
        if (step < 25 * k) a.roll = (a.roll - step / (1.47 * k)) % (Math.PI * 2);
      }
      a.lastX = at.x; a.lastY = at.y;
      if (gf.truck) {
        const material = feature(twin, 'material');
        const loaded = feature(twin, 'payload_t') > 1 && a.tilt < 0.45;
        const loadColor = !loaded ? null : hologram ? (LOAD_TINT[material] ?? HOLO.load) : material === 'waste' ? '#c4c8cc' : material === 'low-grade' ? '#efe2d2' : material === 'clay' ? '#f4e2c4' : '#ffffff';
        const r = a.roll;
        a.angles = { body: a.tilt, wheel_FL: r, wheel_FR: r, wheel_RL: r, wheel_RR: r };
        drawModel(gf.truck, close, frameM, a.angles, tint, loadColor);
        (a.frame ??= new THREE.Matrix4()).copy(frameM);
        a.lod = close ? 0 : 1;
        if (tint) poolsAt.push([at.x, at.y, z, 15 * k, 15 * k, angle, 0, id === selectedId ? 0.3 : 0.42, tint]);
      } else {
      hinged(frameM, TRUCK.hinge, a.tilt, bodyM);
      const j = close ? used.truck++ : used.truckFar++;
      (close ? fleet.truckFrame : fleet.truckFarFrame).setMatrixAt(j, frameM);
      (close ? fleet.truckBody : fleet.truckFarBody).setMatrixAt(j, bodyM);
      if (tint) {
        tmpColor.set(tint);
        (close ? fleet.truckFrame : fleet.truckFarFrame).setColorAt(j, tmpColor);
        (close ? fleet.truckBody : fleet.truckFarBody).setColorAt(j, tmpColor);
        poolsAt.push([at.x, at.y, z, 15 * k, 15 * k, angle, 0, id === selectedId ? 0.3 : 0.42, tint]);
      }
      if (feature(twin, 'payload_t') > 1 && a.tilt < 0.45) {
        const loadMesh = close ? fleet.truckLoad : fleet.truckFarLoad;
        const l = close ? used.load++ : used.loadFar++;
        loadMesh.setMatrixAt(l, bodyM);
        const material = feature(twin, 'material');
        if (hologram) loadMesh.setColorAt(l, tmpColor.set(LOAD_TINT[material] ?? HOLO.load));
        else loadMesh.setColorAt(l, tmpColor.set(material === 'waste' ? '#8f897f' : material === 'low-grade' ? '#8d735c' : material === 'clay' ? '#a8906c' : '#8a6146'));
      }
      }
      markers(slot, id, at.x, at.y, z, TRUCK.radius * k * 0.8, angle, tint ?? truckColor(state));
      if (glows && tint && !close) glows.add(...L(at.x, at.y, z + 4 * k), 7 * k, tmpColor.set(tint).toArray());
      shown.set(id, { x: at.x, y: at.y, z, r: 6 * k, top: z + 6.2 * k, bay: at.bay ?? null });
      const speed = feature(twin, 'speed_kmh') ?? 0;
      if (dust && at.moving && speed > 4 && !paused && weather?.kind === 'dry' && dt > 0 && (!hologram || close)) {
        a.dustDue = (a.dustDue ?? 0) - dt;
        if (a.dustDue <= 0) {
          a.dustDue = 0.16 + Math.random() * 0.1;
          const wet = feature(twins.get(at.at), 'surface') === 'wet';
          if (!wet) {
            const bx = at.x - fx * 6 * k; const by = at.y - fy * 6 * k;
            dust.emit(bx - O.x + (Math.random() - 0.5) * 3, by - O.y + (Math.random() - 0.5) * 3, z - O.z + 1.5,
              { vx: (Math.random() - 0.5) * 1.2 - fx * 0.5, vy: (Math.random() - 0.5) * 1.2 - fy * 0.5, vz: 0.6 + Math.random() * 0.6,
                size: 5 * Math.sqrt(k), growth: 2.4, seconds: 3 + Math.random(), color: hologram ? [0.08, 0.32, 0.4] : skyState.dustColor,
                opacity: (hologram ? 0.16 : 0.26) * clamp(500 / cam.distance, 0.12, 1) });
          }
        }
      }
      if (glows && night > 0.05) {
        const c = Math.cos(angle); const s = Math.sin(angle);
        const p = (lx, ly, lz) => [at.x + (lx * c - ly * s) * k - O.x, at.y + (lx * s + ly * c) * k - O.y, z + lz * k - O.z];
        glows.add(...p(-1.9, 6.2, 1.45), 4 * k, [1.0, 0.95, 0.82]);
        glows.add(...p(1.9, 6.2, 1.45), 4 * k, [1.0, 0.95, 0.82]);
        glows.add(...p(-0.75, -5.8, 1.5), 2.4 * k, [0.95, 0.12, 0.08]);
        glows.add(...p(0.75, -5.8, 1.5), 2.4 * k, [0.95, 0.12, 0.08]);
        // Headlights throw a pool on the road ahead.
        if (state !== 'down') poolsAt.push([at.x + fx * 17 * k, at.y + fy * 17 * k, z, 7 * k, 13 * k, angle, pitch, 0.8]);
      }
      if (beacons && state === 'down') beacons.add(...L(at.x, at.y, z + 7.5 * k), 4 * k, [1, 0.15, 0.1]);
    });
    const passS = Number(byKind('loader')[0]?.attributes?.spec?.pass_s) || 30;
    fleetIds.loader.forEach((id) => {
      const twin = twins.get(id);
      const at = twin && equipmentPlace(twin);
      const slot = index;
      index += 1;
      if (!at) { fleet.blob.setMatrixAt(slot, HIDE); fleet.halo.setMatrixAt(slot, HIDE); return; }
      const state = feature(twin, 'state');
      const k = scaleAt(at.x, at.y, at.z);
      let a = anim.get(id);
      if (!a) { a = { slew: at.angle, moving: false }; anim.set(id, a); }
      let want = at.angle;
      const truck = shown.get(feature(twin, 'truck'));
      let phase = -1;
      if (state === 'loading' && truck && !paused) {
        // Dig at the face, swing to the truck, dump, swing back.
        const toTruck = Math.atan2(-(truck.x - at.x), truck.y - at.y);
        const period = clamp(passS / speedup, 3, 9) * 1000;
        phase = (now % period) / period;
        const swing = phase < 0.35 ? 0 : phase < 0.5 ? (phase - 0.35) / 0.15 : phase < 0.75 ? 1 : 1 - (phase - 0.75) / 0.25;
        const e = swing * swing * (3 - 2 * swing);
        let d = toTruck - at.angle;
        d = Math.atan2(Math.sin(d), Math.cos(d));
        want = at.angle + d * e;
        a.moving = true;
      } else {
        let d = want - a.slew;
        d = Math.atan2(Math.sin(d), Math.cos(d));
        a.moving = Math.abs(d) > 0.01;
        want = a.slew + Math.sign(d) * Math.min(Math.abs(d), dt * 0.8);
      }
      a.slew = want;
      const close = near(at.x, at.y, at.z);
      const tint = hologram ? machineTint(id, state) : null;
      if (gf.loader) {
        // The front: boom, stick and bucket through the pass (dig, swing loaded, dump, swing back), at rest otherwise.
        const goal = phase >= 0 ? digPose(phase) : SHOVEL_REST;
        for (const key of ['boom', 'stick', 'bucket']) {
          const value = a[key] ?? goal[key];
          a[key] = phase >= 0 ? goal[key] : value + Math.sign(goal[key] - value) * Math.min(Math.abs(goal[key] - value), dt * 0.5);
          if (Math.abs(a[key] - goal[key]) > 0.002) a.moving = true;
        }
        let rel = a.slew - at.angle;
        rel = Math.atan2(Math.sin(rel), Math.cos(rel));
        a.angles = { upper: rel, boom: a.boom, stick: a.stick, bucket: a.bucket };
        base(at.x, at.y, at.z, at.angle, 0, k, frameM);
        drawModel(gf.loader, close, frameM, a.angles, tint, null);
        (a.frame ??= new THREE.Matrix4()).copy(frameM);
        a.lod = close ? 0 : 1;
      } else {
        const j = close ? used.loader++ : used.loaderFar++;
        (close ? fleet.loaderBase : fleet.loaderFarBase).setMatrixAt(j, base(at.x, at.y, at.z, at.angle, 0, k, m4));
        (close ? fleet.loaderUpper : fleet.loaderFarUpper).setMatrixAt(j, base(at.x, at.y, at.z, a.slew, 0, k, m4));
        if (tint) {
          tmpColor.set(tint);
          (close ? fleet.loaderBase : fleet.loaderFarBase).setColorAt(j, tmpColor);
          (close ? fleet.loaderUpper : fleet.loaderFarUpper).setColorAt(j, tmpColor);
        }
      }
      if (tint) {
        poolsAt.push([at.x, at.y, at.z, 24 * k, 24 * k, 0, 0, id === selectedId ? 0.35 : 0.55, tint]);
      }
      markers(slot, id, at.x, at.y, at.z, 9 * k, at.angle, tint ?? stateColor(state));
      if (glows && tint && !close) glows.add(...L(at.x, at.y, at.z + 8 * k), 11 * k, tmpColor.set(tint).toArray());
      shown.set(id, { x: at.x, y: at.y, z: at.z, r: 10 * k, top: at.z + 12 * k });
      if (night > 0.05) poolsAt.push([at.x + Math.sin(-a.slew) * 9 * k, at.y + Math.cos(-a.slew) * 9 * k, at.z, 34 * k, 34 * k, 0, 0, 1]);
      if (glows && night > 0.05) {
        const c = Math.cos(a.slew); const s = Math.sin(a.slew);
        const p = (lx, ly, lz) => [at.x + (lx * c - ly * s) * k - O.x, at.y + (lx * s + ly * c) * k - O.y, at.z + lz * k - O.z];
        glows.add(...p(-0.55, 5.2, 8.8), 4.5 * k, [1.0, 0.9, 0.7]);
        glows.add(...p(1.25, 5.2, 8.8), 4.5 * k, [1.0, 0.9, 0.7]);
        glows.add(...p(-2.4, 3.5, 7.6), 3.5 * k, [1.0, 0.9, 0.7]);
      }
      if (beacons && state === 'down') beacons.add(...L(at.x, at.y, at.z + 13 * k), 5 * k, [1, 0.15, 0.1]);
    });
    fleetIds['drill-rig'].forEach((id) => {
      const twin = twins.get(id);
      const at = twin && equipmentPlace(twin);
      const slot = index;
      index += 1;
      if (!at) { fleet.blob.setMatrixAt(slot, HIDE); fleet.halo.setMatrixAt(slot, HIDE); return; }
      const state = feature(twin, 'state');
      const k = scaleAt(at.x, at.y, at.z);
      let a = anim.get(id);
      const up = state === 'drilling' || state === 'positioning';
      // The model's mast lowers backwards onto its rest (+1.55 rad, models.json); the procedural one the other way.
      const want = up ? 0 : gf['drill-rig'] ? 1.55 : -1.45;
      if (!a) { a = { mast: want, moving: false }; anim.set(id, a); }
      if (Math.abs(a.mast - want) > 0.001) {
        a.mast += Math.sign(want - a.mast) * Math.min(Math.abs(want - a.mast), dt * 0.6);
        a.moving = true;
      } else a.moving = false;
      const close = near(at.x, at.y, at.z);
      base(at.x, at.y, at.z, at.angle, 0, k, frameM);
      const tint = hologram ? machineTint(id, state) : null;
      if (gf['drill-rig']) {
        a.angles = { mast: a.mast };
        drawModel(gf['drill-rig'], close, frameM, a.angles, tint, null);
        (a.frame ??= new THREE.Matrix4()).copy(frameM);
        a.lod = close ? 0 : 1;
      } else {
        const j = close ? used.rig++ : used.rigFar++;
        (close ? fleet.rigBase : fleet.rigFarBase).setMatrixAt(j, frameM);
        mastM.multiplyMatrices(frameM, pivotM.makeTranslation(...DRILL.pivot)).multiply(tiltM.makeRotationX(a.mast));
        (close ? fleet.rigMast : fleet.rigFarMast).setMatrixAt(j, mastM);
        if (tint) {
          tmpColor.set(tint);
          (close ? fleet.rigBase : fleet.rigFarBase).setColorAt(j, tmpColor);
          (close ? fleet.rigMast : fleet.rigFarMast).setColorAt(j, tmpColor);
        }
      }
      if (tint) {
        poolsAt.push([at.x, at.y, at.z, 17 * k, 17 * k, 0, 0, id === selectedId ? 0.3 : 0.45, tint]);
      }
      markers(slot, id, at.x, at.y, at.z, 7 * k, at.angle, tint ?? stateColor(state));
      if (glows && tint && !close) glows.add(...L(at.x, at.y, at.z + 6 * k), 8 * k, tmpColor.set(tint).toArray());
      shown.set(id, { x: at.x, y: at.y, z: at.z, r: 7 * k, top: at.z + (up ? (gf['drill-rig'] ? 23.5 : DRILL.height) : 6) * k });
      if (night > 0.05) poolsAt.push([at.x, at.y, at.z, 20 * k, 20 * k, 0, 0, 0.8]);
      if (glows && night > 0.05) glows.add(...L(at.x, at.y, at.z + 6 * k), 3 * k, [1.0, 0.9, 0.7]);
      if (beacons && state === 'down') beacons.add(...L(at.x, at.y, at.z + 8 * k), 4 * k, [1, 0.15, 0.1]);
    });
    const counts = { truckFrame: used.truck, truckBody: used.truck, truckLoad: used.load, truckFarFrame: used.truckFar, truckFarBody: used.truckFar, truckFarLoad: used.loadFar,
      loaderBase: used.loader, loaderUpper: used.loader, loaderFarBase: used.loaderFar, loaderFarUpper: used.loaderFar,
      rigBase: used.rig, rigMast: used.rig, rigFarBase: used.rigFar, rigFarMast: used.rigFar };
    for (const entry of Object.values(gf)) {
      if (!entry) continue;
      entry.meshes.forEach((parts, li) => { for (const meshes of parts) for (const mesh of meshes) { mesh.count = entry.used[li]; mesh.visible = mesh.count > 0; } });
      entry.used[0] = 0; entry.used[1] = 0;
    }
    for (const [name, mesh] of Object.entries(fleet)) {
      if (name in counts) { mesh.count = counts[name]; mesh.visible = mesh.count > 0; }
      mesh.instanceMatrix.needsUpdate = true;
      if (mesh.instanceColor) mesh.instanceColor.needsUpdate = true;
    }
    if (glows) {
      if (night > 0.05 && plant) for (const [x, y, z] of plant.lamps) glows.add(...L(x, y, z), 5, [1.0, 0.85, 0.6]);
      if (night > 0.05) for (const tower of towers) glows.add(...L(tower.x, tower.y, tower.z), 6, [1.0, 0.9, 0.72]);
      glows.material.uniforms.strength.value = hologram ? 1.1 : night;
      glows.end();
    }
    if (beacons) {
      beacons.material.uniforms.strength.value = 0.55 + 0.45 * Math.sin(now / 160);
      beacons.end();
    }
    if (pools) {
      // The hologram stands each unit of the plant in a pool of its own light.
      if (hologram && plant) {
        for (const [id, unit] of units) {
          const kind = kindOf(twins.get(id));
          if (kind === 'conveyor' || kind === 'mill') continue;
          const r = Math.min(70, (unit.r ?? 20) * 1.5);
          poolsAt.push([unit.at.x, unit.at.y, unit.base ?? heightAt(unit.at.x, unit.at.y), r, r, 0, 0, 0.32, unitTint(id)]);
        }
      }
      pools.count = Math.min(poolsAt.length, POOLS);
      poolsAt.slice(0, pools.count).forEach(([x, y, z, rx, ry, angle, pitch, strength, tint], i) => {
        const zg = Math.max(z, heightAt(x, y));
        base(x, y, zg + 0.6, angle, pitch, 1, m4);
        m4.scale(poolScale.set(rx, ry, 1));
        pools.setMatrixAt(i, m4);
        if (tint) pools.setColorAt(i, tmpColor.set(tint).multiplyScalar(strength));
        else pools.setColorAt(i, tmpColor.setRGB(strength, strength * 0.86, strength * 0.62));
      });
      pools.instanceMatrix.needsUpdate = true;
      if (pools.instanceColor) pools.instanceColor.needsUpdate = true;
      M.pool.opacity = hologram ? 0.6 : 0.5 * night;
      M.pool.color.set(hologram ? '#ffffff' : '#ffd9a0');
      pools.visible = (hologram || night > 0.05) && pools.count > 0;
    }
    for (const follower of followers) {
      const at = shown.get(follower.id);
      if (at) {
        follower.mesh.position.set(at.x - O.x, at.y - O.y, at.z - O.z + 1.5);
        follower.mesh.scale.setScalar(at.r * (follower.grow ?? 1.15) * (follower.mesh.userData.wave ?? 1));
      }
    }
    return moving;
  }

  function equipmentPlace(twin) {
    const x = feature(twin, 'pos_x') ?? twin.attributes?.home?.x ?? twin.attributes?.geo?.x;
    const y = feature(twin, 'pos_y') ?? twin.attributes?.home?.y ?? twin.attributes?.geo?.y;
    if (typeof x !== 'number' || typeof y !== 'number') return null;
    const block = twins.get(feature(twin, 'block'));
    let angle = 0;
    if (block?.attributes?.geo?.polygon) {
      const c = centroid(block.attributes.geo.polygon);
      if (Math.hypot(c.x - x, c.y - y) > 1) angle = Math.atan2(-(c.x - x), c.y - y);
    } else if (pitCentre()) {
      const c = pitCentre();
      angle = Math.atan2(-(c.x - x), c.y - y) + Math.PI;
    }
    return { x, y, z: heightAt(x, y), angle };
  }

  const pitCentre = () => terrain?.pit?.centre ?? null;

  // -- marks: selection, scenario, blast ------------------------------------------------------

  function anchorOf(twin) {
    if (!twin) return null;
    const id = twin.thingId;
    const kind = kindOf(twin);
    if (shown.has(id)) return shown.get(id);
    if (units.has(id)) return units.get(id).at;
    // A machine not drawn yet: where it reports.
    const px = feature(twin, 'pos_x');
    const py = feature(twin, 'pos_y');
    if (typeof px === 'number' && typeof py === 'number') return { x: px, y: py, z: heightAt(px, py), r: 8, top: heightAt(px, py) + 8 };
    const geo = twin.attributes?.geo ?? {};
    if (geo.polygon) {
      const c = areaCentroid(geo.polygon);
      const mesh = pickables.find((m) => m.userData.id === id && m.userData.top);
      return { x: c.x, y: c.y, z: mesh ? mesh.userData.top.z : heightAt(c.x, c.y) + (kind === 'bench' ? 0 : 3) };
    }
    if (geo.path) {
      const mesh = roadMeshes.get(id);
      const points = mesh?.userData.points ?? geo.path;
      const mid = points[Math.floor(points.length / 2)];
      return { x: mid[0], y: mid[1], z: (mid[2] ?? heightAt(mid[0], mid[1])) + 2 };
    }
    if (typeof geo.x === 'number') return { x: geo.x, y: geo.y, z: Math.max(geo.z ?? 0, heightAt(geo.x, geo.y)) + 2 };
    return null;
  }

  function outlineOf(twin) {
    const geo = twin?.attributes?.geo ?? {};
    if (geo.polygon && kindOf(twin) !== 'mine') {
      return { closed: true, points: densify([...geo.polygon, geo.polygon[0]].map(([x, y]) => [x, y, 0]), 10).slice(0, -1) };
    }
    if (geo.path) return { closed: false, points: roadMeshes.get(twin.thingId)?.userData.points ?? geo.path };
    return null;
  }

  // Rings and outlines that pulse in the hologram look (selection, scenario, blast).
  let pulses = [];

  function ringMesh(color, opacity) {
    const geometry = new THREE.RingGeometry(1.0, holo() ? 1.14 : 1.1, 48);
    const material = new THREE.MeshBasicMaterial({ color, transparent: true, opacity, side: THREE.DoubleSide, depthTest: false, depthWrite: false, toneMapped: false,
      blending: holo() ? THREE.AdditiveBlending : THREE.NormalBlending });
    const mesh = new THREE.Mesh(geometry, material);
    mesh.renderOrder = 5;
    return mesh;
  }

  function pulse(mesh, amount = 1) {
    mesh.userData.pulse = { opacity: mesh.material.opacity, amount };
    pulses.push(mesh);
  }

  function buildMarks() {
    clearGroup(groups.marks);
    followers = [];
    pulses = [];
    const hologram = holo();
    const k = equipmentScale();
    const marks = [];
    const COLOR = hologram ? { select: HOLO.select, link: HOLO.link, scenario: HOLO.amber } : { select: C.select, link: C.select, scenario: C.scenario };
    if (selectedId) marks.push([selectedId, 'select', 1]);
    for (const id of related) marks.push([id, 'link', 0.45]);
    for (const [id] of affected) marks.push([id, 'scenario', 0.85]);
    const machines = new Set([...fleetIds.truck, ...fleetIds.loader, ...fleetIds['drill-rig']]);
    for (const [id, type, strength] of marks) {
      const color = COLOR[type];
      const twin = twins.get(id);
      if (!twin) continue;
      const kind = kindOf(twin);
      if (['mine', 'journal', 'scenario', 'module', 'forecast'].includes(kind)) continue;
      if (machines.has(id)) {
        // The machine's own halo turns the colour of selection; the hologram adds a pulsing ring round it.
        if (id === selectedId && !hologram) continue;
        // A thin ring for related machines; for a scenario only while it touches a few of them.
        if (type === 'scenario' && [...affected.keys()].filter((key) => machines.has(key)).length > 6) continue;
        const ring = ringMesh(color, 0.35 + 0.4 * strength);
        followers.push({ mesh: ring, id, grow: id === selectedId ? 1.3 : 1.15 });
        groups.marks.add(ring);
        if (hologram && type !== 'link') pulse(ring, id === selectedId ? 1 : 0.7);
        continue;
      }
      const outline = outlineOf(twin);
      // A scenario that touches most roads (heavy rain) is named by its chip and card, not by lines on every road.
      if (kind === 'road-segment' && type === 'scenario' && id !== affected.get(id)?.target
        && [...affected.keys()].filter((key) => kindOf(twins.get(key)) === 'road-segment').length > 6) continue;
      if (outline && kind === 'road-segment') {
        // A road is marked by a line along each edge (and a light wash when selected).
        const w = (twin.attributes?.spec?.width_m ?? 25) / 2 + 3;
        const points = outline.points.map(([x, y, z]) => [x, y, Math.max(z ?? 0, heightAt(x, y)) + 1.3]);
        const edges = new Builder();
        const side = (s) => points.map((p, i) => {
          const q = points[Math.min(points.length - 1, i + 1)];
          const o = points[Math.max(0, i - 1)];
          const dx = q[0] - o[0]; const dy = q[1] - o[1];
          const len = Math.hypot(dx, dy) || 1;
          return L(p[0] - (dy / len) * w * s, p[1] + (dx / len) * w * s, p[2]);
        });
        const lineWidth = 2.4 * Math.max(1, k * 0.5);
        ribbon(edges, side(1), lineWidth, [1, 1, 1], 0.6, false);
        ribbon(edges, side(-1), lineWidth, [1, 1, 1], 0.6, false);
        const edgeMesh = new THREE.Mesh(edges.machineGeometry(), new THREE.MeshBasicMaterial({ toneMapped: false, color, transparent: true, opacity: (0.6 + 0.35 * strength) * (hologram && type === 'link' ? 0.5 : 1), side: THREE.DoubleSide,
          depthWrite: false, polygonOffset: true, polygonOffsetFactor: -4, polygonOffsetUnits: -8, blending: hologram ? THREE.AdditiveBlending : THREE.NormalBlending }));
        groups.marks.add(edgeMesh);
        if (hologram && type !== 'link') pulse(edgeMesh, 0.5);
        if (id === selectedId) {
          const wash = new Builder();
          ribbon(wash, points.map(([x, y, z]) => L(x, y, z)), w * 2, [1, 1, 1], 0.5, false);
          groups.marks.add(new THREE.Mesh(wash.machineGeometry(), new THREE.MeshBasicMaterial({ toneMapped: false, color, transparent: true, opacity: hologram ? 0.12 : 0.16, side: THREE.DoubleSide,
            depthWrite: false, polygonOffset: true, polygonOffsetFactor: -4, polygonOffsetUnits: -8, blending: hologram ? THREE.AdditiveBlending : THREE.NormalBlending })));
        }
        continue;
      }
      if (outline) {
        const b = new Builder();
        const large = ['pit', 'bench', 'waste-dump', 'rom-pad', 'tailings'].includes(kind);
        const width = kind === 'road-segment' ? ((twin.attributes?.spec?.width_m ?? 25) + 10) * Math.max(1, k * 0.4)
          : large ? 3 * Math.max(1, k * 0.35) : 5 * Math.max(1, k * 0.45);
        const points = outline.points.map(([x, y, z]) => [x, y, Math.max(kind === 'road-segment' ? (z ?? 0) : 0, heightAt(x, y)) + 1.2]);
        ribbon(b, points.map(([x, y, z]) => L(x, y, z)), width, [1, 1, 1], 0.6, outline.closed);
        const material = new THREE.MeshBasicMaterial({ toneMapped: false, color, transparent: true, opacity: ((large ? 0.45 : 0.55) + 0.35 * strength) * (hologram && type === 'link' ? 0.45 : 1), side: THREE.DoubleSide, depthWrite: false,
          blending: type === 'scenario' || hologram ? THREE.AdditiveBlending : THREE.NormalBlending, polygonOffset: true, polygonOffsetFactor: -4, polygonOffsetUnits: -8 });
        const mesh = new THREE.Mesh(b.machineGeometry(), material);
        groups.marks.add(mesh);
        if (hologram && type !== 'link') pulse(mesh, 0.5);
        continue;
      }
      const at = anchorOf(twin);
      if (!at) continue;
      const ring = ringMesh(color, 0.35 + 0.55 * strength);
      const r = (units.get(id)?.r ?? 8) + 6;
      const zg = units.get(id)?.base ?? heightAt(at.x, at.y);
      ring.position.set(at.x - O.x, at.y - O.y, zg - O.z + 1.5);
      ring.scale.setScalar(Math.max(r, 10 * k));
      ring.userData.size = ring.scale.x;
      groups.marks.add(ring);
      if (hologram && type !== 'link') pulse(ring, type === 'select' ? 1 : 0.7);
    }
    buildBlast();
    updatePlant();
  }

  // The pulse of the hologram's marks: a slow breath of their size and brightness.
  function stepPulses(now) {
    if (!pulses.length) return;
    const phase = (now % 1600) / 1600;
    const wave = 0.5 - 0.5 * Math.cos(phase * Math.PI * 2);
    for (const mesh of pulses) {
      const p = mesh.userData.pulse;
      mesh.material.opacity = p.opacity * (0.55 + 0.45 * wave * p.amount + 0.45 * (1 - p.amount));
      if (mesh.userData.size) mesh.scale.setScalar(mesh.userData.size * (1 + 0.12 * wave * p.amount));
      mesh.userData.wave = 1 + 0.12 * wave * p.amount;
    }
  }

  function buildBlast() {
    clearGroup(groups.blast);
    blast = null;
    const pit = first('pit');
    const block = twins.get(feature(pit, 'blast_block'));
    const radius = feature(pit, 'blast_radius_m');
    if (!block?.attributes?.geo?.polygon || !(radius > 0)) return;
    const hologram = holo();
    const red = hologram ? HOLO.red : C.bad;
    const blend = hologram ? THREE.AdditiveBlending : THREE.NormalBlending;
    const c = areaCentroid(block.attributes.geo.polygon);
    const z = heightAt(c.x, c.y);
    // The exclusion zone: a dome (in the hologram, glass with a glowing rim).
    const domeGeometry = new THREE.SphereGeometry(radius, 36, 12, 0, Math.PI * 2, 0, Math.PI / 2);
    const dome = new THREE.Mesh(domeGeometry, hologram && !soft ? markGlass(HOLO.red, 0.05)
      : new THREE.MeshBasicMaterial({ toneMapped: false, color: red, transparent: true, opacity: 0.12, side: THREE.DoubleSide, depthWrite: false }));
    dome.rotation.x = Math.PI / 2;
    dome.position.set(c.x - O.x, c.y - O.y, z - O.z);
    groups.blast.add(dome);
    for (const f of hologram ? [0.15, 0.35, 0.55, 0.72, 0.88] : [0.25, 0.55, 0.82]) {
      const r = radius * Math.cos((f * Math.PI) / 2);
      const h = radius * Math.sin((f * Math.PI) / 2);
      const pts = [];
      for (let i = 0; i <= 72; i += 1) {
        const a = (i / 72) * Math.PI * 2;
        pts.push(c.x - O.x + Math.cos(a) * r, c.y - O.y + Math.sin(a) * r, z - O.z + h);
      }
      const g = new THREE.BufferGeometry();
      g.setAttribute('position', new THREE.Float32BufferAttribute(pts, 3));
      groups.blast.add(new THREE.Line(g, new THREE.LineBasicMaterial({ toneMapped: false, color: red, transparent: true, opacity: hologram ? 0.7 : 0.45, depthWrite: false, blending: blend })));
    }
    if (hologram) {
      // Meridians of the dome, so it reads as a cage of light.
      for (let m = 0; m < 12; m += 1) {
        const a = (m / 12) * Math.PI * 2;
        const pts = [];
        for (let i = 0; i <= 16; i += 1) {
          const t = (i / 16) * (Math.PI / 2);
          pts.push(c.x - O.x + Math.cos(a) * Math.cos(t) * radius, c.y - O.y + Math.sin(a) * Math.cos(t) * radius, z - O.z + Math.sin(t) * radius);
        }
        const g = new THREE.BufferGeometry();
        g.setAttribute('position', new THREE.Float32BufferAttribute(pts, 3));
        groups.blast.add(new THREE.Line(g, new THREE.LineBasicMaterial({ toneMapped: false, color: red, transparent: true, opacity: 0.45, depthWrite: false, blending: blend })));
      }
    }
    const b = new Builder();
    const points = [];
    for (let i = 0; i < 120; i += 1) {
      const a = (i / 120) * Math.PI * 2;
      const x = c.x + Math.cos(a) * radius;
      const y = c.y + Math.sin(a) * radius;
      points.push(L(x, y, heightAt(x, y) + 1.2));
    }
    ribbon(b, points, 6, [1, 1, 1], 0.5, true);
    const ground = new THREE.Mesh(b.machineGeometry(), new THREE.MeshBasicMaterial({ toneMapped: false, color: red, transparent: true, opacity: 0.85, side: THREE.DoubleSide, depthWrite: false,
      polygonOffset: true, polygonOffsetFactor: -4, polygonOffsetUnits: -8, blending: blend }));
    groups.blast.add(ground);
    if (hologram) pulse(ground, 1);
    const outline = densify([...block.attributes.geo.polygon, block.attributes.geo.polygon[0]].map(([x, y]) => [x, y, 0]), 6).slice(0, -1);
    const ob = new Builder();
    ribbon(ob, outline.map(([x, y]) => L(x, y, heightAt(x, y) + 1.4)), 2.5, [1, 1, 1], 0.4, true);
    groups.blast.add(new THREE.Mesh(ob.machineGeometry(), new THREE.MeshBasicMaterial({ toneMapped: false, color: hologram ? HOLO.amber : '#ff8a3d', transparent: true, opacity: 0.95,
      side: THREE.DoubleSide, depthWrite: false, polygonOffset: true, polygonOffsetFactor: -4, polygonOffsetUnits: -8, blending: blend })));
    blast = { x: c.x, y: c.y, z, radius, block: block.thingId };
  }

  // Dust over the blast when it is fired, or still hanging when the page opens after it.
  function blastDust(x, y, z, strong) {
    if (!dust) return;
    const n = strong ? 140 : 50;
    for (let i = 0; i < n; i += 1) {
      const a = Math.random() * Math.PI * 2;
      const r = Math.random() * 35;
      dust.emit(x - O.x + Math.cos(a) * r, y - O.y + Math.sin(a) * r, z - O.z + Math.random() * 10,
        { vx: Math.cos(a) * (2 + Math.random() * 5), vy: Math.sin(a) * (2 + Math.random() * 5), vz: 4 + Math.random() * (strong ? 12 : 4),
          size: 24 + Math.random() * 30, growth: 6, seconds: 18 + Math.random() * 22, color: holo() ? [0.16, 0.42, 0.5] : [0.55, 0.47, 0.38], opacity: (strong ? 0.55 : 0.3) * (holo() ? 0.7 : 1) });
    }
  }

  function marksState() {
    related = selectedId ? relatedIds(selectedId) : new Set();
    affected = new Map();
    for (const scenario of activeScenarios()) {
      for (const id of scenario.affected) if (!affected.has(id)) affected.set(id, scenario);
    }
    const pit = first('pit');
    const key = [selectedId, [...related].sort().join(','), [...affected.keys()].sort().join(','), feature(pit, 'blast_block'), feature(pit, 'blast_radius_m'),
      Math.round(Math.log2(equipmentScale()) * 2)].join('|');
    if (key !== marksKey) {
      marksKey = key;
      buildMarks();
    }
    const state = feature(pit, 'blast_state') ?? 'idle';
    if (state !== lastBlastState) {
      if (state === 'reentry' && blast) blastDust(blast.x, blast.y, blast.z, lastBlastState === 'clearing');
      lastBlastState = state;
    }
  }

  // -- light of the moment ------------------------------------------------------------------

  // The simulated time, or the hour of `?time=HH:MM` in the page's address:
  // a preview of the light of that hour (the sun, the sky and the lamps), nothing else.
  const lightHour = (() => {
    const match = /^(\d{1,2}):(\d{2})$/.exec(new URLSearchParams(window.location.search).get('time') ?? '');
    return match && Number(match[1]) < 24 && Number(match[2]) < 60 ? `${match[1].padStart(2, '0')}:${match[2]}` : null;
  })();
  function lightTime(simTime) {
    if (!lightHour) return simTime;
    const day = /^\d{4}-\d{2}-\d{2}/.exec(String(simTime ?? ''))?.[0] ?? '2026-10-28';
    return `${day}T${lightHour}:00`;
  }

  function updateSky(force = false) {
    const mine = first('mine');
    const sun = sunAt(lightTime(feature(mine, 'sim_time')));
    const cond = feature(first('weather'), 'condition') ?? 'dry';
    const wx = cond === 'rain' || cond === 'snow' ? cond : 'dry';
    const key = `${Math.round(sun.elevation * 2)}|${Math.round(Math.atan2(sun.dir.y, sun.dir.x) * 40)}|${wx}`;
    if (!force && key === skyState.key) return false;
    skyState.key = key;
    const p = palette(sun.elevation, wx);
    skyState.elevation = sun.elevation;
    skyState.night = p.night;
    skyState.lamps = p.lamps;
    skyState.colors = p;
    skyState.weather = wx;
    skyState.sun = sun.dir.clone();
    const up = clamp((sun.elevation + 1) / 6, 0, 1);
    const keyDir = keyDirection(sun.dir, sun.elevation);
    LIGHT.sun.copy(keyDir);
    LIGHT.sunColor = p.sun;
    LIGHT.sky = p.sky;
    LIGHT.ground = p.ground;
    LIGHT.rim = mix(scale(p.sky, 0.32), scale(p.sun, 0.05), 0.5);
    LIGHT.localGain = p.lamps;
    // The hologram has no sun: its surfaces keep their own colours.
    if (!holo()) for (const geometry of lit) relight(geometry, LIGHT);
    renderer.toneMappingExposure = p.exposure;
    if (soft) {
      // Machines drawn without lighting: one tint for the time of day.
      const tint = mix(p.sky.map((v) => v * 2.1), [1.06, 1.02, 0.96], clamp(up * Math.min(1, sun.dir.z * 3), 0, 1) * (wx === 'dry' ? 1 : 0.6));
      for (const material of litMaterials) material.color.setRGB(clamp(tint[0], 0.06, 1.1), clamp(tint[1], 0.06, 1.1), clamp(tint[2], 0.08, 1.1));
    } else {
      keyLight.position.copy(keyDir);
      keyLight.color.setRGB(...p.sun);
      backLight.position.set(-keyDir.x, -keyDir.y, 0.35).normalize();
      backLight.color.setRGB(...p.sky);
      backLight.intensity = 0.45 * Math.PI;
      skyLight.color.setRGB(...p.sky);
      skyLight.groundColor.setRGB(...p.ground);
      // At night the machines stand in the light of the floodlights and of each other's lamps.
      siteLight.intensity = p.lamps * 0.32 * Math.PI;
    }
    M.barrier.color.setRGB(...mix([0.9, 0.9, 0.9], [1.5, 1.4, 1.3], up));
    M.glass.color.setRGB(...mix(p.sky.map((v) => v * 1.8), [1, 1, 1], up));
    M.windows.color.setRGB(...mix([0.06, 0.07, 0.08], [3.2, 2.2, 1.1], p.lamps));
    scene.fog.color.setRGB(...p.haze);
    if (soft) scene.background.setRGB(...mix(p.horizon, p.zenith, 0.3));
    if (sky) sky.update(p, sun.dir);
    if (dust) dust.material.uniforms.fogColor.value.setRGB(...p.haze);
    skyState.dustColor = mix([0.36, 0.3, 0.23], p.haze, 0.15).map((v) => v * clamp(0.25 + up, 0.25, 1));
    if (weather) weather.set(wx);
    applyModelLight();
    applyLook();
    applyCamera(false);
    dirty = true;
    return true;
  }

  // What the look changes beyond the materials: the sky or the stage, the
  // tone of the picture, the colour of the haze, rain and dust.
  function applyLook() {
    const hologram = holo();
    if (sky) sky.dome.visible = !hologram;
    if (holoDome) holoDome.visible = hologram;
    groups.holo.visible = hologram;
    renderer.toneMapping = hologram ? THREE.NoToneMapping : THREE.ACESFilmicToneMapping;
    if (weather) {
      weather.rain.material.color.set(hologram ? '#6fd6ff' : '#c7d3dd');
      weather.snow.material.color.set(hologram ? '#c4f4ff' : '#f2f5fa');
    }
    if (panelLayer) panelLayer.hidden = !hologram;
    if (!hologram) return;
    renderer.toneMappingExposure = 1;
    scene.fog.color.set(HOLO.fog);
    if (soft) scene.background.set(HOLO.below);
    if (dust) dust.material.uniforms.fogColor.value.set(HOLO.fog);
  }

  // -- camera -------------------------------------------------------------------------------

  const cam = { target: new THREE.Vector3(), distance: 2400, azimuth: -0.6, elevation: 0.3 };
  let tween = null;
  // A machine flown to is followed while it moves, until the camera is moved by hand.
  let follow = null;
  const followed = () => {
    const at = follow && shown.get(follow);
    return at ? new THREE.Vector3(at.x - O.x, at.y - O.y, at.z - O.z + 3) : null;
  };

  let lastCameraChange = -1e9;
  function applyCamera(moved = true) {
    if (moved) lastCameraChange = performance.now();
    const { target, distance, azimuth, elevation } = cam;
    camera.position.set(
      target.x + distance * Math.cos(elevation) * Math.sin(azimuth),
      target.y - distance * Math.cos(elevation) * Math.cos(azimuth),
      target.z + distance * Math.sin(elevation),
    );
    // Never below the ground.
    const ground = heightAt(camera.position.x + O.x, camera.position.y + O.y) - O.z + 6;
    if (camera.position.z < ground) camera.position.z = ground;
    camera.lookAt(target);
    camera.near = clamp(distance / 40, 2, 60);
    const wet = skyState.weather !== 'dry';
    scene.fog.near = distance * (wet ? 0.5 : 1.5) + (wet ? 400 : 1200);
    scene.fog.far = distance * (wet ? 3.2 : 7) + (wet ? 2500 : 9000);
    camera.far = scene.fog.far * 1.15 + 2000;
    camera.updateProjectionMatrix();
    if (sky) {
      sky.dome.position.copy(camera.position);
      sky.dome.scale.setScalar(camera.far * 0.9);
    }
    if (holoDome) {
      holoDome.position.copy(camera.position);
      holoDome.scale.setScalar(camera.far * 0.9);
    }
    if (holo() && !soft) {
      const lanesOwn = [];
      for (const mesh of roadMeshes.values()) if (mesh.userData.holo) lanesOwn.push(mesh.userData.holo);
      for (const child of groups.blast.children) if (child.material?.uniforms?.uFogNear) lanesOwn.push(child.material);
      setFog([...H.all, ...lanesOwn, ...modelHolo.values()], scene.fog.near, scene.fog.far);
    }
    const scaleK = height / (2 * Math.tan((FOV * Math.PI) / 360));
    if (dust) {
      dust.material.uniforms.scale.value = scaleK;
      dust.material.uniforms.fogNear.value = scene.fog.near;
      dust.material.uniforms.fogFar.value = scene.fog.far;
    }
    if (glows) glows.material.uniforms.scale.value = scaleK;
    if (beacons) beacons.material.uniforms.scale.value = scaleK;
  }

  function setCamera(next, animate = true) {
    const goal = {
      target: next.target ? next.target.clone() : cam.target.clone(),
      distance: clamp(next.distance ?? cam.distance, 40, 14000),
      azimuth: next.azimuth ?? cam.azimuth,
      elevation: clamp(next.elevation ?? cam.elevation, 0.08, 1.5),
    };
    if (animate) {
      let da = goal.azimuth - cam.azimuth;
      da = Math.atan2(Math.sin(da), Math.cos(da));
      goal.azimuth = cam.azimuth + da;
      tween = { from: { target: cam.target.clone(), distance: cam.distance, azimuth: cam.azimuth, elevation: cam.elevation }, to: goal, start: performance.now(), ms: 900 };
    } else {
      tween = null;
      Object.assign(cam, goal);
      applyCamera();
    }
    dirty = true;
  }

  function stepTween(now) {
    if (!tween) {
      const at = followed();
      if (at && at.distanceToSquared(cam.target) > 0.01) {
        cam.target.lerp(at, 0.35);
        applyCamera(false);
        dataDirty = true;
      }
      return;
    }
    const at = followed();
    if (at) tween.to.target.copy(at);
    const f = clamp((now - tween.start) / tween.ms, 0, 1);
    const e = f < 0.5 ? 4 * f * f * f : 1 - (-2 * f + 2) ** 3 / 2;
    const { from, to } = tween;
    cam.target.lerpVectors(from.target, to.target, e);
    // Pull back a little in the middle of a long flight, so the way is seen.
    const hop = Math.sin(Math.PI * e) * Math.min(0.6, (from.target.distanceTo(to.target) / Math.max(to.distance, from.distance)) * 0.35);
    cam.distance = Math.exp(Math.log(from.distance) + (Math.log(to.distance) - Math.log(from.distance)) * e) * (1 + hop);
    cam.azimuth = from.azimuth + (to.azimuth - from.azimuth) * e;
    cam.elevation = from.elevation + (to.elevation - from.elevation) * e;
    applyCamera();
    if (f >= 1) tween = null;
    dirty = true;
  }

  // Camera that shows a box (mine grid) from a direction, in the part of the screen the panels leave free.
  function fit(box, azimuth, elevation, margin = 1.08) {
    const centre = new THREE.Vector3((box.minX + box.maxX) / 2 - O.x, (box.minY + box.maxY) / 2 - O.y, (box.minZ + box.maxZ) / 2 - O.z);
    const ax = Math.sin(azimuth);
    const ay = -Math.cos(azimuth);
    const halfX = (box.maxX - box.minX) / 2;
    const halfY = (box.maxY - box.minY) / 2;
    const across = Math.abs(ax * halfY) + Math.abs(ay * halfX);
    const along = Math.abs(ax * halfX) + Math.abs(ay * halfY);
    const halfHeight = along * Math.sin(elevation) + ((box.maxZ - box.minZ) / 2) * Math.cos(elevation);
    const vfov = (FOV * Math.PI) / 180;
    const freeW = Math.max(200, width - insets.left - insets.right);
    const freeH = Math.max(150, height - insets.top - insets.bottom);
    const hHalf = Math.atan(Math.tan(vfov / 2) * (freeW / Math.max(1, height)));
    const vHalf = Math.atan(Math.tan(vfov / 2) * (freeH / Math.max(1, height)));
    const distance = Math.max(across / Math.tan(hHalf), halfHeight / Math.tan(vHalf)) * margin + along * Math.cos(elevation) * 0.15;
    return { target: centre, distance, azimuth, elevation };
  }

  // The nearest camera from a direction that keeps every point inside `share`
  // of the part of the screen the panels leave free (by projecting them).
  const probeCamera = new THREE.PerspectiveCamera(FOV, 1, 1, 100000);
  probeCamera.up.set(0, 0, 1);
  const probePoint = new THREE.Vector3();
  function fitPoints(points, azimuth, elevation, share = 0.9) {
    if (!points.length || !width || !height) return null;
    const c = points.reduce((acc, p) => [acc[0] + p[0] / points.length, acc[1] + p[1] / points.length, acc[2] + p[2] / points.length], [0, 0, 0]);
    const target = new THREE.Vector3(c[0] - O.x, c[1] - O.y, c[2] - O.z);
    probeCamera.aspect = width / height;
    const dx = Math.round((insets.right - insets.left) / 2);
    const dy = Math.round((insets.bottom - insets.top) / 2);
    if (dx || dy) probeCamera.setViewOffset(width, height, dx, dy, width, height); else probeCamera.clearViewOffset();
    const left = insets.left + (1 - share) * (width - insets.left - insets.right) / 2;
    const right = width - insets.right - (1 - share) * (width - insets.left - insets.right) / 2;
    const top = insets.top + (1 - share) * (height - insets.top - insets.bottom) / 2;
    const bottom = height - insets.bottom - (1 - share) * (height - insets.top - insets.bottom) / 2;
    const fits = (distance) => {
      probeCamera.position.set(target.x + distance * Math.cos(elevation) * Math.sin(azimuth), target.y - distance * Math.cos(elevation) * Math.cos(azimuth), target.z + distance * Math.sin(elevation));
      probeCamera.lookAt(target);
      probeCamera.near = 1;
      probeCamera.far = distance * 10;
      probeCamera.updateProjectionMatrix();
      probeCamera.updateMatrixWorld();
      for (const [x, y, z] of points) {
        probePoint.set(x - O.x, y - O.y, z - O.z).project(probeCamera);
        if (probePoint.z > 1) return false;
        const sx = ((probePoint.x + 1) / 2) * width;
        const sy = ((1 - probePoint.y) / 2) * height;
        if (sx < left || sx > right || sy < top || sy > bottom) return false;
      }
      return true;
    };
    let lo = 50;
    let hi = 30000;
    if (!fits(hi)) return null;
    for (let i = 0; i < 30; i += 1) {
      const mid = Math.sqrt(lo * hi);
      if (fits(mid)) hi = mid; else lo = mid;
    }
    return { target, distance: hi, azimuth, elevation };
  }

  function pitBox() {
    const pit = terrain?.pit;
    if (!pit) return worldBox ? { ...worldBox, minZ: surfaceZ, maxZ: surfaceZ } : null;
    let minX = Infinity; let minY = Infinity; let maxX = -Infinity; let maxY = -Infinity;
    for (const [x, y] of pit.loops[0].pts) {
      minX = Math.min(minX, x); maxX = Math.max(maxX, x); minY = Math.min(minY, y); maxY = Math.max(maxY, y);
    }
    return { minX, maxX, minY, maxY, minZ: pit.floorZ, maxZ: pit.rimZ };
  }

  function presetView(name) {
    if (name === 'plant') {
      const layout = plantLayout();
      const stockpile = first('stockpile');
      const pile = stockpile ? layout.pos.get(stockpile.thingId) : null;
      const points = [...layout.pos.entries()].filter(([key]) => !String(key).includes('#')).map(([key, p]) => {
        const kind = kindOf(twins.get(key));
        if (['tailings', 'conveyor', 'substation', 'workshop', 'fuel-station'].includes(kind)) return null;
        if (kind === 'crusher' && pile && Math.hypot(p.x - pile.x, p.y - pile.y) > 1200) return null;
        return p;
      }).filter(Boolean);
      // Both plants: the clay-ore plant ОФ-2 beside ОФ-1 (its stockpile is not framed: it lies beyond it),
      // seen nearly across the line between them, so that they spread over the screen.
      let azimuth = -0.85;
      const clayAt = first('clay-plant') ? units.get(first('clay-plant').thingId)?.at : null;
      const of1 = hallAnchor ?? pile;
      for (const kind of ['clay-plant', 'desliming', 'clay-flotation']) {
        for (const twin of byKind(kind)) {
          const at = units.get(twin.thingId)?.at;
          if (at) points.push({ x: at.x, y: at.y });
        }
      }
      if (clayAt && of1 && Math.hypot(of1.x - clayAt.x, of1.y - clayAt.y) > 300) {
        const [ax, ay] = of1.x >= clayAt.x ? [of1.x - clayAt.x, of1.y - clayAt.y] : [clayAt.x - of1.x, clayAt.y - of1.y];
        azimuth = Math.atan2(ay, ax);
      }
      if (!points.length) return null;
      const box = { minX: Math.min(...points.map((p) => p.x)) - 40, maxX: Math.max(...points.map((p) => p.x)) + 40,
        minY: Math.min(...points.map((p) => p.y)) - 40, maxY: Math.max(...points.map((p) => p.y)) + 40, minZ: surfaceZ, maxZ: surfaceZ + 50 };
      // Every unit in view, the stockpile with its whole cone, and room above the units for their floating
      // panels: the plants sit in the lower part of the picture, not on a line through its middle.
      const spots = points.flatMap((p) => [[p.x, p.y, heightAt(p.x, p.y) + 20], [p.x, p.y, heightAt(p.x, p.y) + 260]]);
      if (plant?.stockpile) {
        const s = plant.stockpile;
        for (let i = 0; i < 8; i += 1) spots.push([s.x + Math.cos(i * Math.PI / 4) * s.r * 0.8, s.y + Math.sin(i * Math.PI / 4) * s.r * 0.8, s.z]);
      }
      // Turned a little off square to that line: ОФ-1, with most units and panels, in front and lower in the
      // picture, ОФ-2 behind it on the left, so both fill the frame instead of one line across its middle.
      const tilt = 0.45;
      return fitPoints(spots, azimuth + tilt, 0.42, 0.86) ?? fit(box, azimuth + tilt, 0.4, 0.82);
    }
    if (name === 'faces') {
      // A face at work: a shovel with a truck under its bucket (loading, or the truck spotting), its muck
      // pile and the trucks queued for it; one that only waits for a truck comes last.
      const points = [];
      for (const loader of byKind('loader')) {
        const state = feature(loader, 'state');
        if (!['loading', 'waiting'].includes(state)) continue;
        const at = equipmentPlace(loader);
        if (!at) continue;
        const block = resolveRef(feature(loader, 'block'));
        const muck = block && twins.has(block) ? Number(feature(twins.get(block), 'tonnes_remaining')) || 0 : 0;
        points.push({ ...at, loading: state === 'loading', serving: Boolean(feature(loader, 'truck')), queued: Number(feature(loader, 'queue_len')) || 0, muck });
      }
      if (!points.length) return presetView('overview');
      // A truck under the bucket counts most, then the trucks queued and near; a face under a bench wall with
      // a large muck pile reads better than the open floor.
      const floor = terrain?.pit?.floorZ ?? -Infinity;
      const score = (p) => fleetIds.truck.filter((id) => { const t = shown.get(id); return t && Math.hypot(t.x - p.x, t.y - p.y) < 250; }).length * 6
        + (p.z > floor + 10 ? 6 : 0) + (p.loading ? 30 : 0) + (p.serving ? 30 : 0) + Math.min(p.queued, 3) * 4 + (p.muck > 20000 ? 4 : 0);
      points.sort((a, b) => score(b) - score(a));
      const p = points[0];
      const centre = pitCentre() ?? { x: p.x, y: p.y - 100 };
      const away = Math.atan2(centre.x - p.x, -(centre.y - p.y));
      return { target: new THREE.Vector3(p.x - O.x, p.y - O.y, p.z - O.z + 6), distance: 82, azimuth: away + 0.55, elevation: 0.24 };
    }
    if (name === 'ramps') {
      // A hairpin of a ramp, where the trucks climb out of the pit: the
      // sharpest turn of the ramps, preferring one with trucks on it.
      const ramps = byKind('road-segment').filter((segment) => segment.attributes?.spec?.road_class === 'ramp')
        .map((segment) => roadMeshes.get(segment.thingId)?.userData.points ?? segment.attributes.geo.path);
      if (!ramps.length) return presetView('overview');
      // Ramps that meet end to end make one line, so a turn at a junction counts too.
      const turns = [];
      const ends = ramps.flatMap((pts) => [pts[0], pts[pts.length - 1]]);
      for (const pts of ramps) {
        for (let i = 2; i < pts.length - 2; i += 1) turns.push({ p: pts[i], a: pts[i - 2], b: pts[i + 2] });
      }
      for (const e of ends) {
        const near = ramps.filter((pts) => Math.hypot(pts[0][0] - e[0], pts[0][1] - e[1]) < 3 || Math.hypot(pts[pts.length - 1][0] - e[0], pts[pts.length - 1][1] - e[1]) < 3);
        if (near.length < 2) continue;
        const away = near.map((pts) => (Math.hypot(pts[0][0] - e[0], pts[0][1] - e[1]) < 3 ? pts[Math.min(3, pts.length - 1)] : pts[Math.max(0, pts.length - 4)]));
        turns.push({ p: e, a: away[0], b: away[1] });
      }
      const angle = ({ p, a, b }) => {
        const u = [a[0] - p[0], a[1] - p[1]]; const v = [b[0] - p[0], b[1] - p[1]];
        const cos = (u[0] * v[0] + u[1] * v[1]) / ((Math.hypot(...u) * Math.hypot(...v)) || 1);
        return Math.acos(clamp(cos, -1, 1));       // small = sharp hairpin
      };
      const trucksNear = (p) => fleetIds.truck.filter((id) => { const t = shown.get(id); return t && Math.hypot(t.x - p[0], t.y - p[1]) < 220; }).length;
      turns.sort((x, y) => (angle(x) - trucksNear(x.p) * 0.08) - (angle(y) - trucksNear(y.p) * 0.08));
      const best = turns[0];
      if (!best) return presetView('overview');
      const [px, py] = best.p;
      const pz = best.p[2] ?? heightAt(px, py);
      const pc = pitCentre() ?? { x: px, y: py - 100 };
      const az = Math.atan2(pc.x - px, -(pc.y - py)) + 0.35;
      // The ramps that meet at the turn, whole, with the benches they climb.
      const parts = ramps.filter((pts) => pts.some((q) => Math.hypot(q[0] - px, q[1] - py) < 40));
      const spots = parts.flat().filter((_, i) => i % 3 === 0).map((q) => [q[0], q[1], q[2] ?? heightAt(q[0], q[1])]);
      const view = spots.length > 3 ? fitPoints(spots, az, 0.5, 0.92) : null;
      return view ?? { target: new THREE.Vector3(px - O.x, py - O.y, pz - O.z), distance: 420, azimuth: az, elevation: 0.5 };
    }
    if (name === 'all') {
      if (!worldBox) return null;
      return fit({ ...worldBox, minZ: terrain?.pit?.floorZ ?? surfaceZ, maxZ: surfaceZ + 30 }, -0.35, 0.62, 1.0);
    }
    // Overview: from behind the pit, looking along the conveyor to the plant:
    // the pit fills the front of the picture, the crusher, the conveyor and
    // the plant stand behind it, the steppe runs to the horizon.
    const box = pitBox();
    if (!box) return null;
    const crusher = first('crusher')?.attributes?.geo;
    const points = terrain.pit.loops[0].pts.filter((_, i) => i % 6 === 0).map(([x, y]) => [x, y, terrain.pit.rimZ]);
    if (crusher) points.push([crusher.x + 60, crusher.y, crusher.z ?? terrain.pit.rimZ]);
    const pile = plant?.stockpile;
    let azimuth = -0.62;
    if (pile && pitCentre()) {
      const c = pitCentre();
      // The camera on the far side of the pit from the plant, a little to the south.
      azimuth = Math.atan2(c.x - pile.x, -(c.y - pile.y)) + 0.32;
      points.push([pile.x, pile.y, pile.z + pile.h], [pile.x + 400, pile.y + 200, pile.z]);
    }
    // A little steeper than a horizon view: the plant at the far end of the conveyor and the waste dump beside the
    // pit stay readable instead of being foreshortened into the horizon.
    const view = fitPoints(points, azimuth, 0.52, 0.94);
    return view ?? fit(box, azimuth, 0.52, 0.9);
  }

  // -- captions -----------------------------------------------------------------------------

  const widths = new Map();
  function textWidth(font, text) {
    const key = `${font}|${text}`;
    let value = widths.get(key);
    if (value === undefined) {
      lctx.font = font;
      value = lctx.measureText(text).width;
      if (widths.size > 3000) widths.clear();
      widths.set(key, value);
    }
    return value;
  }

  const projected = new THREE.Vector3();
  function toScreen(x, y, z) {
    projected.set(x - O.x, y - O.y, z - O.z).project(camera);
    if (projected.z > 1 || projected.z < -1) return null;
    const sx = ((projected.x + 1) / 2) * width;
    const sy = ((1 - projected.y) / 2) * height;
    if (sx < -40 || sx > width + 40 || sy < -40 || sy > height + 40) return null;
    return { x: sx, y: sy, depth: projected.z };
  }

  function roundRect(ctx, x, y, w, h, r) {
    ctx.beginPath();
    ctx.moveTo(x + r, y);
    ctx.arcTo(x + w, y, x + w, y + h, r);
    ctx.arcTo(x + w, y + h, x, y + h, r);
    ctx.arcTo(x, y + h, x, y, r);
    ctx.arcTo(x, y, x + w, y, r);
    ctx.closePath();
  }

  function isFree(rect, list) {
    for (const b of list) if (rect[0] < b[2] && rect[2] > b[0] && rect[1] < b[3] && rect[3] > b[1]) return false;
    return true;
  }

  function sign(kind, x, y, value) {
    const ctx = lctx;
    const text = String(value);
    ctx.font = `600 10px ${FONT}`;
    const w = Math.max(16, ctx.measureText(text).width + 9);
    roundRect(ctx, x - w / 2, y - 8, w, 16, 8);
    ctx.fillStyle = '#f2b51c'; ctx.fill();
    ctx.fillStyle = '#1b1300'; ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
    ctx.fillText(text, x, y + 0.5);
    return [x - w / 2 - 1, y - 9, x + w / 2 + 1, y + 9];
  }

  function minutesUntil(iso) {
    const now = Date.parse(`${feature(first('mine'), 'sim_time')}Z`);
    const then = Date.parse(`${iso}Z`);
    return Number.isFinite(now) && Number.isFinite(then) ? Math.max(0, Math.round((then - now) / 60000)) : null;
  }

  // -- floating data panels (hologram look) ------------------------------------------------------

  let panelIds = new Set();
  // Which objects get a panel, most important first: the selected one, those
  // in alarm, those a scenario touches, the links of the selection, every unit
  // of the plant when the camera is at the plant, the machines near the
  // camera, and from afar the shovels and the crusher (in short).
  function panelRequests() {
    const out = new Map();
    const machines = new Set([...fleetIds.truck, ...fleetIds.loader, ...fleetIds['drill-rig']]);
    const add = (id, priority, accent = null, forced = false, compact = false) => {
      const before = out.get(id);
      if (before && before.priority <= priority) return;
      const twin = twins.get(id);
      if (!twin || !PANEL_KINDS.has(kindOf(twin))) return;
      let anchor = null;
      let box = null;
      if (machines.has(id)) { const at = shown.get(id); if (at) anchor = { x: at.x, y: at.y, z: at.top, base: at.z, r: at.r }; } else if (units.has(id)) anchor = units.get(id).at;
      if (!anchor) return;
      const at = toScreen(anchor.x, anchor.y, anchor.z);
      if (!at || at.x < 8 || at.x > width - 8 || at.y < 8 || at.y > height - 8) return;
      if (!isFree([at.x - 1, at.y - 1, at.x + 1, at.y + 1], overlayRects)) return;
      // The selected machine itself stays in sight: no panel over it.
      if (forced && anchor.base !== undefined) {
        const foot = toScreen(anchor.x, anchor.y, anchor.base);
        if (foot) {
          // Its length runs away from the eye: the box reaches above its top and to the sides.
          const h = Math.max(16, Math.abs(foot.y - at.y));
          const w = Math.max(30, h * 2.6);
          box = [at.x - w / 2, at.y - h * 0.8, at.x + w / 2, foot.y + h * 0.4];
        }
      }
      out.set(id, { twin, at, priority, accent, forced, compact, box });
    };
    if (selectedId) add(selectedId, 0, 'select', true);
    for (const id of machines) if (feature(twins.get(id), 'state') === 'down') add(id, 0.5, 'bad');
    for (const id of units.keys()) if (['stopped', 'down'].includes(feature(twins.get(id), 'state'))) add(id, 0.6, 'bad');
    const touched = [...affected.keys()].filter((id) => machines.has(id) || units.has(id));
    for (const id of touched) if (touched.length <= 6 || affected.get(id)?.target === id) add(id, 1, 'scenario');
    for (const id of related) add(id, 1.5, 'link');
    const hall = hallAnchor ?? plant?.stockpile;
    const atPlant = hall && cam.distance < 2200 && Math.hypot(cam.target.x + O.x - hall.x, cam.target.y + O.y - hall.y) < 1500;
    if (atPlant) {
      const order = ['stockpile', 'sag-mill', 'ball-mill', 'flotation', 'concentrate', 'tailings', 'conveyor', 'crusher', 'clay-plant', 'desliming', 'clay-flotation', 'substation', 'workshop', 'fuel-station'];
      for (const id of units.keys()) {
        const kind = kindOf(twins.get(id));
        if (order.includes(kind)) add(id, 2 + order.indexOf(kind) * 0.01);
      }
    }
    // The machines near the camera: the nearest four (two beside a selection, which has its own).
    const near = [];
    for (const id of machines) {
      const at = shown.get(id);
      if (!at) continue;
      const d = Math.hypot(at.x - O.x - camera.position.x, at.y - O.y - camera.position.y, at.z - O.z - camera.position.z);
      if (d < 600) near.push([id, d]);
    }
    near.sort((a, b) => a[1] - b[1]).slice(0, selectedId ? 2 : 4).forEach(([id, d]) => add(id, 3 + d / 1000));
    if (cam.distance > 1100) {
      // From afar, in short: the crusher, the stockpile and the grinding, the chain the mine feeds.
      for (const [kind, k] of [['crusher', 4], ['stockpile', 4.1], ['sag-mill', 4.2]]) {
        const twin = first(kind);
        if (twin) add(twin.thingId, k, null, false, true);
      }
    }
    return [...out.values()].sort((a, b) => a.priority - b.priority);
  }

  const LEADER = { select: 'rgba(240, 253, 255, 0.95)', bad: 'rgba(255, 90, 105, 0.9)', scenario: 'rgba(255, 180, 70, 0.9)', link: 'rgba(170, 240, 255, 0.85)' };
  function drawPanels() {
    if (!panels) return [];
    if (!holo()) { panels.hide(); placedPanels = []; panelIds = new Set(); return []; }
    const max = Math.round(clamp((width * height) / 90000, 6, 16));
    placedPanels = panels.layout(panelRequests(), { width, height, avoid: overlayRects, max, onStale: () => { dataDirty = true; } });
    panelIds = new Set(placedPanels.map((p) => p.id));
    const ctx = lctx;
    ctx.save();
    ctx.lineWidth = 1;
    for (const p of placedPanels) {
      const [x0, y0, x1, y1] = p.rect;
      const beside = p.at.y >= y0 && p.at.y <= y1;
      const ex = beside ? (p.at.x < x0 ? x0 : x1) : clamp(p.at.x, x0 + 6, x1 - 6);
      const ey = beside ? clamp(p.at.y, y0 + 4, y1 - 4) : p.at.y < y0 ? y0 : y1;
      ctx.strokeStyle = LEADER[p.accent] ?? 'rgba(90, 220, 255, 0.8)';
      ctx.beginPath();
      ctx.moveTo(p.at.x, p.at.y);
      ctx.lineTo(ex, ey);
      ctx.stroke();
      ctx.beginPath();
      ctx.arc(p.at.x, p.at.y, 2.4, 0, Math.PI * 2);
      ctx.fillStyle = ctx.strokeStyle;
      ctx.fill();
    }
    ctx.restore();
    return placedPanels.map((p) => [p.rect[0] - 3, p.rect[1] - 3, p.rect[2] + 3, p.rect[3] + 3]);
  }

  function captionRequests() {
    const out = [];
    const near = cam.distance < 1300;
    const close = cam.distance < 560;
    const strongMark = (id) => (id === selectedId ? 'select' : affected.get(id)?.target === id ? 'scenario' : null);
    const decorate = (id, item) => {
      const mark = strongMark(id);
      if (mark === 'select') return { ...item, text: titleOf(twins.get(id)), pill: true, accent: C.select, bold: true, force: true, priority: 0 };
      if (mark === 'scenario') return { ...item, text: `Сценарий: ${titleOf(affected.get(id).twin)}`, pill: true, accent: C.scenario, bold: true, force: true, priority: 0.2 };
      if (related.has(id) || affected.has(id)) return { ...item, color: '#ffffff', bold: true, priority: Math.min(item.priority, 1) };
      return item;
    };
    const push = (id, priority, text, z, options = {}) => {
      if (panelIds.has(id)) return;          // its floating panel names it
      const twin = twins.get(id);
      const anchor = anchorOf(twin);
      if (!anchor) return;
      const at = toScreen(anchor.x, anchor.y, z ?? anchor.top ?? anchor.z);
      if (!at) return;
      const item = decorate(id, { priority, text, at, ...options });
      if (item.text) out.push(item);
    };

    for (const loader of byKind('loader')) {
      const state = feature(loader, 'state');
      const name = shortTitle('loader', titleOf(loader));
      push(loader.thingId, 2, near ? `${name} · ${stateText(state)}` : name, undefined, { color: '#f1f3f4' });
      const queue = feature(loader, 'queue_len');
      const anchor = shown.get(loader.thingId);
      if (queue > 0 && anchor && near && !panelIds.has(loader.thingId)) {
        const at = toScreen(anchor.x, anchor.y, anchor.top);
        if (at) out.push({ priority: 1.5, sign: 'queue', value: queue, at: { x: at.x - 14, y: at.y - 6 } });
      }
    }
    if (near) for (const rig of byKind('drill-rig')) push(rig.thingId, 3, shortTitle('drill-rig', titleOf(rig)));
    for (const truck of byKind('truck')) {
      const id = truck.thingId;
      const state = feature(truck, 'state');
      const special = id === selectedId || related.has(id) || state === 'down';
      if (!close && !special) continue;
      let text = shortTitle('truck', titleOf(truck));
      if (close && feature(truck, 'payload_t') > 1) text += ` · ${formatNumber(feature(truck, 'payload_t'), 0)} т`;
      if (state === 'down') text += ' · неисправен';
      push(id, special ? 1.8 : 4, text, undefined, { size: 10.5 });
    }
    // From afar the two plants stand close together on the screen: each pill goes below its plant (the panels float
    // above it), on the side away from the other plant.
    const plantX = {};
    for (const kind of ['mill', 'clay-plant']) {
      const u = first(kind) ? units.get(first(kind).thingId) : null;
      const at = u ? toScreen(u.at.x, u.at.y, u.at.z) : null;
      if (at) plantX[kind] = at.x;
    }
    const pillSides = (kind) => {
      const other = plantX[kind === 'mill' ? 'clay-plant' : 'mill'];
      if (other === undefined || plantX[kind] === undefined) return undefined;
      return plantX[kind] < other ? ['bl', 'b', 'l', 'tl', 't', 'br', 'r', 'tr'] : ['br', 'b', 'r', 'tr', 't', 'bl', 'l', 'tl'];
    };
    for (const [id, unit] of units) {
      const twin = twins.get(id);
      const kind = kindOf(twin);
      const state = feature(twin, 'state');
      const note = state && !['running', 'idle'].includes(state) ? ` · ${stateText(state)}` : '';
      let text = null;
      if (kind === 'crusher') text = `Дробилка${note}`;
      else if (kind === 'stockpile') text = `Склад руды · ${formatNumber(feature(twin, 'level_pct') ?? 0, 0)} %`;
      else if (kind === 'sag-mill') text = near ? `МПСИ${note}` : null;
      else if (kind === 'ball-mill') text = near ? `Шаровые мельницы${note}` : null;
      // From afar the grinding circuit stands for the whole plant, the mine's bottleneck: named so, with its feed;
      // the plant is marked on the ground (plantRings).
      else if (kind === 'mill') text = near ? null : `ОФ-1 · сульфидная руда · ${formatNumber(feature(twin, 'feed_tph') ?? 0, 0)} т/ч${note}`;
      else if (kind === 'flotation') text = `Флотация${note}`;
      else if (kind === 'concentrate') text = 'Концентрат';
      else if (kind === 'tailings') text = 'Хвостохранилище';
      else if (kind === 'substation') text = near ? 'Подстанция' : null;
      else if (kind === 'workshop') text = 'Мастерская';
      else if (kind === 'fuel-station') text = near ? 'Заправка' : null;
      else if (kind === 'conveyor') text = 'Конвейер';
      else if (kind === 'clay-plant') text = near ? `ОФ-2${note}` : `ОФ-2 · глинистая руда · ${formatNumber(feature(twin, 'feed_tph') ?? 0, 0)} т/ч${note}`;
      else if (kind === 'desliming') text = near ? `Обесшламливание${note}` : null;
      else if (kind === 'clay-flotation') text = near ? `Флотация ОФ-2${note}` : null;
      const main = ['crusher', 'stockpile', 'sag-mill', 'ball-mill', 'mill', 'flotation', 'clay-plant'].includes(kind);
      // The two plants from afar: a pill over each, ahead of every other caption.
      const plantPill = !near && (kind === 'mill' || kind === 'clay-plant')
        ? { pill: true, bold: true, size: 12, accent: holo() ? HOLO.crest : '#ffffff', ...(pillSides(kind) ? { sides: pillSides(kind) } : {}) } : {};
      push(id, plantPill.pill ? 1.2 : main ? 2.5 : 5, text, unit.at.z, { color: main ? '#f1f3f4' : '#d5dadd', ...plantPill });
      if (kind === 'crusher' && feature(twin, 'queue_len') > 0 && near && !panelIds.has(id)) {
        const at = toScreen(unit.at.x, unit.at.y, unit.at.z);
        if (at) out.push({ priority: 1.5, sign: 'queue', value: feature(twin, 'queue_len'), at: { x: at.x - 18, y: at.y - 12 } });
      }
    }
    for (const dump of byKind('waste-dump')) push(dump.thingId, 4.5, 'Отвал');
    for (const pad of byKind('clay-stockpile')) push(pad.thingId, 4.7, near ? `Склад глинистой руды · ${formatNumber(feature(pad, 'level_t') ?? 0, 0)} т` : 'Склад глинистой руды');
    for (const pad of byKind('rom-pad')) push(pad.thingId, 4.6, near ? `${shortTitle('rom-pad', titleOf(pad))} · ${formatNumber(feature(pad, 'level_t') ?? 0, 0)} т` : shortTitle('rom-pad', titleOf(pad)));
    const pit = first('pit');
    if (pit && terrain?.pit) {
      const model = terrain.pit;
      const toward = { x: -Math.sin(cam.azimuth), y: Math.cos(cam.azimuth) };
      const farthest = (pts) => pts.reduce((best, p) => ((p[0] - model.centre.x) * toward.x + (p[1] - model.centre.y) * toward.y
        > (best[0] - model.centre.x) * toward.x + (best[1] - model.centre.y) * toward.y ? p : best), pts[0]);
      const rimPoint = farthest(model.loops[0].pts);
      const spec = pit.attributes?.spec ?? {};
      const depth = typeof spec.rim_elevation_m === 'number' && typeof spec.floor_elevation_m === 'number'
        ? ` · глубина ${formatNumber(spec.rim_elevation_m - spec.floor_elevation_m, 0)} м` : '';
      const pitAt = toScreen(rimPoint[0], rimPoint[1], model.rimZ + 10);
      if (pitAt) out.push(decorate(pit.thingId, { priority: 3.5, text: `Карьер${depth}`, at: pitAt, size: 12, sides: ['t', 'tr', 'tl'] }));
      if (close) {
        for (const ring of model.rings) {
          const p = farthest(ring.pts);
          const at = toScreen(p[0], p[1], ring.z + 2);
          const bench = twins.get(ring.id);
          if (at && bench) out.push(decorate(ring.id, { priority: 6, text: shortTitle('bench', titleOf(bench)), at, size: 10, color: '#c9cfd3', sides: ['b', 'r', 'l'] }));
        }
      }
    }
    for (const segment of byKind('road-segment')) {
      const status = feature(segment, 'status');
      const id = segment.thingId;
      if (status !== 'closed' && status !== 'restricted' && id !== selectedId) continue;
      // Speed limits (all roads in heavy rain) are named only from close by; their signs stand on the road.
      if (status === 'restricted' && !close && id !== selectedId) continue;
      const anchor = anchorOf(segment);
      const at = anchor && toScreen(anchor.x, anchor.y, anchor.z + 6);
      if (!at) continue;
      const limit = feature(segment, 'speed_limit_kmh');
      const text = status === 'closed' ? 'Дорога закрыта' : status === 'restricted' ? (typeof limit === 'number' ? `Ограничение ${formatNumber(limit, 0)} км/ч` : 'Ограничение') : null;
      if (text) {
        // The road's state stays named even when the road is selected or touched by a scenario.
        out.push({ priority: 0.7, text, at, gap: 12, pill: true, accent: status === 'closed' ? C.bad : C.warn, force: status === 'closed', sides: ['t', 'b', 'r', 'l'] });
        if (id === selectedId) out.push(decorate(id, { priority: 0, text: titleOf(segment), at: { x: at.x, y: at.y + 34 }, gap: 8, sides: ['b', 'r', 'l'] }));
      } else out.push(decorate(id, { priority: 1, text: titleOf(segment), at, gap: 8 }));
    }
    if (blast) {
      const at = toScreen(blast.x, blast.y, blast.z + blast.radius * 0.6) ?? toScreen(blast.x, blast.y, blast.z + 10);
      const state = feature(pit, 'blast_state');
      let text = `Опасная зона взрыва · ${formatNumber(blast.radius, 0)} м`;
      if (state === 'clearing') {
        const left = minutesUntil(feature(pit, 'next_blast_sim_time'));
        if (left !== null) text = `Взрыв через ${left} мин · зона ${formatNumber(blast.radius, 0)} м`;
      } else if (state === 'reentry') text = `Взрыв произведён · ожидание допуска · зона ${formatNumber(blast.radius, 0)} м`;
      if (at) out.push({ priority: 0.4, text, at, pill: true, accent: C.bad, bold: true, force: true, sides: ['t', 'b'] });
    }
    for (const note of notes) {
      const anchor = anchorOf(twins.get(note.id));
      const at = anchor && toScreen(anchor.x, anchor.y, (anchor.top ?? anchor.z) + 20);
      if (at) out.push({ priority: 0.9, text: note.text, at, pill: true, accent: '#f2a65a', sides: ['t', 'tr', 'tl', 'r'] });
    }
    if (selectedId && !panelIds.has(selectedId) && !out.some((item) => item.pill && item.accent === C.select)) {
      const twin = twins.get(selectedId);
      const anchor = anchorOf(twin);
      const at = anchor && toScreen(anchor.x, anchor.y, anchor.top ?? anchor.z);
      if (at) out.push({ priority: 0, text: titleOf(twin), at, pill: true, accent: C.select, bold: true, force: true });
    }
    return out;
  }

  const SIDES = ['t', 'r', 'l', 'b', 'tr', 'tl', 'br', 'bl'];
  function captionRect(at, side, w, h, gap) {
    switch (side) {
      case 'r': return [at.x + gap, at.y - h / 2];
      case 'l': return [at.x - gap - w, at.y - h / 2];
      case 'b': return [at.x - w / 2, at.y + gap];
      case 'tr': return [at.x + gap * 0.7, at.y - gap * 0.7 - h];
      case 'tl': return [at.x - gap * 0.7 - w, at.y - gap * 0.7 - h];
      case 'br': return [at.x + gap * 0.7, at.y + gap * 0.7];
      case 'bl': return [at.x - gap * 0.7 - w, at.y + gap * 0.7];
      default: return [at.x - w / 2, at.y - gap - h];
    }
  }

  function paint(item, x0, y0, w, h, font) {
    const ctx = lctx;
    ctx.font = font;
    ctx.textAlign = 'left';
    ctx.textBaseline = 'middle';
    if (item.pill) {
      ctx.strokeStyle = item.accent ?? 'rgba(255,255,255,0.6)';
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(item.at.x, item.at.y);
      ctx.lineTo(Math.min(Math.max(item.at.x, x0 + 4), x0 + w - 4), item.at.y < y0 ? y0 : y0 + h);
      ctx.stroke();
      ctx.beginPath(); ctx.arc(item.at.x, item.at.y, 2.2, 0, Math.PI * 2); ctx.fillStyle = item.accent ?? '#fff'; ctx.fill();
      roundRect(ctx, x0, y0, w, h, 4);
      ctx.fillStyle = 'rgba(17, 21, 25, 0.86)';
      ctx.fill();
      ctx.fillStyle = item.accent ?? '#fff';
      ctx.fillRect(x0, y0 + 3, 2.5, h - 6);
      ctx.fillStyle = '#f4f6f7';
      ctx.fillText(item.text, x0 + 9, y0 + h / 2 + 0.5);
    } else {
      ctx.lineJoin = 'round';
      ctx.lineWidth = 3;
      ctx.strokeStyle = 'rgba(12, 15, 18, 0.72)';
      ctx.strokeText(item.text, x0, y0 + h / 2);
      ctx.fillStyle = item.color ?? '#e4e8ea';
      ctx.fillText(item.text, x0, y0 + h / 2);
    }
  }

  // From afar the two plants are small beside a pit four kilometres long: each is outlined on the ground by a
  // ring round its units (ОФ-1: the stockpile, the mills, the flotation, the concentrate; ОФ-2: the building and
  // the feed stockpile where the trucks tip), drawn on the caption layer, so it costs the 3D view nothing.
  function plantSites() {
    const sites = [];
    const site = (kinds, extra = []) => {
      const pts = [...extra];
      for (const kind of kinds) for (const twin of byKind(kind)) { const u = units.get(twin.thingId); if (u) pts.push([u.at.x, u.at.y, u.r]); }
      if (!pts.length) return;
      const cx = pts.reduce((s, p) => s + p[0], 0) / pts.length;
      const cy = pts.reduce((s, p) => s + p[1], 0) / pts.length;
      const r = Math.max(...pts.map((p) => Math.hypot(p[0] - cx, p[1] - cy) + (p[2] ?? 0))) + 40;
      sites.push({ x: cx, y: cy, r });
    };
    site(['stockpile', 'sag-mill', 'ball-mill', 'mill', 'flotation', 'concentrate']);
    const yard = first('clay-plant')?.attributes?.geo?.polygon ?? [];
    site(['clay-plant', 'desliming', 'clay-flotation'], yard.map((p) => [p[0], p[1], 0]));
    return sites;
  }
  function plantRings() {
    if (cam.distance < 1300) return;
    const ctx = lctx;
    const tone = holo() ? HOLO.crest : '#ffffff';
    for (const s of plantSites()) {
      const ring = [];
      for (let i = 0; i <= 40; i += 1) {
        const a = (i / 40) * Math.PI * 2;
        const x = s.x + Math.cos(a) * s.r;
        const y = s.y + Math.sin(a) * s.r;
        projected.set(x - O.x, y - O.y, heightAt(x, y) + 2 - O.z).project(camera);
        if (projected.z > 1 || projected.z < -1) { ring.length = 0; break; }
        ring.push({ x: ((projected.x + 1) / 2) * width, y: ((1 - projected.y) / 2) * height });
      }
      if (ring.length < 3) continue;
      ctx.beginPath();
      ring.forEach((p, i) => (i ? ctx.lineTo(p.x, p.y) : ctx.moveTo(p.x, p.y)));
      ctx.closePath();
      ctx.fillStyle = holo() ? 'rgba(85, 230, 255, 0.10)' : 'rgba(255, 255, 255, 0.12)';
      ctx.fill();
      ctx.setLineDash([7, 4]);
      ctx.lineWidth = 1.6;
      ctx.strokeStyle = tone;
      ctx.stroke();
      ctx.setLineDash([]);
    }
  }

  let captionCount = 0;
  let placedCaptions = [];
  function drawCaptions() {
    const ctx = lctx;
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.clearRect(0, 0, width, height);
    plantRings();
    const panelRects = drawPanels();
    const requests = captionRequests().sort((a, b) => a.priority - b.priority);
    const boxes = [...panelRects];
    const forced = [];
    captionCount = 0;
    placedCaptions = [];
    for (const item of requests) {
      if (!item.sign) continue;
      if (!isFree([item.at.x - 9, item.at.y - 9, item.at.x + 9, item.at.y + 9], overlayRects)) continue;
      boxes.push(sign(item.sign, item.at.x, item.at.y, item.value));
    }
    for (const item of requests) {
      if (item.sign) continue;
      const size = item.size ?? 11;
      const font = `${item.bold ? '600 ' : '500 '}${size}px ${FONT}`;
      const w = textWidth(font, item.text) + (item.pill ? 17 : 0);
      const h = size + (item.pill ? 9 : 4);
      const gap = item.gap ?? (item.pill ? 16 : 6);
      let done = false;
      for (const side of item.sides ?? SIDES) {
        const [x0, y0] = captionRect(item.at, side, w, h, gap);
        const rect = [x0 - 2, y0 - 1, x0 + w + 2, y0 + h + 1];
        const inside = x0 >= 3 && x0 + w <= width - 3 && y0 >= 3 && y0 + h <= height - 3;
        if (inside && isFree(rect, boxes) && isFree(rect, overlayRects)) {
          paint(item, x0, y0, w, h, font);
          boxes.push(rect);
          if (item.force) forced.push(rect);
          placedCaptions.push({ text: item.text, rect, forced: Boolean(item.force) });
          captionCount += 1;
          done = true;
          break;
        }
      }
      if (!done && item.force) {
        let placed = null;
        for (const side of [...(item.sides ?? []), ...SIDES]) {
          let [x0, y0] = captionRect(item.at, side, w, h, gap);
          x0 = clamp(x0, 3, Math.max(3, width - w - 3));
          y0 = clamp(y0, 3, Math.max(3, height - h - 3));
          const rect = [x0 - 2, y0 - 1, x0 + w + 2, y0 + h + 1];
          if (!placed) placed = [x0, y0, rect];
          if (isFree(rect, forced) && isFree(rect, overlayRects) && isFree(rect, panelRects)) { placed = [x0, y0, rect]; break; }
        }
        paint(item, placed[0], placed[1], w, h, font);
        boxes.push(placed[2]);
        forced.push(placed[2]);
        placedCaptions.push({ text: item.text, rect: placed[2], forced: true });
        captionCount += 1;
      }
    }
  }

  // -- frames ---------------------------------------------------------------------------------

  // Dynamic resolution: while the camera moves on a slow renderer the frames
  // are drawn with fewer pixels and scaled up; when it stops, a sharp frame.
  let renderScale = 1;
  let moveScale = 1;
  let gapEma = 0;
  let lastMoveFrame = 0;
  const interacting = (now) => now - lastCameraChange < 220;

  const stats = { frames: 0, cost: 0, worst: 0, interval: 0, longest: 0, last: 0, since: performance.now(), skipped: 0, first: 0 };
  let frameMs = FRAME_MS;
  let lastStep = 0;
  let particlesAlive = false;

  function render(now) {
    const moving = interacting(now);
    const scaleWanted = soft ? 1 : moving ? moveScale : 1;
    if (scaleWanted !== renderScale) {
      renderScale = scaleWanted;
      renderer.setPixelRatio(renderScale);
      renderer.setSize(width, height, false);
    }
    if (moving && !soft) {
      // How long the renderer takes, from how far apart the frames come.
      if (lastMoveFrame && now - lastMoveFrame < 400) {
        gapEma = gapEma ? gapEma * 0.7 + (now - lastMoveFrame) * 0.3 : now - lastMoveFrame;
        if (gapEma > 44 && moveScale > 0.5) moveScale = Math.max(0.5, moveScale * 0.9);
        else if (gapEma < 27 && moveScale < 1) moveScale = Math.min(1, moveScale * 1.05);
      }
      lastMoveFrame = now;
    } else lastMoveFrame = 0;
    const started = performance.now();
    const dt = lastStep ? clamp((now - lastStep) / 1000, 0, 0.25) : 0;
    lastStep = now;
    marksState();
    if (holo()) stepPulses(now);
    updateFleet(now, dt);
    stepPlantModels(dt);
    stepRoof();
    stepConveyor();
    stepTowers();
    if (dust) particlesAlive = dust.step(dt);
    if (weather) {
      weather.step(dt, cam.target.x, cam.target.y, cam.target.z, clamp(cam.distance * 0.9, 160, 1600));
      // In the hologram the weather is a light touch: faint streaks near the camera, none from afar.
      const fade = holo() ? 1 - clamp((cam.distance - 900) / 900, 0, 1) : 1;
      weather.rain.material.opacity = holo() ? 0.22 * fade : 0.38;
      weather.snow.material.opacity = holo() ? 0.5 * fade : 0.85;
    }
    renderer.render(scene, camera);
    if (timing && gl) {
      const pixel = new Uint8Array(4);
      gl.readPixels(0, 0, 1, 1, gl.RGBA, gl.UNSIGNED_BYTE, pixel);
    }
    drawCaptions();
    const cost = performance.now() - started;
    if (!stats.first && terrain) stats.first = performance.now();
    // The Blender models load once the first frame of the procedural scene is on the screen.
    if (stats.first && libState === 'idle') setTimeout(startModels, 30);
    stats.frames += 1;
    stats.cost += cost;
    stats.worst = Math.max(stats.worst, cost);
    if (stats.last) {
      stats.interval += now - stats.last;
      stats.longest = Math.max(stats.longest, now - stats.last);
    }
    stats.last = now;
    lastRender = now;
    dirty = false;
    dataDirty = false;
    frameMs = Math.max(DETAIL.minFrameMs, cost > 45 ? 90 : cost > 25 ? 55 : FRAME_MS);
  }

  function busy() {
    if (motion.anyMoving() || anyAnimating() || particlesAlive) return true;
    if (holo() && pulses.length) return true;
    if (plantTurning) return true;
    if (weather && weather.kind !== 'dry') return true;
    for (const id of fleetIds.loader) if (feature(twins.get(id), 'state') === 'loading') return true;
    for (const list of Object.values(fleetIds)) for (const id of list) if (feature(twins.get(id), 'state') === 'down') return true;
    return false;
  }

  function frame(now) {
    if (!active) { raf = 0; return; }
    raf = requestAnimationFrame(frame);
    if (!width) return;
    stepTween(now);
    if (dataDue && now >= dataDue) applyData();
    // The camera has stopped: draw it once more in full resolution.
    if (renderScale < 1 && !interacting(now)) dirty = true;
    const moving = !dirty && !dataDirty && busy();
    // A changed camera is drawn at once; data and moving machines at most every frameMs.
    if (!dirty && !moving && !dataDirty) { stats.skipped += 1; stats.last = 0; lastStep = 0; return; }
    if (!dirty && now - lastRender < frameMs) return;
    if (soft && now - lastRender < 60) return;
    render(now);
  }

  // -- data ------------------------------------------------------------------------------------

  const pendingKinds = new Set();
  function applyData() {
    dataDue = 0;
    const kinds = new Set(pendingKinds);
    pendingKinds.clear();
    if (kinds.has('block') || kinds.has('pit')) { buildMuck(); colourBlocks(); }
    if (kinds.has('road-segment')) updateRoads();
    if (kinds.has('rom-pad') || kinds.has('stockpile') || kinds.has('conveyor')) updateAreas();
    updatePlant();
    if (kinds.has('pit') || kinds.has('scenario')) marksKey = '';
    if (kinds.has('mine') || kinds.has('weather')) updateSky();
    dataDirty = true;
  }

  function resize() {
    const box = canvas.parentElement.getBoundingClientRect();
    const w = Math.max(100, Math.round(box.width));
    const h = Math.max(100, Math.round(box.height));
    if (w === width && h === height) return;
    width = w;
    height = h;
    renderer.setSize(w, h, false);
    labelCanvas.width = w;
    labelCanvas.height = h;
    camera.aspect = w / h;
    applyOffset();
    applyCamera(false);
    dirty = true;
  }

  // The middle of the picture is the middle of what the panels leave free.
  function applyOffset() {
    if (!width || !height) return;
    const dx = Math.round((insets.right - insets.left) / 2);
    const dy = Math.round((insets.bottom - insets.top) / 2);
    if (dx || dy) camera.setViewOffset(width, height, dx, dy, width, height);
    else camera.clearViewOffset();
  }

  // -- pointer and keys -------------------------------------------------------------------------

  const raycaster = new THREE.Raycaster();
  const ndc = new THREE.Vector2();

  function local(event) {
    const box = canvas.getBoundingClientRect();
    return [event.clientX - box.left, event.clientY - box.top];
  }

  function pick(px, py) {
    let best = null;
    for (const [id, at] of shown) {
      const screen = toScreen(at.x, at.y, at.z + at.r * 0.4);
      if (!screen) continue;
      const reach = Math.max(10, (at.r / cam.distance) * height * 1.3);
      const d = Math.hypot(screen.x - px, screen.y - py);
      if (d <= reach && (!best || d < best.d)) best = { id, d };
    }
    if (best) return twins.get(best.id) ?? null;
    ndc.set((px / width) * 2 - 1, -(py / height) * 2 + 1);
    raycaster.setFromCamera(ndc, camera);
    const hits = raycaster.intersectObjects(pickables, false);
    for (const hit of hits) {
      const data = hit.object.userData;
      if (data.blocks) {
        if (!hit.object.visible) continue;
        const vertex = hit.faceIndex * 3;
        const range = blockRanges.find((r) => vertex >= r.from && vertex < r.from + r.count);
        if (range) return twins.get(range.id) ?? null;
        continue;
      }
      if (data.pit) {
        const band = terrain?.pit?.bands[data.owners[hit.faceIndex]];
        return twins.get(band?.bench) ?? first('pit') ?? null;
      }
      if (data.owners) {
        const id = data.owners[hit.faceIndex];
        if (id && twins.has(id)) return twins.get(id);
        continue;
      }
      if (data.id && twins.has(data.id)) return twins.get(data.id);
    }
    return null;
  }

  const pointer = { down: false, moved: false, x: 0, y: 0, button: 0, id: null };
  let hoverTimer = 0;

  canvas.addEventListener('contextmenu', (event) => event.preventDefault());
  canvas.addEventListener('pointerdown', (event) => {
    follow = null;
    pointer.down = true;
    pointer.moved = false;
    pointer.x = event.clientX;
    pointer.y = event.clientY;
    pointer.button = event.button;
    pointer.pan = event.button === 2 || event.button === 1 || event.shiftKey;
    pointer.id = event.pointerId;
    tween = null;
  });
  canvas.addEventListener('pointermove', (event) => {
    if (pointer.down) {
      const dx = event.clientX - pointer.x;
      const dy = event.clientY - pointer.y;
      if (!pointer.moved && Math.hypot(dx, dy) < 4) return;
      if (!pointer.moved) {
        pointer.moved = true;
        canvas.setPointerCapture(pointer.id);
        canvas.classList.add('dragging');
        if (onHover) onHover(null);
      }
      pointer.x = event.clientX;
      pointer.y = event.clientY;
      if (pointer.pan) panBy(dx, dy);
      else {
        cam.azimuth -= dx * 0.006;
        cam.elevation = clamp(cam.elevation + dy * 0.005, 0.08, 1.5);
        applyCamera();
        dirty = true;
      }
      return;
    }
    if (!onHover) return;
    clearTimeout(hoverTimer);
    const [px, py] = local(event);
    const { clientX, clientY } = event;
    hoverTimer = setTimeout(() => {
      const twin = pick(px, py);
      canvas.style.cursor = twin ? 'pointer' : '';
      onHover(twin, clientX, clientY);
    }, 60);
  });
  const release = (event) => {
    if (!pointer.down) return;
    pointer.down = false;
    canvas.classList.remove('dragging');
    if (pointer.moved || event.type !== 'pointerup' || pointer.button !== 0) return;
    const [px, py] = local(event);
    const twin = pick(px, py);
    if (twin) onSelect(twin.thingId);
  };
  canvas.addEventListener('pointerup', release);
  canvas.addEventListener('pointercancel', release);
  canvas.addEventListener('pointerleave', () => { if (onHover) onHover(null); });

  function panBy(dx, dy) {
    const metresPerPixel = (2 * cam.distance * Math.tan((FOV * Math.PI) / 360)) / height;
    const right = { x: Math.cos(cam.azimuth), y: Math.sin(cam.azimuth) };
    const forward = { x: -Math.sin(cam.azimuth), y: Math.cos(cam.azimuth) };
    cam.target.x -= (right.x * dx - (forward.x * dy) / Math.max(0.3, Math.sin(cam.elevation))) * metresPerPixel;
    cam.target.y -= (right.y * dx - (forward.y * dy) / Math.max(0.3, Math.sin(cam.elevation))) * metresPerPixel;
    applyCamera();
    dirty = true;
  }

  function zoomBy(factor, px, py) {
    const baseCam = tween ? tween.to : cam;
    const distance = clamp(baseCam.distance / factor, 40, 14000);
    if (px !== undefined) {
      ndc.set((px / width) * 2 - 1, -(py / height) * 2 + 1);
      raycaster.setFromCamera(ndc, camera);
      const plane = new THREE.Plane(new THREE.Vector3(0, 0, 1), -cam.target.z);
      const point = new THREE.Vector3();
      if (raycaster.ray.intersectPlane(plane, point)) {
        const f = 1 - distance / baseCam.distance;
        cam.target.lerp(point, clamp(f, -1, 0.9));
      }
      tween = null;
      cam.distance = distance;
      applyCamera();
      dirty = true;
      return;
    }
    setCamera({ ...baseCam, target: baseCam.target, distance });
  }

  canvas.addEventListener('wheel', (event) => {
    event.preventDefault();
    follow = null;
    const [px, py] = local(event);
    zoomBy(Math.exp(clamp(-event.deltaY, -240, 240) * 0.0018), px, py);
  }, { passive: false });
  canvas.addEventListener('dblclick', (event) => {
    const [px, py] = local(event);
    zoomBy(1.8, px, py);
  });
  canvas.addEventListener('keydown', (event) => {
    const step = 0.12;
    if (event.key.startsWith('Arrow') || event.key === '0' || event.key === 'Home') follow = null;
    if (event.key.startsWith('Arrow')) {
      if (event.shiftKey) {
        const d = 60;
        const moves = { ArrowLeft: [d, 0], ArrowRight: [-d, 0], ArrowUp: [0, d], ArrowDown: [0, -d] };
        panBy(...moves[event.key]);
      } else {
        if (event.key === 'ArrowLeft') cam.azimuth += step;
        if (event.key === 'ArrowRight') cam.azimuth -= step;
        if (event.key === 'ArrowUp') cam.elevation = clamp(cam.elevation + step * 0.6, 0.08, 1.5);
        if (event.key === 'ArrowDown') cam.elevation = clamp(cam.elevation - step * 0.6, 0.08, 1.5);
        tween = null;
        applyCamera();
        dirty = true;
      }
    } else if (event.key === '+' || event.key === '=') zoomBy(1.4);
    else if (event.key === '-' || event.key === '_') zoomBy(1 / 1.4);
    else if (event.key === '0' || event.key === 'Home') api.preset('overview');
    else return;
    event.preventDefault();
  });

  new ResizeObserver(() => { resize(); }).observe(canvas.parentElement);

  // A change of look: every mesh changes its material, the marks are built again.
  function switchLook() {
    const hologram = holo();
    stepConveyor(true);
    insideKey = '';
    if (soft && hologram) for (const geometry of lit) holoRelight(geometry, geometry.userData.holoKind);
    styleAll();
    if (!hologram) whiteFleet();
    plantTintKey = '';
    roadsKey = '';
    updateRoads();
    updatePlant();
    marksKey = '';
    updateSky(true);
    if (canvas.parentElement) canvas.parentElement.dataset.look = style;
    dirty = true;
  }
  if (canvas.parentElement) canvas.parentElement.dataset.look = style;

  // -- api ---------------------------------------------------------------------------------------

  let built = false;
  const api = {
    reload() {
      pickables = [];
      const before = worldBox;
      worldBox = boxOfTwins();
      if (!worldBox) return;
      const moved = before && ['minX', 'maxX', 'minY', 'maxY'].some((key) => Math.abs(before[key] - worldBox[key]) > 200);
      if (moved) built = false;
      const mine = first('mine');
      surfaceZ = mine?.attributes?.geo?.z ?? first('pit')?.attributes?.spec?.rim_elevation_m ?? 0;
      O.x = (worldBox.minX + worldBox.maxX) / 2;
      O.y = (worldBox.minY + worldBox.maxY) / 2;
      O.z = surfaceZ;
      buildWorld();
      buildBlocks();
      buildFleet();
      // The clay-ore plant's model, when its twins have appeared since the models were loaded.
      if (libState === 'ready' && byKind('clay-plant').length && !lib.models.clay_plant) loadMore(['clay_plant']);
      if (!pools && !soft) {
        pools = new THREE.InstancedMesh(new THREE.PlaneGeometry(2, 2), M.pool, POOLS);
        pools.frustumCulled = false;
        pools.count = 0;
        groups.events.add(pools);
      }
      roadsKey = '';
      updateRoads();
      updateAreas();
      updatePlant();
      updateSky(true);
      marksKey = '';
      resize();
      if (!built) {
        built = true;
        const view = presetView('overview');
        if (view) setCamera(view, false);
        if (feature(first('pit'), 'blast_state') === 'reentry') lastBlastState = 'reentry-seen';
      }
      dirty = true;
    },
    patch(twin) {
      const kind = kindOf(twin);
      if (kind === 'truck') motion.report(twin);
      else pendingKinds.add(kind);
      if (kind === 'loader' || kind === 'drill-rig' || kind === 'truck') dataDirty = true;
      if (kind !== 'truck' && !dataDue) dataDue = performance.now() + DATA_DELAY_MS;
    },
    select(thingId) {
      if (thingId !== selectedId) follow = null;
      selectedId = thingId;
      dirty = true;
    },
    get layer() { return layer; },
    setLayer(name) {
      if (name === 'none' || LAYERS[name]) { layer = name; colourBlocks(); dirty = true; }
    },
    setOverlays(rects) { overlayRects = rects; dirty = true; },
    // The parts of the screen the panels cover: presets frame what is left.
    setInsets(next) {
      insets = { top: 0, right: 0, bottom: 0, left: 0, ...next };
      applyOffset();
      applyCamera(false);
      dirty = true;
    },
    setNotes(list) { notes = list ?? []; dirty = true; },
    get notes() { return notes; },
    setActive(on) {
      active = on;
      if (on) {
        resize();
        dirty = true;
        if (!raf) raf = requestAnimationFrame(frame);
      }
    },
    preset(name) {
      follow = null;
      const view = presetView(name === 'pit' ? 'overview' : name);
      if (view) setCamera(view);
    },
    zoomBy(factor) { zoomBy(factor); },
    // A camera looking at a point of the mine grid (for the screenshot scripts): from `distance`
    // metres, `azimuth` and `elevation` in radians as the camera API has them; `follow` a machine.
    look({ x, y, z, distance, azimuth = cam.azimuth, elevation = cam.elevation, follow: id = null, animate = false }) {
      follow = id;
      setCamera({ target: new THREE.Vector3(x - O.x, y - O.y, (z ?? heightAt(x, y)) - O.z), distance, azimuth, elevation }, animate);
    },
    // Where a machine stands now and where the plant's units are (for the screenshot scripts).
    where(thingId) {
      const at = shown.get(thingId) ?? units.get(thingId)?.at ?? null;
      return at ? { x: at.x, y: at.y, z: at.z, bay: Boolean(at.bay), base: units.get(thingId)?.base ?? at.z } : null;
    },
    reveal(thingId, closer = false) {
      const twin = twins.get(thingId);
      const anchor = anchorOf(twin);
      if (!anchor) return false;
      const target = new THREE.Vector3(anchor.x - O.x, anchor.y - O.y, (units.get(thingId)?.base ?? anchor.z) - O.z);
      const screen = toScreen(anchor.x, anchor.y, anchor.z);
      const free = screen && screen.x > insets.left + 40 && screen.x < width - insets.right - 40 && screen.y > insets.top + 30 && screen.y < height - insets.bottom - 30;
      if (!closer && free) return true;
      const kind = kindOf(twin);
      const geo = twin.attributes?.geo ?? {};
      // Deep in the pit the camera looks down more steeply, so the walls do not hide the object.
      const deep = terrain?.pit && anchor.z < terrain.pit.rimZ - 30;
      const elevation = closer ? clamp(cam.elevation, deep ? 0.45 : 0.3, 0.85) : cam.elevation;
      if (closer && blast && thingId === blast.block) {
        // The block that is about to blow: the whole exclusion zone in view.
        setCamera({ target: new THREE.Vector3(blast.x - O.x, blast.y - O.y, blast.z - O.z), distance: blast.radius * 4.2, elevation: Math.max(elevation, 0.6) });
        return true;
      }
      const unit = units.get(thingId);
      if (closer && unit && geo.polygon?.length >= 3 && kind !== 'mine') {
        // A building with its yard (ОФ-2 and the feed stockpile where the trucks tip): both in view, seen across
        // the line between them, in the part of the screen the panels leave free.
        const yard = geo.polygon.map((p) => [p[0], p[1], p[2] ?? heightAt(p[0], p[1])]);
        const c = yard.reduce((acc, p) => [acc[0] + p[0] / yard.length, acc[1] + p[1] / yard.length], [0, 0]);
        const spots = [...yard];
        for (let i = 0; i < 8; i += 1) {
          const a = (i * Math.PI) / 4;
          spots.push([unit.at.x + Math.cos(a) * unit.r * 1.2, unit.at.y + Math.sin(a) * unit.r * 1.2, unit.at.z + 25]);
        }
        let azimuth = Math.atan2(c[1] - unit.at.y, c[0] - unit.at.x);
        if (Math.cos(azimuth - cam.azimuth) < 0) azimuth += Math.PI;      // the side nearer the camera's
        const view = Math.hypot(c[0] - unit.at.x, c[1] - unit.at.y) > 30 ? fitPoints(spots, azimuth, Math.max(elevation, 0.55), 0.8) : null;
        if (view) { setCamera(view); return true; }
      }
      if (closer && (geo.path || (geo.polygon && kind !== 'mine'))) {
        // A road or an area: its whole outline in view.
        const pts = geo.path ?? geo.polygon;
        const zs = pts.map((p) => p[2] ?? heightAt(p[0], p[1]));
        const box = { minX: Math.min(...pts.map((p) => p[0])), maxX: Math.max(...pts.map((p) => p[0])), minY: Math.min(...pts.map((p) => p[1])), maxY: Math.max(...pts.map((p) => p[1])),
          minZ: Math.min(...zs), maxZ: Math.max(...zs) };
        const pad = Math.max(40, 0.15 * Math.max(box.maxX - box.minX, box.maxY - box.minY));
        const view = fit({ ...box, minX: box.minX - pad, maxX: box.maxX + pad, minY: box.minY - pad, maxY: box.maxY + pad }, cam.azimuth, elevation, 1);
        view.distance = clamp(view.distance, 260, 3200);
        setCamera(view);
        return true;
      }
      const size = units.get(thingId)?.r ?? 20;
      const machine = { truck: 110, loader: 130, 'drill-rig': 110 }[kind];
      const distance = closer ? (machine ?? clamp(size * 6, 200, 2600)) : cam.distance;
      let azimuth = cam.azimuth;
      if (closer && deep && pitCentre()) {
        // In the pit: from the pit's middle towards the object, so no wall stands in between.
        const c = pitCentre();
        if (Math.hypot(c.x - anchor.x, c.y - anchor.y) > 60) azimuth = Math.atan2(c.x - anchor.x, -(c.y - anchor.y)) + 0.3;
      }
      // Lift the camera until the ground under it is well below it.
      let lift = elevation;
      for (let i = 0; i < 8; i += 1) {
        const cx = anchor.x + distance * Math.cos(lift) * Math.sin(azimuth);
        const cy = anchor.y - distance * Math.cos(lift) * Math.cos(azimuth);
        if (heightAt(cx, cy) + 25 < anchor.z + distance * Math.sin(lift)) break;
        lift = Math.min(1.3, lift + 0.12);
      }
      setCamera({ target, distance, elevation: lift, azimuth });
      if (closer && ['truck', 'loader', 'drill-rig'].includes(kind)) follow = thingId;
      return true;
    },
    setTiming(on) { timing = on; },
    frameStats() {
      const info = renderer.info.render;
      const out = {
        seconds: (performance.now() - stats.since) / 1000,
        frames: stats.frames,
        avgCostMs: stats.frames ? stats.cost / stats.frames : 0,
        worstCostMs: stats.worst,
        avgIntervalMs: stats.frames > 1 ? stats.interval / (stats.frames - 1) : 0,
        longestIntervalMs: stats.longest,
        idleChecks: stats.skipped,
        triangles: info.triangles,
        drawCalls: info.calls,
        captions: captionCount,
        canvas: `${width}x${height}`,
        movingFrameMs: frameMs,
        timing,
        soft,
      };
      Object.assign(stats, { frames: 0, cost: 0, worst: 0, interval: 0, longest: 0, last: 0, since: performance.now(), skipped: 0 });
      return out;
    },
    // When the first frame was drawn (performance.now() of the page).
    get firstFrameAt() { return stats.first; },
    // For the page check: draw now and say how much of the picture is the mine (not sky).
    probe() {
      if (sky) sky.dome.visible = false;
      const domeShown = holoDome?.visible;
      if (holoDome) holoDome.visible = false;
      const background = scene.background;
      scene.background = new THREE.Color(0, 0, 0);
      const fog = scene.fog;
      scene.fog = null;
      render(performance.now());
      let pixels;
      if (gl) {
        pixels = new Uint8Array(gl.drawingBufferWidth * gl.drawingBufferHeight * 4);
        gl.readPixels(0, 0, gl.drawingBufferWidth, gl.drawingBufferHeight, gl.RGBA, gl.UNSIGNED_BYTE, pixels);
      } else {
        pixels = canvas.getContext('2d').getImageData(0, 0, canvas.width, canvas.height).data;
      }
      let other = 0;
      let total = 0;
      for (let i = 0; i < pixels.length; i += 4 * 16) {
        total += 1;
        if (pixels[i] + pixels[i + 1] + pixels[i + 2] > 12) other += 1;
      }
      if (sky) sky.dome.visible = !holo();
      if (holoDome) holoDome.visible = domeShown;
      scene.background = background;
      scene.fog = fog;
      render(performance.now());
      const counts = {};
      for (const [kind, ids] of Object.entries(fleetIds)) counts[kind] = ids.length;
      // Triangles each part of the world holds (machines: all instances).
      const tris = (geometry) => (geometry.index ? geometry.index.count : geometry.attributes.position.count) / 3;
      const parts = {};
      for (const [name, group] of Object.entries(groups)) {
        let n = 0;
        group.traverse((node) => { if (node.isMesh && node.visible) n += tris(node.geometry) * (node.isInstancedMesh ? node.count : 1); });
        parts[name] = Math.round(n);
      }
      parts.machines = Math.round(Object.values(fleet).reduce((sum, mesh) => sum + (mesh.visible ? tris(mesh.geometry) * mesh.count : 0), 0));
      // How many meshes each part of the world draws (visible ones; machines: one per part and level of detail).
      const meshes = {};
      for (const [name, group] of Object.entries(groups)) { let n = 0; group.traverse((node) => { if ((node.isMesh || node.isLine || node.isPoints) && node.visible && (!node.isInstancedMesh || node.count > 0)) n += 1; }); meshes[name] = n; }
      meshes.machines = Object.values(fleet).filter((mesh) => mesh.visible && mesh.count > 0).length;
      return { filled: other / Math.max(1, total), soft, triangles: renderer.info.render.triangles, drawCalls: renderer.info.render.calls,
        trucks: counts.truck, loaders: counts.loader, rigs: counts['drill-rig'], blocks: blockRanges.length, roads: roadMeshes.size,
        plantUnits: units.size, benches: terrain?.pit?.rings.length ?? 0, captions: captionCount, parts, meshes, lod: lodTriangles, towers: towers.length,
        sun: { elevation: skyState.elevation, night: skyState.night, weather: skyState.weather }, style, panels: placedPanels.length,
        camera: { x: cam.target.x + O.x, y: cam.target.y + O.y, z: cam.target.z + O.z, distance: cam.distance, azimuth: cam.azimuth, elevation: cam.elevation } };
    },
    captions() {
      return [...placedPanels.map((p) => ({ text: p.title, rect: p.rect.slice(), forced: p.forced, panel: true })),
        ...placedCaptions.map((item) => ({ ...item, rect: item.rect.map((v) => Math.round(v)) }))];
    },
    // The floating panels shown now, with what they say (hologram look).
    // fresh: read the twins into the panels now and draw (the page check compares them with the twins).
    panels(fresh = false) {
      if (!panels || !holo()) return [];
      if (fresh && width) { panels.stale(); render(performance.now()); }
      return panels.shown();
    },
    get style() { return style; },
    setStyle(next) {
      const wanted = next === 'realistic' ? 'realistic' : 'hologram';
      if (wanted === style) return;
      style = wanted;
      switchLook();
    },
    positions() {
      const out = {};
      for (const id of fleetIds.truck) {
        const at = shown.get(id);
        if (at) out[id] = [Math.round(at.x * 10) / 10, Math.round(at.y * 10) / 10];
      }
      return out;
    },
    // Where a machine is drawn and how its moving parts stand, for the page check.
    pose(thingId) {
      const at = shown.get(thingId);
      const a = anim.get(thingId);
      return at ? { x: at.x, y: at.y, z: at.z, tilt: a?.tilt ?? 0, mast: a?.mast ?? 0, slew: a?.slew ?? null,
        roll: a?.roll ?? 0, boom: a?.boom ?? null, stick: a?.stick ?? null, bucket: a?.bucket ?? null, lod: a?.lod ?? null } : null;
    },
    // The Blender models: whether they are loaded, which, what they draw (for the page check).
    models() {
      const kinds = {};
      for (const [kind, entry] of Object.entries(gf)) kinds[kind] = entry ? entry.rigs.map((rig) => rig.triangles) : null;
      return { state: libState, loadMs: libReadyAt ? Math.round(libReadyAt - libStarted) : null, loaded: lib ? Object.keys(lib.models) : [], failed: lib?.failed ?? [],
        machines: kinds, plant: placed.map((entry) => entry.placement.model), conveyor: conveyorModel ? conveyorModel.segments.length : 0,
        towers: towerMeshes.length && towerMeshes[0].isInstancedMesh && libState === 'ready' ? towers.length : 0, roofOpen,
        turning: Object.fromEntries(placed.flatMap((entry) => entry.turning.map((t) => [`${entry.placement.model}/${t.name}`, entry.angles[t.name] ?? 0]))) };
    },
    // Where the parts of a machine drawn with its model are now (mine grid, metres), by node name.
    nodes(thingId) {
      const a = anim.get(thingId);
      const kind = kindOf(twins.get(thingId));
      const entry = gf[kind];
      if (!a?.frame || !entry || !G) return null;
      const rig = entry.rigs[0];
      const mats = rig.parts.map(() => new THREE.Matrix4());
      G.poseRig(rig, a.frame, a.angles, mats);
      const out = {};
      rig.parts.forEach((part, i) => {
        const p = new THREE.Vector3().setFromMatrixPosition(mats[i]);
        out[part.name] = [p.x + O.x, p.y + O.y, p.z + O.z].map((v) => Math.round(v * 100) / 100);
      });
      return out;
    },
    get hallAnchor() { return hallAnchor; },
    get renderer() { return gl ? gl.getParameter(gl.VERSION) : '2D canvas (no WebGL)'; },
  };
  return api;
}

// Whether this browser can draw the 3D view with WebGL at all.
export function webglAvailable() {
  try {
    const probe = document.createElement('canvas');
    return Boolean(probe.getContext('webgl2') || probe.getContext('webgl'));
  } catch {
    return false;
  }
}
