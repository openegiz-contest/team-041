# The modelling kit shared by every model script: a part builder over bmesh
# (boxes with chamfers, cylinders, lathed profiles, prisms, beams, lofts,
# handrails), the shared PBR materials and the small procedural textures,
# vertex-colour grime, node pivots and the glTF export.
#
# Conventions (the same as the procedural models in mine/ui/site/js/models3d.js):
# metres, Z up, a machine faces +Y, the origin on the ground under its centre.
# The glTF exporter turns this into glTF's +Y up: Blender (x, y, z) becomes
# glTF (x, z, -y), so a machine faces glTF -Z.

import math
import os

import bmesh
import bpy
import numpy as np
from mathutils import Matrix, Vector

V = Vector


def lin(hexstr):
    """sRGB hex colour to linear RGB."""
    h = hexstr.lstrip('#')
    out = []
    for i in range(3):
        c = int(h[i * 2:i * 2 + 2], 16) / 255.0
        out.append(c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4)
    return tuple(out)


# -- materials -----------------------------------------------------------------
# name: base colour (sRGB hex), metallic, roughness, extras
MATERIALS = {
    # machines
    'cat_yellow':    ('#e8b01c', 0.0, 0.48, {}),
    'cat_yellow_dk': ('#b7860f', 0.0, 0.55, {}),
    'hitachi_orange': ('#ee6a14', 0.0, 0.45, {}),
    'hitachi_orange_dk': ('#b74f0d', 0.0, 0.55, {}),
    'sandvik_yellow': ('#f2b415', 0.0, 0.48, {}),
    'sandvik_grey':  ('#4c5154', 0.1, 0.6, {}),
    'paint_white':   ('#e6e6e0', 0.0, 0.5, {}),
    'paint_black':   ('#1b1c1d', 0.0, 0.6, {}),
    'steel_dark':    ('#34373a', 0.45, 0.62, {}),
    'steel':         ('#6a6d6f', 0.55, 0.55, {}),
    'steel_worn':    ('#8a8174', 0.5, 0.45, {}),
    'chrome':        ('#d8dbdc', 1.0, 0.18, {}),
    'rubber':        ('#2a2a2a', 0.0, 0.85, {}),
    'rim':           ('#b4ad9f', 0.2, 0.5, {}),
    'glass':         ('#2e4356', 0.0, 0.12, {}),
    'lamp':          ('#fff6dc', 0.0, 0.3, {'emissive': '#fff1cc', 'strength': 1.0}),
    'lamp_red':      ('#c0180e', 0.0, 0.3, {'emissive': '#d0200f', 'strength': 1.0}),
    'lamp_amber':    ('#f08a10', 0.0, 0.3, {'emissive': '#f08a10', 'strength': 1.0}),
    'rock':          ('#8a6a52', 0.0, 0.95, {}),
    'bucket_wear':   ('#6e665c', 0.6, 0.42, {}),
    # plant
    'concrete':      ('#b3ada2', 0.0, 0.9, {'texture': 'concrete'}),
    'concrete_dk':   ('#8e897f', 0.0, 0.92, {'texture': 'concrete'}),
    'clad_white':    ('#d4d8d6', 0.1, 0.6, {'texture': 'clad'}),
    'clad_warm':     ('#c9c2b2', 0.1, 0.6, {'texture': 'clad'}),
    'clad_blue':     ('#2f5878', 0.1, 0.55, {'texture': 'clad'}),
    'roof':          ('#66788a', 0.25, 0.55, {'texture': 'clad'}),
    'trim':          ('#2f5272', 0.1, 0.5, {}),
    'struct':        ('#5d6468', 0.35, 0.6, {}),
    'struct_blue':   ('#3a6487', 0.1, 0.55, {}),
    'rail_yellow':   ('#e0aa1e', 0.0, 0.5, {}),
    'tank':          ('#c3c7c0', 0.2, 0.55, {}),
    'shell_green':   ('#6f8a7a', 0.15, 0.55, {}),
    'motor':         ('#3e4447', 0.3, 0.55, {}),
    'froth':         ('#b49f5e', 0.0, 0.75, {}),
    'water':         ('#4d7473', 0.0, 0.15, {}),
    'slurry':        ('#6b6650', 0.0, 0.4, {}),
    'belt':          ('#262625', 0.0, 0.85, {}),
    'ore':           ('#7d6450', 0.0, 0.95, {}),
    'window':        ('#22323f', 0.0, 0.12, {}),
    'hood':          ('#c7ccca', 0.2, 0.55, {'texture': 'clad', 'double': True}),
    'grating':       ('#4f5559', 0.4, 0.65, {}),
    'rust':          ('#7a4a2c', 0.0, 0.85, {}),
}

# Materials that take no grime.
CLEAN = {'glass', 'lamp', 'lamp_red', 'lamp_amber', 'chrome', 'window', 'water', 'slurry', 'froth', 'rock', 'ore'}

_textures = {}


def _noise(rng, size, octaves):
    """Tileable value noise in [0, 1] from a seeded generator."""
    acc = np.zeros((size, size))
    amp = 1.0
    total = 0.0
    for o in range(octaves):
        cells = 4 * 2 ** o
        grid = rng.random((cells, cells))
        idx = np.arange(size) * cells / size
        i0 = np.floor(idx).astype(int) % cells
        i1 = (i0 + 1) % cells
        f = idx - np.floor(idx)
        f = f * f * (3 - 2 * f)
        a = grid[i0][:, i0] * (1 - f)[None, :] + grid[i0][:, i1] * f[None, :]
        b = grid[i1][:, i0] * (1 - f)[None, :] + grid[i1][:, i1] * f[None, :]
        acc += amp * (a * (1 - f)[:, None] + b * f[:, None])
        total += amp
        amp *= 0.5
    return acc / total


def texture(name):
    """The shared detail textures (grey, tinted by each material's colour)."""
    if name in _textures:
        return _textures[name]
    size = 256
    rng = np.random.default_rng({'clad': 11, 'concrete': 23}[name])
    if name == 'clad':
        # Trapezoidal profiled sheet: ribs every 16 px (0.25 m at 4 m a tile),
        # streaks running down, a sheet joint every 64 px.
        u = np.arange(size)
        rib = np.clip(np.abs(((u % 16) - 8) / 8.0) * 1.6 - 0.3, 0, 1)
        shade = 0.84 + 0.16 * rib
        shade = shade - 0.08 * ((u % 64) < 1)
        streak = _noise(rng, size, 4)
        streak = 0.5 * streak + 0.5 * np.repeat(streak.mean(axis=0, keepdims=True), size, axis=0)
        img = shade[None, :] * (0.9 + 0.12 * streak)
    else:
        # Concrete: blotches and pores, form-tie joints on a 1.2 m grid (8 m a tile).
        n = _noise(rng, size, 5)
        pores = rng.random((size, size)) < 0.015
        img = 0.82 + 0.2 * n - 0.1 * pores
        joint = (np.arange(size) % 38) < 1
        img = img - 0.06 * joint[None, :] - 0.06 * joint[:, None]
    img = np.clip(img, 0, 1)
    rgba = np.ones((size, size, 4), dtype=np.float32)
    for c in range(3):
        rgba[:, :, c] = img
    image = bpy.data.images.new('tex_' + name, size, size, alpha=False)
    image.colorspace_settings.name = 'sRGB'
    image.pixels.foreach_set(rgba.ravel())
    image.pack()
    _textures[name] = image
    return image


_materials = {}


def material(name):
    if name in _materials:
        return _materials[name]
    base, metal, rough, extra = MATERIALS[name]
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    m.use_backface_culling = not extra.get('double', False)
    nt = m.node_tree
    bsdf = nt.nodes['Principled BSDF']
    col = lin(base) + (1.0,)
    bsdf.inputs['Metallic'].default_value = metal
    bsdf.inputs['Roughness'].default_value = rough
    if 'texture' in extra:
        tex = nt.nodes.new('ShaderNodeTexImage')
        tex.image = texture(extra['texture'])
        tex.interpolation = 'Linear'
        mix = nt.nodes.new('ShaderNodeMix')
        mix.data_type = 'RGBA'
        mix.blend_type = 'MULTIPLY'
        mix.inputs['Factor'].default_value = 1.0
        mix.inputs[7].default_value = col          # B (colour)
        nt.links.new(tex.outputs['Color'], mix.inputs[6])   # A (colour)
        nt.links.new(mix.outputs[2], bsdf.inputs['Base Color'])
    else:
        bsdf.inputs['Base Color'].default_value = col
    if 'emissive' in extra:
        bsdf.inputs['Emission Color'].default_value = lin(extra['emissive']) + (1.0,)
        bsdf.inputs['Emission Strength'].default_value = extra['strength']
    _materials[name] = m
    return m


# -- geometry helpers ----------------------------------------------------------

def frame(p0, p1, up=(0, 0, 1)):
    """A matrix whose local Z runs from p0 towards p1 (origin p0); local Y is
    as close to `up` as possible."""
    p0 = V(p0)
    z = V(p1) - p0
    length = z.length
    z.normalize()
    u = V(up)
    if abs(z.dot(u.normalized())) > 0.999:
        u = V((1, 0, 0)) if abs(z.x) < 0.9 else V((0, 1, 0))
    x = u.cross(z).normalized()
    y = z.cross(x)
    m = Matrix((
        (x.x, y.x, z.x, p0.x),
        (x.y, y.y, z.y, p0.y),
        (x.z, y.z, z.z, p0.z),
        (0, 0, 0, 1)))
    return m, length


def rot_z(angle, origin=(0, 0, 0)):
    o = Matrix.Translation(V(origin))
    return o @ Matrix.Rotation(angle, 4, 'Z') @ o.inverted()


class Part:
    """One mesh node: geometry in the model's coordinates, a pivot."""

    def __init__(self, name, pivot=(0, 0, 0), parent=None, smooth_deg=46.0, uv=False, rot=None):
        self.name = name
        self.pivot = V(pivot)
        self.rot = rot if rot is not None else Matrix.Identity(4)
        self.parent = parent
        self.smooth = math.radians(smooth_deg)
        self.uv = uv
        self.bm = bmesh.new()
        self.mats = []

    def mi(self, mat):
        if mat not in self.mats:
            self.mats.append(mat)
        return self.mats.index(mat)

    def _add(self, cos, faces, mat, m=None, mats=None, recalc=True):
        if m is not None:
            cos = [m @ V(c) for c in cos]
        vs = [self.bm.verts.new(c) for c in cos]
        out = []
        for k, f in enumerate(faces):
            face = self.bm.faces.new([vs[i] for i in f])
            face.material_index = self.mi(mats[k] if mats else mat)
            out.append(face)
        if recalc and out:
            bmesh.ops.recalc_face_normals(self.bm, faces=out)
        return vs, out

    def _bevel(self, vs, edges_idx, amount, segments=1):
        if amount <= 0:
            return
        edges = []
        for a, b in edges_idx:
            e = self.bm.edges.get((vs[a], vs[b]))
            if e is not None:
                edges.append(e)
        bmesh.ops.bevel(self.bm, geom=edges, offset=amount, offset_type='OFFSET', segments=segments,
                        profile=0.5, affect='EDGES', clamp_overlap=True)

    # A box given by its centre and size; `bevel` chamfers its edges
    # ('all', 'vt': the vertical and the top ones, 'v': vertical only).
    def box(self, c, s, mat, bevel=0.0, edges='all', m=None, segments=1, skip=()):
        cx, cy, cz = c
        hx, hy, hz = s[0] / 2, s[1] / 2, s[2] / 2
        cos = [(cx - hx, cy - hy, cz - hz), (cx + hx, cy - hy, cz - hz), (cx + hx, cy + hy, cz - hz), (cx - hx, cy + hy, cz - hz),
               (cx - hx, cy - hy, cz + hz), (cx + hx, cy - hy, cz + hz), (cx + hx, cy + hy, cz + hz), (cx - hx, cy + hy, cz + hz)]
        faces = {'bottom': (0, 3, 2, 1), 'top': (4, 5, 6, 7), '-y': (0, 1, 5, 4), '+x': (1, 2, 6, 5), '+y': (2, 3, 7, 6), '-x': (3, 0, 4, 7)}
        fl = [f for k, f in faces.items() if k not in skip]
        vs, out = self._add(cos, fl, mat, m, recalc=not skip)
        if bevel and not skip:
            vert = [(0, 4), (1, 5), (2, 6), (3, 7)]
            top = [(4, 5), (5, 6), (6, 7), (7, 4)]
            bot = [(0, 1), (1, 2), (2, 3), (3, 0)]
            sel = {'all': vert + top + bot, 'vt': vert + top, 'v': vert, 't': top}[edges]
            self._bevel(vs, sel, bevel, segments)
        return out

    def boxz(self, x, y, z0, sx, sy, sz, mat, **kw):
        """A box standing on z0."""
        return self.box((x, y, z0 + sz / 2), (sx, sy, sz), mat, **kw)

    # A cylinder or a frustum from p0 to p1.
    def cyl(self, p0, p1, r0, mat, r1=None, n=12, caps=(True, True), capmat=None, up=(0, 0, 1), phase=0.0):
        r1 = r0 if r1 is None else r1
        m, length = frame(p0, p1, up)
        cos = []
        for i in range(n):
            a = phase + 2 * math.pi * i / n
            cos.append((math.cos(a) * r0, math.sin(a) * r0, 0))
        for i in range(n):
            a = phase + 2 * math.pi * i / n
            cos.append((math.cos(a) * r1, math.sin(a) * r1, length))
        faces = [(i, (i + 1) % n, n + (i + 1) % n, n + i) for i in range(n)]
        mats = [mat] * n
        if caps[0]:
            faces.append(tuple(reversed(range(n))))
            mats.append(capmat or mat)
        if caps[1]:
            faces.append(tuple(range(n, 2 * n)))
            mats.append(capmat or mat)
        return self._add(cos, faces, mat, m, mats, recalc=caps[0] and caps[1])[1]

    # A surface of revolution about the local Z of `m`: profile [(r, z)],
    # one material per profile segment (or one for all).
    def lathe(self, profile, mat, n=16, m=None, phase=0.0, close=True):
        mats_seg = mat if isinstance(mat, (list, tuple)) else [mat] * (len(profile) - 1)
        cos = []
        rings = []
        for r, z in profile:
            if r <= 1e-6:
                rings.append([len(cos)])
                cos.append((0, 0, z))
            else:
                ring = []
                for i in range(n):
                    a = phase + 2 * math.pi * i / n
                    ring.append(len(cos))
                    cos.append((math.cos(a) * r, math.sin(a) * r, z))
                rings.append(ring)
        faces = []
        mats = []
        for k in range(len(rings) - 1):
            ra, rb = rings[k], rings[k + 1]
            for i in range(n):
                j = (i + 1) % n
                if len(ra) == 1 and len(rb) == 1:
                    continue
                if len(ra) == 1:
                    faces.append((ra[0], rb[j], rb[i]))
                elif len(rb) == 1:
                    faces.append((ra[i], ra[j], rb[0]))
                else:
                    faces.append((ra[i], ra[j], rb[j], rb[i]))
                mats.append(mats_seg[k])
        return self._add(cos, faces, None, m, mats, recalc=close)[1]

    # A prism: a polygon [(u, v)] in the plane across `axis`, from a0 to a1.
    def prism(self, poly, axis, a0, a1, mat, capmat=None, bevel=0.0, m=None):
        def p(u, v, a):
            return {'x': (a, u, v), 'y': (u, a, v), 'z': (u, v, a)}[axis]
        n = len(poly)
        cos = [p(u, v, a0) for u, v in poly] + [p(u, v, a1) for u, v in poly]
        faces = [(i, (i + 1) % n, n + (i + 1) % n, n + i) for i in range(n)]
        faces.append(tuple(reversed(range(n))))
        faces.append(tuple(range(n, 2 * n)))
        mats = [mat] * n + [capmat or mat] * 2
        vs, out = self._add(cos, faces, mat, m, mats)
        if bevel:
            self._bevel(vs, [(i, (i + 1) % n) for i in range(n)] + [(n + i, n + (i + 1) % n) for i in range(n)], bevel)
        return out

    # A rectangular member from p0 to p1, w wide and h deep (h along `up`),
    # tapering to w1 x h1.
    def beam(self, p0, p1, w, h, mat, w1=None, h1=None, up=(0, 0, 1), caps=True, bevel=0.0):
        w1 = w if w1 is None else w1
        h1 = h if h1 is None else h1
        m, length = frame(p0, p1, up)
        cos = [(-w / 2, -h / 2, 0), (w / 2, -h / 2, 0), (w / 2, h / 2, 0), (-w / 2, h / 2, 0),
               (-w1 / 2, -h1 / 2, length), (w1 / 2, -h1 / 2, length), (w1 / 2, h1 / 2, length), (-w1 / 2, h1 / 2, length)]
        faces = [(0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)]
        if caps:
            faces += [(3, 2, 1, 0), (4, 5, 6, 7)]
        vs, out = self._add(cos, faces, mat, m, recalc=caps)
        if bevel and caps:
            self._bevel(vs, [(0, 4), (1, 5), (2, 6), (3, 7)], bevel)
        return out

    # A thin bar (handrail, brace): a square tube without ends.
    def bar(self, p0, p1, t, mat):
        return self.beam(p0, p1, t, t, mat, caps=False, up=(0, 0, 1) if abs(V(p1).z - V(p0).z) < 0.9 * (V(p1) - V(p0)).length else (1, 0, 0))

    # A loft through rectangles: sections [(centre, w, h)] along a path, with `up`.
    def loft(self, sections, mat, up=(0, 0, 1), caps=True, bevel=0.0):
        cos = []
        n = len(sections)
        for k, (c, w, h) in enumerate(sections):
            a = V(sections[max(k - 1, 0)][0])
            b = V(sections[min(k + 1, n - 1)][0])
            m, _ = frame(a, b, up)
            x = m.col[0].xyz
            y = m.col[1].xyz
            c = V(c)
            for sx, sy in ((-1, -1), (1, -1), (1, 1), (-1, 1)):
                cos.append(c + x * sx * w / 2 + y * sy * h / 2)
        faces = []
        for k in range(n - 1):
            o = 4 * k
            for i in range(4):
                j = (i + 1) % 4
                faces.append((o + i, o + j, o + 4 + j, o + 4 + i))
        if caps:
            faces.append((3, 2, 1, 0))
            o = 4 * (n - 1)
            faces.append((o, o + 1, o + 2, o + 3))
        vs, out = self._add(cos, faces, mat, recalc=caps)
        if bevel:
            self._bevel(vs, [(4 * k + i, 4 * (k + 1) + i) for k in range(n - 1) for i in range(4)], bevel)
        return out

    def face(self, pts, mat, m=None, normal=None):
        """One polygon; with `normal`, its winding is turned to face that way."""
        if normal is not None:
            pv = [V(p) for p in pts]
            nn = V((0, 0, 0))
            for i in range(len(pv)):
                a, b = pv[i], pv[(i + 1) % len(pv)]
                nn += V(((a.y - b.y) * (a.z + b.z), (a.z - b.z) * (a.x + b.x), (a.x - b.x) * (a.y + b.y)))
            if nn.dot(V(normal)) < 0:
                pts = list(reversed(pts))
        return self._add(pts, [tuple(range(len(pts)))], mat, m, recalc=False)[1]

    # A handrail along a path [(x, y)] at height z: posts, top and knee rails.
    def rail(self, path, z, mat, h=1.05, post=1.6, t=0.06):
        for i in range(1, len(path)):
            (x0, y0), (x1, y1) = path[i - 1], path[i]
            self.bar((x0, y0, z + h), (x1, y1, z + h), t, mat)
            self.bar((x0, y0, z + h * 0.5), (x1, y1, z + h * 0.5), t * 0.8, mat)
            n = max(1, round(math.hypot(x1 - x0, y1 - y0) / post))
            for k in range(0 if i == 1 else 1, n + 1):
                f = k / n
                x, y = x0 + (x1 - x0) * f, y0 + (y1 - y0) * f
                self.bar((x, y, z), (x, y, z + h), t, mat)

    # A stair from p (bottom) to q (top), w wide: stringers, treads, a handrail.
    def stair(self, p, q, w, mat, tread='grating', rails=(True, True)):
        p, q = V(p), V(q)
        d = q - p
        side = V((-d.y, d.x, 0)).normalized() * (w / 2)
        for s in (1, -1):
            self.beam(p + side * s, q + side * s, 0.06, 0.22, mat, up=(0, 0, 1))
        steps = max(2, round(d.z / 0.28))
        for i in range(1, steps):
            c = p + d * (i / steps)
            self.beam(c - side, c + side, 0.26, 0.04, tread)
        for k, s in enumerate((1, -1)):
            if rails[k]:
                self.bar(p + side * s * 1.02 + V((0, 0, 1.0)), q + side * s * 1.02 + V((0, 0, 1.0)), 0.05, mat)
                self.bar(p + side * s * 1.02, p + side * s * 1.02 + V((0, 0, 1.0)), 0.05, mat)
                self.bar(q + side * s * 1.02, q + side * s * 1.02 + V((0, 0, 1.0)), 0.05, mat)

    # An operator's cab: a box whose upper part is glazed on all sides with
    # pillars left at the corners; a roof with a visor. Centre (x, y), floor z0.
    def cab(self, x, y, z0, sx, sy, sz, mat, glass_from=0.4, roofmat=None, bevel=0.08, front=1):
        zg = z0 + sz * glass_from
        self.boxz(x, y, z0, sx, sy, zg - z0, mat, bevel=bevel * 0.5, edges='v')
        top = z0 + sz - 0.16
        out = self.boxz(x, y, zg, sx, sy, top - zg, mat, bevel=0.0)
        sides = [f for f in out if abs(f.normal.z) < 0.5]
        res = bmesh.ops.inset_individual(self.bm, faces=sides, thickness=0.11, depth=-0.03)
        for f in sides:
            f.material_index = self.mi('glass')
        # a sun visor over the front glass and the roof
        self.boxz(x, y + front * 0.06, top, sx + 0.12, sy + 0.24, 0.16, roofmat or mat, bevel=0.05)
        return res

    def world(self):
        return Matrix.Translation(self.pivot) @ self.rot

    def finish(self, collection, dirt=None, objects=None):
        """Make the Blender object: smooth shading split at sharp edges and
        material changes, grime as vertex colour, UVs, the pivot."""
        bm = self.bm
        for f in bm.faces:
            f.smooth = True
        for e in bm.edges:
            lf = e.link_faces
            if len(lf) != 2:
                e.smooth = False
            elif lf[0].material_index != lf[1].material_index:
                e.smooth = False
            else:
                try:
                    e.smooth = e.calc_face_angle() <= self.smooth
                except ValueError:
                    e.smooth = False
        bm.normal_update()
        me = bpy.data.meshes.new(self.name)
        bm.to_mesh(me)
        bm.free()
        for name in self.mats:
            me.materials.append(material(name))
        # grime: low parts dusty-brown, upward faces with a light dust
        col = me.color_attributes.new('Col', 'FLOAT_COLOR', 'POINT')
        clean = [True] * len(me.vertices)
        for poly in me.polygons:
            if self.mats[poly.material_index] not in CLEAN:
                for vi in poly.vertices:
                    clean[vi] = False
        values = []
        for i, v in enumerate(me.vertices):
            if clean[i] or dirt is None:
                values.extend((1.0, 1.0, 1.0, 1.0))
            else:
                values.extend(dirt(v.co, v.normal) + (1.0,))
        col.data.foreach_set('color', values)
        me.color_attributes.active_color = col
        me.color_attributes.render_color_index = 0
        if self.uv:
            uvl = me.uv_layers.new(name='UVMap')
            for poly in me.polygons:
                n = poly.normal
                ax = max(range(3), key=lambda k: abs(n[k]))
                for li in poly.loop_indices:
                    co = me.vertices[me.loops[li].vertex_index].co
                    if ax == 0:
                        uv = (co.y * (1 if n.x > 0 else -1), co.z)
                    elif ax == 1:
                        uv = (co.x * (-1 if n.y > 0 else 1), co.z)
                    else:
                        uv = (co.x, co.y)
                    uvl.data[li].uv = (uv[0] / 4.0, uv[1] / 4.0)
        me.transform(self.world().inverted())
        ob = bpy.data.objects.new(self.name, me)
        collection.objects.link(ob)
        wn = ob.modifiers.new('weighted', 'WEIGHTED_NORMAL')
        wn.mode = 'FACE_AREA'
        wn.weight = 50
        wn.keep_sharp = True
        return ob


class Model:
    """A model: a root node and its parts (with parents and pivots)."""

    def __init__(self, name, dirt=None):
        self.name = name
        self.parts = []
        self.pins = []
        self.dirt = dirt
        self.info = {}

    def part(self, name, pivot=(0, 0, 0), parent=None, **kw):
        p = Part(name, pivot, parent, **kw)
        self.parts.append(p)
        return p

    def pin(self, name, pos, parent=None):
        """An empty node: a point on a moving part that a cylinder aims at."""
        self.pins.append((name, V(pos), parent))

    def build(self):
        for ob in list(bpy.data.objects):
            bpy.data.objects.remove(ob)
        scene = bpy.context.scene
        coll = scene.collection
        root = bpy.data.objects.new(self.name, None)
        coll.objects.link(root)
        obs = {}
        worlds = {}
        for p in self.parts:
            ob = p.finish(coll, self.dirt)
            obs[p.name] = ob
            worlds[p.name] = p.world()
        for p in self.parts:
            ob = obs[p.name]
            ob.parent = obs[p.parent] if p.parent else root
            parent_w = worlds[p.parent] if p.parent else Matrix.Identity(4)
            ob.matrix_parent_inverse = Matrix.Identity(4)
            ob.matrix_basis = parent_w.inverted() @ worlds[p.name]
        for name, pos, parent in self.pins:
            e = bpy.data.objects.new(name, None)
            coll.objects.link(e)
            e.parent = obs[parent] if parent else root
            parent_w = worlds[parent] if parent else Matrix.Identity(4)
            e.matrix_parent_inverse = Matrix.Identity(4)
            e.matrix_basis = parent_w.inverted() @ Matrix.Translation(pos)
        self.objects = obs
        self.root = root
        return obs


def triangles(ob):
    deps = bpy.context.evaluated_depsgraph_get()
    ev = ob.evaluated_get(deps)
    me = ev.to_mesh()
    me.calc_loop_triangles()
    n = len(me.loop_triangles)
    ev.to_mesh_clear()
    return n


def export(path):
    bpy.ops.export_scene.gltf(
        filepath=path,
        export_format='GLB',
        use_selection=False,
        export_apply=True,
        export_yup=True,
        export_texcoords=True,
        export_normals=True,
        export_tangents=False,
        export_vertex_color='ACTIVE',
        export_all_vertex_colors=False,
        export_materials='EXPORT',
        export_image_format='JPEG',
        export_jpeg_quality=85,
        export_cameras=False,
        export_lights=False,
        export_extras=False,
        export_animations=False,
        export_skins=False,
        export_morph=False,
        export_draco_mesh_compression_enable=True,
        export_draco_mesh_compression_level=7,
        export_draco_position_quantization=14,
        export_draco_normal_quantization=10,
        export_draco_texcoord_quantization=12,
        export_draco_color_quantization=8,
        export_draco_generic_quantization=12,
    )


# -- grime ----------------------------------------------------------------------

def _hash_noise(co):
    x, y, z = co
    return 0.5 + 0.25 * (math.sin(x * 1.73 + y * 2.31 + z * 0.87) * math.sin(x * 0.61 - y * 1.13 + z * 2.07)
                         + math.sin(x * 4.1 - z * 3.3 + y * 0.7) * 0.6)


def grime(ground=2.0, amount=0.55, dust=0.25, mud='#7a6248', dust_col='#c9bca6'):
    """Grime for vertex colours: mud splashed up to `ground` metres, light
    dust on faces that look up, a little noise everywhere."""
    mud_c = lin(mud)
    dust_c = lin(dust_col)

    def f(co, normal):
        n = _hash_noise(co)
        low = max(0.0, min(1.0, 1.0 - co.z / ground)) ** 1.5
        k = min(1.0, amount * low * (0.6 + 0.8 * n))
        up = max(0.0, normal.z) ** 2 * dust * (0.5 + n)
        out = []
        for c in range(3):
            v = 1.0 * (1 - k) + mud_c[c] * 1.6 * k
            v = v * (1 - up) + dust_c[c] * 1.4 * up
            v *= 0.94 + 0.08 * n
            out.append(max(0.0, min(1.0, v)))
        return tuple(out)
    return f
