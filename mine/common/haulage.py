"""Travel times over the haul roads for a nominal truck.

Used wherever routes are compared: by the simulator when a truck chooses its
way, and by the dispatch module when it estimates how long a truck needs to
reach a loader or a dump point. Each user keeps its own road graph; this
module only turns the state of a graph into seconds.
"""
from __future__ import annotations

from typing import Callable

from mine.common import physics
from mine.common.roads import RoadNetwork, Segment


def mean_pace(truck: dict) -> float:
    """Drivers keep between `pace_min` and 100 % of the speed the road allows."""
    return (truck["pace_min"] + 1.0) / 2


class TravelTimes:
    """Seconds a nominal truck needs for each segment, empty and loaded. The
    table is rebuilt when the road graph reports a change of limits or surface."""

    def __init__(self, roads: RoadNetwork, truck: dict):
        self.roads, self.truck = roads, truck
        self.pace = mean_pace(truck)
        self._tables: dict[bool, tuple[int, dict]] = {}
        self._routes: tuple[int, dict] = (-1, {})

    def cost(self, loaded: bool) -> Callable[[Segment, str], float]:
        """Cost function for `RoadNetwork.route`: travel time over a segment entered at `from_node`."""
        version, table = self._tables.get(loaded, (-1, {}))
        if version != self.roads.version:
            table = {}
            self._tables[loaded] = (self.roads.version, table)
        p = self.truck
        mass_t = p["empty_mass_t"] + (p["payload_t"] if loaded else 0.0)

        def cost(seg: Segment, from_node: str) -> float:
            key = (seg.name, from_node)
            seconds = table.get(key)
            if seconds is None:
                seconds = table[key] = sum(
                    length / (physics.steady_speed_kmh(p, mass_t, loaded, grade, seg.rr_pct, seg.speed_limit_kmh)
                              * self.pace / 3.6)
                    for length, grade in seg.pieces_from(from_node))
            return seconds
        return cost

    def fastest(self, start: str, goal: str, loaded: bool, ignore: frozenset = frozenset()) -> list[str] | None:
        """`RoadNetwork.route` with this table as the cost. The answer depends only on the state of the graph,
        so it is kept until the graph reports a change: trucks and dispatch ask the same questions all shift."""
        version, known = self._routes
        if version != self.roads.version:
            known = {}
            self._routes = (self.roads.version, known)
        key = (start, goal, loaded, ignore)
        if key not in known:
            known[key] = self.roads.route(start, goal, self.cost(loaded), ignore)
        route = known[key]
        return None if route is None else list(route)

    def route(self, start: str, goal: str, loaded: bool) -> tuple[list[str], float] | None:
        """Fastest open route and its travel time in seconds, or None when there is no way."""
        route = self.fastest(start, goal, loaded)
        if route is None:
            return None
        return route, self.roads.route_time(route, self.cost(loaded))
