# Sandvik D55SP rotary blasthole drill (crawler, diesel), Sandvik yellow.
# Spec dimensions: mast up 13.72 m long x 5.05 m wide x 23.06 m high; mast
# down 22.63 m long x 5.33 m high, shipping width 4.40 m; holes 172-254 mm,
# single pass 17 m; engine 708 kW.

from kit import Model, grime
from machine_parts import crawler, crawler_low, ram, axis_rotation

PIVOT = (0.5, 5.0, 4.1)       # mast trunnion
MAST_FOOT = 2.1
MAST_TOP = 22.0             # crown sheaves top out at 23.1 m (spec 23.06)
LOWERED = 1.55                # rad about X: the mast lies back over the power pack
GAUGE = 3.64
SHOE = 0.76
TRACK_L = 6.6
TRACK_H = 1.25
DECK = 1.75
RAMS = {
    'mast_cyl_L': ((-0.3, 6.2, 1.9), (-0.3, 4.45, 7.1)),
    'mast_cyl_R': ((1.3, 6.2, 1.9), (1.3, 4.45, 7.1)),
}

META = {
    'id': 'd55sp',
    'title': 'Sandvik D55SP rotary blasthole drill',
    'kind': 'machine',
    'reference': {
        'mast_up_m': [13.72, 5.05, 23.06], 'mast_down_m': [22.63, 4.40, 5.33], 'hole_mm': [172, 254],
        'single_pass_m': 17, 'engine_kw': 708, 'colour': 'Sandvik yellow',
    },
    'anim': {
        'base': {'what': 'crawlers, frame, deck, power pack, cab'},
        'mast': {'what': 'lattice mast with rotary head and drill pipe', 'pivot': 'mast trunnion pin', 'axis': 'x',
                 'range_rad': [0, LOWERED],
                 'note': 'built standing (drilling); positive rotation about X lowers it backwards onto the mast rest; %.2f rad = transport' % LOWERED},
        'mast_cyl_L': {'what': 'mast raise cylinder, child of base', 'pivot': 'pin on the deck', 'aim': 'pin_mast_cyl_L'},
        'mast_cyl_R': {'what': 'mast raise cylinder, child of base', 'pivot': 'pin on the deck', 'aim': 'pin_mast_cyl_R'},
    },
}


def build(lod):
    mdl = Model('D55SP', dirt=grime(ground=1.8, amount=0.6, dust=0.3))
    base = mdl.part('base')
    mast = mdl.part('mast', pivot=PIVOT)
    if lod == 0:
        lod0(mdl, base, mast)
    else:
        lod1(base, mast)
    return mdl


def lod0(mdl, b, m):
    Y = 'sandvik_yellow'
    G = 'sandvik_grey'
    RL = 'rail_yellow'
    for s in (-1, 1):
        crawler(b, s * GAUGE / 2, SHOE, TRACK_L, TRACK_H, plate=0.5, rollers=6, grouser=0.06)
        b.boxz(s * 1.2, 0, 1.0, 0.9, 2.6, 0.55, G)                                  # cross frame to the tracks
    b.boxz(0, -0.1, 1.0, 2.0, 12.6, 0.6, G, bevel=0.06, edges='v')                 # main frame
    b.boxz(0, -0.05, 1.6, 4.6, 12.7, 0.15, 'grating')                              # deck
    for s in (-1, 1):
        b.boxz(s * 2.38, -2.0, 1.55, 0.16, 8.4, 0.2, G)                             # walkway edges
    b.rail([(-2.45, -6.2), (-2.45, 2.6)], DECK, RL)
    b.rail([(2.45, -6.2), (2.45, 6.2), (1.4, 6.35)], DECK, RL)
    b.rail([(-2.45, -6.38), (2.45, -6.38)], DECK, RL, post=1.6)
    b.stair((-2.95, -4.8, 0.25), (-2.55, -3.1, DECK), 0.62, RL, rails=(True, False))
    # levelling jacks
    for x, y in ((-1.95, 5.7), (1.95, 5.7), (-1.95, -5.9), (1.95, -5.9)):
        b.cyl((x, y, 0.35), (x, y, 2.3), 0.2, 'steel_dark', n=8)
        b.cyl((x, y, 0.05), (x, y, 0.9), 0.13, 'chrome', n=8)
        b.boxz(x, y, 0.0, 0.7, 0.7, 0.08, 'steel_dark')
    # the power pack: engine and compressor under one hood, radiator at the rear
    b.boxz(0, -3.45, DECK, 4.2, 5.7, 1.7, Y, bevel=0.12, edges='vt')
    b.boxz(0, -6.33, DECK + 0.1, 3.8, 0.08, 1.45, 'paint_black')
    for i in range(5):
        b.boxz(0, -6.39, DECK + 0.25 + i * 0.28, 3.6, 0.05, 0.08, 'steel')
    for s in (-1, 1):
        for i in range(6):
            b.boxz(s * 2.11, -5.6 + i * 0.6, DECK + 0.35, 0.04, 0.3, 1.0, 'paint_black')
    b.cyl((-1.55, -1.6, DECK + 1.7), (-1.55, -1.6, DECK + 2.3), 0.16, 'steel_dark', n=8)     # exhaust
    # mast rest at the rear, the dust collector, the receiver, tanks
    for s in (-1, 1):
        b.beam((0.5 + s * 0.85, -5.7, DECK + 1.7), (0.5 + s * 0.65, -5.7, 3.72), 0.18, 0.18, G)
    b.boxz(0.5, -5.7, 3.62, 1.5, 0.35, 0.12, 'rubber')
    b.cyl((1.7, 3.5, DECK), (1.7, 3.5, 3.3), 0.5, G, n=12)
    b.cyl((1.7, 3.5, 3.3), (1.7, 3.5, 3.75), 0.5, G, r1=0.12, n=12)
    b.cyl((1.75, -0.2, DECK + 0.8), (1.75, 2.1, DECK + 0.8), 0.55, 'paint_white', n=12)       # air receiver
    b.boxz(-1.55, 0.4, DECK, 1.6, 2.2, 1.35, Y, bevel=0.1, edges='vt')                        # hydraulic tank
    # the mast A-frame and trunnion; the dust hood under the deck at the hole
    for s in (-1, 1):
        b.prism([(4.1, DECK), (5.95, DECK), (5.35, 4.35), (4.65, 4.35)], 'x', 0.5 + s * 0.78 - 0.08, 0.5 + s * 0.78 + 0.08, G)
    b.cyl((-0.45, PIVOT[1], PIVOT[2]), (1.45, PIVOT[1], PIVOT[2]), 0.18, 'steel_dark', n=8, up=(0, 0, 1))
    b.boxz(0.5, 5.0, 0.0, 1.5, 1.5, 1.0, 'rubber')
    b.boxz(0.5, 5.0, 1.0, 1.7, 1.7, 0.6, 'steel_dark')
    # cab, front left
    b.cab(-1.45, 3.9, DECK, 1.9, 2.2, 2.6, Y, glass_from=0.3, roofmat=G)
    b.boxz(-1.45, 5.06, DECK + 2.25, 0.9, 0.08, 0.18, 'lamp')
    b.boxz(-1.45, 3.9, DECK + 2.6, 0.2, 0.2, 0.2, 'lamp_amber')

    # -- the mast: four chords, lacing on every face, crown, rotary head, pipe --
    x0, x1 = PIVOT[0] - 0.5, PIVOT[0] + 0.5
    y0, y1 = PIVOT[1] - 0.55, PIVOT[1] + 0.55
    for x in (x0, x1):
        for y in (y0, y1):
            m.beam((x, y, MAST_FOOT), (x, y, MAST_TOP), 0.2, 0.2, Y, caps=False)
    bay = 1.5
    z = MAST_FOOT + 0.3
    k = 0
    while z + bay <= MAST_TOP - 0.6:
        za, zb = (z, z + bay) if k % 2 == 0 else (z + bay, z)
        m.bar((x0, y0, za), (x1, y0, zb), 0.09, Y)
        m.bar((x0, y1, zb), (x1, y1, za), 0.09, Y)
        m.bar((x0, y0, zb), (x0, y1, za), 0.09, Y)
        m.bar((x1, y0, za), (x1, y1, zb), 0.09, Y)
        if k % 2 == 0:
            m.bar((x0, y0, z), (x1, y0, z), 0.09, Y)
            m.bar((x0, y1, z), (x1, y1, z), 0.09, Y)
        z += bay
        k += 1
    m.boxz(PIVOT[0], PIVOT[1], MAST_TOP - 0.2, 1.45, 1.55, 0.7, Y, bevel=0.06)            # crown
    for s in (-1, 1):
        m.cyl((PIVOT[0] + s * 0.25 - 0.1, PIVOT[1], MAST_TOP + 0.75), (PIVOT[0] + s * 0.25 + 0.1, PIVOT[1], MAST_TOP + 0.75), 0.38, 'steel_dark', n=12, up=(0, 0, 1))
    m.boxz(PIVOT[0], PIVOT[1] + 0.85, MAST_TOP - 0.6, 0.4, 0.1, 0.3, 'lamp')
    m.boxz(PIVOT[0], PIVOT[1] + 0.85, 11.0, 0.4, 0.1, 0.3, 'lamp')
    m.boxz(PIVOT[0], PIVOT[1], 12.0, 0.85, 0.95, 1.5, G, bevel=0.08)                         # rotary head
    m.cyl((PIVOT[0] + 0.45, PIVOT[1], 13.0), (PIVOT[0] + 0.45, PIVOT[1], 14.1), 0.28, 'steel_dark', n=10)   # motor
    m.cyl((PIVOT[0], PIVOT[1], MAST_FOOT - 0.1), (PIVOT[0], PIVOT[1], 12.0), 0.12, 'steel', n=8)   # drill pipe
    m.cyl((x1 + 0.4, y0 + 0.2, 3.0), (x1 + 0.4, y0 + 0.2, 13.5), 0.38, G, n=10)                # pipe carousel
    for z in (3.2, 8.0, 13.0):
        m.bar((x1, y0 + 0.2, z), (x1 + 0.4, y0 + 0.2, z), 0.12, G)
    m.boxz(PIVOT[0], PIVOT[1], MAST_FOOT - 0.2, 1.3, 1.4, 0.35, 'steel_dark')               # foot / table

    for name, (a, c) in RAMS.items():
        part = mdl.part(name, pivot=a, rot=axis_rotation(a, c))
        ram(part, a, c, 0.17, G, frac=0.5)
        mdl.pin('pin_' + name, c, parent='mast')


def lod1(b, m):
    Y = 'sandvik_yellow'
    for s in (-1, 1):
        crawler_low(b, s * GAUGE / 2, SHOE, TRACK_L, TRACK_H)
    b.boxz(0, -0.05, 1.0, 4.6, 12.7, 0.75, 'sandvik_grey')
    b.boxz(0, -3.45, DECK, 4.2, 5.7, 1.7, Y)
    b.boxz(-1.45, 3.9, DECK, 1.9, 2.2, 1.0, Y)
    b.boxz(-1.45, 3.9, DECK + 1.0, 1.9, 2.2, 1.6, 'glass')
    b.boxz(1.7, 3.5, DECK, 0.9, 0.9, 1.9, 'sandvik_grey')
    m.boxz(PIVOT[0], PIVOT[1], MAST_FOOT, 1.1, 1.2, MAST_TOP - MAST_FOOT, Y)
    m.boxz(PIVOT[0], PIVOT[1], MAST_TOP - 0.2, 1.45, 1.55, 0.9, Y)
