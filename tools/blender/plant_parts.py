# Parts shared by the plant models: clad buildings with window bands, pitched
# roofs, tanks, lattice members, platforms with handrails.

import math

from kit import V


def building(p, cx, cy, z0, sx, sy, sz, wall='clad_white', roof='roof', plinth=1.2, bands=(), doors=(),
             parapet=True, columns=0.0, trim='trim', pitch=0.0, eave=0.6, top=True):
    """A steel-framed building: a concrete plinth, clad walls (the texture
    draws the profiled sheet), bands of windows standing proud of the wall,
    roller doors, columns showing on the walls, a flat roof with a parapet or
    a pitched roof (`pitch`: rise of the ridge, running along X)."""
    if plinth:
        p.boxz(cx, cy, z0, sx + 0.1, sy + 0.1, plinth, 'concrete', skip=('bottom',))
    p.boxz(cx, cy, z0 + plinth, sx, sy, sz - plinth, wall, skip=('bottom', 'top') if (pitch or not top) else ('bottom',))
    for za, zb in bands:
        for (ax, ay, w, d) in ((cx, cy - sy / 2, sx * 0.94, 0.12), (cx, cy + sy / 2, sx * 0.94, 0.12),
                               (cx - sx / 2, cy, 0.12, sy * 0.9), (cx + sx / 2, cy, 0.12, sy * 0.9)):
            p.boxz(ax, ay, z0 + za, w, d, zb - za, 'window', skip=('bottom', 'top'))
    for side, off, w, h in doors:
        # side: '-y' '+y' '-x' '+x'
        if side in ('-y', '+y'):
            y = cy + (sy / 2 if side == '+y' else -sy / 2)
            p.boxz(cx + off, y, z0 + 0.05, w, 0.16, h, 'steel', skip=('bottom',))
            p.boxz(cx + off, y, z0 + h, w + 0.4, 0.3, 0.3, trim)
        else:
            x = cx + (sx / 2 if side == '+x' else -sx / 2)
            p.boxz(x, cy + off, z0 + 0.05, 0.16, w, h, 'steel', skip=('bottom',))
            p.boxz(x, cy + off, z0 + h, 0.3, w + 0.4, 0.3, trim)
    if columns:
        nx = max(1, round(sx / columns))
        for i in range(nx + 1):
            x = cx - sx / 2 + sx * i / nx
            for y in (cy - sy / 2, cy + sy / 2):
                p.boxz(x, y, z0 + plinth, 0.45, 0.45, sz - plinth - 0.1, trim, skip=('bottom', 'top'))
    z1 = z0 + sz
    if pitch:
        hx, hy = sx / 2 + eave, sy / 2 + eave
        p.face([(cx - hx, cy - hy, z1 - 0.3), (cx + hx, cy - hy, z1 - 0.3), (cx + hx, cy, z1 + pitch), (cx - hx, cy, z1 + pitch)], roof, normal=(0, -1, 3))
        p.face([(cx - hx, cy, z1 + pitch), (cx + hx, cy, z1 + pitch), (cx + hx, cy + hy, z1 - 0.3), (cx - hx, cy + hy, z1 - 0.3)], roof, normal=(0, 1, 3))
        for s in (-1, 1):
            p.face([(cx + s * sx / 2, cy - sy / 2, z1), (cx + s * sx / 2, cy + sy / 2, z1), (cx + s * sx / 2, cy, z1 + pitch - 0.2)], wall, normal=(s, 0, 0))
            p.boxz(cx, cy + s * (sy / 2 + eave * 0.5), z1 - 0.7, sx + 2 * eave, eave, 0.45, trim)       # fascia
    else:
        if parapet:
            p.boxz(cx, cy, z1 - 0.45, sx + 0.25, sy + 0.25, 0.9, trim, skip=('bottom',))
        # the roof itself is the top of the wall box


def roof_lights(p, cx, cy, z1, sx, sy, pitch, strips=(0.3, 0.62), length=0.84, mat='window'):
    """Strips of roof glazing on both slopes of a pitched roof along X."""
    for s in (-1, 1):
        for f in strips:
            ya, yb = s * (sy / 2) * f, s * (sy / 2) * (f + 0.1)
            za = z1 + pitch - abs(ya) / (sy / 2) * pitch + 0.08
            zb = z1 + pitch - abs(yb) / (sy / 2) * pitch + 0.08
            hx = sx * length / 2
            p.face([(cx - hx, cy + ya, za), (cx + hx, cy + ya, za), (cx + hx, cy + yb, zb), (cx - hx, cy + yb, zb)], mat, normal=(0, s, 3))


def tank(p, x, y, z0, r, h, mat='tank', n=20, top='flat', topmat=None, rings=0):
    """A vertical cylindrical tank: flat or conical roof, stiffener rings."""
    if top == 'cone':
        p.lathe([(r, z0), (r, z0 + h), (r * 0.15, z0 + h + r * 0.25), (0, z0 + h + r * 0.25)], [mat, topmat or mat, topmat or mat], n=n,
                m=_t(x, y), close=False)
    elif top == 'open':
        p.cyl((x, y, z0), (x, y, z0 + h), r, mat, n=n, caps=(False, False))
    else:
        p.cyl((x, y, z0), (x, y, z0 + h), r, mat, n=n, caps=(False, True), capmat=topmat or mat)
    for k in range(rings):
        z = z0 + h * (k + 1) / (rings + 1)
        p.cyl((x, y, z), (x, y, z + 0.18), r + 0.06, 'struct', n=n, caps=(False, False))


def _t(x, y):
    from mathutils import Matrix
    return Matrix.Translation(V((x, y, 0)))


def lattice(p, a, b, w, h, mat='struct', bay=2.5, chord=0.18, lace=0.09, up=(0, 0, 1)):
    """A box truss from a to b: four chords, verticals and diagonals on the two
    sides, top lacing."""
    a, b = V(a), V(b)
    d = b - a
    L = d.length
    dn = d.normalized()
    side = dn.cross(V(up)).normalized()
    upv = side.cross(dn).normalized()
    corners = [(-1, -1), (1, -1), (1, 1), (-1, 1)]

    def at(t, cx, cz):
        return a + dn * t + side * (cx * w / 2) + upv * (cz * h / 2)
    for cx, cz in corners:
        p.beam(at(0, cx, cz), at(L, cx, cz), chord, chord, mat, caps=False, up=tuple(upv))
    n = max(1, round(L / bay))
    for i in range(n + 1):
        t = L * i / n
        for cx in (-1, 1):
            p.bar(at(t, cx, -1), at(t, cx, 1), lace, mat)
        p.bar(at(t, -1, 1), at(t, 1, 1), lace, mat)
        p.bar(at(t, -1, -1), at(t, 1, -1), lace, mat)
        if i < n:
            t2 = L * (i + 1) / n
            for cx in (-1, 1):
                if i % 2 == 0:
                    p.bar(at(t, cx, -1), at(t2, cx, 1), lace, mat)
                else:
                    p.bar(at(t, cx, 1), at(t2, cx, -1), lace, mat)


def platform(p, x, y, z, sx, sy, rail_sides='all', mat='grating', rail='rail_yellow', legs=True):
    """A grating platform on posts with handrails round it."""
    p.boxz(x, y, z - 0.15, sx, sy, 0.15, mat)
    hx, hy = sx / 2, sy / 2
    path = [(x - hx, y - hy), (x + hx, y - hy), (x + hx, y + hy), (x - hx, y + hy), (x - hx, y - hy)]
    p.rail(path, z, rail, post=2.0)
    if legs and z > 0.5:
        for cx in (x - hx + 0.2, x + hx - 0.2):
            for cy in (y - hy + 0.2, y + hy - 0.2):
                p.beam((cx, cy, 0), (cx, cy, z - 0.15), 0.25, 0.25, 'struct', caps=False)


def pipe(p, pts, r, mat='struct', n=8):
    for i in range(len(pts) - 1):
        p.cyl(pts[i], pts[i + 1], r, mat, n=n, caps=(False, False))
