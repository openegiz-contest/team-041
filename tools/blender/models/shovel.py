# Hitachi EX3600-6 hydraulic mining excavator, loading-shovel front (21 m3
# bottom-dump bucket), Hitachi orange.
# Spec dimensions: undercarriage length 8.70 m, track gauge 5.50 m, width over
# tracks 6.77 m (1.27 m shoes), ground clearance 0.90 m, tail swing radius
# 6.78 m, transport height (cab roof) 7.83 m, operating weight about 360 t.

import math

from kit import Model, grime, V
from machine_parts import crawler, crawler_low, ram, axis_rotation

SLEW = (0.0, 0.0, 2.6)
B0 = (0.0, 1.9, 3.9)        # boom foot pin
B1 = (0.0, 7.2, 8.6)        # boom point = stick pin
S1 = (0.0, 8.95, 3.9)       # stick end = bucket pin
T_STICK = (0.0, 6.79, 9.51) # top of the stick (arm cylinder pin)
GAUGE = 5.5
SHOE = 1.27
TRACK_L = 8.7
TRACK_H = 2.2
TAIL = 6.78

RAMS = {
    # name: (parent, pin parent, from, to, radius)
    'boom_cyl_L': ('upper', 'boom', (-1.25, 2.7, 3.4), (-1.05, 4.3, 6.3), 0.27),
    'boom_cyl_R': ('upper', 'boom', (1.25, 2.7, 3.4), (1.05, 4.3, 6.3), 0.27),
    'arm_cyl_L': ('boom', 'stick', (-0.72, 3.0, 6.3), (-0.72, 6.79, 9.51), 0.24),
    'arm_cyl_R': ('boom', 'stick', (0.72, 3.0, 6.3), (0.72, 6.79, 9.51), 0.24),
    'bucket_cyl_L': ('boom', 'bucket', (-1.18, 6.1, 7.4), (-1.18, 9.6, 4.85), 0.22),
    'bucket_cyl_R': ('boom', 'bucket', (1.18, 6.1, 7.4), (1.18, 9.6, 4.85), 0.22),
}

META = {
    'id': 'ex3600',
    'title': 'Hitachi EX3600-6 loading shovel',
    'kind': 'machine',
    'reference': {
        'operating_weight_t': 360, 'bucket_m3': 21, 'undercarriage_length_m': 8.70, 'track_gauge_m': 5.50,
        'width_over_tracks_m': 6.77, 'shoe_width_m': 1.27, 'tail_swing_radius_m': 6.78, 'transport_height_m': 7.83,
        'colour': 'Hitachi orange',
    },
    'anim': {
        'base': {'what': 'undercarriage: crawlers, car body, swing circle'},
        'upper': {'what': 'upper structure', 'pivot': 'slewing centre on the swing circle', 'axis': 'z',
                  'note': 'slew: rotate about local Z (scene) = glTF Y; positive turns left (counter-clockwise seen from above)'},
        'boom': {'what': 'boom, child of upper', 'pivot': 'boom foot pin', 'axis': 'x', 'range_rad': [-0.35, 0.35],
                 'note': 'positive rotation about X raises the boom'},
        'stick': {'what': 'stick (arm), child of boom', 'pivot': 'boom point pin', 'axis': 'x', 'range_rad': [-0.5, 0.9],
                  'note': 'positive rotation about X crowds the stick out and up'},
        'bucket': {'what': 'bottom-dump bucket, child of stick', 'pivot': 'stick end pin', 'axis': 'x', 'range_rad': [-0.8, 0.6],
                   'note': 'positive rotation about X curls the bucket (lip up)'},
    },
}
for _name, (_parent, _pinp, _a, _b, _r) in RAMS.items():
    META['anim'][_name] = {'what': 'hydraulic cylinder, child of ' + _parent, 'pivot': 'pin on ' + _parent, 'aim': 'pin_' + _name}


def build(lod):
    mdl = Model('EX3600', dirt=grime(ground=2.4, amount=0.65, dust=0.25))
    base = mdl.part('base')
    upper = mdl.part('upper', pivot=SLEW)
    boom = mdl.part('boom', pivot=B0, parent='upper')
    stick = mdl.part('stick', pivot=B1, parent='boom')
    bucket = mdl.part('bucket', pivot=S1, parent='stick')
    if lod == 0:
        lod0(mdl, base, upper, boom, stick, bucket)
    else:
        lod1(base, upper, boom, stick, bucket)
    return mdl


def tail(r, n=8):
    a0 = math.acos(3.45 / r)
    out = []
    for i in range(n + 1):
        a = -(math.pi - a0) + (math.pi - 2 * a0) * i / n      # from the left side round the back to the right
        out.append((math.cos(a) * r, math.sin(a) * r))
    return out


def shell(part, path, x0, x1, t, outer, inner):
    """A bent plate along a path [(y, z)] across x0..x1; its inner face (to
    the left of the path) in `inner`."""
    for i in range(len(path) - 1):
        (ya, za), (yb, zb) = path[i], path[i + 1]
        dy, dz = yb - ya, zb - za
        L = math.hypot(dy, dz)
        ny, nz = -dz / L * t, dy / L * t
        cos = [(x0, ya, za), (x1, ya, za), (x1, yb, zb), (x0, yb, zb),
               (x0, ya + ny, za + nz), (x1, ya + ny, za + nz), (x1, yb + ny, zb + nz), (x0, yb + ny, zb + nz)]
        faces = [(0, 1, 2, 3), (4, 7, 6, 5), (0, 3, 7, 4), (1, 5, 6, 2), (0, 4, 5, 1), (3, 2, 6, 7)]
        part._add(cos, faces, outer, mats=[outer, inner, outer, outer, outer, outer])


def lod0(mdl, base, up, boom, stick, bucket):
    O = 'hitachi_orange'
    OD = 'hitachi_orange_dk'
    RL = 'rail_yellow'
    # -- undercarriage --
    for s in (-1, 1):
        crawler(base, s * GAUGE / 2, SHOE, TRACK_L, TRACK_H, plate=0.62, rollers=7)
    base.boxz(0, 0, 0.9, 3.6, 4.6, 1.4, 'steel_dark', bevel=0.25, edges='vt')
    for s in (-1, 1):
        base.boxz(s * 1.95, 0, 1.05, 0.9, 3.4, 1.0, 'steel_dark', bevel=0.12, edges='t')    # side frames to the tracks
        base.boxz(s * GAUGE / 2, 0, 1.55, 0.7, 5.0, 0.3, 'steel_dark')                       # track frame top
    base.cyl((0, 0, 2.3), (0, 0, 2.6), 2.35, 'steel', n=24, capmat='steel_dark')
    # -- upper: deck, counterweight, house, cab --
    up.prism([(-3.62, 2.6)] + [(-3.62, -3.6)] + tail(TAIL - 0.04, 10) + [(3.62, -3.6), (3.62, 2.6)], 'z', 2.6, 2.95, 'steel_dark')
    cw = [(-3.45, -3.5)] + tail(TAIL - 0.1, 10) + [(3.45, -3.5)]
    up.prism(cw, 'z', 2.95, 3.6, 'steel_dark')
    up.prism(cw, 'z', 3.6, 5.85, O, bevel=0.12)
    up.prism([(-3.3, -3.6)] + tail(TAIL - 0.35, 8) + [(3.3, -3.6)], 'z', 5.85, 6.1, OD)
    # house and engine hood
    up.boxz(0.25, -1.45, 2.95, 6.3, 4.3, 3.35, O, bevel=0.14, edges='vt')
    up.boxz(0.45, -2.3, 6.3, 4.8, 2.6, 0.55, O, bevel=0.1, edges='vt')
    up.boxz(0.45, -2.3, 6.85, 4.2, 2.1, 0.04, 'paint_black')
    for i in range(6):
        up.boxz(0.45, -3.15 + i * 0.34, 6.86, 4.1, 0.1, 0.06, 'steel')
    for x in (1.7, 2.4):
        up.cyl((x, -0.75, 6.3), (x, -0.75, 7.6), 0.2, 'steel_dark', n=8)
        up.cyl((x, -0.75, 7.6), (x, -0.75, 7.75), 0.24, 'paint_black', n=8)
    # louvres on the right side, doors on the left
    for i in range(7):
        up.boxz(3.41, -3.2 + i * 0.45, 3.6, 0.04, 0.26, 2.0, 'paint_black')
    for y in (-3.0, -1.6, -0.2):
        up.boxz(-2.86, y, 3.3, 0.04, 0.05, 2.7, OD)
    # front frame with the boom foot brackets
    up.boxz(0, 1.75, 2.95, 3.4, 1.7, 1.55, OD, bevel=0.12, edges='vt')
    for s in (-1, 1):
        up.prism([(0.9, 2.95), (2.65, 2.95), (2.65, 3.6), (2.1, 4.6), (1.3, 4.6)], 'x', s * 1.0 - 0.15, s * 1.0 + 0.15, OD)
        up.boxz(s * 1.25, 2.55, 2.95, 0.6, 0.6, 0.6, 'steel_dark')
    # cab pedestal and cab, front left
    up.boxz(-2.35, 1.75, 2.95, 2.3, 2.2, 2.35, O, bevel=0.1, edges='v')
    up.cab(-2.35, 1.85, 5.3, 2.2, 2.3, 2.53, O, glass_from=0.22, roofmat='paint_white')
    up.boxz(-2.35, 3.03, 7.2, 1.2, 0.08, 0.2, 'lamp')
    up.boxz(-1.1, 1.0, 5.3, 0.3, 0.3, 0.6, OD)
    # walkways: handrails round the deck and the house roof
    up.rail([(-3.58, -3.4), (-3.58, 0.55)], 2.95, RL)
    up.rail([(3.58, -3.4), (3.58, 2.55), (1.75, 2.55)], 2.95, RL)
    up.rail([(-2.85, -3.5), (-2.85, 0.65), (3.35, 0.65), (3.35, -3.5)], 6.3, RL, h=0.9, post=2.0)
    up.rail([(-3.4, 0.7), (-3.4, 2.95), (-1.2, 2.95)], 5.3, RL, h=0.95)
    # the folding access stair down the left front
    up.stair((-4.25, 3.4, 0.35), (-3.7, 0.7, 2.95), 0.85, RL)
    # work lamps on the house front
    for x in (-0.9, 0.9):
        up.boxz(x, 2.62, 4.2, 0.35, 0.12, 0.28, 'lamp')

    # -- boom: a tapering box girder --
    boom.loft([((0, 1.6, 3.6), 2.1, 1.4), ((0, 4.6, 7.3), 1.85, 1.7), ((0, 7.45, 8.9), 1.45, 1.15)], O, up=(0, -1, 1), bevel=0.08)
    boom.cyl((-1.25, B0[1], B0[2]), (1.25, B0[1], B0[2]), 0.38, 'steel_dark', n=10, up=(0, 0, 1))
    boom.cyl((-0.95, B1[1], B1[2]), (0.95, B1[1], B1[2]), 0.32, 'steel_dark', n=10, up=(0, 0, 1))
    for s in (-1, 1):
        boom.boxz(s * 1.05, 4.3, 6.0, 0.3, 0.6, 0.55, 'steel_dark')       # boom cylinder lugs
        boom.boxz(s * 1.18, 6.1, 7.1, 0.25, 0.55, 0.5, 'steel_dark')       # bucket cylinder lugs
        boom.boxz(s * 0.72, 3.0, 6.05, 0.3, 0.6, 0.5, 'steel_dark')        # arm cylinder lugs
    boom.boxz(0.75, 5.4, 8.15, 0.36, 0.2, 0.28, 'lamp')
    boom.boxz(-0.75, 5.4, 8.15, 0.36, 0.2, 0.28, 'lamp')

    # -- stick --
    stick.loft([(V(T_STICK) + V((0, -0.12, 0.25)), 1.25, 0.95), (V(B1), 1.35, 1.05), (V(S1) + V((0, 0.0, 0.2)), 1.2, 0.9)], O,
               up=(0, 1, 0), bevel=0.06)
    stick.cyl((-0.75, T_STICK[1], T_STICK[2]), (0.75, T_STICK[1], T_STICK[2]), 0.26, 'steel_dark', n=8, up=(0, 0, 1))

    # -- bucket: back and floor as a bent plate, side plates, lip, teeth --
    W = 2.45
    path = [(10.6, 4.55), (9.05, 4.55), (8.8, 1.6), (9.3, 0.5), (12.25, 0.3)]
    shell(bucket, path, -W, W, 0.16, O, 'steel_worn')
    side = [(10.6, 4.55), (9.05, 4.55), (8.8, 1.6), (9.3, 0.5), (12.25, 0.3), (12.45, 0.72), (11.05, 3.4)]
    for s in (-1, 1):
        bucket.prism(side, 'x', s * W - 0.08, s * W + 0.08, OD, bevel=0.03)
        bucket.prism([(9.6, 4.55), (9.0, 4.55), (8.9, 3.6), (9.5, 3.6)], 'x', s * W + 0.08, s * W + 0.18, 'bucket_wear')
        bucket.boxz(s * 1.18, 9.55, 4.55, 0.3, 0.6, 0.45, 'steel_dark')       # bucket cylinder lug
        bucket.boxz(s * 0.55, 8.85, 3.4, 0.3, 0.5, 1.1, 'steel_dark')         # stick lugs
        # clam cylinders on the back
        bucket.cyl((s * 1.6, 8.75, 1.4), (s * 1.6, 8.7, 3.6), 0.17, 'steel_dark', n=8)
    bucket.beam((-W - 0.05, 12.3, 0.45), (W + 0.05, 12.3, 0.45), 0.42, 0.3, 'bucket_wear', up=(0, 0, 1))
    for i in range(7):
        x = -2.1 + i * 0.7
        bucket.beam((x, 12.35, 0.45), (x, 12.95, 0.33), 0.3, 0.26, 'steel', w1=0.14, h1=0.1, up=(0, 0, 1))
    bucket.boxz(0, 9.15, 0.62, 2 * W - 0.4, 0.06, 2.6, OD)                     # clam seam band
    for x in (-1.5, 0, 1.5):
        bucket.beam((x, 9.3, 0.42), (x, 12.0, 0.25), 0.18, 0.08, 'bucket_wear', up=(0, 0, 1))   # wear bars

    # -- hydraulic cylinders, aimed at pins on the parts they move --
    for name, (parent, pin_parent, a, b, r) in RAMS.items():
        part = mdl.part(name, pivot=a, parent=parent, rot=axis_rotation(a, b))
        ram(part, a, b, r, OD, frac=0.55)
        mdl.pin('pin_' + name, b, parent=pin_parent)


def lod1(base, up, boom, stick, bucket):
    O = 'hitachi_orange'
    for s in (-1, 1):
        crawler_low(base, s * GAUGE / 2, SHOE, TRACK_L, TRACK_H)
    base.boxz(0, 0, 0.9, 4.2, 4.6, 1.7, 'steel_dark')
    up.prism([(-3.45, -3.5)] + tail(TAIL - 0.1, 4) + [(3.45, -3.5)], 'z', 2.6, 6.0, O)
    up.boxz(0.25, -1.45, 2.6, 6.3, 4.3, 3.7, O)
    up.boxz(0, 1.75, 2.6, 3.4, 1.7, 1.9, 'hitachi_orange_dk')
    up.boxz(-2.35, 1.8, 2.6, 2.3, 2.3, 3.6, O)
    up.boxz(-2.35, 1.85, 6.2, 2.2, 2.3, 1.6, 'glass')
    boom.loft([((0, 1.6, 3.6), 2.1, 1.4), ((0, 4.6, 7.3), 1.85, 1.7), ((0, 7.45, 8.9), 1.45, 1.15)], O, up=(0, -1, 1))
    stick.beam(T_STICK, S1, 1.3, 1.0, O, up=(0, 1, 0))
    bucket.prism([(10.6, 4.55), (9.05, 4.55), (8.8, 1.6), (9.3, 0.5), (12.25, 0.3), (12.45, 0.72), (11.05, 3.4)], 'x', -2.5, 2.5,
                 'hitachi_orange_dk')
