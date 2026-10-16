# One 24 m segment of the overland conveyor: a box truss with a walkway, a
# 1.8 m troughed belt on three-roll idlers, the return belt, and half-round
# weather hoods with inspection gaps. Built along local +Y from y = 0 to 24
# (scene axes), the truss's bottom chords at z = 0, so segments tile end to
# end. The ore on the belt and the trestle are their own nodes.

import math

from kit import Model, grime
from plant_parts import lattice

LEN = 24.0
BW = 1.8          # belt width
TW, TH = 3.6, 2.4  # truss width, height
BX = -0.35        # belt centre (walkway on +X)

META = {
    'id': 'conveyor',
    'title': 'Covered conveyor truss segment, 24 m',
    'kind': 'plant',
    'lods': [0, 1],
    'reference': {'segment_m': LEN, 'belt_width_m': BW, 'truss_m': [TW, TH], 'tiling': 'along local +Y (scene) = glTF -Z, 24 m a segment'},
    'anim': {
        'truss': {'what': 'truss, walkway, idlers, belt, hoods'},
        'ore': {'what': 'ore on the belt', 'note': 'show while the belt runs; material "ore"'},
        'trestle': {'what': 'portal trestle under the segment start, legs 1 m long at rest',
                    'note': 'scale glTF Y (scene Z) by the truss height above the ground in metres'},
    },
}


def build(lod):
    mdl = Model('Conveyor', dirt=grime(ground=0.8, amount=0.3, dust=0.25))
    t = mdl.part('truss', uv=True)
    ore = mdl.part('ore', smooth_deg=0)
    tr = mdl.part('trestle')
    trough = [(BX - BW / 2, 0.95 + 0.42), (BX - 0.33, 0.95), (BX + 0.33, 0.95), (BX + BW / 2, 0.95 + 0.42)]
    if lod == 0:
        lattice(t, (0, 0, TH / 2), (0, LEN, TH / 2), TW, TH, 'struct', bay=3.0)
        t.boxz(1.15, LEN / 2, 0.0, 1.0, LEN, 0.1, 'grating')
        t.rail([(1.65, 0.0), (1.65, LEN)], 0.05, 'rail_yellow', post=3.0)
        # idlers: three rolls on a frame every 1.5 m
        for k in range(16):
            y = 0.75 + k * 1.5
            t.beam((BX - BW / 2 - 0.1, y, 0.85), (BX + BW / 2 + 0.1, y, 0.85), 0.1, 0.1, 'steel_dark', caps=False)
            for (a, b) in zip(trough[:-1], trough[1:]):
                t.cyl((a[0], y, a[1] - 0.06), (b[0], y, b[1] - 0.06), 0.07, 'steel', n=6, up=(0, 0, 1))
            if k % 2 == 0:
                t.cyl((BX - BW / 2, y, 0.3), (BX + BW / 2, y, 0.3), 0.07, 'steel', n=6, up=(0, 0, 1))   # return roll
        # the belt (carrying and return)
        for (a, b) in zip(trough[:-1], trough[1:]):
            t.face([(a[0], 0, a[1]), (b[0], 0, b[1]), (b[0], LEN, b[1]), (a[0], LEN, a[1])], 'belt', normal=(0, 0, 1))
        t.face([(BX - BW / 2, 0, 0.38), (BX + BW / 2, 0, 0.38), (BX + BW / 2, LEN, 0.38), (BX - BW / 2, LEN, 0.38)], 'belt', normal=(0, 0, 1))
        # hoods: half-round covers in 3.6 m pieces with 0.4 m gaps
        rh = 1.15
        for piece in range(6):
            ya, yb = piece * 4.0 + 0.2, piece * 4.0 + 3.8
            for s in range(6):
                a0, a1 = math.pi * s / 6, math.pi * (s + 1) / 6
                pa = (BX + math.cos(a0) * rh, 1.05 + math.sin(a0) * rh)
                pb = (BX + math.cos(a1) * rh, 1.05 + math.sin(a1) * rh)
                mid = (a0 + a1) / 2
                t.face([(pa[0], ya, pa[1]), (pb[0], ya, pb[1]), (pb[0], yb, pb[1]), (pa[0], yb, pa[1])], 'hood',
                       normal=(math.cos(mid), 0, math.sin(mid)))
            t.beam((BX - rh, (ya + yb) / 2, 1.05), (BX + rh, (ya + yb) / 2, 1.05), 0.06, 0.06, 'struct', caps=False)
        # ore: a low ridge on the belt
        for (a, b, ha, hb) in ((trough[0], trough[1], 0.04, 0.2), (trough[1], trough[2], 0.2, 0.2), (trough[2], trough[3], 0.2, 0.04)):
            ore.face([(a[0] + 0.05, 0, a[1] + ha), (b[0], 0, b[1] + hb), (b[0], LEN, b[1] + hb), (a[0] + 0.05, LEN, a[1] + ha)], 'ore', normal=(0, 0, 1))
        # trestle: two legs, a cross beam, bracing, foot plates
        for s in (-1, 1):
            tr.beam((s * 1.6, 0.6, 0), (s * 1.9, 0.6, -1.0), 0.3, 0.3, 'struct', up=(0, 1, 0))
            tr.boxz(s * 1.9, 0.6, -1.0, 0.7, 0.7, 0.05, 'struct')
        tr.beam((-1.75, 0.6, -0.1), (1.75, 0.6, -0.1), 0.25, 0.3, 'struct')
        tr.bar((-1.75, 0.6, -0.15), (1.85, 0.6, -0.9), 0.1, 'struct')
    else:
        t.boxz(0, LEN / 2, 0, TW, LEN, 0.25, 'struct')
        for s in (-1, 1):
            t.boxz(s * (TW / 2 - 0.1), LEN / 2, 0, 0.2, LEN, TH, 'struct')
        t.face([(BX - BW / 2, 0, 1.2), (BX + BW / 2, 0, 1.2), (BX + BW / 2, LEN, 1.2), (BX - BW / 2, LEN, 1.2)], 'belt', normal=(0, 0, 1))
        for s in range(3):
            a0, a1 = math.pi * s / 3, math.pi * (s + 1) / 3
            pa = (BX + math.cos(a0) * 1.15, 1.05 + math.sin(a0) * 1.15)
            pb = (BX + math.cos(a1) * 1.15, 1.05 + math.sin(a1) * 1.15)
            mid = (a0 + a1) / 2
            t.face([(pa[0], 0, pa[1]), (pb[0], 0, pb[1]), (pb[0], LEN, pb[1]), (pa[0], LEN, pa[1])], 'hood', normal=(math.cos(mid), 0, math.sin(mid)))
        ore.face([(BX - 0.6, 0, 1.35), (BX + 0.6, 0, 1.35), (BX + 0.6, LEN, 1.35), (BX - 0.6, LEN, 1.35)], 'ore', normal=(0, 0, 1))
        for s in (-1, 1):
            tr.beam((s * 1.6, 0.6, 0), (s * 1.9, 0.6, -1.0), 0.3, 0.3, 'struct', up=(0, 1, 0))
    return mdl
