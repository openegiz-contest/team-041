# Builds every model of the 3D view from source and writes the .glb files and
# models.json. Run headless (see README.md):
#
#   blender -b --factory-startup --python-exit-code 1 -P tools/blender/build.py -- [--out DIR] [--only id,id]

import hashlib
import importlib
import json
import os
import sys

import bpy

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, 'models'))

import kit  # noqa: E402

MODELS = ['truck', 'shovel', 'drill', 'crusher', 'mill_hall', 'flotation', 'thickener', 'conveyor', 'floodlight', 'clay_plant']
BUDGET = {'machine': {0: 8000, 1: 800}, 'plant': {0: 15000, 1: 800}}
TOTAL_BYTES = 4 * 1024 * 1024


def args():
    argv = sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else []
    out = os.path.normpath(os.path.join(HERE, '..', '..', 'mine', 'ui', 'site', 'models'))
    only = None
    i = 0
    while i < len(argv):
        if argv[i] == '--out':
            out = argv[i + 1]
            i += 1
        elif argv[i] == '--only':
            only = argv[i + 1].split(',')
            i += 1
        i += 1
    return out, only


def reset():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    kit._materials.clear()
    kit._textures.clear()


def gltf_vec(v):
    """Blender/scene coordinates (Z up) to glTF (Y up)."""
    return [round(v[0], 4), round(v[2], 4), round(-v[1], 4)]


def bounds(objects):
    lo = [1e9] * 3
    hi = [-1e9] * 3
    deps = bpy.context.evaluated_depsgraph_get()
    for ob in objects:
        ev = ob.evaluated_get(deps)
        me = ev.to_mesh()
        mw = ob.matrix_world
        for v in me.vertices:
            w = mw @ v.co
            for k in range(3):
                lo[k] = min(lo[k], w[k])
                hi[k] = max(hi[k], w[k])
        ev.to_mesh_clear()
    return lo, hi


def main():
    out, only = args()
    os.makedirs(out, exist_ok=True)
    manifest_path = os.path.join(out, 'models.json')
    old = {}
    if only and os.path.exists(manifest_path):
        with open(manifest_path) as fh:
            old = {m['id']: m for m in json.load(fh)['models']}
    entries = []
    failures = []
    for name in MODELS:
        if not os.path.exists(os.path.join(HERE, 'models', name + '.py')):
            failures.append('missing model script: ' + name)
            continue
        mod = importlib.import_module(name)
        variants = mod.VARIANTS if hasattr(mod, 'VARIANTS') else [mod.META]
        for meta in variants:
            if only and meta['id'] not in only and name not in only:
                if meta['id'] in old:
                    entries.append(old[meta['id']])
                continue
            lods = meta.get('lods', [0, 1] if meta['kind'] == 'machine' else [0])
            entry = {k: meta[k] for k in ('id', 'title', 'kind') if k in meta}
            entry['reference'] = meta.get('reference', {})
            entry['lods'] = []
            for lod in lods:
                reset()
                mdl = mod.build(lod, meta) if hasattr(mod, 'VARIANTS') else mod.build(lod)
                obs = mdl.build()
                file = '%s_lod%d.glb' % (meta['id'], lod)
                path = os.path.join(out, file)
                kit.export(path)
                nodes = []
                total = 0
                for p in mdl.parts:
                    tri = kit.triangles(obs[p.name])
                    total += tri
                    node = {'name': p.name, 'parent': p.parent or mdl.name, 'triangles': tri,
                            'pivot_m': [round(c, 4) for c in p.pivot], 'pivot_gltf': gltf_vec(p.pivot),
                            'materials': list(p.mats)}
                    info = meta.get('anim', {}).get(p.name)
                    if info:
                        node.update(info)
                        if 'aim' in info:
                            target = {n: pos for n, pos, _ in mdl.pins}.get(info['aim'])
                            if target is None:
                                target = next(q.pivot for q in mdl.parts if q.name == info['aim'])
                            node['rest_length_m'] = round((target - p.pivot).length, 4)
                            node.setdefault('note', 'lookAt(world position of %s), then scale.z = distance / rest_length_m' % info['aim'])
                    nodes.append(node)
                for name, pos, parent in mdl.pins:
                    nodes.append({'name': name, 'parent': parent or mdl.name, 'empty': True,
                                  'pivot_m': [round(c, 4) for c in pos], 'pivot_gltf': gltf_vec(pos),
                                  'what': 'attachment point a cylinder aims at'})
                lo, hi = bounds(list(obs.values()))
                size = os.path.getsize(path)
                with open(path, 'rb') as fh:
                    digest = hashlib.sha256(fh.read()).hexdigest()
                budget = BUDGET[meta['kind']].get(lod, BUDGET[meta['kind']][0])
                if total > budget:
                    failures.append('%s: %d triangles > %d' % (file, total, budget))
                entry['lods'].append({
                    'lod': lod, 'file': file, 'root': mdl.name, 'triangles': total, 'budget': budget,
                    'bytes': size, 'sha256': digest,
                    'bounds_m': {'min': [round(c, 3) for c in lo], 'max': [round(c, 3) for c in hi]},
                    'size_m': [round(hi[k] - lo[k], 3) for k in range(3)],
                    'nodes': nodes,
                })
                print('BUILT %-28s %6d tris %8d bytes' % (file, total, size))
            entries.append(entry)
    order = {}
    for name in MODELS:
        if not os.path.exists(os.path.join(HERE, 'models', name + '.py')):
            continue
        mod = importlib.import_module(name)
        for meta in (mod.VARIANTS if hasattr(mod, 'VARIANTS') else [mod.META]):
            order[meta['id']] = len(order)
    entries.sort(key=lambda e: order.get(e['id'], 999))
    total_bytes = sum(l['bytes'] for e in entries for l in e['lods'])
    if total_bytes > TOTAL_BYTES:
        failures.append('total %d bytes > %d' % (total_bytes, TOTAL_BYTES))
    manifest = {
        'generator': 'tools/blender/build.py (Blender %s), deterministic' % bpy.app.version_string,
        'units': 'metres',
        'axes': {
            'source': 'Blender/scene: Z up, a machine faces +Y, origin on the ground under its centre',
            'gltf': 'glTF: +Y up, a machine faces -Z; Blender (x, y, z) = glTF (x, z, -y)',
            'to_scene': 'for the Z-up scene: rotate the loaded root by +PI/2 about X (root.rotation.x = Math.PI / 2)',
            'rotations': 'rotations about X have the same sign in both frames; pivot_m is in scene axes, pivot_gltf in glTF axes',
        },
        'compression': 'KHR_draco_mesh_compression: load with GLTFLoader + DRACOLoader (vendor/three/addons/libs/draco/gltf/)',
        'materials': {k: {'base_srgb': v[0], 'metallic': v[1], 'roughness': v[2], **{kk: vv for kk, vv in v[3].items()}}
                      for k, v in kit.MATERIALS.items()},
        'vertex_colours': 'COLOR_0 holds grime (multiplies the base colour; white = clean)',
        'total_bytes': total_bytes,
        'total_budget_bytes': TOTAL_BYTES,
        'models': entries,
    }
    with open(manifest_path, 'w', newline='\n') as fh:
        json.dump(manifest, fh, indent=1)
        fh.write('\n')
    print('TOTAL %d bytes in %d files' % (total_bytes, sum(len(e['lods']) for e in entries)))
    if failures:
        for f in failures:
            print('BUDGET FAILED:', f)
        sys.exit(1)


main()
