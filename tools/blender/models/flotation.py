# Rougher flotation: two rows of seven tank cells (7.2 m diameter, about
# 300 m3 each) on a concrete plinth, with the froth, launders, the drives on
# their bridges, the walkway between the rows, feed and tailings boxes.
# Rows run along local X.

import math

from kit import Model, grime
from plant_parts import pipe

CELLS = 7
R = 3.6
PITCH = 8.6
ROW_Y = 5.6
Z0 = 1.2
ZT = 7.5

META = {
    'id': 'flotation',
    'title': 'Flotation: two rows of seven tank cells',
    'kind': 'plant',
    'reference': {'cells': 2 * CELLS, 'cell_diameter_m': 2 * R, 'cell_height_m': ZT - Z0, 'bank_m': [CELLS * PITCH + 10, 2 * (ROW_Y + R) + 4]},
    'anim': {'flotation': {'what': 'static; the froth uses material "froth"'}},
}


def build(lod):
    mdl = Model('Flotation', dirt=grime(ground=2.0, amount=0.4, dust=0.12))
    p = mdl.part('flotation', uv=True)
    length = CELLS * PITCH + 10
    p.boxz(0, 0, 0, length, 2 * (ROW_Y + R) + 4, Z0, 'concrete', bevel=0.1, edges='t', skip=())
    x0 = -(CELLS - 1) / 2 * PITCH
    for row in (-1, 1):
        y = row * ROW_Y
        for i in range(CELLS):
            x = x0 + i * PITCH
            from mathutils import Matrix
            m = Matrix.Translation((x, y, 0))
            # tank, rim, the launder lip round the top, the froth with the crowder cone
            p.lathe([(R, Z0), (R, ZT), (R + 0.25, ZT), (R + 0.25, ZT + 0.35), (R - 0.15, ZT + 0.35), (R - 0.15, ZT - 0.2)],
                    ['tank', 'struct', 'struct', 'struct', 'struct'], n=20, m=m, close=False)
            p.lathe([(R - 0.15, ZT - 0.2), (R * 0.55, ZT - 0.12), (1.1, ZT - 0.05), (0.9, ZT + 0.5), (0.0, ZT + 0.55)],
                    ['froth', 'froth', 'struct', 'struct'], n=20, m=m, close=False)
            for k in range(2):
                z = Z0 + (ZT - Z0) * (k + 1) / 3
                p.cyl((x, y, z), (x, y, z + 0.16), R + 0.06, 'struct', n=20, caps=(False, False))
            # bridge with the mechanism drive
            p.beam((x, y - R - 0.2, ZT + 0.6), (x, y + R + 0.2, ZT + 0.6), 0.5, 0.45, 'struct')
            p.boxz(x, y, ZT + 0.8, 1.3, 1.3, 0.8, 'motor', bevel=0.08)
            p.cyl((x, y, ZT + 1.6), (x, y, ZT + 2.7), 0.45, 'struct_blue', n=10)
            # connection box to the next cell
            if i < CELLS - 1:
                p.boxz(x + PITCH / 2, y, Z0, 1.2, 1.6, ZT - Z0 - 0.8, 'tank', bevel=0.05, edges='v')
        # concentrate launder along the outer side, falling to the end
        ly = row * (ROW_Y + R + 0.9)
        p.boxz(0, ly, ZT - 1.6, CELLS * PITCH, 0.9, 0.9, 'struct', skip=('top',))
        # feed box at -X, tailings box at +X
        p.boxz(x0 - PITCH / 2 - 0.6, y, Z0, 2.4, 2.6, ZT - Z0 + 0.6, 'tank', bevel=0.08, edges='vt')
        p.boxz(-x0 + PITCH / 2 + 0.6, y, Z0, 2.4, 2.6, ZT - Z0 - 1.0, 'tank', bevel=0.08, edges='vt')
    # walkway between the rows, its handrails and posts, stairs at both ends
    wz = ZT + 0.45
    p.boxz(0, 0, wz - 0.15, CELLS * PITCH + 4, 2.6, 0.15, 'grating')
    for s in (-1, 1):
        p.rail([(-(CELLS * PITCH) / 2 - 2, s * 1.3), ((CELLS * PITCH) / 2 + 2, s * 1.3)], wz, 'rail_yellow', post=2.2)
    for i in range(CELLS + 1):
        x = x0 - PITCH / 2 + i * PITCH
        p.beam((x, 0, Z0), (x, 0, wz - 0.15), 0.3, 0.3, 'struct', caps=False)
    for s in (-1, 1):
        ex = s * ((CELLS * PITCH) / 2 + 2)
        p.stair((ex + s * 7.0, -0.2, Z0), (ex, -0.2, wz), 1.0, 'rail_yellow')
    # feed and tailings pipes
    pipe(p, [(x0 - PITCH / 2 - 0.6, -ROW_Y, ZT + 0.5), (x0 - PITCH / 2 - 0.6, -ROW_Y, ZT + 1.6), (x0 - PITCH / 2 - 8, -ROW_Y, ZT + 1.6)], 0.35, 'struct', n=8)
    pipe(p, [(-x0 + PITCH / 2 + 0.6, ROW_Y, Z0 + 1.0), (-x0 + PITCH / 2 + 8, ROW_Y, Z0 + 1.0)], 0.4, 'struct', n=8)
    return mdl
