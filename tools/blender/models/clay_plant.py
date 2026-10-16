# The clay-ore plant: a smaller concentrator for the clayey ore beside the
# main plant. A main building (64 x 30 m, 20 m to the eaves) with the
# reagent and electrical annex; the desliming cyclone cluster (ten cyclones
# round a central distributor) on a steel tower at 16 m; a row of five
# flotation tank cells outside; slurry tanks; the feed conveyor gallery
# rising to the roof; the pipes between them.

import math

from mathutils import Matrix

from kit import Model, grime
from plant_parts import building, roof_lights, lattice, platform, pipe, tank

META = {
    'id': 'clay_plant',
    'title': 'Clay-ore plant: desliming cyclones and flotation',
    'kind': 'plant',
    'reference': {'building_m': [64, 30, 24], 'cyclones': 10, 'cyclone_platform_m': 16, 'flotation_cells': 5,
                  'cell_diameter_m': 5.0},
    'anim': {'clay_plant': {'what': 'static'}},
}


def cyclone(p, x, y, z, r=0.36):
    """A hydrocyclone standing at (x, y): head with its inlet, cylinder, cone
    down to the apex; z is the top of the head."""
    p.cyl((x, y, z), (x, y, z - 1.0), r, 'paint_white', n=10, caps=(True, False))
    p.cyl((x, y, z - 1.0), (x, y, z - 3.0), r, 'paint_white', r1=0.07, n=10, caps=(False, True))
    p.cyl((x, y, z + 0.0), (x, y, z + 0.45), 0.12, 'struct', n=6, caps=(False, True))


def build(lod):
    mdl = Model('ClayPlant', dirt=grime(ground=2.0, amount=0.4, dust=0.15))
    p = mdl.part('clay_plant', uv=True)
    p.boxz(4, 2, -0.3, 110, 64, 0.3, 'concrete_dk', skip=('bottom',))
    # main building and annex
    building(p, 0, 0, 0, 64, 30, 20, wall='clad_warm', roof='roof', plinth=2.5, bands=((6.0, 7.5), (13.5, 15.0)),
             doors=(('-y', -18, 6, 7), ('-y', 6, 8, 9)), columns=8.0, pitch=4.0)
    roof_lights(p, 0, 0, 20, 64, 30, 4.0, strips=(0.35,), length=0.8)
    building(p, -41, -4, 0, 18, 18, 10, wall='clad_white', plinth=1.0, bands=((4.5, 6.0),), doors=(('-y', 0, 4, 4.5),))
    p.boxz(-41, -4, 10.0, 3, 3, 1.2, 'struct')
    # desliming tower beside the building (+X): four columns, braces, platform at 16 m, stair
    tx, ty = 42.0, -2.0
    for sx in (-1, 1):
        for sy in (-1, 1):
            p.beam((tx + sx * 5, ty + sy * 5, 0), (tx + sx * 5, ty + sy * 5, 16.0), 0.5, 0.5, 'struct_blue', caps=False)
    for z in (5.0, 10.5):
        for (a, b) in (((-5, -5), (5, -5)), ((5, -5), (5, 5)), ((5, 5), (-5, 5)), ((-5, 5), (-5, -5))):
            p.beam((tx + a[0], ty + a[1], z), (tx + b[0], ty + b[1], z), 0.3, 0.4, 'struct_blue', caps=False)
            p.bar((tx + a[0], ty + a[1], z - 5.0 if z > 6 else 0.0), (tx + b[0], ty + b[1], z), 0.14, 'struct_blue')
    platform(p, tx, ty, 16.0, 11.5, 11.5, legs=False)
    p.boxz(tx, ty, 10.5, 11, 11, 0.12, 'grating')
    # the cyclone cluster: distributor, ten cyclones round it, overflow and underflow launders
    tank(p, tx, ty, 16.0, 1.1, 3.4, 'paint_white', n=14, top='cone')
    for k in range(10):
        a = 2 * math.pi * k / 10
        cx, cy = tx + math.cos(a) * 2.7, ty + math.sin(a) * 2.7
        cyclone(p, cx, cy, 19.6)
        p.cyl((tx + math.cos(a) * 1.1, ty + math.sin(a) * 1.1, 18.6), (cx - math.cos(a) * 0.36, cy - math.sin(a) * 0.36, 19.2), 0.14, 'struct', n=6, caps=(False, False))
    p.lathe([(3.5, 16.5), (3.5, 17.2), (2.0, 17.2), (2.0, 16.5), (3.5, 16.5)], 'struct', n=20, m=Matrix.Translation((tx, ty, 0)), close=False)
    p.lathe([(3.2, 20.3), (3.2, 20.8), (2.3, 20.8), (2.3, 20.3), (3.2, 20.3)], 'struct', n=20, m=Matrix.Translation((tx, ty, 0)), close=False)
    for k in range(4):
        z0 = k * 4.0
        a, b = (ty + 6.2, ty - 4.0) if k % 2 == 0 else (ty - 4.0, ty + 6.2)
        p.stair((tx + 6.4, a, z0), (tx + 6.4, b, z0 + 4.0), 1.0, 'rail_yellow', rails=(True, False))
    # pipes: feed from the building up to the distributor, overflow down to the flotation
    pipe(p, [(32.0, -4.0, 10.0), (tx - 1.0, -4.0, 10.0), (tx - 1.0, ty - 0.6, 16.4)], 0.35, 'struct')
    pipe(p, [(tx + 2.0, ty + 3.0, 16.6), (tx + 2.0, 21.0, 9.0), (20.0, 21.0, 9.0)], 0.4, 'struct')
    # flotation: five tank cells in a row outside the north wall, walkway, drives
    for i in range(5):
        x = -20.0 + i * 7.0
        m = Matrix.Translation((x, 22.0, 0))
        p.lathe([(2.5, 1.0), (2.5, 6.0), (2.7, 6.0), (2.7, 6.3), (2.35, 6.3), (2.35, 5.9)], ['tank', 'struct', 'struct', 'struct', 'struct'],
                n=16, m=m, close=False)
        p.lathe([(2.35, 5.9), (1.0, 6.0), (0.8, 6.4), (0.0, 6.45)], ['froth', 'struct', 'struct'], n=16, m=m, close=False)
        p.beam((x, 22.0 - 2.7, 6.9), (x, 22.0 + 2.7, 6.9), 0.4, 0.35, 'struct')
        p.boxz(x, 22.0, 7.05, 1.0, 1.0, 0.6, 'motor')
        p.cyl((x, 22.0, 7.65), (x, 22.0, 8.5), 0.32, 'struct_blue', n=8)
    p.boxz(-6.0, 22.0, 0, 38, 7.5, 1.0, 'concrete', skip=('bottom',))
    p.boxz(-6.0, 25.6, 6.6, 34, 1.4, 0.12, 'grating')
    p.rail([(-23.0, 26.3), (11.0, 26.3)], 6.7, 'rail_yellow', post=2.4)
    p.stair((14.5, 25.6, 1.0), (11.0, 25.6, 6.7), 1.0, 'rail_yellow')
    # slurry and process water tanks at the west end
    tank(p, -36.0, 20.0, 0, 4.5, 9.0, 'tank', n=20, top='cone', rings=2)
    tank(p, -47.0, 20.0, 0, 3.5, 7.0, 'tank', n=18, top='flat', rings=1)
    # feed conveyor gallery rising from the south to the roof
    a, b = (8.0, -62.0, 2.0), (8.0, -15.0, 20.0)
    lattice(p, a, b, 3.4, 2.6, 'struct', bay=4.0)
    d = (b[1] - a[1], b[2] - a[2])
    for k in range(3):
        f = (k + 1) / 4
        y, z = a[1] + d[0] * f, a[2] + d[1] * f
        for s in (-1, 1):
            p.beam((8.0 + s * 2.0, y, 0), (8.0 + s * 1.6, y, z - 1.4), 0.5, 0.5, 'struct', caps=False)
    p.beam(a, b, 3.0, 0.15, 'roof', up=(0, 0, 1))
    p.beam((a[0], a[1], a[2] + 1.35), (b[0], b[1], b[2] + 1.35), 3.6, 0.12, 'roof', up=(0, 0, 1))
    return mdl
