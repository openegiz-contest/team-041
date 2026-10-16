# Parts shared by the machines: tyres with lugs and rims, crawler tracks with
# shoes, hydraulic cylinders split into barrel and rod.

import math

import bmesh
from mathutils import Matrix, Vector

from kit import V

ROT_Y90 = Matrix.Rotation(math.pi / 2, 4, 'Y')


def tyre(part, centre, R, width, rim_r, n=20, lugs=True, rim_side=0, mat='rubber'):
    """A mining tyre on an axle along X at `centre`: a lathed carcass with
    E4-style lugs alternating from both shoulders; `rim_side` (+1/-1/0) puts
    the rim, its flange and the hub on that face."""
    m = Matrix.Translation(V(centre)) @ ROT_Y90       # local Z -> world X
    w = width / 2
    prof = [(rim_r, -w + 0.03), (R * 0.82, -w), (R * 0.95, -w + 0.06), (R - 0.02, -w + 0.16),
            (R - 0.02, 0.0), (R - 0.02, w - 0.16), (R * 0.95, w - 0.06), (R * 0.82, w), (rim_r, w - 0.03)]
    faces = part.lathe(prof, mat, n=n, m=m, close=False)
    if lugs:
        # tread ring faces: segments 3 and 4 of the profile (two halves)
        left = faces[3 * n:4 * n]
        right = faces[4 * n:5 * n]
        sel = [f for i, f in enumerate(left) if i % 2 == 0] + [f for i, f in enumerate(right) if i % 2 == 1]
        res = bmesh.ops.extrude_discrete_faces(part.bm, faces=sel)
        c = V(centre)
        for f in res['faces']:
            for v in f.verts:
                d = v.co - c
                d.x = 0
                v.co += d.normalized() * 0.075
        # the inner wall of the rim
    rim_faces = []
    for side in (-1, 1):
        x = centre[0] + side * (w - 0.03)
        if side == rim_side:
            # flange ring, a dished disc, the hub with its cap
            x0 = centre[0] + side * (w - 0.03)
            part.lathe([(rim_r, 0), (rim_r * 0.86, 0), (rim_r * 0.80, -0.16), (0.46, -0.16)], 'rim', n=n,
                       m=Matrix.Translation(V((x0, centre[1], centre[2]))) @ Matrix.Rotation(side * math.pi / 2, 4, 'Y'), close=False)
            part.lathe([(0.46, -0.16), (0.42, 0.02), (0.30, 0.10), (0.0, 0.12)], ['steel_dark', 'steel_dark', 'steel_dark'], n=10,
                       m=Matrix.Translation(V((x0, centre[1], centre[2]))) @ Matrix.Rotation(side * math.pi / 2, 4, 'Y'), close=False)
        else:
            # the back of the rim: a plain disc
            part.lathe([(rim_r, 0), (0.0, 0.0)], 'steel_dark', n=n,
                       m=Matrix.Translation(V((x, centre[1], centre[2]))) @ Matrix.Rotation(side * math.pi / 2, 4, 'Y'), close=False)
    return rim_faces


def tyre_low(part, centre, R, width, n=8, mat='rubber'):
    """A tyre from afar: an octagonal drum with a rim disc on both faces."""
    x = centre[0]
    part.cyl((x - width / 2, centre[1], centre[2]), (x + width / 2, centre[1], centre[2]), R, mat, n=n, capmat='rim', up=(0, 0, 1))


def crawler(part, x, width, length, height, plate=0.62, rollers=6, mat='steel_dark', frame_mat='steel_dark',
            grouser=0.09, wheel_mat='steel'):
    """A crawler track along Y centred at x: a loop of shoes with grousers,
    the track frame inside it, idler and sprocket, track rollers."""
    rr = height / 2
    half = length / 2 - rr
    loop = []
    straight = max(2, round(2 * half / plate))
    for i in range(straight):
        loop.append((-half + 2 * half * i / straight, 0.0))
    for i in range(6):
        a = -math.pi / 2 + math.pi * i / 6
        loop.append((half + math.cos(a) * rr, rr + math.sin(a) * rr))
    for i in range(straight):
        loop.append((half - 2 * half * i / straight, height))
    for i in range(6):
        a = math.pi / 2 + math.pi * i / 6
        loop.append((-half + math.cos(a) * rr, rr + math.sin(a) * rr))
    n = len(loop)
    x0, x1 = x - width / 2, x + width / 2
    thick = 0.28

    def outward(y, z):
        oy = y - max(-half, min(half, y))
        oz = z - rr
        if abs(oy) < 1e-6:
            return (0.0, -1.0) if z < rr else (0.0, 1.0)
        L = math.hypot(oy, oz)
        return (oy / L, oz / L)

    inner = []
    for y, z in loop:
        oy, oz = outward(y, z)
        inner.append((y - oy * thick, z - oz * thick))
    # Each shoe: an outer plate with a grouser bar across it, side faces.
    for i in range(n):
        j = (i + 1) % n
        (ya, za), (yb, zb) = loop[i], loop[j]
        oy, oz = outward((ya + yb) / 2, (za + zb) / 2)
        # gap between shoes: shrink each plate a little along the loop
        dy, dz = yb - ya, zb - za
        ga = (ya + dy * 0.04, za + dz * 0.04)
        gb = (yb - dy * 0.04, zb - dz * 0.04)
        ia, ib = inner[i], inner[j]
        # a closed block per shoe: outer, inner, two sides, two ends
        cos = [(x0, ga[0], ga[1]), (x1, ga[0], ga[1]), (x1, gb[0], gb[1]), (x0, gb[0], gb[1]),
               (x0, ia[0], ia[1]), (x1, ia[0], ia[1]), (x1, ib[0], ib[1]), (x0, ib[0], ib[1])]
        faces = [(0, 1, 2, 3), (4, 7, 6, 5), (0, 3, 7, 4), (1, 5, 6, 2), (0, 4, 5, 1), (3, 2, 6, 7)]
        part._add(cos, faces, mat)
        # grouser: a bar standing out of the plate, at its leading third
        g0 = (ga[0] + dy * 0.25, ga[1] + dz * 0.25)
        g1 = (ga[0] + dy * 0.45, ga[1] + dz * 0.45)
        gc = [(x0 + 0.02, g0[0], g0[1]), (x1 - 0.02, g0[0], g0[1]), (x1 - 0.02, g1[0], g1[1]), (x0 + 0.02, g1[0], g1[1])]
        go = [(cx, cy + oy * grouser, cz + oz * grouser) for cx, cy, cz in gc]
        part._add(gc + go, [(0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)], mat)
    # Track frame (inside the loop), idler at the front, sprocket at the rear, rollers.
    fh = height - 2 * thick - 0.12
    part.boxz(x, 0, thick + 0.06, width * 0.7, 2 * half, fh, frame_mat, bevel=0.06, edges='v')
    for end, sides in ((1, 14), (-1, 10)):
        part.cyl((x0 + 0.06, end * half, rr), (x1 - 0.06, end * half, rr), rr - thick - 0.02, wheel_mat, n=sides, capmat=wheel_mat, up=(0, 0, 1))
        part.cyl((x0 - 0.04, end * half, rr), (x1 + 0.04, end * half, rr), (rr - thick) * 0.45, 'steel_dark', n=8, up=(0, 0, 1))
    for i in range(rollers):
        y = -half + (i + 0.5) * 2 * half / rollers
        part.cyl((x0 + 0.08, y, thick + 0.2), (x1 - 0.08, y, thick + 0.2), 0.2, wheel_mat, n=8, up=(0, 0, 1))


def crawler_low(part, x, width, length, height, mat='steel_dark'):
    rr = height / 2
    half = length / 2 - rr
    poly = [(-half, 0), (half, 0), (half + rr * 0.85, rr * 0.5), (half + rr * 0.85, rr * 1.5), (half, height),
            (-half, height), (-half - rr * 0.85, rr * 1.5), (-half - rr * 0.85, rr * 0.5)]
    part.prism(poly, 'x', x - width / 2, x + width / 2, mat)


def ram(part, p, q, r, mat, frac=0.55, n=10):
    """A hydraulic cylinder from pin p to pin q: barrel, then the chrome rod,
    eyes at both pins. Built as one node that aims at q and stretches."""
    p, q = V(p), V(q)
    d = (q - p).normalized()
    m = p + (q - p) * frac
    part.cyl(p - d * 0.06, p + d * 0.3, r * 1.18, 'steel_dark', n=n)
    part.cyl(p + d * 0.3, m, r, mat, n=n, capmat='steel_dark')
    part.cyl(m - d * 0.2, m, r * 1.12, 'steel_dark', n=n)
    part.cyl(m, q - d * 0.2, r * 0.6, 'chrome', n=8, caps=(False, True))
    part.cyl(q - d * 0.2, q + d * 0.06, r * 0.9, 'steel_dark', n=8)


def axis_rotation(p, q):
    """A rotation turning Blender's local -Y onto the direction p -> q, so that
    after export the node's glTF +Z points from p to q (three.js lookAt aims +Z)."""
    d = (V(q) - V(p)).normalized()
    return (-V((0, 1, 0))).rotation_difference(d).to_matrix().to_4x4()
