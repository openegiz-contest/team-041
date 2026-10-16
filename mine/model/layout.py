"""Geometry of the pit, built from the short description in world.yaml.

The pit is a bowl of benches cut into the ground below an elliptical rim.
Every bench is `drill.bench_height_m` high; its face stands at
`pit.face_angle_deg` and is followed, going down, by the bench floor: a berm
of `pit.berm_m` on a final wall, a wide working floor where a pushback is
under way. Each bench's toe line is the rim offset inwards (a parallel
offset along the rim's normal) by the horizontal run of all faces and floors
above it, so the wall angle is the same all round wherever no pushback
widens it.

Ramps run down along the walls at a constant grade. A ramp is given by the
angle on the rim where it leaves the pit and the angles where it turns back
(switchbacks); its centreline at each elevation follows the wall there.
Road nodes are placed on a ramp by elevation, on the floor or on the surface
by coordinates; the path of a segment along a ramp is the ramp's centreline
between its two nodes.

Blast blocks sit on the working floor of their bench: rows across the floor
(the row next to the working strip is mined first) and columns along the
bench, between the toe of the face above and the strip where the loader and
the trucks stand.

    python -m mine.model.layout          # benches, ramps and their lengths and grades
"""
from __future__ import annotations

import math

RAMP_STEP_DEG = 0.05            # angular step of the ramp walk
PATH_STEP_M = 80.0              # longest straight piece kept on a road path
PATH_TOLERANCE_M = 0.5          # how far a kept path may stray from the ramp's centreline
RING_POINTS = 120               # points of a bench outline


class Rim:
    """The top edge of the pit, in plan: an ellipse (`{ellipse: {a, b}}`, half-axes) or a stadium
    (`{stadium: {length, width}}`: two half-circles of the half-width joined by straight walls, the shape of a
    long narrow pit). A point on it is given by an angle t, counted counter-clockwise from the east end; on a
    stadium t runs evenly along the perimeter (t = 90° is the middle of the north wall). `at` returns the point
    and the outward unit normal there. A stadium's walls can be set back by up to its half-width without the
    bench lines folding over, which an ellipse as long and narrow does not allow at its ends."""

    def __init__(self, spec: dict):
        if "stadium" in spec:
            self.kind = "stadium"
            length, width = float(spec["stadium"]["length"]), float(spec["stadium"]["width"])
            self.r = width / 2.0
            self.h = (length - width) / 2.0              # half the length of the straight walls
            self.a, self.b = length / 2.0, width / 2.0
            self.perimeter = 4.0 * self.h + 2.0 * math.pi * self.r
        else:
            self.kind = "ellipse"
            self.a, self.b = float(spec["ellipse"]["a"]), float(spec["ellipse"]["b"])

    @property
    def length_m(self) -> float:
        return 2.0 * self.a

    @property
    def width_m(self) -> float:
        return 2.0 * self.b

    def at(self, t: float) -> tuple[float, float, float, float]:
        if self.kind == "ellipse":
            px, py = self.a * math.cos(t), self.b * math.sin(t)
            nx, ny = self.b * math.cos(t), self.a * math.sin(t)
            n = math.hypot(nx, ny)
            return px, py, nx / n, ny / n
        h, r = self.h, self.r
        s = (t % (2.0 * math.pi)) / (2.0 * math.pi) * self.perimeter
        quarter = math.pi * r / 2.0
        for length, piece in ((quarter, "east1"), (2.0 * h, "north"), (2.0 * quarter, "west"),
                              (2.0 * h, "south"), (quarter, "east2")):
            if s <= length + 1e-9:
                break
            s -= length
        if piece == "east1":
            phi = s / r
            return h + r * math.cos(phi), r * math.sin(phi), math.cos(phi), math.sin(phi)
        if piece == "north":
            return h - s, r, 0.0, 1.0
        if piece == "west":
            phi = math.pi / 2.0 + s / r
            return -h + r * math.cos(phi), r * math.sin(phi), math.cos(phi), math.sin(phi)
        if piece == "south":
            return -h + s, -r, 0.0, -1.0
        phi = 1.5 * math.pi + s / r
        return h + r * math.cos(phi), r * math.sin(phi), math.cos(phi), math.sin(phi)

    def polygon(self, centre: list, points: int = 72) -> list[list[float]]:
        out = []
        for i in range(points):
            px, py, _, _ = self.at(2.0 * math.pi * i / points)
            out.append([round(centre[0] + px), round(centre[1] + py)])
        return out


class Wall:
    """The pit wall: bench toe lines as functions of the angle on the rim."""

    def __init__(self, pit: dict, params: dict):
        self.cx, self.cy = pit["centre"]
        self.rim = Rim(pit["rim"])
        self.a, self.b = self.rim.a, self.rim.b
        self.rim_z, self.floor_z = float(pit["rim_z"]), float(pit["floor_z"])
        p = params["pit"]
        self.h = float(params["drill"]["bench_height_m"])
        self.face_run = self.h / math.tan(math.radians(p["face_angle_deg"]))
        self.berm = float(p["berm_m"])
        self.count = round((self.rim_z - self.floor_z) / self.h)
        if abs(self.rim_z - self.count * self.h - self.floor_z) > 1e-6:
            raise ValueError("pit: rim and floor must be a whole number of benches apart")
        # Working floors: bench number -> (centre deg, core half-width deg, taper deg, extra width m)
        self.extras: dict[int, tuple[float, float, float, float]] = {}
        for bench in pit.get("working", []):
            k = self.index(bench["elevation_m"])
            s = bench["sector"]
            self.extras[k] = (float(s["centre_deg"]), float(s["core_deg"]), float(s["taper_deg"]), float(s["width_m"]))

    # -- benches -------------------------------------------------------------------

    def index(self, z: float) -> int:
        """Bench number (1 at the top) of the bench whose floor is at elevation z."""
        k = (self.rim_z - z) / self.h
        if abs(k - round(k)) > 1e-6 or not 1 <= round(k) <= self.count:
            raise ValueError(f"no bench floor at {z} m")
        return round(k)

    def elevation(self, k: int) -> float:
        return self.rim_z - k * self.h

    def extra(self, k: int, t: float) -> float:
        if k not in self.extras:
            return 0.0
        centre, core, taper, width = self.extras[k]
        d = abs((math.degrees(t) - centre + 180.0) % 360.0 - 180.0)
        if d <= core:
            return width
        if d >= core + taper:
            return 0.0
        return width * math.cos(math.pi / 2 * (d - core) / taper) ** 2

    def platform(self, k: int, t: float) -> float:
        """Width of the floor of bench k at angle t: the berm, wider where a pushback works."""
        return self.berm + self.extra(k, t)

    def toe(self, k: int, t: float) -> float:
        """Horizontal distance in from the rim of the toe of bench k's face."""
        return self.face_run * k + sum(self.platform(j, t) for j in range(1, k))

    def inset(self, z: float, t: float) -> float:
        """The wall line at elevation z: between the toes of the benches above and below."""
        if z >= self.rim_z:
            return 0.0
        k = (self.rim_z - z) / self.h
        k0 = int(k)
        if k0 >= self.count:
            return self.toe(self.count, t)
        f = k - k0
        return self.toe(k0, t) * (1 - f) + self.toe(k0 + 1, t) * f

    # -- plan coordinates ------------------------------------------------------------

    def point(self, t: float, inset: float) -> tuple[float, float]:
        """The point `inset` metres in from the rim along the rim's normal at angle t."""
        px, py, nx, ny = self.rim.at(t)
        return self.cx + px - inset * nx, self.cy + py - inset * ny

    def ring(self, inset_of, points: int = RING_POINTS) -> list[list[float]]:
        out = []
        for i in range(points):
            t = 2 * math.pi * i / points
            x, y = self.point(t, inset_of(t))
            out.append([round(x, 1), round(y, 1)])
        return out

    def toe_ring(self, k: int) -> list[list[float]]:
        return self.ring(lambda t: self.toe(k, t))

    def crest_ring(self, k: int) -> list[list[float]]:
        """Inner edge of bench k's floor: the crest of the face below it."""
        return self.ring(lambda t: self.toe(k, t) + self.platform(k, t))

    def angle_along(self, k: int, t0: float, metres: float, inset: float = 0.0) -> float:
        """The angle reached from t0 after `metres` along bench k's toe (offset `inset` further in);
        positive metres run counter-clockwise."""
        step = math.radians(0.02) * (1 if metres >= 0 else -1)
        t, done = t0, 0.0
        x0, y0 = self.point(t, self.toe(k, t) + inset)
        while done < abs(metres):
            t += step
            x, y = self.point(t, self.toe(k, t) + inset)
            done += math.hypot(x - x0, y - y0)
            x0, y0 = x, y
        return t


class Ramp:
    """A ramp's centreline: from its exit on the rim down to the floor."""

    def __init__(self, wall: Wall, spec: dict):
        self.id, self.name = spec["id"], spec["name"]
        self.grade = float(spec["grade_pct"]) / 100.0
        t = math.radians(spec["exit_deg"])
        z = wall.rim_z
        x, y = wall.point(t, 0.0)
        pts = [(x, y, z)]
        self.turns: list[tuple[float, float, float]] = []
        self.turn_index: list[int] = []                 # where in `points` the switchbacks are
        for end in spec["legs_deg"]:
            sign = 1 if end > math.degrees(t) else -1
            while (math.degrees(t) - end) * sign < 0 and z > wall.floor_z:
                t += math.radians(RAMP_STEP_DEG) * sign
                x0, y0, z0 = pts[-1]
                # The new point sits on the wall at its own elevation, which depends on the step it takes:
                # a few rounds of z = z0 - grade x (horizontal step to the wall at z) settle it.
                z = z0
                for _ in range(4):
                    x, y = wall.point(t, wall.inset(z, t))
                    z = max(wall.floor_z, z0 - self.grade * math.hypot(x - x0, y - y0))
                x, y = wall.point(t, wall.inset(z, t))
                pts.append((x, y, z))
            if z <= wall.floor_z:
                break
            self.turns.append(pts[-1])
            self.turn_index.append(len(pts) - 1)
        if z > wall.floor_z + 1e-6:
            raise ValueError(f"ramp {self.id} ends at {z:.1f} m, above the floor: add a leg")
        self.points = pts

    def at(self, z: float) -> tuple[int, tuple[float, float, float]]:
        """Index and point where the centreline reaches elevation z (going down)."""
        for i in range(1, len(self.points)):
            if self.points[i][2] <= z + 1e-9:
                (x0, y0, z0), (x1, y1, z1) = self.points[i - 1], self.points[i]
                f = (z0 - z) / (z0 - z1) if z0 != z1 else 1.0
                return i, (x0 + (x1 - x0) * f, y0 + (y1 - y0) * f, z)
        raise ValueError(f"ramp {self.id} does not reach {z} m")

    def path(self, z_from: float, z_to: float) -> list[list[float]]:
        """The centreline between two elevations, in the direction from -> to, thinned to about PATH_STEP_M."""
        top, bottom = max(z_from, z_to), min(z_from, z_to)
        i, start = self.at(top)
        j, end = self.at(bottom)
        middle = self.points[i:j]
        pts = [start, *middle, end]
        # Keep the shape: the switchbacks, and wherever the centreline bends away from a straight chord by more
        # than PATH_TOLERANCE_M (Douglas-Peucker); no two kept points further apart than PATH_STEP_M.
        keep = {0, len(pts) - 1} | {k - i + 1 for k in self.turn_index if i <= k < j}
        stack = sorted(keep)
        segments = list(zip(stack, stack[1:]))
        while segments:
            a, b = segments.pop()
            if b - a < 2:
                continue
            (xa, ya, _), (xb, yb, _) = pts[a], pts[b]
            dx, dy = xb - xa, yb - ya
            span = math.hypot(dx, dy) or 1.0
            far, worst = None, PATH_TOLERANCE_M
            for k in range(a + 1, b):
                off = abs((pts[k][0] - xa) * dy - (pts[k][1] - ya) * dx) / span
                if off > worst:
                    far, worst = k, off
            if far is None and span > PATH_STEP_M:
                far = (a + b) // 2
            if far is not None:
                keep.add(far)
                segments += [(a, far), (far, b)]
        out = [[round(pts[k][0], 1), round(pts[k][1], 1), round(pts[k][2], 2)] for k in sorted(keep)]
        return out if z_from >= z_to else out[::-1]

    def length(self) -> float:
        return sum(math.dist(p, q) for p, q in zip(self.points, self.points[1:]))


def _cells(wall: Wall, bench: dict, k: int) -> list[dict]:
    """Blast blocks of a working bench: polygons on its floor, the row next to the working strip first."""
    g = bench["blocks"]
    along, across = g["cell_m"]
    columns, rows = g["columns"], g["rows"]
    gap = float(g.get("gap_m", 5.0))
    strip = float(g["strip_m"])                      # the working strip inside the blocks
    face_offset = float(g["face_offset_m"])
    t0 = math.radians(g["start_deg"])
    direction = 1 if g.get("direction", "ccw") == "ccw" else -1
    cells = []
    # Rows from the working strip outwards; the inner edge of the blocks is gap + rows x across in from the toe.
    for row in range(rows):
        d_inner = gap + (rows - row) * across
        d_outer = d_inner - across
        for column in range(columns):
            ta = wall.angle_along(k, t0, direction * column * along)
            tb = wall.angle_along(k, t0, direction * (column + 1) * along)
            tm = wall.angle_along(k, t0, direction * (column + 0.5) * along)
            corners = []
            for t, d in ((ta, d_outer), (tb, d_outer), (tb, d_inner), (ta, d_inner)):
                corners.append(wall.point(t, wall.toe(k, t) + d))
            # The outline follows the bench: two extra points on the long sides.
            outline = [corners[0], wall.point(tm, wall.toe(k, tm) + d_outer), corners[1],
                       corners[2], wall.point(tm, wall.toe(k, tm) + d_inner), corners[3]]
            cx, cy = wall.point(tm, wall.toe(k, tm) + (d_inner + d_outer) / 2)
            fx, fy = wall.point(tm, wall.toe(k, tm) + gap + rows * across + min(face_offset, strip / 2))
            cells.append({"x": round(cx, 1), "y": round(cy, 1),
                          "outline": [[round(x, 1), round(y, 1)] for x, y in outline],
                          "face": [round(fx, 1), round(fy, 1)]})
    return cells


def _floor_cells(bench: dict) -> list[dict]:
    """Blocks on the pit floor: a straight grid, the row next to the working strip first."""
    g = bench["blocks"]
    angle = math.radians(g["rotation_deg"])
    ux, uy = math.cos(angle), math.sin(angle)       # along the columns
    vx, vy = uy, -ux                                # across the rows, away from the working strip
    width, depth = g["cell_m"]
    cx, cy = g["centre"]
    cells = []
    order = -1 if g.get("reverse_columns") else 1     # which end of each row is mined first
    for row in range(g["rows"]):
        for column in range(g["columns"]):
            du = order * (column - (g["columns"] - 1) / 2) * width
            dv = (row - (g["rows"] - 1) / 2) * depth
            x, y = cx + ux * du + vx * dv, cy + uy * du + vy * dv
            corners = [(-width / 2, -depth / 2), (width / 2, -depth / 2), (width / 2, depth / 2), (-width / 2, depth / 2)]
            reach = depth / 2 + g["face_offset_m"]
            cells.append({"x": round(x, 1), "y": round(y, 1),
                          "outline": [[round(x + ux * a + vx * b, 1), round(y + uy * a + vy * b, 1)] for a, b in corners],
                          "face": [round(x - vx * reach, 1), round(y - vy * reach, 1)]})
    return cells


def expand(world: dict, params: dict) -> dict:
    """world.yaml with the generated geometry filled in:
    `benches` (every bench, top down, with outline and blocks), `road_nodes` with xyz,
    `road_segments` with their path, `trucks` from the fleet allocation, `ramps` with their centrelines."""
    pit = world["pit"]
    wall = Wall(pit, params)
    ramps = {r["id"]: Ramp(wall, r) for r in world["ramps"]}
    working = {b["elevation_m"]: b for b in pit.get("working", [])}

    benches = []
    for k in range(1, wall.count + 1):
        z = wall.elevation(k)
        spec = working.get(z, {})
        bench = {"id": f"bench-{k:02d}", "name": f"Уступ {z:.0f} м", "elevation_m": z, "number": k,
                 "outline": wall.toe_ring(k),
                 "crest": wall.crest_ring(k) if k < wall.count else None,
                 "working": bool(spec), "grade_trend": spec.get("grade_trend", 0.0),
                 "access_node": spec.get("access_node")}
        if spec:
            bench["name"] = spec.get("name", bench["name"])
            bench["blocks"] = spec["blocks"]
            bench["cells"] = _floor_cells(spec) if k == wall.count else _cells(wall, spec, k)
        benches.append(bench)

    nodes = []
    for n in world["road_nodes"]:
        n = dict(n)
        if "ramp" in n:
            _, (x, y, z) = ramps[n["ramp"]].at(float(n["z"]))
            n["xyz"] = [round(x, 1), round(y, 1), round(z, 2)]
        elif "floor" in n:
            n["xyz"] = [*n["floor"], wall.floor_z]
        nodes.append(n)
    xyz = {n["id"]: n["xyz"] for n in nodes}
    on_ramp = {n["id"]: n for n in world["road_nodes"] if "ramp" in n}

    segments = []
    for s in world["road_segments"]:
        s = dict(s)
        if "ramp" in s:
            a, b = on_ramp[s["from"]], on_ramp[s["to"]]
            if a["ramp"] != s["ramp"] or b["ramp"] != s["ramp"]:
                raise ValueError(f"{s['id']}: both ends must lie on ramp {s['ramp']}")
            s["path"] = ramps[s["ramp"]].path(float(a["z"]), float(b["z"]))
        else:
            s["path"] = [xyz[s["from"]], *s.get("via", []), xyz[s["to"]]]
        segments.append(s)

    out = dict(world)
    out["benches"] = benches
    out["road_nodes"] = nodes
    out["road_segments"] = segments
    out["trucks"] = trucks(world)
    # What the generated geometry comes to, for the pit twin and the checks.
    out["ramp_info"] = [{"id": r.id, "name": r.name, "grade_pct": round(100 * r.grade, 1), "length_m": round(r.length()),
                         "switchbacks": len(r.turns), "switchback_z": [round(z) for _, _, z in r.turns]}
                        for r in ramps.values()]
    out["wall_info"] = {"depth_m": wall.rim_z - wall.floor_z, "benches": wall.count, "face_run_m": round(wall.face_run, 1),
                         "berm_m": wall.berm,
                         "overall_angle_deg": round(math.degrees(math.atan(wall.h / (wall.face_run + wall.berm))), 1)}
    return out


def trucks(world: dict) -> list[dict]:
    """The haul trucks of the fleet allocation, with their starting state spread evenly so that
    services come due one after another and the trucks start spread over their loaders' loops."""
    fleet = world["fleet"]
    out, number = [], 0
    total = sum(fleet["allocation"].values())
    interval = float(fleet["service_spread_h"])
    for loader, count in fleet["allocation"].items():
        starts = fleet["start_nodes"][loader]
        for i in range(count):
            number += 1
            share = (number * 0.618034) % 1.0          # golden-ratio spread: no two trucks alike
            out.append({"id": f"truck-{number:02d}", "name": f"Самосвал №{number}", "loader": loader,
                        "start_node": starts[i % len(starts)],
                        "fuel_pct": round(30 + 65 * share),
                        "hours_since_service": round(interval * (number - 0.5) / total, 1)})
    return out


def main() -> None:
    from mine.model.build_twins import load_world
    world = load_world()
    w = world["wall_info"]
    print(f"depth {w['depth_m']:.0f} m in {w['benches']} benches; face run {w['face_run_m']} m + berm {w['berm_m']} m: "
          f"final wall {w['overall_angle_deg']} deg")
    for r in world["ramp_info"]:
        print(f"ramp {r['id']}: {r['length_m']} m at {r['grade_pct']} %, switchbacks at {r['switchback_z']} m")
    for b in world["benches"]:
        if b["working"]:
            print(f"{b['id']} {b['name']}: {len(b['cells'])} blocks")


if __name__ == "__main__":
    main()
