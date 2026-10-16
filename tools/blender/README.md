# Blender models of the 3D view

The machines and plant buildings of the browser 3D scene, built from Python
scripts run in Blender headless and exported as glTF binary (`.glb`) to
`mine/ui/site/models/`. Nothing is modelled by hand, so every file can be rebuilt
from this directory with one command, and the build is deterministic: a second
build gives byte-identical `.glb` files and the same `models.json` (only the
generator line names the Blender version).

## Rebuild

Blender 4.2 LTS, headless:

```sh
# Blender 4.2 LTS from blender.org (no root needed), or any `blender` 4.2 on the PATH
curl -LO https://download.blender.org/release/Blender4.2/blender-4.2.3-linux-x64.tar.xz
tar xf blender-4.2.3-linux-x64.tar.xz
BLENDER=$PWD/blender-4.2.3-linux-x64/blender tools/blender/build.sh            # all models
BLENDER=... tools/blender/build.sh --only cat785c,ex3600                       # some of them
BLENDER=... tools/blender/build.sh --out /tmp/models                           # elsewhere
```

`build.sh` runs `blender -b --factory-startup --python-exit-code 1 -P build.py`.
The build fails (exit 1) when a model is over its triangle budget or all files
together are over 4 MB. It was made and checked with Blender 4.2.3 LTS (the
official Linux build). Ubuntu 24.04's `apt install blender` gives 4.0.2,
which was not tried and probably needs a change: in 4.0 the Weighted Normal
modifier wants the mesh's Auto Smooth on (removed in 4.1), and its output bytes
would differ from 4.2's anyway. Use 4.2 LTS.

Check the result in a browser: serve `mine/ui/site/` (e.g. `python3 -m http.server
8765` in that directory) and open `/models/preview.html`. To take the screenshots
in headless Chrome with `--disable-gpu`, failing on any console error:

```sh
node tools/blender/shots.mjs <output dir> http://localhost:8765 [id,id]
```

## Files

| File | What it is |
|---|---|
| `build.sh`, `build.py` | entry point: builds every model and LOD, exports, writes `models.json`, checks budgets |
| `kit.py` | the modelling kit: part builder over bmesh (chamfered boxes, cylinders, lathes, prisms, beams, lofts, handrails, stairs, cabs), shared PBR materials, procedural textures, vertex-colour grime, pivots, export settings |
| `machine_parts.py` | tyres with lugs and rims, crawler tracks with shoes and grousers, hydraulic cylinders |
| `plant_parts.py` | clad buildings with window bands, pitched roofs, roof lights, tanks, box trusses, platforms, pipes |
| `models/*.py` | one script per model (below) |
| `shots.mjs` | screenshots of the preview page through `mine/ui/tests/cdp.mjs` |

## Conventions

- Metres; Blender/scene axes as in `mine/ui/site/js/models3d.js`: Z up, a
  machine faces +Y, origin on the ground under the machine's centre.
- The glTF files are spec glTF (+Y up): Blender (x, y, z) = glTF (x, z, -y), so a
  machine faces glTF -Z. In the Z-up scene, set `root.rotation.x = Math.PI / 2`.
  Rotations about X have the same sign in both frames.
- Every moving part is a node whose origin is its pivot (hinge pin, wheel
  centre, slewing centre, boom foot, mast trunnion, mill axis). `models.json`
  lists, for every node, its parent, its pivot in scene and glTF axes, the axis
  and range of motion and a note on the sign.
- Hydraulic cylinders are one node each, origin at one pin, glTF +Z along the
  cylinder; an empty node `pin_<name>` sits on the part at the other pin. To
  follow the moving part: `cyl.lookAt(pin.getWorldPosition(v))`, then
  `cyl.scale.z = distance / rest_length_m` (the preview page does this in its
  working pose).
- Machines come in two levels of detail with the same names for the moving
  nodes: LOD0 at most 8,000 triangles, LOD1 at most 800 (LOD1 leaves out the
  cylinders). Plant pieces are one LOD0 at most 15,000 triangles; the conveyor
  segment and the floodlight tower, drawn many times, also have a LOD1.
- Materials are shared by name across all files (`cat_yellow`, `steel`,
  `glass`, `concrete`, `clad_white`, `window`, `water`, `lamp`, …; the list with
  values is in `models.json`). Painted metal is dielectric (metallic 0), bare
  steel 0.45 to 0.55, chrome 1. Grime is in the vertex colours (`COLOR_0`, white
  where clean), which three.js multiplies with the base colour.
- Plant materials share two procedural 256 px textures, generated with a fixed
  seed: `clad` (profiled sheet, ribs every 0.25 m, 4 m a tile) and `concrete`
  (8 m a tile), tinted by each material's base colour; UVs are box-projected
  in metres. A `.glb` cannot point at an image in another file, so each plant
  file embeds its own JPEG copy (about 20 KB each).
- Compression: KHR_draco_mesh_compression (positions 14 bits, normals 10,
  UVs 12, colours 8). Load with `GLTFLoader` and `DRACOLoader`, vendored
  unmodified from three.js r170 in `mine/ui/site/vendor/three/addons/` (no CDN):
  `new DRACOLoader().setDecoderPath('vendor/three/addons/libs/draco/gltf/')`.

## Models and reference dimensions

| id | Model | Built to |
|---|---|---|
| `cat785c` | Caterpillar 785C mining truck, CAT yellow. Frame, deck with railings, diagonal access stair, cab on the left, air cleaners, tanks; dual-slope body with canopy and ribs; heaped load; six 33.00R51 tyres with lugs; hoist cylinders | overall length 11.02 m, operating width 6.64 m, height to canopy 5.77 m, wheelbase 5.18 m, tyres 33.00R51 (2.94 m), body raise 45° (0.79 rad), payload 136 t |
| `ex3600` | Hitachi EX3600-6 loading shovel, Hitachi orange. Crawlers with 1.27 m shoes, car body, swing circle; upper with counterweight round the 6.78 m tail radius, house, engine hood, exhausts, cab on its pedestal, walkway rails, folding stair; boom, stick, 21 m³ bottom-dump bucket with teeth; six cylinders | undercarriage length 8.70 m, track gauge 5.50 m, width over tracks 6.77 m, ground clearance 0.90 m, tail swing radius 6.78 m, transport height 7.83 m, bucket 21 m³, operating weight about 360 t |
| `d55sp` | Sandvik D55SP rotary blasthole drill, Sandvik yellow. Crawlers, frame, deck, power pack with radiator, levelling jacks, cab front left, dust collector, receiver; lattice mast with crown, rotary head, drill pipe, pipe carousel, lowered backwards onto the mast rest; two mast cylinders | mast up 13.72 × 5.05 × 23.06 m, mast down 22.63 m long and 5.33 m high, shipping width 4.40 m, single pass 17 m |
| `crusher` | Primary gyratory crusher station: two-bay dump pocket with hopper, wheel stops and hood, rock breaker, crusher building with crane bay, control room, stair tower, dust collector, E-house, discharge gallery | building about 18 × 26 m, 34 m high, hopper 8 × 20 m; road side local -X, truck level z = 0 |
| `mill_hall` | Grinding hall with roof lights and overhead crane; SAG mill with gearless ring motor, feed chute and trommel; two ball mills with girth gear and dual pinion drives. Roof and each mill shell are separate nodes | hall 160 × 46 × 30 m (ridge +7 m); SAG 12.2 m × 7.9 m (40 ft); ball mills 8.5 m × 13.4 m (28 ft) |
| `flotation` | Two rows of seven tank cells with froth, launders, drives, walkway, stairs, feed and tailings boxes | cells 7.2 m diameter, 6.3 m high (about 300 m³) |
| `thickener_tailings`, `thickener_concentrate` | Round thickeners: tank, overflow launder, feedwell, bridge, drive house, pump house; water and rotating arm as nodes | 76 m (centre column, traction arm) and 26 m (bridge-supported) |
| `conveyor` | 24 m covered conveyor truss segment: box truss, walkway, 1.8 m troughed belt on three-roll idlers, return belt, half-round hoods; ore and trestle as nodes | tiles along local +Y, 24 m a segment; trestle legs 1 m at rest, scaled to height |
| `floodlight` | Mobile floodlight tower: trailer, outriggers, generator canopy, 9 m telescopic mast, four LED floods (lamp faces as a node) | mast 9 m |
| `clay_plant` | Clay-ore plant: main building with annex, desliming cluster of ten cyclones on a 16 m tower, five flotation cells, tanks, pipes, feed conveyor gallery | building 64 × 30 m, 20 m to the eaves; cells 5 m |

Sources for the machine dimensions (public specification data):

- Caterpillar 785C: Caterpillar 785C specifications (cat.com product pages for the
  785 series; Lectura Specs, "Caterpillar 785C", transport length 11.02 m, width
  6.64 m, height 5.77 m; Equipment World 785C listing, wheelbase 5,182 mm, payload
  136 t, 78 m³ SAE 2:1 body).
- Hitachi EX3600-6: Hitachi EX3600-6 specification as given by Ritchie Specs
  ("Hitachi EX3600-6 Shovel": undercarriage 28.55 ft, track gauge 18.05 ft, width
  over tracks 22.22 ft, shoe 50 in, tail swing radius 22.25 ft, ground clearance
  2.97 ft) and Lectura Specs ("EX 3600-6", transport height 7.83 m).
- Sandvik D55SP: Sandvik D55SP rotary blasthole drill rig, product page
  (rocktechnology.sandvik / mining.sandvik: hole range, pull-down, single pass,
  engine) and Geotechpedia "D55SP rotary blasthole rigs" (13.72 × 5.05 × 23.06 m
  mast up; 22.63 × 4.40 × 5.33 m mast down).
- The plant pieces follow the sizes the current procedural scene uses for
  Bozshakol (mill hall, SAG and ball mill sizes, flotation cells, thickener
  diameters) and typical layouts for the rest; they are not drawings of the
  real buildings.

Smaller parts (cab sizes, rails, tanks, cylinders) are proportioned from
photographs of the machines and are approximate.

## Licence

The models and these scripts are the team's own work, released under the MIT
licence like the rest of the repository. The vendored three.js loaders are MIT
(three.js authors) and the Draco decoder is Apache 2.0 (Google); their licence
files are in `mine/ui/site/vendor/three/addons/`.
