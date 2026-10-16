"""Haul-road graph: nodes joined by two-way segments, with shortest-time routing.

A segment can be closed for several reasons at once (a scenario, a blast);
it reopens when the last reason is withdrawn. Rain changes its surface: the
rolling resistance rises and the speed limit drops.
"""
from __future__ import annotations

import bisect
import heapq
import math
from dataclasses import dataclass, field
from typing import Callable

BLAST = "blast"       # closure reason used for the exclusion zone of a blast


@dataclass
class Segment:
    name: str
    title: str
    a: str                      # node at the start of `path`
    b: str                      # node at the end of `path`
    path: list[list[float]]     # [[x, y, z], ...] from a to b
    grade_pct: float            # mean grade from a to b, positive uphill
    road_class: str             # surface / ramp / bench
    dry_limit_kmh: float
    dry_rr_pct: float
    speed_limit_kmh: float = 0.0
    rr_pct: float = 0.0
    surface: str = "dry"
    closures: set = field(default_factory=set)
    cap_kmh: float = 0.0        # a limit on top of the surface's (a road repaired in a hurry, a treated wet ramp); 0: none
    length: float = 0.0
    _cumulative: list[float] = field(default_factory=list)
    _grades: list[float] = field(default_factory=list)      # grade of each piece of the path, a -> b

    def __post_init__(self):
        self.speed_limit_kmh, self.rr_pct = self.dry_limit_kmh, self.dry_rr_pct
        self._cumulative = [0.0]
        for p, q in zip(self.path, self.path[1:]):
            piece = math.dist(p, q)
            self._cumulative.append(self._cumulative[-1] + piece)
            run = math.sqrt(max(piece ** 2 - (q[2] - p[2]) ** 2, 1e-6))
            self._grades.append(100.0 * (q[2] - p[2]) / run)
        self.length = self._cumulative[-1]

    @property
    def status(self) -> str:
        if self.closures:
            return "closed"
        return "restricted" if self.speed_limit_kmh < self.dry_limit_kmh else "open"

    @property
    def closed_by(self) -> str:
        return ",".join(sorted(self.closures))

    def other(self, node: str) -> str:
        return self.b if node == self.a else self.a

    def grade_from(self, node: str) -> float:
        """Mean grade seen by a vehicle that entered the segment at `node`."""
        return self.grade_pct if node == self.a else -self.grade_pct

    def _piece(self, dist: float, from_node: str) -> tuple[int, float, bool]:
        # Called for every truck on the road every simulated second: plain comparisons instead of min/max.
        forward = from_node == self.a
        length, cumulative = self.length, self._cumulative
        d = dist if forward else length - dist
        if d < 0.0:
            d = 0.0
        if length < d:
            d = length
        # The first piece whose end lies at or beyond d (a ramp's path has dozens of pieces).
        i = bisect.bisect_left(cumulative, d)
        if i < 1:
            i = 1
        if i > len(cumulative) - 1:
            i = len(cumulative) - 1
        return i, d, forward

    def grade_at(self, dist: float, from_node: str) -> float:
        """Grade under a vehicle `dist` metres into the segment from `from_node`."""
        i, _, forward = self._piece(dist, from_node)
        return self._grades[i - 1] if forward else -self._grades[i - 1]

    def pieces_from(self, node: str) -> list[tuple[float, float]]:
        """(length, grade) of every straight piece in driving order from `node`."""
        lengths = [b - a for a, b in zip(self._cumulative, self._cumulative[1:])]
        if node == self.a:
            return list(zip(lengths, self._grades))
        return [(length, -grade) for length, grade in zip(reversed(lengths), reversed(self._grades))]

    def point_at(self, dist: float, from_node: str) -> tuple[float, float, float, float]:
        """Position and compass heading (0 = north, clockwise) after driving
        `dist` metres into the segment from `from_node`."""
        i, d, forward = self._piece(dist, from_node)
        p, q = self.path[i - 1], self.path[i]
        piece = self._cumulative[i] - self._cumulative[i - 1]
        f = (d - self._cumulative[i - 1]) / piece if piece else 0.0
        x, y, z = (p[k] + (q[k] - p[k]) * f for k in range(3))
        heading = math.degrees(math.atan2(q[0] - p[0], q[1] - p[1]))
        if not forward:
            heading += 180.0
        return x, y, z, heading % 360.0

    def distance_to(self, x: float, y: float) -> float:
        """Shortest distance in plan from the point to the segment's path."""
        best = math.inf
        for p, q in zip(self.path, self.path[1:]):
            dx, dy = q[0] - p[0], q[1] - p[1]
            f = ((x - p[0]) * dx + (y - p[1]) * dy) / ((dx * dx + dy * dy) or 1.0)
            f = min(max(f, 0.0), 1.0)
            best = min(best, math.hypot(x - (p[0] + dx * f), y - (p[1] + dy * f)))
        return best


class RoadNetwork:
    def __init__(self, nodes: dict[str, tuple[float, float, float]], segments: list[Segment], road: dict):
        self.nodes = nodes
        self.segments = {s.name: s for s in segments}
        self.road = road                 # params group `road`
        self.version = 0                 # grows whenever speeds or closures change
        self.at_node: dict[str, list[Segment]] = {n: [] for n in nodes}
        for s in segments:
            self.at_node[s.a].append(s)
            self.at_node[s.b].append(s)

    @classmethod
    def from_things(cls, things: list[dict], road: dict) -> "RoadNetwork":
        """Build the graph from the generated twins of kind road-node and road-segment."""
        short = lambda thing_id: thing_id.split(":", 1)[1]
        nodes, segments = {}, []
        for t in things:
            attrs, name = t["attributes"], short(t["thingId"])
            if attrs["kind"] == "road-node":
                nodes[name] = (attrs["geo"]["x"], attrs["geo"]["y"], attrs["geo"]["z"])
            elif attrs["kind"] == "road-segment":
                value = lambda feature: t["features"][feature]["properties"]["value"]
                segments.append(Segment(
                    name=name, title=attrs["name"],
                    a=short(attrs["links"]["from"]), b=short(attrs["links"]["to"]),
                    path=attrs["geo"]["path"], grade_pct=value("grade_pct"),
                    road_class=attrs["spec"]["road_class"],
                    dry_limit_kmh=value("speed_limit_kmh"), dry_rr_pct=value("rolling_resistance_pct")))
        return cls(nodes, segments, road)

    # -- state ---------------------------------------------------------------------

    def close(self, segment: Segment, reason: str) -> None:
        segment.closures.add(reason)
        self.version += 1

    def reopen(self, segment: Segment, reason: str) -> None:
        segment.closures.discard(reason)
        self.version += 1

    def set_surface(self, wet: bool) -> None:
        """Dry or wet roads: wet adds rolling resistance and lowers the speed limits."""
        for s in self.segments.values():
            s.surface = "wet" if wet else "dry"
            self._limit(s)
        self.version += 1

    def set_cap(self, segment: Segment, kmh: float) -> None:
        """A speed limit on top of the surface's (a road reopened after an emergency repair, a treated wet
        ramp); 0 lifts it."""
        segment.cap_kmh = float(kmh)
        self._limit(segment)
        self.version += 1

    def _limit(self, s: Segment) -> None:
        if s.surface == "wet":
            factor = self.road["wet_speed_factor_ramp" if s.road_class == "ramp" else "wet_speed_factor_flat"]
            s.rr_pct = s.dry_rr_pct + self.road["wet_rr_added_pct"]
            s.speed_limit_kmh = round(s.dry_limit_kmh * factor, 1)
        else:
            s.rr_pct, s.speed_limit_kmh = s.dry_rr_pct, s.dry_limit_kmh
        if s.cap_kmh > 0:
            s.speed_limit_kmh = min(s.speed_limit_kmh, s.cap_kmh)

    # -- routing -------------------------------------------------------------------

    def route(self, start: str, goal: str, cost: Callable[[Segment, str], float],
              ignore: frozenset = frozenset()) -> list[str] | None:
        """Fastest node sequence from start to goal (Dijkstra). `cost(segment,
        from_node)` is the travel time in s. A segment is usable when it has no
        closure reason outside `ignore`. Returns None when the goal cannot be reached."""
        best = {start: 0.0}
        previous: dict[str, str] = {}
        heap = [(0.0, start)]
        while heap:
            time_s, node = heapq.heappop(heap)
            if node == goal:
                route = [node]
                while node in previous:
                    node = previous[node]
                    route.append(node)
                return route[::-1]
            if time_s > best.get(node, math.inf):
                continue
            for seg in self.at_node[node]:
                if seg.closures and not seg.closures <= ignore:
                    continue
                nxt = seg.other(node)
                candidate = time_s + cost(seg, node)
                if candidate < best.get(nxt, math.inf):
                    best[nxt] = candidate
                    previous[nxt] = node
                    heapq.heappush(heap, (candidate, nxt))
        return None

    def segment_between(self, a: str, b: str) -> Segment:
        """The segment joining two neighbouring nodes; an open one if there is a choice."""
        found = None
        for seg in self.at_node[a]:
            if seg.other(a) == b:
                if not seg.closures:
                    return seg
                found = seg
        if found is None:
            raise KeyError(f"no segment between {a} and {b}")
        return found

    def connected(self, a: str, b: str) -> bool:
        return any(seg.other(a) == b for seg in self.at_node.get(a, []))

    def route_time(self, route: list[str], cost: Callable[[Segment, str], float]) -> float:
        return sum(cost(self.segment_between(a, b), a) for a, b in zip(route, route[1:]))

    def route_length(self, route: list[str]) -> float:
        return sum(self.segment_between(a, b).length for a, b in zip(route, route[1:]))

    def shortest_length(self, start: str, goal: str) -> float:
        """Road distance between two nodes, closures ignored (used for moving machines)."""
        route = self.route(start, goal, lambda seg, node: seg.length, ignore=frozenset({"*"}) | self._all_reasons())
        return self.route_length(route) if route else math.dist(self.nodes[start], self.nodes[goal])

    def _all_reasons(self) -> frozenset:
        return frozenset(reason for s in self.segments.values() for reason in s.closures)
