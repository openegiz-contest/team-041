# The grinding hall: one SAG mill (12.2 m x 7.9 m EGL, 40 ft, gearless
# wrap-around motor) and two ball mills (8.5 m x 13.4 m, 28 ft, dual pinion
# drives) under a pitched roof with roof lights, with the overhead crane.
# The hall runs along local X (160 x 46 m, 30 m to the eaves, ridge +7 m);
# the mills' axes run across it (local Y). The roof is its own node, so the
# scene can hide it to show the mills; each mill shell is a node that turns
# about its axis.

import math

from kit import Model, grime
from plant_parts import building, roof_lights

L, W, H, RISE = 160.0, 46.0, 30.0, 7.0
SAG = {'x': -45.0, 'dia': 12.2, 'len': 7.9}
BALLS = [{'x': 15.0, 'dia': 8.5, 'len': 13.4}, {'x': 42.0, 'dia': 8.5, 'len': 13.4}]


def axis_z(dia):
    return dia / 2 + 3.4


META = {
    'id': 'mill_hall',
    'title': 'Grinding hall: SAG mill and two ball mills',
    'kind': 'plant',
    'reference': {'hall_m': [L, W, H + RISE], 'sag_mill_m': [SAG['dia'], SAG['len']], 'ball_mills_m': [8.5, 13.4],
                  'ball_mill_count': 2, 'mill_axes': 'across the hall (local Y)'},
    'anim': {
        'hall': {'what': 'walls, floor, columns, gable ends'},
        'roof': {'what': 'pitched roof with roof lights and ridge vent', 'note': 'hide (or fade) to show the mills'},
        'crane': {'what': 'overhead travelling crane', 'note': 'may travel along local X (scene) = glTF X'},
        'sag_mill': {'what': 'SAG mill: piers, bearings, motor, feed chute, trommel'},
        'sag_shell': {'what': 'SAG mill shell, child of sag_mill', 'pivot': 'on the mill axis', 'axis': 'y',
                      'note': 'turns about local Y (scene) = glTF -Z: about 10 rpm (1.0 rad/s)'},
        'ball_mill_1': {'what': 'ball mill 1: piers, bearings, girth gear guard, motors'},
        'ball_shell_1': {'what': 'ball mill 1 shell', 'pivot': 'on the mill axis', 'axis': 'y', 'note': 'about 13 rpm (1.4 rad/s)'},
        'ball_mill_2': {'what': 'ball mill 2'},
        'ball_shell_2': {'what': 'ball mill 2 shell', 'pivot': 'on the mill axis', 'axis': 'y'},
    },
}


def mill_static(p, x, dia, length, gearless):
    r = dia / 2
    zc = axis_z(dia)
    for s in (-1, 1):
        y = s * (length / 2 + 2.6)
        p.boxz(x, y, 0, r * 1.25, 3.4, zc - 1.6, 'concrete', bevel=0.15, edges='t')           # pier
        p.boxz(x, y, zc - 1.6, r * 0.9, 2.6, 1.0, 'motor', bevel=0.1)                          # bearing housing
        p.cyl((x, y - 1.4, zc), (x, y + 1.4, zc), r * 0.36, 'shell_green', n=16, up=(0, 0, 1))  # trunnion
    if gearless:
        # the ring motor round the shell, on its own base
        p.boxz(x, 0, 0, r * 2.4, length * 0.32, 2.2, 'concrete')
        p.lathe([(r + 1.5, -length * 0.15), (r + 1.5, length * 0.15)], 'motor', n=28,
                m=_axis(x, 0, zc), close=False)
        p.lathe([(r + 1.5, length * 0.15), (r + 0.25, length * 0.15)], 'motor', n=28, m=_axis(x, 0, zc), close=False)
        p.lathe([(r + 0.25, -length * 0.15), (r + 1.5, -length * 0.15)], 'motor', n=28, m=_axis(x, 0, zc), close=False)
        # feed chute and the discharge trommel
        y_feed = -(length / 2 + 4.2)
        p.beam((x, y_feed - 5.0, zc + 5.0), (x, y_feed + 1.0, zc), 2.2, 1.8, 'steel', up=(0, 0, 1))
        y_dis = length / 2 + 4.0
        p.cyl((x, y_dis, zc), (x, y_dis + 4.5, zc), r * 0.42, 'grating', n=14, up=(0, 0, 1))
        p.boxz(x, y_dis + 2.2, 0, 4.5, 5.5, zc - r * 0.42, 'steel_dark', bevel=0.1, edges='t')
    else:
        # girth gear guard and two pinion drives with motors
        p.lathe([(r + 0.9, -0.7), (r + 0.9, 0.7)], 'shell_green', n=24, m=_axis(x, length * 0.38, zc), close=False)
        for s in (-1, 1):
            p.boxz(x + s * (r + 2.4), length * 0.38, 0, 3.0, 2.8, zc - 0.6, 'concrete')
            p.boxz(x + s * (r + 2.4), length * 0.38, zc - 0.6, 2.0, 2.0, 1.6, 'motor', bevel=0.1)
            p.boxz(x + s * (r + 2.4), length * 0.38 + 3.6, 0, 3.0, 4.0, 2.4, 'concrete')
            p.cyl((x + s * (r + 2.4), length * 0.38 + 2.0, zc - 2.3), (x + s * (r + 2.4), length * 0.38 + 5.4, zc - 2.3), 1.2, 'motor', n=14, up=(0, 0, 1))
        y_feed = -(length / 2 + 4.0)
        p.beam((x, y_feed - 3.0, zc + 1.0), (x, y_feed + 1.2, zc), 1.0, 1.0, 'steel', up=(0, 0, 1))
        p.boxz(x, length / 2 + 4.5, 0, 4.0, 3.6, zc - 1.0, 'steel_dark', bevel=0.1, edges='t')   # discharge box


def mill_shell(p, x, dia, length):
    """The shell with its heads and trunnions, bolted flanges and manholes, so
    that its turning shows."""
    r = dia / 2
    zc = axis_z(dia)
    m = _axis(x, 0, zc)
    hl = length / 2
    prof = [(r * 0.36, -hl - 1.8), (r * 0.4, -hl - 1.2), (r * 0.98, -hl), (r, -hl + 0.2), (r, hl - 0.2),
            (r * 0.98, hl), (r * 0.4, hl + 1.2), (r * 0.36, hl + 1.8)]
    p.lathe(prof, ['shell_green', 'shell_green', 'shell_green', 'shell_green', 'shell_green', 'shell_green', 'shell_green'], n=28, m=m, close=False)
    for f in (-0.3, 0.3):
        p.lathe([(r + 0.12, f * length - 0.12), (r + 0.12, f * length + 0.12)], 'motor', n=28, m=m, close=False)
    for k in range(6):
        a = 2 * math.pi * k / 6 + 0.3
        for f in (-0.16, 0.16):
            y = f * length
            cz, cx = math.sin(a) * r, math.cos(a) * r
            p.box((x + cx * 1.01, y, zc + cz * 1.01), (0.9, 1.2, 0.9), 'motor')


def _axis(x, y, z):
    from mathutils import Matrix
    return Matrix.Translation((x, y, z)) @ Matrix.Rotation(-math.pi / 2, 4, 'X')   # local Z -> world +Y


def build(lod):
    mdl = Model('MillHall', dirt=grime(ground=2.5, amount=0.4, dust=0.12))
    hall = mdl.part('hall', uv=True)
    roof = mdl.part('roof', uv=True)
    crane = mdl.part('crane', uv=True)
    # floor, walls, columns, gables
    hall.boxz(0, 0, -0.3, L + 4, W + 4, 0.3, 'concrete_dk', skip=('bottom',))
    building(hall, 0, 0, 0, L, W, H, wall='clad_white', plinth=7.0, bands=((12.0, 14.5), (22.0, 24.0)),
             doors=(('-y', -50, 8, 9), ('-y', 30, 8, 9), ('-x', 0, 10, 12)), columns=12.0, pitch=0.0, parapet=False, top=False)
    for s in (-1, 1):
        hall.face([(s * L / 2, -W / 2, H), (s * L / 2, W / 2, H), (s * L / 2, 0, H + RISE)], 'clad_white', normal=(s, 0, 0))
        hall.boxz(s * L / 2, 0, H - 0.6, 0.6, W + 0.6, 0.6, 'trim')
    # the inner face of the walls, seen when the roof is hidden
    e = 0.06
    corners = [(-L / 2 + e, -W / 2 + e), (L / 2 - e, -W / 2 + e), (L / 2 - e, W / 2 - e), (-L / 2 + e, W / 2 - e)]
    for i in range(4):
        (ax, ay), (bx, by) = corners[i], corners[(i + 1) % 4]
        inward = (-(by - ay), bx - ax, 0)
        hall.face([(ax, ay, 0), (bx, by, 0), (bx, by, H), (ax, ay, H)], 'clad_white', normal=inward)
    # crane rails on brackets along both long walls
    for s in (-1, 1):
        hall.boxz(0, s * (W / 2 - 1.2), 21.5, L - 2, 0.8, 1.2, 'struct')
    # roof
    hx, hy = L / 2 + 1.2, W / 2 + 1.2
    roof.face([(-hx, -hy, H - 0.35), (hx, -hy, H - 0.35), (hx, 0, H + RISE), (-hx, 0, H + RISE)], 'roof', normal=(0, -1, 3))
    roof.face([(-hx, 0, H + RISE), (hx, 0, H + RISE), (hx, hy, H - 0.35), (-hx, hy, H - 0.35)], 'roof', normal=(0, 1, 3))
    for s in (-1, 1):
        roof.boxz(0, s * (hy - 0.4), H - 1.1, 2 * hx, 0.8, 0.75, 'trim')
    roof.boxz(0, 0, H + RISE - 0.4, L * 0.9, 2.8, 1.6, 'struct', bevel=0.2, edges='t')     # ridge vent
    roof_lights(roof, 0, 0, H, L, W, RISE)
    # overhead crane: two girders, end carriages, trolley
    cx = -10.0
    for dx in (-1.2, 1.2):
        crane.boxz(cx + dx, 0, 22.7, 1.0, W - 3.0, 2.0, 'rail_yellow', bevel=0.05, edges='v')
    for s in (-1, 1):
        crane.boxz(cx, s * (W / 2 - 1.2), 22.7, 5.0, 1.4, 1.2, 'rail_yellow')
    crane.boxz(cx, -6.0, 24.7, 3.8, 4.0, 1.6, 'rail_yellow', bevel=0.05)
    crane.cyl((cx, -6.0, 22.7), (cx, -6.0, 15.0), 0.06, 'steel_dark', n=4, caps=(False, False))
    crane.boxz(cx, -6.0, 14.4, 0.8, 0.5, 0.6, 'rail_yellow')
    # mills
    sag = mdl.part('sag_mill', uv=True)
    mill_static(sag, SAG['x'], SAG['dia'], SAG['len'], True)
    shell = mdl.part('sag_shell', pivot=(SAG['x'], 0, axis_z(SAG['dia'])), parent='sag_mill')
    mill_shell(shell, SAG['x'], SAG['dia'], SAG['len'])
    for k, b in enumerate(BALLS, 1):
        st = mdl.part('ball_mill_%d' % k, uv=True)
        mill_static(st, b['x'], b['dia'], b['len'], False)
        sh = mdl.part('ball_shell_%d' % k, pivot=(b['x'], 0, axis_z(b['dia'])), parent='ball_mill_%d' % k)
        mill_shell(sh, b['x'], b['dia'], b['len'])
    return mdl
