# Caterpillar 785C mining truck (136 t payload), CAT yellow.
# Spec-sheet dimensions: overall length 11.02 m, operating width 6.64 m,
# height to the top of the canopy 5.77 m, wheelbase 5.18 m, tyres 33.00R51
# (2.94 m over the tread), body raise angle about 45 degrees.

import math

from mathutils import Vector

from kit import Model, grime
from machine_parts import tyre, tyre_low, ram, axis_rotation

FA = 3.15            # front axle (y)
RA = FA - 5.18       # rear axle
R = 1.47             # tyre radius
TW = 0.86            # tyre width
RIM = 0.66           # 51 in rim
HINGE = (0.0, -4.62, 2.02)
FRONT_X = 2.18
DUAL_IN, DUAL_OUT = 1.60, 2.54
Y_TAIL = -5.42
HOIST_LOW = [(s * 1.28, 2.0, 1.15) for s in (-1, 1)]
HOIST_HIGH = [(s * 1.28, -0.3, 2.18) for s in (-1, 1)]
FW = 1.45            # front wall of the body (behind the front tyres)

META = {
    'id': 'cat785c',
    'title': 'Caterpillar 785C mining truck',
    'kind': 'machine',
    'reference': {
        'payload_t': 136, 'overall_length_m': 11.02, 'operating_width_m': 6.64, 'canopy_height_m': 5.77,
        'wheelbase_m': 5.18, 'tyres': '33.00R51', 'body_raise_deg': 45, 'colour': 'CAT yellow',
    },
    'anim': {
        'body': {'what': 'dump body', 'pivot': 'rear hinge pin', 'axis': 'x', 'range_rad': [0, 0.79],
                 'note': 'positive rotation about local X tips the body (front up); 0.79 rad = 45 deg fully raised'},
        'load': {'what': 'heaped load, child of body', 'note': 'hide when empty or tipped'},
        'wheel_FL': {'what': 'front left wheel', 'pivot': 'wheel centre', 'axis': 'x', 'note': 'roll about X; steer about Z (front only)'},
        'wheel_FR': {'what': 'front right wheel', 'pivot': 'wheel centre', 'axis': 'x'},
        'wheel_RL': {'what': 'rear left dual wheels', 'pivot': 'axle centre of the pair', 'axis': 'x'},
        'wheel_RR': {'what': 'rear right dual wheels', 'pivot': 'axle centre of the pair', 'axis': 'x'},
        'hoist_L': {'what': 'hoist cylinder (left)', 'pivot': 'lower pin on the frame', 'aim': 'pin_hoist_L',
                    'note': 'to follow the body: lookAt(world position of pin_hoist_L), then scale.z = distance / rest_length_m'},
        'hoist_R': {'what': 'hoist cylinder (right)', 'pivot': 'lower pin on the frame', 'aim': 'pin_hoist_R'},
    },
}

SIDE = [(FW, 2.30), (-2.85, 2.30), (-5.25, 3.08), (Y_TAIL, 3.50), (-4.95, 4.80), (-2.0, 5.02), (FW, 5.12)]


def build(lod):
    mdl = Model('CAT785C', dirt=grime(ground=2.6, amount=0.6, dust=0.22))
    frame = mdl.part('frame')
    body = mdl.part('body', pivot=HINGE)
    load = mdl.part('load', pivot=HINGE, parent='body', smooth_deg=0)
    wheels = {
        'wheel_FL': (-FRONT_X, FA), 'wheel_FR': (FRONT_X, FA),
        'wheel_RL': (-(DUAL_IN + DUAL_OUT) / 2, RA), 'wheel_RR': ((DUAL_IN + DUAL_OUT) / 2, RA),
    }
    wp = {k: mdl.part(k, pivot=(x, y, R)) for k, (x, y) in wheels.items()}
    if lod == 0:
        build_lod0(mdl, frame, body, load, wp)
    else:
        build_lod1(frame, body, load, wp)
    return mdl


def build_lod0(mdl, f, b, load, wp):
    Y = 'cat_yellow'
    YD = 'cat_yellow_dk'
    # -- wheels --
    tyre(wp['wheel_FL'], (-FRONT_X, FA, R), R, TW, RIM, rim_side=-1)
    tyre(wp['wheel_FR'], (FRONT_X, FA, R), R, TW, RIM, rim_side=1)
    for s, key in ((-1, 'wheel_RL'), (1, 'wheel_RR')):
        tyre(wp[key], (s * DUAL_OUT, RA, R), R, TW, RIM, rim_side=s)
        tyre(wp[key], (s * DUAL_IN, RA, R), R, TW, RIM, rim_side=0)

    # -- frame: rails, cross members, axle housing, suspension --
    for s in (-1, 1):
        f.boxz(s * 0.78, -0.2, 0.98, 0.42, 9.6, 0.95, 'steel_dark', bevel=0.04, edges='v')
    for y in (4.3, 0.75, -4.55):
        f.boxz(0, y, 1.12, 1.98, 0.42, 0.72, 'steel_dark')
    f.cyl((-1.25, RA, R), (1.25, RA, R), 0.5, 'steel_dark', n=12)
    f.boxz(0, RA, 0.78, 1.5, 1.35, 1.3, 'steel_dark', bevel=0.15)
    for s in (-1, 1):
        f.cyl((s * 1.2, RA, R), (s * 1.42, RA, R), 0.62, 'steel_dark', n=12)       # final drive housing
        f.cyl((s * 1.62, FA, 1.25), (s * 1.62, FA, 3.25), 0.33, 'steel_dark', n=10)   # front strut barrel
        f.cyl((s * 1.62, FA, 0.95), (s * 1.62, FA, 1.35), 0.22, 'chrome', n=8)
        f.cyl((s * 1.35, FA, R), (s * 1.72, FA, R), 0.42, 'steel_dark', n=10)       # spindle
    f.beam((-1.55, FA + 0.55, 1.2), (1.55, FA + 0.55, 1.2), 0.16, 0.16, 'steel')      # tie rod
    # -- power module under the deck, radiator, bumper platform --
    f.boxz(0, 3.55, 1.55, 2.5, 2.9, 1.8, Y, bevel=0.14, edges='vt')
    f.boxz(0, 5.0, 1.62, 2.7, 0.14, 1.78, 'paint_black')
    for i in range(6):
        f.boxz(0, 5.09, 1.78 + i * 0.27, 2.55, 0.06, 0.07, 'steel')
    f.boxz(0, 5.03, 3.38, 2.8, 0.18, 0.18, Y)                                        # grille header
    f.boxz(0, 5.22, 0.98, 5.3, 0.72, 0.78, YD, bevel=0.06, edges='vt')                # bumper / platform
    for x in (-1.95, -1.5, 1.5, 1.95):
        f.boxz(x, 5.59, 1.24, 0.3, 0.04, 0.24, 'lamp')
    f.boxz(0, 5.59, 1.05, 0.9, 0.03, 0.42, 'paint_black')                            # tow point plate
    # the ladder at the left end of the bumper and the diagonal stair to the deck
    for s in (-1, 1):
        f.bar((-2.55 + s * 0.28, 5.42, 0.38), (-2.55 + s * 0.28, 5.42, 1.76), 0.06, YD)
    for z in (0.5, 0.85, 1.2):
        f.bar((-2.83, 5.42, z), (-2.27, 5.42, z), 0.05, 'steel')
    f.stair((-2.25, 5.3, 1.76), (-0.35, 5.3, 3.56), 0.62, YD, rails=(True, False))
    # -- the deck over the front wheels, its braces, rails --
    f.boxz(0, 4.1, 3.34, 6.42, 2.72, 0.2, Y, bevel=0.05, edges='v')
    for s in (-1, 1):
        f.beam((s * 1.3, 4.9, 2.0), (s * 2.9, 4.9, 3.34), 0.2, 0.25, YD)
        f.beam((s * 1.3, 3.0, 2.0), (s * 2.9, 3.0, 3.34), 0.2, 0.25, YD)
        f.boxz(s * 2.18, 4.1, 2.9, 1.45, 2.72, 0.44, Y, bevel=0.1, edges='t')         # fender skirt
    f.rail([(-3.16, 2.85), (-3.16, 5.42), (-0.55, 5.42)], 3.54, YD)
    f.rail([(0.25, 5.42), (3.16, 5.42), (3.16, 2.85)], 3.54, YD)
    # -- the cab on the left of the deck, mirrors, beacon --
    f.cab(-2.08, 4.0, 3.54, 2.0, 2.25, 1.88, Y, glass_from=0.36, front=1)
    f.boxz(-2.08, 2.84, 3.54, 1.6, 0.12, 1.2, YD)                                     # rear wall equipment box
    f.bar((-3.08, 5.0, 4.9), (-3.55, 5.35, 5.05), 0.05, 'paint_black')
    f.boxz(-3.58, 5.38, 4.62, 0.1, 0.32, 0.52, 'paint_black')
    f.bar((3.08, 5.3, 4.2), (3.5, 5.55, 4.5), 0.05, 'paint_black')
    f.boxz(3.53, 5.58, 4.25, 0.1, 0.3, 0.48, 'paint_black')
    # air cleaners and the electrical cabinet on the right of the deck
    for x in (1.35, 2.35):
        f.cyl((x, 4.75, 3.54), (x, 4.75, 4.42), 0.36, Y, n=12, capmat=YD)
        f.cyl((x, 4.75, 4.42), (x, 4.75, 4.7), 0.2, 'paint_black', n=8)
    f.boxz(1.85, 3.45, 3.54, 1.6, 0.8, 1.05, Y, bevel=0.05, edges='vt')
    # -- tanks between the axles, the rear --
    f.boxz(-2.05, 0.55, 1.0, 1.2, 1.95, 1.45, Y, bevel=0.2, edges='all')               # fuel tank
    f.boxz(2.0, 0.55, 1.1, 1.1, 1.8, 1.35, Y, bevel=0.2, edges='all')                  # hydraulic tank
    f.cyl((-2.05, 1.1, 2.45), (-2.05, 1.1, 2.6), 0.13, 'steel_dark', n=8)
    for s in (-1, 1):
        f.boxz(s * 0.78, -4.95, 1.25, 0.32, 0.08, 0.22, 'lamp_red')
    # hoist cylinders: frame pin to body pin, aimed and stretched by the scene
    for k, s in enumerate((-1, 1)):
        side = 'L' if s < 0 else 'R'
        lo, hi = HOIST_LOW[k], HOIST_HIGH[k]
        pr = mdl.part('hoist_' + side, pivot=lo, rot=axis_rotation(lo, hi))
        ram(pr, lo, hi, 0.21, YD, frac=0.6)
        mdl.pin('pin_hoist_' + side, hi, parent='body')

    # -- the dump body --
    W = 3.32
    for s in (-1, 1):
        b.prism(SIDE, 'x', s * (W - 0.12), s * W, Y)
        # worn liner inside each side wall
        liner = [(FW - 0.15, 2.62), (-2.85, 2.62), (-5.15, 3.38), (-4.85, 4.75), (-2.0, 4.98), (FW - 0.15, 5.06)]
        b.face([(s * (W - 0.125), y, z) for y, z in liner], 'steel_worn', normal=(-s, 0, 0))
        # top rail, lower rail, vertical ribs and the rib along the tail
        b.loft([((s * (W + 0.03), y, z + 0.02), 0.3, 0.28) for y, z in ((FW, 5.12), (-2.0, 5.02), (-4.95, 4.8))], Y,
               up=(0, 0, 1), bevel=0.04)
        b.loft([((s * (W + 0.05), FW, 2.42), 0.24, 0.26), ((s * (W + 0.05), -2.85, 2.42), 0.24, 0.26),
                ((s * (W + 0.05), -5.2, 3.2), 0.24, 0.26)], YD)
        for y in (1.0, -0.4, -1.8, -3.0):
            top = 5.12 - (FW - y) * (0.1 / (FW + 2.0)) if y > -2.0 else 5.0
            b.beam((s * (W + 0.07), y, 2.5), (s * (W + 0.07), y, top - 0.12), 0.16, 0.3, YD, up=(0, 1, 0))
        b.beam((s * (W + 0.07), -3.9, 2.75), (s * (W + 0.07), -3.9, 4.72), 0.16, 0.3, YD, up=(0, 1, 0))
        # hinge brackets at the rear
        b.boxz(s * 1.0, HINGE[1], 1.7, 0.45, 0.75, 0.75, 'steel_dark')
        b.boxz(s * 1.28, -0.55, 1.95, 0.4, 0.5, 0.35, 'steel_dark')       # hoist pin bracket
    # floor, rising to the tail
    floor = [(FW, 2.30), (-2.85, 2.30), (-5.25, 3.08), (-5.25, 3.36), (-2.85, 2.6), (FW, 2.6)]
    b.prism(floor, 'x', -(W - 0.12), W - 0.12, YD)
    b.face([(-(W - 0.13), FW - 0.15, 2.61), (W - 0.13, FW - 0.15, 2.61), (W - 0.13, -2.85, 2.61), (-(W - 0.13), -2.85, 2.61)], 'steel_worn', normal=(0, 0, 1))
    b.face([(-(W - 0.13), -2.85, 2.61), (W - 0.13, -2.85, 2.61), (W - 0.13, -5.15, 3.37), (-(W - 0.13), -5.15, 3.37)], 'steel_worn', normal=(0, 0.3, 1))
    # longitudinal beams under the floor
    for s in (-1, 1):
        b.boxz(s * 0.78, -1.6, 2.02, 0.4, 5.6, 0.3, 'steel_dark')
    # front wall and the canopy over the cab
    b.boxz(0, FW - 0.08, 2.3, 2 * W, 0.18, 3.3, Y)
    b.face([(-(W - 0.13), FW - 0.18, 2.62), (W - 0.13, FW - 0.18, 2.62), (W - 0.13, FW - 0.18, 5.1), (-(W - 0.13), FW - 0.18, 5.1)], 'steel_worn', normal=(0, -1, 0))
    b.boxz(0, 3.45, 5.55, 2 * W, 4.2, 0.22, Y, bevel=0.06, edges='t')
    b.boxz(0, 5.6, 5.22, 2 * W, 0.12, 0.5, YD)                                        # canopy front lip
    for s in (-1, 1):
        b.boxz(s * (W - 0.06), 3.45, 5.2, 0.12, 4.0, 0.36, YD)                         # side lips
    for x in (-2.2, -0.75, 0.75, 2.2):
        b.boxz(x, 3.45, 5.24, 0.18, 4.0, 0.32, YD)                                     # ribs under the canopy
    for x in (-2.6, 2.6):
        b.beam((x, FW - 0.1, 3.6), (x, 3.2, 5.25), 0.22, 0.22, YD)                      # canopy braces
    b.boxz(0, Y_TAIL + 0.12, 3.2, 2 * W - 0.4, 0.22, 0.45, YD, bevel=0.05)            # tail lip

    # -- the load: a heap of broken rock (flat-shaded facets) --
    nx, ny = 9, 11
    y0, y1 = -5.0, FW - 0.25

    def h(x, y):
        fx = max(0.0, 1 - (x / 3.15) ** 2) ** 0.6
        fy = math.sin(math.pi * max(0.0, min(1.0, (y - y0) / (y1 - y0)))) ** 0.55
        lump = 0.2 * math.sin(x * 3.1 + y * 1.7) * math.cos(y * 2.3 - x) + 0.12 * math.sin(x * 7.3 - y * 5.1)
        return 4.6 + 1.5 * fx * fy + lump * (0.3 + fx)

    def jitter(x, y):
        return (x + 0.12 * math.sin(y * 9.1 + x * 3.3), y + 0.12 * math.sin(x * 8.7 - y * 2.9))
    for i in range(nx):
        for j in range(ny):
            pts = []
            for (u, v) in ((i, j), (i + 1, j), (i + 1, j + 1), (i, j + 1)):
                x = -3.15 + 6.3 * u / nx
                y = y0 + (y1 - y0) * v / ny
                if 0 < u < nx and 0 < v < ny:
                    x, y = jitter(x, y)
                pts.append((x, y, h(x, y)))
            load.face(pts[:3], 'rock', normal=(0, 0, 1))
            load.face([pts[0], pts[2], pts[3]], 'rock', normal=(0, 0, 1))


def build_lod1(f, b, load, wp):
    Y = 'cat_yellow'
    tyre_low(wp['wheel_FL'], (-FRONT_X, FA, R), R, TW)
    tyre_low(wp['wheel_FR'], (FRONT_X, FA, R), R, TW)
    for s, key in ((-1, 'wheel_RL'), (1, 'wheel_RR')):
        tyre_low(wp[key], (s * (DUAL_IN + DUAL_OUT) / 2, RA, R), R, DUAL_OUT - DUAL_IN + TW)
    f.boxz(0, -0.2, 0.98, 1.98, 9.6, 0.95, 'steel_dark')
    f.boxz(0, 3.75, 1.55, 2.5, 3.0, 1.8, Y)
    f.boxz(0, 5.0, 1.62, 2.7, 0.14, 1.78, 'paint_black')
    f.boxz(0, 5.22, 0.98, 5.3, 0.72, 0.78, 'cat_yellow_dk')
    f.boxz(0, 4.1, 3.34, 6.42, 2.72, 0.2, Y)
    f.boxz(-2.08, 4.0, 3.54, 2.0, 2.25, 0.7, Y)
    f.boxz(-2.08, 4.0, 4.24, 2.0, 2.25, 1.18, 'glass')
    f.boxz(-2.05, 0.55, 1.0, 1.2, 1.95, 1.45, Y)
    f.boxz(2.0, 0.55, 1.1, 1.1, 1.8, 1.35, Y)
    W = 3.32
    b.prism(SIDE, 'x', -W, W, Y)
    b.boxz(0, 3.45, 5.55, 2 * W, 4.2, 0.22, Y)
    load.face([(-3.0, -5.0, 4.75), (3.0, -5.0, 4.75), (1.9, -1.6, 6.05), (-1.9, -1.6, 6.05)], 'rock', normal=(0, 0, 1))
    load.face([(-1.9, -1.6, 6.05), (1.9, -1.6, 6.05), (3.0, FW - 0.3, 4.85), (-3.0, FW - 0.3, 4.85)], 'rock', normal=(0, 0, 1))
