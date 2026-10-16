# Mobile floodlight tower (diesel light tower on a single-axle trailer, of
# the kind used in open pits): trailer with drawbar and outriggers, the
# generator canopy, a 9 m telescopic mast raised, four LED floods facing +Y.
# The lamp faces are their own node ("lamps") to switch at night.

from kit import Model, grime

MAST = 9.0

META = {
    'id': 'floodlight',
    'title': 'Mobile floodlight tower',
    'kind': 'plant',
    'lods': [0, 1],
    'reference': {'mast_m': MAST, 'trailer_m': [1.6, 4.2], 'floods': 4, 'faces': 'local +Y (scene) = glTF -Z'},
    'anim': {
        'tower': {'what': 'trailer, generator canopy, mast, lamp frame'},
        'lamps': {'what': 'the four lamp faces (material "lamp", emissive)', 'note': 'light at night; the light source sits here'},
    },
}


def build(lod):
    mdl = Model('Floodlight', dirt=grime(ground=1.2, amount=0.5, dust=0.25))
    t = mdl.part('tower')
    lm = mdl.part('lamps')
    Y = 'rail_yellow'
    heads = [(-0.62, MAST - 0.15), (0.62, MAST - 0.15), (-0.62, MAST + 0.45), (0.62, MAST + 0.45)]
    if lod == 0:
        t.boxz(0, -0.2, 0.45, 1.3, 3.6, 0.2, 'steel_dark')                              # chassis
        t.beam((-0.55, 1.5, 0.55), (0, 3.0, 0.55), 0.12, 0.12, 'steel_dark')            # drawbar
        t.beam((0.55, 1.5, 0.55), (0, 3.0, 0.55), 0.12, 0.12, 'steel_dark')
        t.cyl((0, 2.8, 0.15), (0, 2.8, 0.6), 0.05, 'steel', n=6)
        t.cyl((-0.9, -0.5, 0.36), (0.9, -0.5, 0.36), 0.06, 'steel_dark', n=6)
        for s in (-1, 1):
            t.cyl((s * 0.78, -0.5, 0.36), (s * 0.98, -0.5, 0.36), 0.36, 'rubber', n=12, capmat='rim')
            t.boxz(s * 0.88, -0.5, 0.75, 0.3, 0.9, 0.06, Y)                             # mudguard
        for sx in (-1, 1):
            for sy in (-1, 1):
                t.beam((sx * 0.6, sy * 1.4 - 0.2, 0.55), (sx * 1.5, sy * 1.9 - 0.2, 0.55), 0.1, 0.1, Y)
                t.cyl((sx * 1.5, sy * 1.9 - 0.2, 0.05), (sx * 1.5, sy * 1.9 - 0.2, 0.7), 0.05, 'steel', n=6)
                t.boxz(sx * 1.5, sy * 1.9 - 0.2, 0.0, 0.25, 0.25, 0.05, 'steel_dark')
        t.boxz(0, -0.3, 0.65, 1.4, 3.2, 1.35, Y, bevel=0.12, edges='vt')               # generator canopy
        for i in range(5):
            t.boxz(0.71, -1.4 + i * 0.22, 0.95, 0.03, 0.1, 0.7, 'paint_black')
        t.boxz(-0.71, -0.4, 0.8, 0.03, 1.2, 1.0, 'steel_dark')                          # control door
        t.cyl((0.4, -1.5, 2.0), (0.4, -1.5, 2.35), 0.05, 'steel_dark', n=6)            # exhaust
        # mast: three telescopic sections on a pivot post at the rear
        t.boxz(0, -1.75, 0.65, 0.4, 0.4, 1.6, 'steel_dark')
        for (z0, z1, w) in ((1.9, 4.6, 0.24), (4.5, 7.0, 0.19), (6.9, MAST - 0.2, 0.15)):
            t.beam((0, -1.75, z0), (0, -1.75, z1), w, w, 'steel', caps=True)
        t.boxz(0, -1.68, MAST - 0.35, 1.7, 0.12, 0.12, 'steel_dark')
        t.boxz(0, -1.68, MAST + 0.25, 1.7, 0.12, 0.12, 'steel_dark')
        for x, z in heads:
            t.boxz(x, -1.55, z - 0.22, 0.55, 0.18, 0.42, 'paint_black', bevel=0.03)
            lm.boxz(x, -1.45, z - 0.18, 0.47, 0.03, 0.34, 'lamp', skip=('bottom', 'top', '-x', '+x', '-y'))
        t.cyl((-0.2, -1.95, MAST + 0.6), (-0.2, -1.95, MAST + 0.75), 0.08, 'lamp_amber', n=6)
    else:
        t.boxz(0, -0.3, 0.3, 1.4, 3.6, 1.7, Y)
        t.beam((0, -1.75, 1.9), (0, -1.75, MAST + 0.4), 0.2, 0.2, 'steel')
        t.boxz(0, -1.6, MAST - 0.4, 1.8, 0.2, 1.1, 'paint_black')
        lm.boxz(0, -1.48, MAST - 0.35, 1.7, 0.03, 1.0, 'lamp', skip=('bottom', 'top', '-x', '+x', '-y'))
    return mdl
