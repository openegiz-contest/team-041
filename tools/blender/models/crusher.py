# Primary gyratory crusher station with its dump pocket: two truck bays on the
# road side (local -X), a hopper falling to the crusher mouth, a rock breaker,
# the crusher building with the crane bay over the crusher, the control room,
# the dust collector, the E-house and the discharge gallery (+X) towards the
# overland conveyor. Truck level is z = 0. Sizes after typical 60x113 in
# gyratory stations (building about 18 x 26 m, 34 m to the crane bay roof).

from kit import Model, grime
from plant_parts import building, platform, lattice, tank

META = {
    'id': 'crusher',
    'title': 'Primary gyratory crusher with dump pocket',
    'kind': 'plant',
    'reference': {'dump_bays': 2, 'building_m': [18, 26, 34], 'hopper_m': [8, 20], 'truck_level_z': 0,
                  'road_side': 'local -X (scene); trucks back up towards +X'},
    'anim': {'crusher': {'what': 'static: dump pocket, crusher building, services'}},
}


def build(lod):
    mdl = Model('Crusher', dirt=grime(ground=3.0, amount=0.45, dust=0.15))
    p = mdl.part('crusher', uv=True)
    # -- the dump pocket --
    p.boxz(-19, 0, -0.3, 22, 33, 0.3, 'concrete_dk', skip=('bottom',))                  # approach apron
    for s in (-1, 1):
        p.boxz(-4, s * 13.25, -0.3, 8, 6.5, 0.3, 'concrete_dk', skip=('bottom',))
        p.boxz(-15, s * 16.9, 0, 30, 0.8, 1.3, 'concrete', bevel=0.15, edges='t')         # side barriers
        p.boxz(-8.7, s * 5.4, 0, 0.9, 8.6, 1.1, 'rail_yellow', bevel=0.1, edges='t')       # wheel stops
        for k in range(4):
            p.boxz(-9.17, s * (2.0 + k * 2.2), 0.2, 0.04, 0.9, 0.7, 'paint_black')
    p.boxz(-4, 0, 0, 8, 1.2, 3.5, 'concrete', bevel=0.1, edges='t')                     # splitter wall
    # the hopper: walls sloping down to the crusher mouth
    rim = [(-8, -10), (0, -10), (0, 10), (-8, 10)]
    mouth = [(-5.2, -3.2), (-2.0, -3.2), (-2.0, 3.2), (-5.2, 3.2)]
    for i in range(4):
        a, b = rim[i], rim[(i + 1) % 4]
        c, d = mouth[(i + 1) % 4], mouth[i]
        p.face([(a[0], a[1], 0), (b[0], b[1], 0), (c[0], c[1], -7), (d[0], d[1], -7)], 'steel_worn', normal=(0, 0, 1))
    p.face([(m[0], m[1], -7) for m in mouth], 'paint_black', normal=(0, 0, 1))
    p.boxz(-4, 0, -0.25, 8.6, 20.6, 0.25, 'steel', skip=('bottom', 'top'))             # rim angle
    # the hood over the pocket on columns
    p.prism([(-9.5, 13.5), (1, 15.6), (1, 16.2), (-9.5, 14.1)], 'y', -12.5, 12.5, 'roof')
    for y in (-12, 0.0, 12):
        p.beam((-9.2, y, 0), (-9.2, y, 13.6), 0.5, 0.5, 'struct', caps=False)
        p.beam((-9.2, y, 13.0), (1.0, y, 15.0), 0.35, 0.6, 'struct', caps=False)
    # rock breaker on its pedestal
    p.boxz(-3.5, 12.0, 0, 2.6, 2.6, 3.2, 'concrete', bevel=0.1, edges='t')
    p.cyl((-3.5, 12.0, 3.2), (-3.5, 12.0, 3.9), 1.0, 'rail_yellow', n=12)
    p.beam((-3.5, 12.0, 3.8), (-5.0, 6.5, 8.0), 0.8, 0.7, 'rail_yellow')
    p.beam((-5.0, 6.5, 8.0), (-4.6, 3.2, 3.0), 0.6, 0.55, 'rail_yellow')
    p.cyl((-4.6, 3.2, 3.0), (-4.5, 3.0, 1.2), 0.3, 'steel_dark', n=8)
    p.cyl((-3.5, 11.0, 4.2), (-4.5, 7.6, 7.0), 0.18, 'chrome', n=6)
    # -- the crusher building --
    p.boxz(9, 0, -0.3, 18, 26, 8.3, 'concrete', skip=('bottom',))
    building(p, 9, 0, 8, 16.5, 24, 19, wall='clad_white', bands=((3.5, 5.0), (12.0, 13.2)), plinth=0, columns=6.0)
    building(p, 9, 0, 27, 10, 15, 7, wall='clad_white', plinth=0, bands=((3.0, 4.0),))
    p.boxz(9, 0, 34, 3, 3, 1.2, 'struct')                                                # roof vent
    for x in (5, 9, 13):
        p.boxz(x, -12.15, 0.05, 2.8, 0.2, 4.0, 'steel', skip=('bottom',))                 # doors at the crusher floor
    # control room for the breaker operator, on columns
    p.boxz(-7.5, -14.8, 5.0, 4.5, 3.4, 1.2, 'clad_warm')
    p.boxz(-7.5, -14.8, 6.2, 4.5, 3.4, 1.6, 'window')
    p.boxz(-7.5, -14.8, 7.8, 4.9, 3.8, 0.35, 'trim')
    for x in (-9.3, -5.7):
        for y in (-16.1, -13.5):
            p.beam((x, y, 0), (x, y, 5.0), 0.3, 0.3, 'struct', caps=False)
    p.stair((-12.5, -15.4, 0.0), (-9.8, -15.4, 5.0), 1.0, 'rail_yellow')
    # stair tower on the north side of the building
    for k in range(4):
        z0 = k * 6.5
        a = (3.0 if k % 2 == 0 else 15.0)
        b = (15.0 if k % 2 == 0 else 3.0)
        p.stair((a, 13.9, z0), (b, 13.9, z0 + 6.5), 1.1, 'rail_yellow', rails=(True, False))
        p.boxz(b + (1.0 if k % 2 == 0 else -1.0), 13.9, z0 + 6.35, 2.2, 1.4, 0.15, 'grating')
    for x in (2.0, 16.0):
        p.beam((x, 14.5, 0), (x, 14.5, 26.0), 0.3, 0.3, 'struct', caps=False)
    # dust collector with its cyclones and stack
    p.boxz(22.5, -8.0, 4.5, 5, 4, 4.5, 'clad_blue')
    for y in (-9.0, -7.0):
        p.cyl((22.5, y, 4.5), (22.5, y, 1.5), 0.9, 'tank', r1=0.2, n=12, caps=(True, False))
    for x, y in ((20.4, -9.7), (24.6, -9.7), (20.4, -6.3), (24.6, -6.3)):
        p.beam((x, y, 0), (x, y, 4.5), 0.25, 0.25, 'struct', caps=False)
    tank(p, 25.5, -5.0, 0, 0.6, 14.0, 'struct', n=10)
    p.cyl((17.8, -7.0, 18.0), (22.5, -7.5, 9.0), 0.45, 'tank', n=8, caps=(False, False))   # duct from the building
    # E-house on short legs, transformer beside it
    p.boxz(8, 18.5, 0.8, 12, 4.5, 3.6, 'clad_warm', bevel=0.05, edges='t')
    for x in (2.5, 13.5):
        p.boxz(x, 18.5, 0, 0.4, 4.4, 0.8, 'struct')
    p.boxz(17, 18.5, 0, 3.2, 2.6, 3.0, 'steel', bevel=0.1, edges='t')
    for i in range(4):
        p.boxz(15.2, 17.6 + i * 0.6, 0.4, 0.4, 0.08, 2.2, 'steel_dark')
    # discharge gallery to the overland conveyor
    p.boxz(25, 0, 0, 14, 4.4, 3.4, 'clad_white', skip=('bottom',))
    p.boxz(25, 0, 3.4, 14.4, 4.8, 0.3, 'trim')
    p.boxz(32.2, 0, 0, 0.6, 5.4, 4.2, 'concrete')
    return mdl
