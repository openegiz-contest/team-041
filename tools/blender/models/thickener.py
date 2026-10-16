# Round thickeners: the tailings thickener (76 m, column-supported, its rake
# drive turning a truss arm to a carriage on the wall) and the concentrate
# thickener (26 m, bridge-supported). Each has the concrete tank with its
# overflow launder, the feedwell, the access bridge, the drive house; the
# water is its own node (material "water"/"slurry") and the rotating arm is the
# "rake" node.

import math

from mathutils import Matrix

from kit import Model, grime
from plant_parts import lattice, pipe

VARIANTS = [
    {'id': 'thickener_tailings', 'title': 'Tailings thickener, 76 m', 'kind': 'plant', 'r': 38.0, 'wall': 5.0,
     'reference': {'diameter_m': 76, 'wall_m': 5.0, 'drive': 'centre column, traction arm'}},
    {'id': 'thickener_concentrate', 'title': 'Concentrate thickener, 26 m', 'kind': 'plant', 'r': 13.0, 'wall': 4.0,
     'reference': {'diameter_m': 26, 'wall_m': 4.0, 'drive': 'bridge-supported centre drive'}},
]
for _v in VARIANTS:
    _v['anim'] = {
        'tank': {'what': 'tank, launder, centre column, bridge, drive house'},
        'water': {'what': 'water surface (clear at the rim, slurry in the feedwell)', 'note': 'swap in the scene water material'},
        'rake': {'what': 'rotating rake arm (above the water)', 'pivot': 'tank centre', 'axis': 'z',
                 'note': 'turns slowly about local Z (scene) = glTF Y, about 0.05 rad/s'},
    }


def build(lod, meta):
    r = meta['r']
    hw = meta['wall']
    big = r > 20
    mdl = Model('Thickener', dirt=grime(ground=1.5, amount=0.35, dust=0.1))
    t = mdl.part('tank', uv=True)
    w = mdl.part('water')
    rk = mdl.part('rake', pivot=(0, 0, 0))
    n = 48 if big else 32
    zw = hw - 0.35
    # concrete wall: outside, top, inside down to the water
    t.lathe([(r, 0), (r, hw), (r - 0.4, hw), (r - 0.4, zw - 0.05)], 'concrete', n=n, close=False)
    # overflow launder round the outside
    t.lathe([(r + 0.02, hw - 0.9), (r + 1.4, hw - 0.9), (r + 1.4, hw - 0.1), (r + 1.2, hw - 0.1), (r + 1.2, hw - 0.6), (r + 0.02, hw - 0.6)],
            'concrete', n=n, close=False)
    rf = max(2.0, r * 0.11)
    # water: clear outside the feedwell, slurry inside
    w.lathe([(r - 0.4, zw), (rf * 1.6, zw), (rf, zw)], ['water', 'water'], n=n, close=False)
    w.lathe([(rf, zw), (0.0, zw)], 'slurry', n=16, close=False)
    # feedwell
    t.cyl((0, 0, zw - 1.5), (0, 0, hw + 0.6), rf, 'struct', n=16, caps=(False, False))
    if big:
        t.cyl((0, 0, 0), (0, 0, hw + 3.0), 1.6, 'concrete', n=12, caps=(False, True))       # centre column
        t.boxz(0, 0, hw + 3.0, 5.0, 5.0, 3.2, 'clad_white', bevel=0.08, edges='vt')          # drive house
        t.boxz(0, 0, hw + 6.2, 5.4, 5.4, 0.3, 'trim')
        # access bridge from the wall to the drive house
        lattice(t, (-r - 1.6, 0, hw + 1.2), (-2.5, 0, hw + 2.6), 2.0, 1.6, 'struct', bay=4.0)
        t.beam((-r - 1.6, 0, hw + 0.4), (-2.5, 0, hw + 1.8), 2.0, 0.12, 'grating', up=(0, 0, 1))
        pipe(t, [(-r - 3, 0.8, hw + 0.6), (-2.5, 0.8, hw + 2.0)], 0.4, 'struct')
        # the rake: a truss arm from the column to a traction carriage on the wall
        lattice(rk, (1.8, 0, zw + 0.9), (r - 0.6, 0, zw + 0.7), 1.2, 1.4, 'struct_blue', bay=4.0)
        rk.boxz(r - 0.4, 0, hw - 0.1, 2.2, 2.4, 1.4, 'struct_blue', bevel=0.08)
        rk.cyl((1.8, -0.6, zw + 0.3), (1.8, 0.6, zw + 0.3), 1.4, 'struct_blue', n=12, up=(0, 0, 1))
        for k in range(10):
            x = 4 + k * (r - 6) / 9
            rk.beam((x, -0.6, zw + 0.25), (x, 0.6, zw + 0.25), 0.3, 0.2, 'struct')
    else:
        # bridge across the whole tank with the drive in the middle
        lattice(t, (-r - 1.4, 0, hw + 1.0), (r + 1.4, 0, hw + 1.0), 2.0, 1.4, 'struct', bay=3.2)
        t.beam((-r - 1.4, 0, hw + 0.25), (r + 1.4, 0, hw + 0.25), 2.0, 0.1, 'grating', up=(0, 0, 1))
        t.boxz(0, 0, hw + 1.7, 3.6, 3.6, 2.4, 'clad_white', bevel=0.06, edges='vt')
        t.boxz(0, 0, hw + 4.1, 4.0, 4.0, 0.25, 'trim')
        t.cyl((0, 0, zw - 1.0), (0, 0, hw + 1.7), 0.5, 'struct', n=8)
        # skimmer arms
        for a in (0.0, math.pi):
            rk.beam((math.cos(a) * rf, math.sin(a) * rf, zw + 0.25), (math.cos(a) * (r - 1.2), math.sin(a) * (r - 1.2), zw + 0.25),
                    0.5, 0.4, 'struct_blue')
    # handrails round the launder walk
    ring = [(math.cos(2 * math.pi * k / 24) * (r + 1.3), math.sin(2 * math.pi * k / 24) * (r + 1.3)) for k in range(25)]
    t.rail(ring, hw - 0.1, 'rail_yellow', post=r * 0.6, t=0.07)
    # underflow pump house
    t.boxz(r + 6, -4, 0, 6, 5, 4, 'clad_warm', bevel=0.05, edges='t')
    pipe(t, [(r + 3, -4, 1.0), (r - 0.2, -4, 1.0)], 0.35, 'struct')
    return mdl
