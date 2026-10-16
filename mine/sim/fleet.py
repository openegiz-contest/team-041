"""The mobile fleet: haul trucks, loaders and the fuel station.

A truck always has a goal: load at a loader, dump at a dump point, refuel
or go to the workshop. It drives there over the road graph, choosing its way
again at every node, queues, is served and gets its next goal. While no
dispatch message arrives the goals follow the fixed plan: every truck serves
its home loader, and every load goes where its block is destined.

The dispatch module changes that with `assign` messages (a loader for the
next load, a dump point for the load on board, a route for that leg) and
withdraws its orders with `release`. An order that is neither carried out
nor repeated within the decision time-out lapses by itself.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from mine.model.build_twins import bucket_load_t, full_power_fuel_lph
from mine.common import physics
from mine.common.haulage import TravelTimes
from mine.sim.kpis import PRODUCTIVE_STATES, UNAVAILABLE_STATES, Rolling
from mine.sim.reliability import Fault, service_view
from mine.common.roads import BLAST, Segment
from mine.sim.text import MATERIALS, num

if TYPE_CHECKING:
    from mine.sim.model import Simulation

# State of a truck on its way to each kind of goal. Driving to the fuel station
# and to the workshop are states of their own: `refuel` and `service` mean
# that the truck stands there.
TRAVEL_STATE = {"load": "returning", "dump": "hauling", "refuel": "to_fuel", "service": "to_service", "hold": "returning"}
IGNORE_BLAST = frozenset({BLAST})


def short(thing_id: str) -> str:
    return thing_id.split(":", 1)[1] if ":" in thing_id else thing_id


def initial(thing: dict, feature: str):
    return thing["features"][feature]["properties"]["value"]


@dataclass
class LocalMove:
    """A short drive off the road graph, between a road node and a loader's face."""
    start: tuple[float, float, float]
    end: tuple[float, float, float]
    duration_s: float
    then_leg: bool = False                  # start the road leg when the move ends
    elapsed_s: float = 0.0


@dataclass(eq=False)
class Truck:
    name: str
    title: str
    node: str                               # last node reached; where it stands when not driving
    home_loader: str
    fuel_l: float
    service_h: float
    engine_temp_c: float
    tyre_temp_c: float
    state: str = "standby"
    goal: tuple[str, str] | None = None     # (load | dump | refuel | service | hold, target)
    dest: str = ""                          # node the current leg ends at
    waiting_route: bool = False             # standing because no open route leads to `dest`
    hold: bool = False                      # standing outside a blast zone until it is lifted
    route: list[str] = field(default_factory=list)
    route_idx: int = 0                      # position of `node` in `route`
    seg: Segment | None = None
    seg_from: str = ""
    dist: float = 0.0
    pace: float = 1.0
    speed_ms: float = 0.0
    arrived_via: tuple[Segment, str] | None = None
    local: LocalMove | None = None
    face: tuple[float, float, float] | None = None    # set while it stands at a loader
    place: tuple[float, float, float] | None = None   # set while it is served at a facility
    queue: list | None = None               # the queue it waits in
    heading_deg: float = 0.0
    payload_t: float = 0.0
    payload_cu_t: float = 0.0
    payload_bwi: float = 0.0                # tonne-weighted sum of hardness
    payload_p80: float = 0.0                # tonne-weighted sum of fragment size
    payload_clay: float = 0.0               # tonne-weighted sum of clay content (% x t)
    hauled_t: float = 0.0                   # payload of the trip under way
    material: str = "none"
    timer_s: float = 0.0
    queue_since: float = 0.0
    queue_s: float = 0.0                    # queueing time in the current cycle
    cycle_start: float | None = None        # when it set off for the loader
    loaded_m: float = 0.0                   # distance driven with a load in the current cycle
    cycle_min: float = 0.0
    trips: int = 0
    shift: int = 0                          # the shift its crew belongs to
    engine_on: bool = True
    engine_kw: float = 0.0
    load_factor: float = 0.0
    fuel_rate_lph: float = 0.0
    standby_s: float = 0.0
    loader_target: str = ""
    dump_target: str = ""
    source: str = "static"                  # who made the current assignment
    override_loader: str = ""               # set by `assign` messages, used once
    override_dump: str = ""
    override_route: list[str] = field(default_factory=list)
    route_goal: tuple[str, str] | None = None   # the leg `override_route` was ordered for
    override_at: float = 0.0                # when the last `assign` message arrived
    plan_dump: str = ""                     # where the fixed plan sends the load on board
    # condition (mine/sim/reliability.py)
    hazard: dict = field(default_factory=dict)
    fault: Fault | None = None
    repair_s: float = 0.0
    service_due: str = ""
    service_reason: str = ""
    in_bay: bool = False
    resume_state: str = ""
    service_hold_until: float = 0.0         # an operator keeps it at work: no planned service until then
    break_at: float = 0.0                   # when the crew's meal break of this shift falls due; 0 once taken
    standby_reason: str = ""                # why it stands: crew change, meal break, no route, blast zone
    # Its own random streams (Simulation.rng): the pace on each segment, the tipping time at a dump point,
    # its defects and its repairs (mine/sim/reliability.py).
    rng_drive: random.Random | None = None
    rng_tip: random.Random | None = None
    rng_fail: random.Random | None = None
    rng_repair: random.Random | None = None


@dataclass(eq=False)
class Loader:
    name: str
    title: str
    block: str | None
    node: str
    pos: tuple[float, float]
    service_h: float
    dug: Rolling
    # idle / waiting / loading (a truck under the bucket, a pass under way) / relocating / setup (preparing
    # the new face) / standby (ordered out of a blast zone, waiting for the all-clear) / down
    state: str = "waiting"
    truck: Truck | None = None
    digging: bool = False                   # False while the truck is still spotting
    timer_s: float = 0.0
    passes: int = 0
    power_kw: float = 0.0
    queue: list[Truck] = field(default_factory=list)
    move_from: tuple[float, float] = (0.0, 0.0)
    move_to: tuple[float, float] = (0.0, 0.0)
    move_total_s: float = 0.0
    move_setup_s: float = 0.0               # of the move, the time spent at the new face before it digs
    return_to: tuple[float, float] = (0.0, 0.0)     # the face it left for a blast
    withdrawing: bool = False               # on its way out of a blast zone
    stood_down: bool = False                # ordered out of a blast zone
    withdrawn: bool = False                 # has left the zone
    forced_by: str = ""                     # the scenario that keeps the loader broken down
    crews: list = field(default_factory=list)   # extra crews on its repair (mine/sim/response.py)
    job: str = "repair"                     # what is being done while it is down: repair / service / fix / inspect
    material: str = "none"
    move_order: str = ""                    # block an operator ordered it to, taken up when no truck is under it
    hazard: dict = field(default_factory=dict)
    fault: Fault | None = None
    repair_s: float = 0.0
    service_due: str = ""
    service_reason: str = ""
    # Its own random streams: the fill of each bucket, the time of each pass, its defects and its repairs.
    rng_fill: random.Random | None = None
    rng_pass: random.Random | None = None
    rng_fail: random.Random | None = None
    rng_repair: random.Random | None = None


class FuelStation:
    def __init__(self, sim: "Simulation"):
        thing = sim.by_kind["fuel-station"][0]
        geo = thing["attributes"]["geo"]
        self.sim, self.p = sim, sim.params["fuel_station"]
        self.name = short(thing["thingId"])
        self.node = short(thing["attributes"]["links"]["access_node"])
        self.pos = (geo["x"], geo["y"], geo["z"])
        self.pumps: list[Truck | None] = [None] * int(self.p["pumps"])
        self.queue: list[Truck] = []
        self.litres_issued = 0.0

    def step(self, dt: float) -> None:
        tank = self.sim.params["truck"]["tank_l"]
        for i, truck in enumerate(self.pumps):
            if truck is None and self.queue:
                truck = self.pumps[i] = self.queue.pop(0)
                truck.queue, truck.place = None, self.pos
                self.sim.fleet.engine_off(truck)
                truck.timer_s = self.p["position_min"] * 60.0
            if truck is None:
                continue
            if truck.timer_s > 0:
                truck.timer_s -= dt
                continue
            added = min(self.p["flow_l_min"] * dt / 60.0, tank - truck.fuel_l)
            truck.fuel_l += added
            self.litres_issued += added
            if truck.fuel_l >= tank - 1e-6:
                self.pumps[i], truck.engine_on = None, True
                self.sim.fleet.assign_next(truck)

    def values(self) -> dict:
        return {"queue_len": len(self.queue), "trucks_fuelling": sum(1 for t in self.pumps if t is not None),
                "litres_issued": round(self.litres_issued)}


class Fleet:
    def __init__(self, sim: "Simulation"):
        self.sim = sim
        p = sim.params
        self.p_truck, self.p_loader = p["truck"], p["loader"]
        self.full_lph = full_power_fuel_lph(p)
        self.bucket_t = bucket_load_t(p)
        self.station = FuelStation(sim)
        self.travel = TravelTimes(sim.roads, self.p_truck)
        self.timeout_s = p["sim"]["decision_timeout_min"] * 60.0
        rate_window_s = self.rate_window_s = p["sim"]["rate_window_min"] * 60.0

        self.loaders: dict[str, Loader] = {}
        for t in sim.by_kind["loader"]:
            block = sim.pit.blocks[short(initial(t, "block"))]
            block.reserved_by = short(t["thingId"])
            self.loaders[short(t["thingId"])] = Loader(
                name=short(t["thingId"]), title=t["attributes"]["name"], block=block.name, node=block.node,
                pos=block.face, service_h=initial(t, "hours_since_service"), dug=Rolling(rate_window_s),
                material=block.material)
        self.trucks: list[Truck] = []
        for t in sim.by_kind["truck"]:
            links = t["attributes"]["links"]
            self.trucks.append(Truck(
                name=short(t["thingId"]), title=t["attributes"]["name"], node=short(links["home_node"]),
                home_loader=short(links["home_loader"]),
                fuel_l=initial(t, "fuel_pct") / 100.0 * self.p_truck["tank_l"],
                service_h=initial(t, "hours_since_service"),
                engine_temp_c=initial(t, "engine_temp_c"), tyre_temp_c=initial(t, "tyre_temp_c")))
        self.by_name = {t.name: t for t in self.trucks}
        self.reseed()

    def reseed(self) -> None:
        """Every truck and loader draws from streams of its own (Simulation.rng)."""
        sim = self.sim
        for truck in self.trucks:
            truck.rng_drive, truck.rng_tip = sim.rng(f"truck/{truck.name}/drive"), sim.rng(f"truck/{truck.name}/tip")
        for loader in self.loaders.values():
            loader.rng_fill, loader.rng_pass = sim.rng(f"loader/{loader.name}/fill"), sim.rng(f"loader/{loader.name}/pass")

    def start(self) -> None:
        """Give every machine its failure clocks and every truck its first goal."""
        for loader in self.loaders.values():
            self.sim.reliability.start(loader, loader=True)
        for truck in self.trucks:
            self.sim.reliability.start(truck)
            truck.break_at = self.break_time(truck)
            self.assign_next(truck)

    # -- routing ---------------------------------------------------------------

    def cost(self, loaded: bool):
        """Travel time over a segment for a nominal truck, used to compare routes."""
        return self.travel.cost(loaded)

    def plan(self, truck: Truck, start: str, goal: str) -> list[str] | None:
        """Route for a truck: the one a dispatch message asked for if it is still
        drivable, else the fastest. A truck inside a blast zone may leave it over
        the roads the blast has closed."""
        roads = self.sim.roads
        ignore = IGNORE_BLAST if self.sim.pit.node_in_zone(start) else frozenset()
        wanted = truck.override_route if truck.route_goal == truck.goal else None
        if wanted and start in wanted:
            tail = wanted[wanted.index(start):]
            if tail[-1] == goal and all(
                    roads.connected(a, b) and roads.segment_between(a, b).closures <= ignore
                    for a, b in zip(tail, tail[1:])):
                return tail
        return self.travel.fastest(start, goal, truck.payload_t > 1.0, ignore)

    def roads_changed(self) -> None:
        """A segment opened or closed. Trucks on the road pick a new route at
        their next node; trucks waiting for a route try again at once."""
        for truck in self.trucks:
            if truck.waiting_route:
                self._start_leg(truck)

    # -- choosing loaders and dumps ---------------------------------------------

    def loader_available(self, loader: Loader) -> bool:
        if loader.block is None or loader.state == "down" or loader.stood_down:
            return False
        return not (loader.state in ("relocating", "setup") and loader.timer_s > self.p_loader["fallback_wait_min"] * 60.0)

    def _choose_loader(self, truck: Truck) -> tuple[Loader, str]:
        """The loader an empty truck heads for, and who decided it."""
        wanted = self.loaders.get(truck.override_loader)
        if wanted is not None and self.loader_available(wanted):
            return wanted, "dispatch"
        home = self.loaders[truck.home_loader]
        if self.loader_available(home):
            return home, "static"
        options = [l for l in self.loaders.values() if self.loader_available(l)]
        if not options:
            return home, "static"
        home_ore = home.material != "waste"
        heading = lambda l: sum(1 for t in self.trucks if t.goal == ("load", l.name) and t.state == "returning")
        best = min(options, key=lambda l: ((l.material != "waste") != home_ore, len(l.queue) + heading(l), l.name))
        return best, "fallback"

    def _dest_node(self, truck: Truck) -> str:
        kind, target = truck.goal
        if kind == "load":
            return self.loaders[target].node
        if kind == "dump":
            return self.sim.plant.sinks[target].node
        if kind == "refuel":
            return self.station.node
        if kind == "service":
            return self.sim.reliability.node
        return target                           # hold: the node itself

    def assign_next(self, truck: Truck) -> None:
        """Decide what an empty, free truck does next."""
        sim, p = self.sim, self.p_truck
        if truck.service_due:
            self._send(truck, ("service", sim.reliability.name))
        elif truck.shift < sim.shift_index:
            # Crew change: the truck stands for the handover and the pre-start check, then comes back here.
            truck.shift = sim.shift_index
            truck.break_at = self.break_time(truck)
            truck.state, truck.timer_s, truck.speed_ms = "standby", p["changeover_min"] * 60.0, 0.0
            truck.standby_reason = "пересменка"
        elif truck.break_at and sim.now >= truck.break_at:
            # The crew's meal break, staggered over the middle of the shift so that the loaders keep working.
            truck.break_at = 0.0
            truck.state, truck.timer_s, truck.speed_ms = "standby", p["meal_break_min"] * 60.0, 0.0
            truck.standby_reason = "перерыв экипажа"
        elif 100.0 * truck.fuel_l / p["tank_l"] < p["refuel_below_pct"]:
            self._send(truck, ("refuel", self.station.name))
        else:
            loader, truck.source = self._choose_loader(truck)
            truck.loader_target = loader.name
            truck.cycle_start, truck.queue_s, truck.loaded_m = sim.now, 0.0, 0.0
            self._send(truck, ("load", loader.name))

    def break_time(self, truck: Truck) -> float:
        """When this shift's meal break of the truck's crew falls due: a moment of its own between
        `truck.break_from_h` and `truck.break_to_h` after the shift began (golden-ratio spread over the fleet)."""
        p, sim = self.p_truck, self.sim
        index = self.trucks.index(truck) + 1
        share = (index * 0.618034) % 1.0
        began = sim.next_shift_at - (sim.clock["day_shift_to_h"] - sim.clock["day_shift_from_h"]) * 3600.0
        return began + (p["break_from_h"] + (p["break_to_h"] - p["break_from_h"]) * share) * 3600.0

    def _send(self, truck: Truck, goal: tuple[str, str]) -> None:
        truck.goal = goal
        truck.standby_reason = ""
        self._depart(truck)

    # -- truck movement --------------------------------------------------------

    def _leave_queue(self, truck: Truck) -> None:
        if truck.queue is not None and truck in truck.queue:
            truck.queue.remove(truck)
        truck.queue = None

    def _depart(self, truck: Truck) -> None:
        """Set off towards the goal: first back to the road if the truck stands at a face."""
        self._leave_queue(truck)
        truck.state, truck.waiting_route, truck.hold, truck.place = TRAVEL_STATE[truck.goal[0]], False, False, None
        if truck.seg is not None:
            return                              # on the road: the new goal takes effect at the next node
        if truck.face is not None:
            node_xyz = self.sim.roads.nodes[truck.node]
            seconds = math.dist(truck.face[:2], node_xyz[:2]) / (self.p_truck["local_speed_kmh"] / 3.6)
            truck.local, truck.face = LocalMove(truck.face, node_xyz, seconds, then_leg=True), None
            return
        self._start_leg(truck)

    def _start_leg(self, truck: Truck) -> None:
        dest = self._dest_node(truck)
        route = self.plan(truck, truck.node, dest)
        if route is None:
            self._wait_for_route(truck)
            return
        truck.state, truck.waiting_route = TRAVEL_STATE[truck.goal[0]], False
        truck.dest, truck.route, truck.route_idx = dest, route, 0
        if len(route) == 1:
            self._arrive(truck)
        else:
            self._enter(truck, route[1])

    def _wait_for_route(self, truck: Truck) -> None:
        if not truck.waiting_route:
            self.sim.log("warning", truck.name, f"{truck.title}: нет открытого маршрута, ожидание в узле "
                                                f"«{self.sim.things[truck.node]['attributes']['name']}»")
        truck.state, truck.waiting_route, truck.speed_ms, truck.engine_kw = "standby", True, 0.0, 0.0
        truck.standby_reason = "нет открытого маршрута"

    def _enter(self, truck: Truck, next_node: str) -> None:
        truck.seg = self.sim.roads.segment_between(truck.node, next_node)
        truck.seg_from, truck.dist = truck.node, 0.0
        truck.pace = truck.rng_drive.uniform(self.p_truck["pace_min"], 1.0)

    def _drive(self, truck: Truck, dt: float) -> None:
        seg, p = truck.seg, self.p_truck
        loaded = truck.payload_t > 1.0
        grade = seg.grade_at(truck.dist, truck.seg_from)
        cap_ms = physics.speed_cap_kmh(p, loaded, grade, seg.speed_limit_kmh) * truck.pace / 3.6
        if truck.route_idx == len(truck.route) - 2:
            # Last segment of the leg: slow down in time to stop at its end.
            cap_ms = min(cap_ms, math.sqrt(2.0 * p["brake_ms2"] * max(seg.length - truck.dist, 0.0)) + 1.0)
        truck.speed_ms, truck.engine_kw = physics.advance(
            p, p["empty_mass_t"] + truck.payload_t, grade, seg.rr_pct, truck.speed_ms, cap_ms, dt)
        truck.dist += truck.speed_ms * dt
        if loaded:
            truck.loaded_m += truck.speed_ms * dt
        if truck.dist >= seg.length:
            over = truck.dist - seg.length
            self._reach_node(truck, seg.other(truck.seg_from))
            if truck.seg is not None:
                truck.dist = min(over, truck.seg.length)

    def _reach_node(self, truck: Truck, node: str) -> None:
        *_, truck.heading_deg = truck.seg.point_at(truck.seg.length, truck.seg_from)
        truck.arrived_via = (truck.seg, truck.seg_from)
        truck.node, truck.seg = node, None
        truck.route_idx += 1
        if truck.goal[0] == "load":
            # The loader may have moved, failed or been replaced by a dispatch order.
            loader, source = self._choose_loader(truck)
            if loader.name != truck.goal[1]:
                truck.goal, truck.source, truck.loader_target = ("load", loader.name), source, loader.name
        dest = self._dest_node(truck)
        if node == dest:
            self._arrive(truck)
            return
        # Choose the way onward again at every node: this is where a closure
        # or a reopening takes effect for a truck that is already on the road.
        route = self.plan(truck, node, dest)
        if route is None:
            self._wait_for_route(truck)
            return
        if route != truck.route[truck.route_idx:]:
            if dest == truck.dest:
                via = self.sim.roads.segment_between(route[0], route[1]).title
                self.sim.log("info", truck.name, f"{truck.title} сменил маршрут в узле "
                                                 f"«{self.sim.things[node]['attributes']['name']}»: далее «{via}»")
            truck.route = truck.route[:truck.route_idx] + route
        truck.dest = dest
        self._enter(truck, route[1])

    def _arrive(self, truck: Truck) -> None:
        """The truck reached the end of its leg: join the queue there."""
        sim = self.sim
        truck.speed_ms, truck.engine_kw, truck.queue_since = 0.0, 0.0, sim.now
        kind, target = truck.goal
        if truck.route_goal == truck.goal:
            truck.override_route, truck.route_goal = [], None     # the ordered route has been driven
        if kind == "load":
            loader = self.loaders[target]
            if loader.withdrawn and sim.pit.node_in_zone(loader.node):
                self._hold(truck)               # the face is inside a blast zone
                return
            truck.state, truck.queue = "queue_load", loader.queue
            loader.queue.append(truck)
        elif kind == "dump":
            plant = sim.plant
            if target == plant.crusher.name and not truck.override_dump and plant.divert_from_crusher():
                pad = plant.pad_with_room(truck)
                if pad is not None:
                    plant.note_divert(truck.title, pad)
                    self.redirect(truck, pad, "overflow")
                    return
            sink = plant.sinks[target]
            if target in plant.rom_pads and not sink.room_for(truck):
                # The pad filled up while the truck was on its way: the load goes to the crusher.
                sim.log("warning", target, f"{sink.title} заполнен: {truck.title} везёт руду на дробилку")
                truck.override_dump = ""
                self.redirect(truck, plant.crusher.name, "overflow")
                return
            if target in (plant.clay_plant.name, plant.clay_stock.name) and not sink.room_for(truck):
                # No room on the feed stockpile of ОФ-2: the clay-ore stockpile beside it; no room there either: ОФ-1.
                other = plant.clay_sink_with_room(truck) if target == plant.clay_plant.name else None
                if other is not None:
                    plant.note_clay_divert(truck.title, other)
                    truck.override_dump = ""
                    self.redirect(truck, other, "overflow")
                    return
                sim.log("warning", target, f"{sink.title}: нет места, {truck.title} везёт руду на дробилку ОФ-1")
                truck.override_dump = ""
                self.redirect(truck, plant.crusher.name, "overflow")
                return
            truck.state, truck.queue = "queue_dump", sink.queue
            sink.queue.append(truck)
        elif kind == "refuel":
            truck.state, truck.queue = "refuel", self.station.queue
            self.station.queue.append(truck)
        elif kind == "service":
            self.engine_off(truck)
            sim.reliability.arrive(truck)
        else:
            truck.state, truck.hold = "standby", True
            truck.standby_reason = "ожидание у границы зоны взрыва"

    def redirect(self, truck: Truck, dump: str, source: str) -> None:
        """Send a loaded truck to another dump point from where it stands."""
        self._leave_queue(truck)
        truck.goal, truck.source, truck.dump_target = ("dump", dump), source, dump
        truck.state, truck.place = "hauling", None
        if truck.seg is None and truck.local is None:
            self._start_leg(truck)

    def _hold(self, truck: Truck) -> None:
        """Send the truck to the nearest road node outside the blast zone to wait there."""
        roads, pit = self.sim.roads, self.sim.pit
        cost = self.cost(truck.payload_t > 1.0)
        best = None
        for node in roads.nodes:
            if pit.node_in_zone(node):
                continue
            route = roads.route(truck.node, node, cost, IGNORE_BLAST)
            if route is not None:
                seconds = roads.route_time(route, cost)
                if best is None or seconds < best[0]:
                    best = (seconds, node)
        truck.goal = ("hold", best[1] if best else truck.node)
        self._start_leg(truck)

    def clear_zone(self) -> None:
        """A blast is being prepared: trucks waiting inside its zone at a loader
        that will not serve them drive out and wait at the nearest safe node."""
        pit = self.sim.pit
        for loader in self.loaders.values():
            serving = loader.block is not None and loader.state not in ("down", "idle") and not loader.withdrawn
            if serving:
                continue
            for truck in list(loader.queue):
                if pit.in_zone(*self.position(truck)[:2]):
                    loader.queue.remove(truck)
                    truck.queue = None
                    self._hold(truck)
        for truck in self.trucks:
            if truck.hold and pit.node_in_zone(truck.node):
                self._hold(truck)               # it was waiting where the new zone now is

    def stand_up(self, loader: Loader) -> None:
        """The blast zone is lifted: the loader drives back to its face."""
        loader.stood_down = False
        if loader.withdrawn or loader.withdrawing:
            loader.withdrawn = loader.withdrawing = False
            self._move(loader, loader.return_to, 0.0)

    def _move(self, loader: Loader, to: tuple[float, float], setup_s: float, metres: float | None = None) -> None:
        """Start a move of the loader on its tracks, followed by `setup_s` of preparing the face."""
        metres = math.dist(loader.pos, to) if metres is None else metres
        loader.move_from, loader.move_to, loader.move_setup_s = loader.pos, to, setup_s
        loader.timer_s = loader.move_total_s = metres / (self.p_loader["travel_kmh"] / 3.6) + setup_s
        loader.state = "relocating"

    def release_holds(self) -> None:
        for truck in self.trucks:
            if truck.goal and truck.goal[0] == "hold":
                loader, truck.source = self._choose_loader(truck)
                truck.loader_target = loader.name
                self._send(truck, ("load", loader.name))

    def position(self, truck: Truck) -> tuple[float, float, float]:
        if truck.local is not None:
            move = truck.local
            f = min(move.elapsed_s / move.duration_s, 1.0) if move.duration_s > 0 else 1.0
            return tuple(a + (b - a) * f for a, b in zip(move.start, move.end))
        if truck.seg is not None:
            return truck.seg.point_at(truck.dist, truck.seg_from)[:3]
        if truck.face is not None:
            return truck.face
        if truck.place is not None:
            return truck.place
        queue = truck.queue
        if queue is not None and truck.arrived_via and truck in queue:
            # Waiting trucks line up along the segment they arrived on.
            seg, came_from = truck.arrived_via
            back = (queue.index(truck) + 1) * self.p_truck["queue_spacing_m"]
            return seg.point_at(max(seg.length - back, 0.0), came_from)[:3]
        return self.sim.roads.nodes[truck.node]

    # -- loading ---------------------------------------------------------------

    def release_queue(self, loader: Loader) -> None:
        """The loader moved or stopped: its waiting trucks choose a loader again."""
        for truck in list(loader.queue):
            loader.queue.remove(truck)
            truck.queue = None
            chosen, truck.source = self._choose_loader(truck)
            truck.loader_target = chosen.name
            self._send(truck, ("load", chosen.name))

    def _new_block(self, loader: Loader) -> None:
        """Send the loader to its next blasted block, or leave it idle if there is none."""
        sim, p = self.sim, self.p_loader
        block = sim.pit.next_block(loader)
        if block is None:
            if loader.block is not None:
                sim.log("alarm", loader.name, f"{loader.title}: взорванная горная масса закончилась, простой")
                loader.block, loader.state = None, "idle"
                self.release_queue(loader)
            return
        self._relocate(loader, block)

    def _relocate(self, loader: Loader, block, why: str = "") -> None:
        sim, p = self.sim, self.p_loader
        block.status, block.reserved_by = "mining", loader.name
        metres = sim.pit.travel_m(loader.pos, loader.node, block)
        loader.block, loader.node, loader.material = block.name, block.node, block.material
        self._move(loader, block.face, p["relocate_setup_min"] * 60.0, metres)
        loader.dug = Rolling(self.rate_window_s)
        sim.log("info", loader.name, f"{loader.title} переезжает на {block.title}{why}: {metres:.0f} м, "
                                     f"около {loader.timer_s / 60:.0f} мин ({MATERIALS[block.material]}, {num(block.grade_pct, 2)} % Cu)")
        self.release_queue(loader)

    def move_to_block(self, loader: Loader, body: dict) -> None:
        """Live message `move-to-block {block}`: an operator sends the loader to
        another blasted block, for example from a waste face to the ore face of
        a broken-down loader. It leaves after the truck under it is loaded; the
        block it leaves stays blasted for the plan to give to a loader later."""
        sim, pit = self.sim, self.sim.pit
        name = short(body["block"]) if isinstance(body.get("block"), str) else ""
        block = pit.blocks.get(name)
        holder = self.loaders.get(block.reserved_by) if block is not None else None
        if block is None:
            problem = f"блока {name!r} нет"
        elif loader.state == "down" or loader.stood_down:
            problem = "экскаватор неисправен или выведен из зоны взрыва"
        elif block.name in (loader.block, loader.move_order):
            problem = f"экскаватор уже назначен на {block.title}"
        elif block.status not in ("blasted", "mining") or block.tonnes <= 0:
            problem = f"{block.title} не взорван или отработан"
        elif holder is not None and holder is not loader and holder.state != "down":
            problem = f"в блоке работает {holder.title}"
        elif block.reserved_by and holder is None:
            problem = f"{block.title} занят"
        elif pit.blast_block is not None and pit.in_zone(*block.centre):
            problem = f"{block.title} в опасной зоне взрыва"
        else:
            problem = ""
        if problem:
            sim.refuse(loader.name, f"{loader.title}: перевод на другой блок не выполнен: {problem}")
            return
        if holder is not None and holder is not loader:
            # A broken-down loader gives up its face; once repaired it takes the next block of the plan.
            holder.block, holder.material, holder.move_order = None, "none", ""
            sim.log("info", holder.name, f"{holder.title}: {block.title} передан {loader.title}")
        if loader.move_order:
            pit.blocks[loader.move_order].reserved_by = ""
        block.reserved_by, loader.move_order = loader.name, block.name
        if loader.truck is not None:
            sim.log("info", loader.name, f"{loader.title}: переезд на {block.title} после погрузки текущего самосвала")

    def _take_move_order(self, loader: Loader) -> None:
        block = self.sim.pit.blocks[loader.move_order]
        loader.move_order = ""
        old = self.sim.pit.blocks.get(loader.block) if loader.block else None
        if old is not None and old is not block and old.reserved_by == loader.name:
            old.reserved_by = ""
            if old.status == "mining" and old.tonnes > 0:
                old.status = "blasted"
        self._relocate(loader, block, " по указанию оператора")

    def _pass(self, loader: Loader, block, truck: Truck) -> None:
        """One bucket: coarser muck fills the bucket less."""
        p, sim = self.p_loader, self.sim
        ref = sim.params["blast"]["p80_ref_mm"]
        mean = p["fill_factor"] * (1.0 - p["fill_frag_slope"] * (block.frag_p80_mm - ref) / ref)
        fill = min(p["fill_factor_max"], max(p["fill_factor_min"], loader.rng_fill.gauss(mean, p["fill_factor_sd"])))
        room = self.p_truck["payload_t"] * self.p_truck["overload_limit"] - truck.payload_t
        load_t = min(p["bucket_m3"] * fill * sim.params["rock"]["loose_density_t_m3"], block.tonnes, room)
        if block.tonnes - load_t < 1e-6:
            load_t = block.tonnes
        cu_t = load_t * block.grade_pct / 100.0
        block.tonnes -= load_t
        truck.payload_t += load_t
        truck.payload_cu_t += cu_t
        truck.payload_bwi += load_t * block.hardness_bwi
        truck.payload_p80 += load_t * block.frag_p80_mm
        truck.payload_clay += load_t * block.clay_pct
        truck.material = block.material
        loader.passes += 1
        loader.dug.add(sim.now, load_t)
        sim.kpis.mined(load_t, cu_t, block.material, load_t * block.clay_pct, block.actual_class(sim.params))

    def _loaded(self, truck: Truck, block) -> None:
        """The truck is full: it goes where the block is destined, unless a
        dispatch order or a stopped crusher says otherwise."""
        plant = self.sim.plant
        dump = truck.plan_dump = block.destination
        if dump in plant.rom_pads and not plant.rom_pads[dump].room_for(truck):
            dump = truck.plan_dump = plant.crusher.name       # the pad the block is destined for is full
        if truck.override_dump:
            dump, truck.source = truck.override_dump, "dispatch"
        if truck.material == "waste" and dump != plant.waste_dump.name and block.destination == plant.waste_dump.name:
            self.sim.log("warning", truck.name, f"{truck.title}: вскрышу нельзя везти на «{plant.sinks[dump].title}», "
                                                f"рейс идёт на отвал")
            dump, truck.override_dump = plant.waste_dump.name, ""
        if truck.material != "waste" and dump == plant.waste_dump.name and block.destination != plant.waste_dump.name:
            # An order given for another load (the loader has since moved to an ore block): ore does not go to the
            # waste dump unless its block is destined there, so the mine's ore and waste tallies match the dumps.
            dump, truck.override_dump = block.destination, ""
            if dump in plant.rom_pads and not plant.rom_pads[dump].room_for(truck):
                dump = plant.crusher.name
        if dump == plant.crusher.name and not truck.override_dump and plant.crusher_stop_is_long():
            pad = plant.pad_with_room(truck)
            if pad is not None:
                dump, truck.source = pad, "overflow"
                plant.note_divert(truck.title, dump)
        if dump == plant.clay_plant.name and not truck.override_dump and not plant.clay_plant.room_for(truck):
            other = plant.clay_sink_with_room(truck)
            if other is not None:
                dump, truck.source = other, "overflow"
                plant.note_clay_divert(truck.title, dump)
        truck.dump_target, truck.hauled_t = dump, truck.payload_t
        self._send(truck, ("dump", dump))

    def _step_loader(self, loader: Loader, dt: float) -> None:
        sim, p = self.sim, self.p_loader
        idle_kw = p["power_rated_kw"] * p["load_factor_idle"]
        if loader.state == "down":
            loader.power_kw = 0.0
            return
        loader.power_kw = idle_kw
        if loader.move_order and loader.truck is None and not loader.stood_down:
            self._take_move_order(loader)
        if loader.state in ("relocating", "setup"):
            loader.timer_s -= dt
            travel_s = loader.move_total_s - loader.move_setup_s
            if loader.move_total_s - loader.timer_s < travel_s:
                # On its tracks: the drive draws its travel power.
                loader.state = "relocating"
                loader.power_kw = p["power_rated_kw"] * p["load_factor_travel"]
                f = (loader.move_total_s - loader.timer_s) / travel_s if travel_s > 0 else 1.0
                loader.pos = (round(loader.move_from[0] + (loader.move_to[0] - loader.move_from[0]) * f, 1),
                              round(loader.move_from[1] + (loader.move_to[1] - loader.move_from[1]) * f, 1))
            else:
                loader.state, loader.pos = "setup", loader.move_to       # at the new face: cable, floor
            if loader.timer_s > 0:
                return
            loader.pos = loader.move_to
            if loader.withdrawing:
                loader.withdrawing, loader.withdrawn, loader.state = False, True, "standby"
                return
            loader.state = "waiting"
        if loader.stood_down:
            # It loads the trucks already waiting, if it has rock to load, and then leaves.
            if loader.withdrawn:
                loader.state = "standby"            # out of the zone, waiting for the all-clear
                return
            if loader.truck is None and (not loader.queue or loader.block is None):
                # Out of the blast zone: straight away from the block being fired.
                centre = sim.pit.blast_block.centre
                away = math.dist(loader.pos, centre) or 1.0
                reach = sim.params["blast"]["equipment_withdrawal_m"] + p["face_offset_m"]
                loader.return_to = loader.pos
                self._move(loader, (round(centre[0] + (loader.pos[0] - centre[0]) / away * reach, 1),
                                    round(centre[1] + (loader.pos[1] - centre[1]) / away * reach, 1)), 0.0)
                loader.withdrawing = True
                return
        if loader.block is None:
            loader.state = "idle"
            if int(sim.now) % 60 == 0:
                self._new_block(loader)
            return
        block = sim.pit.blocks[loader.block]
        if loader.truck is None:
            if not loader.queue:
                loader.state = "waiting"
                return
            truck = loader.queue.pop(0)
            truck.queue = None
            truck.queue_s += sim.now - truck.queue_since
            truck.state = "approach"            # off the road to the face, then spotting under the bucket
            if truck.override_loader == loader.name:
                truck.override_loader = ""          # the dispatch order is carried out
            node_xyz = sim.roads.nodes[loader.node]
            face = (loader.pos[0], loader.pos[1], node_xyz[2])
            approach_s = math.dist(node_xyz[:2], face[:2]) / (self.p_truck["local_speed_kmh"] / 3.6)
            truck.local, truck.face = LocalMove(node_xyz, face, approach_s), face
            loader.truck, loader.digging, loader.passes = truck, False, 0
            loader.timer_s = approach_s + p["spot_s"]
        # Loading means a truck stands under the bucket and a pass is under way; while it drives up and spots, the loader waits.
        loader.state = "loading" if loader.digging else "waiting"
        if loader.digging:
            loader.power_kw = p["power_rated_kw"] * p["load_factor_digging"]
        loader.timer_s -= dt
        if loader.timer_s > 0:
            return
        truck = loader.truck
        if loader.digging:
            self._pass(loader, block, truck)
        else:
            truck.state = loader.state = "loading"         # spotted: the first pass begins
            loader.power_kw = p["power_rated_kw"] * p["load_factor_digging"]
        loader.digging = True
        limit = self.p_truck["payload_t"] * self.p_truck["overload_limit"]
        full = truck.payload_t >= self.p_truck["payload_t"] - self.bucket_t / 2 or truck.payload_t >= limit - 1e-6
        if full or block.tonnes <= 0:
            loader.truck, loader.digging = None, False
            loader.state, loader.power_kw = "waiting", idle_kw   # until the next truck in the queue spots
            self._loaded(truck, block)
            if block.tonnes <= 0:
                sim.pit.depleted(block)
                self._new_block(loader)
        else:
            spread = p["pass_spread"]
            loader.timer_s += p["pass_s"] * loader.rng_pass.uniform(1 - spread, 1 + spread)

    def loader_down(self, loader: Loader) -> None:
        """The loader failed or stopped for service: the truck under it leaves
        with what it has, the others go elsewhere."""
        truck = loader.truck
        loader.state, loader.truck, loader.passes, loader.digging, loader.power_kw = "down", None, 0, False, 0.0
        loader.dug = Rolling(self.rate_window_s)
        if truck is not None:
            if truck.payload_t > 1.0:
                self._loaded(truck, self.sim.pit.blocks[loader.block])
            else:
                truck.cycle_start = None
                chosen, truck.source = self._choose_loader(truck)
                truck.loader_target = chosen.name
                self._send(truck, ("load", chosen.name))
        self.release_queue(loader)

    def loader_up(self, loader: Loader) -> None:
        loader.state = "waiting" if loader.block else "idle"

    # -- end of a trip, failures -----------------------------------------------

    def finish_trip(self, truck: Truck) -> None:
        """Called by the dump point when the body is empty."""
        truck.payload_t = truck.payload_cu_t = truck.payload_bwi = truck.payload_p80 = truck.payload_clay = 0.0
        truck.material = "none"
        truck.trips += 1
        if truck.cycle_start is not None:
            cycle = self.sim.kpis.cycle_done(truck.cycle_start, truck.queue_s, truck.hauled_t, truck.loaded_m,
                                             truck.dump_target)
            truck.cycle_min = cycle.duration_s / 60.0
        truck.cycle_start, truck.override_dump, truck.plan_dump = None, "", ""
        if truck.route_goal is not None and truck.route_goal[0] == "dump":
            truck.override_route, truck.route_goal = [], None
        self.assign_next(truck)

    def break_down(self, truck: Truck) -> None:
        """The truck stops where it is; a trip cut short is not counted as a cycle."""
        truck.resume_state, truck.state = truck.state, "down"
        truck.speed_ms, truck.cycle_start = 0.0, None
        self.engine_off(truck)

    def engine_off(self, truck: Truck) -> None:
        truck.engine_on, truck.engine_kw, truck.load_factor, truck.fuel_rate_lph = False, 0.0, 0.0, 0.0

    def resume(self, truck: Truck) -> None:
        """Repaired where it stood: carry on with the leg."""
        truck.state, truck.engine_on, truck.shift = truck.resume_state, True, self.sim.shift_index

    def tow(self, truck: Truck, node: str, pos: tuple[float, float, float]) -> None:
        truck.seg, truck.local, truck.route, truck.route_idx = None, None, [], 0
        truck.node, truck.place, truck.arrived_via = node, pos, None

    def leave_workshop(self, truck: Truck, resume: bool) -> None:
        truck.engine_on, truck.shift, truck.place = True, self.sim.shift_index, None
        if resume and truck.goal is not None and truck.goal[0] == "dump" and truck.payload_t > 1.0:
            self._start_leg(truck)              # deliver the load it broke down with
        else:
            self.assign_next(truck)

    # -- messages --------------------------------------------------------------

    def assign(self, truck: Truck, body: dict) -> None:
        """Live message `assign` from the dispatch module: a loader for the next
        load, a dump for the load on board, a route for the leg to the loader
        or dump named with it (or for the leg under way). Each part is used
        once; without further messages the fixed plan returns."""
        sim, plant = self.sim, self.sim.plant
        loader = short(body["loader"]) if isinstance(body.get("loader"), str) else None
        dump = short(body["dump"]) if isinstance(body.get("dump"), str) else None
        route = [short(n) for n in body["route"].split(">")] if isinstance(body.get("route"), str) and body["route"] else None
        problems = []
        if loader is not None and loader not in self.loaders:
            problems.append(f"экскаватор {loader!r} не существует")
        if dump is not None and dump not in plant.sinks:
            problems.append(f"пункт разгрузки {dump!r} не существует")
        if dump is not None and dump in plant.sinks and truck.material == "waste" and dump != plant.waste_dump.name:
            problems.append("вскрышу можно везти только на отвал")
        if route is not None and not all(a in sim.roads.nodes and sim.roads.connected(a, b)
                                         for a, b in zip(route, route[1:])):
            problems.append(f"маршрут {body['route']!r} не проходит по дорогам")
        if loader is None and dump is None and route is None:
            problems.append("нет ни экскаватора, ни пункта разгрузки, ни маршрута")
        if problems:
            sim.refuse(truck.name, f"{truck.title}: назначение диспетчера отклонено: {'; '.join(problems)}")
            return
        truck.override_at = sim.now
        if route is not None:
            truck.override_route = route
            truck.route_goal = ("load", loader) if loader is not None else ("dump", dump) if dump is not None else truck.goal
        if loader is not None:
            truck.override_loader = loader
            if truck.goal and truck.goal[0] == "load" and truck.state in ("returning", "queue_load") \
                    and self.loader_available(self.loaders[loader]):
                truck.source = "dispatch"
                if truck.goal[1] != loader:
                    truck.goal, truck.loader_target = ("load", loader), loader
                    if truck.state == "queue_load":
                        self._depart(truck)
        if dump is not None:
            truck.override_dump = dump
            if truck.goal and truck.goal[0] == "dump" and truck.state in ("hauling", "queue_dump"):
                truck.source = "dispatch"
                if truck.goal[1] != dump:
                    self.redirect(truck, dump, "dispatch")

    def release(self, truck: Truck) -> None:
        """Live message `release` from the dispatch module: its orders for this
        truck are withdrawn and the truck follows the fixed plan from now on."""
        plant = self.sim.plant
        truck.override_loader = truck.override_dump = ""
        truck.override_route, truck.route_goal = [], None
        if truck.source != "dispatch" or truck.goal is None:
            return
        truck.source = "static"
        if truck.goal[0] == "load" and truck.state in ("returning", "queue_load"):
            loader, truck.source = self._choose_loader(truck)
            if loader.name != truck.goal[1]:
                truck.goal, truck.loader_target = ("load", loader.name), loader.name
                if truck.state == "queue_load":
                    self._depart(truck)
        elif truck.goal[0] == "dump" and truck.state in ("hauling", "queue_dump") and truck.plan_dump \
                and truck.plan_dump != truck.goal[1] and plant.sinks[truck.plan_dump].room_for(truck):
            self.redirect(truck, truck.plan_dump, "static")

    def _expire_orders(self) -> None:
        """Drop dispatch orders that were neither carried out nor repeated in time."""
        deadline = self.sim.now - self.timeout_s
        for truck in self.trucks:
            if truck.override_at < deadline and (truck.override_loader or truck.override_dump or truck.override_route):
                truck.override_loader = truck.override_dump = ""
                truck.override_route, truck.route_goal = [], None

    # -- one step --------------------------------------------------------------

    def _step_truck(self, truck: Truck, dt: float) -> None:
        p = self.p_truck
        mass_t = p["empty_mass_t"] + truck.payload_t
        if truck.local is not None:
            move = truck.local
            move.elapsed_s += dt
            truck.speed_ms = p["local_speed_kmh"] / 3.6 if move.elapsed_s < move.duration_s else 0.0
            # Level driving over the bench floor.
            truck.engine_kw = (physics.resistance_kn(mass_t, 0.0, self.sim.params["road"]["rr_bench_pct"])
                               * truck.speed_ms / p["drivetrain_efficiency"])
            if move.elapsed_s >= move.duration_s and (move.then_leg or truck.state not in ("approach", "loading")):
                truck.local = None
                if move.then_leg:
                    self._start_leg(truck)
        elif truck.seg is not None:
            if truck.state != "down":
                self._drive(truck, dt)
        elif truck.state == "standby":
            if truck.waiting_route:
                if int(self.sim.now) % 10 == 0:
                    self._start_leg(truck)
            elif not truck.hold:
                truck.timer_s -= dt
                if truck.timer_s <= 0:
                    self.assign_next(truck)

        # Fuel and temperatures follow the power the engine delivers.
        ambient = self.sim.weather.temperature_c
        fault = truck.fault
        if truck.engine_on:
            idle = p["idle_load_factor"]
            load = truck.load_factor = max(idle, truck.engine_kw / p["engine_kw"])
            truck.fuel_rate_lph = load * self.full_lph
            burned = min(truck.fuel_l, truck.fuel_rate_lph * dt / 3600.0)
            truck.fuel_l -= burned
            target = (p["engine_temp_thermostat_c"]
                      + (p["engine_temp_full_load_c"] - p["engine_temp_thermostat_c"]) * (load - idle) / (1.0 - idle)
                      + p["engine_temp_ambient_gain"] * (ambient - p["engine_temp_ambient_ref_c"]))
            if fault is not None and fault.indicator == "engine":
                target += self.sim.reliability.indicator_rise(truck, "engine")
            truck.engine_temp_c += (target - truck.engine_temp_c) * dt / (p["engine_temp_lag_min"] * 60.0)
        else:
            burned = truck.load_factor = truck.fuel_rate_lph = 0.0
            truck.engine_temp_c += (ambient - truck.engine_temp_c) * dt / (p["engine_cooldown_lag_min"] * 60.0)
        tkph = mass_t / p["tyres"] * truck.speed_ms * 3.6
        target = ambient + p["tyre_temp_rise_at_rating_c"] * tkph / p["tyre_tkph_rating"]
        if fault is not None and fault.indicator == "tyre":
            target += self.sim.reliability.indicator_rise(truck, "tyre")
        truck.tyre_temp_c += (target - truck.tyre_temp_c) * dt / (p["tyre_temp_lag_min"] * 60.0)
        self._burned += burned
        if truck.seg is None and truck.local is None:
            truck.engine_kw = truck.speed_ms = 0.0

    def step(self, dt: float) -> None:
        self._burned = 0.0
        unavailable = productive = engines = 0
        kpis = self.sim.kpis
        for truck in self.trucks:
            self._step_truck(truck, dt)
            state = truck.state
            if state in UNAVAILABLE_STATES:
                unavailable += 1
            elif state in PRODUCTIVE_STATES:
                productive += 1
            if truck.engine_on:
                engines += 1
            if state == "standby":
                truck.standby_s += dt
                if truck.standby_s > kpis.longest_standby_s:
                    kpis.longest_standby_s = truck.standby_s
            else:
                truck.standby_s = 0.0
        for loader in self.loaders.values():
            self._step_loader(loader, dt)
        self.station.step(dt)
        if int(self.sim.now) % 60 == 0:
            self._expire_orders()
        kpis.fleet_step(dt, len(self.trucks), unavailable, productive, engines, self._burned)

    # -- forecasts -------------------------------------------------------------

    def forecast(self, queueing: bool = True) -> tuple[float | None, float]:
        """Mean truck cycle (s) and ore delivered to the crusher (t/h) that the
        fixed plan gives on the roads as they are now: fastest routes, nominal
        loading and dumping, the recent queueing time. The cycle is None when
        some truck has no way through."""
        sim, plant, p = self.sim, self.sim.plant, self.sim.params
        loaded, empty = self.cost(True), self.cost(False)
        nominal_t = self.p_truck["payload_t"]
        passes = round(nominal_t / self.bucket_t)
        queue_s = sim.kpis.mean_queue_s() if queueing else 0.0
        cycles, feed, blocked = [], 0.0, False
        for loader in self.loaders.values():
            trucks = sum(1 for t in self.trucks if t.home_loader == loader.name and t.state not in UNAVAILABLE_STATES)
            if not trucks or loader.block is None:
                continue
            block = sim.pit.blocks[loader.block]
            sink = plant.sinks[block.destination]
            out = sim.roads.route(loader.node, sink.node, loaded)
            back = sim.roads.route(sink.node, loader.node, empty)
            if out is None or back is None:
                blocked = True
                continue
            local_s = 2 * math.dist(sim.roads.nodes[loader.node][:2], loader.pos) / (self.p_truck["local_speed_kmh"] / 3.6)
            cycle = (sim.roads.route_time(out, loaded) + sim.roads.route_time(back, empty) + local_s
                     + self.p_loader["spot_s"] + passes * self.p_loader["pass_s"]
                     + sink.spot_s + sink.dump_s + queue_s)
            cycles += [cycle] * trucks
            if sink is plant.crusher:
                feed += trucks * nominal_t * 3600.0 / cycle
        if blocked or not cycles:
            return None, feed
        return sum(cycles) / len(cycles), feed

    # -- published -------------------------------------------------------------

    def _repair_end(self, loader: Loader) -> str:
        sim = self.sim
        if loader.state != "down":
            return ""
        if loader.forced_by:
            end = sim.scenarios[loader.forced_by].expected_end
            return sim.sim_time(end) if end is not None else ""
        return sim.sim_time(sim.now + max(loader.repair_s, 0.0))

    def values(self) -> dict[str, dict]:
        sim, p = self.sim, self.p_truck
        tid, rel = sim.tid, sim.reliability
        out: dict[str, dict] = {}
        on_segment: dict[str, int] = {}
        waiting: dict[str, int] = {}
        for t in self.trucks:
            x, y, z = self.position(t)
            moving = t.seg is not None or (t.local is not None and t.speed_ms > 0)
            if t.seg is not None:
                on_segment[t.seg.name] = on_segment.get(t.seg.name, 0) + 1
                *_, heading = t.seg.point_at(t.dist, t.seg_from)
                t.heading_deg = heading
            elif t.state in ("queue_load", "queue_dump", "standby", "refuel") or (t.state == "service" and not t.in_bay):
                waiting[t.node] = waiting.get(t.node, 0) + 1
            out[t.name] = {
                "state": t.state,
                # The last tenth of a tonne still in the body is shown as 0,1 t, not as an empty body.
                "payload_t": round(max(t.payload_t, 0.1), 1) if t.payload_t > 1e-6 else 0.0,
                "material": t.material,
                "grade_pct": round(100 * t.payload_cu_t / t.payload_t, 3) if t.payload_t > 1e-6 else 0.0,
                "speed_kmh": round(t.speed_ms * 3.6, 1) if moving and t.state != "down" else 0.0,
                "pos_x": round(x, 1), "pos_y": round(y, 1), "pos_z": round(z, 1),
                "heading_deg": round(t.heading_deg, 1),
                "segment": tid(t.seg.name) if t.seg is not None else "",
                "assigned_loader": tid(t.loader_target or t.home_loader),
                "assigned_dump": tid(t.dump_target) if t.dump_target else "",
                "route": ">".join(t.route),
                "assignment_source": t.source,
                "fuel_pct": round(100 * t.fuel_l / p["tank_l"], 1),
                "fuel_rate_lph": round(t.fuel_rate_lph),
                "engine_load_pct": round(100 * t.load_factor),
                "engine_temp_c": round(t.engine_temp_c, 1),
                "tyre_temp_c": round(t.tyre_temp_c, 1),
                "health_pct": round(rel.health_pct(t), 1),
                "hours_since_service": round(t.service_h, 1),
                **dict(zip(("service_reason", "service_kind"), service_view(t.service_reason, t.service_due))),
                "service_hold_until": sim.sim_time(t.service_hold_until) if t.service_hold_until > sim.now else "",
                "standby_reason": t.standby_reason if t.state == "standby" else "",
                "trips": t.trips,
                "cycle_min": round(t.cycle_min, 1),
            }
        for loader in self.loaders.values():
            working = loader.state in ("loading", "waiting")
            if loader.stood_down:
                ready_s = sim.pit.zone_left_s()            # back at the face when the blast zone is lifted
            else:
                ready_s = loader.timer_s if loader.state in ("relocating", "setup") else 0.0
            out[loader.name] = {
                "state": loader.state,
                "block": tid(loader.block) if loader.block else "",
                "material": loader.material if loader.block else "none",
                "truck": tid(loader.truck.name) if loader.truck else "",
                # The pass under way counts: the first bucket of a truck is pass 1.
                "passes": loader.passes + 1 if loader.state == "loading" else 0,
                # Rate at the face being worked: zero while the loader moves, stands idle or is down.
                "dig_rate_tph": round(loader.dug.per_hour(sim.now)) if working else 0,
                "ready_in_min": round(max(ready_s, 0.0) / 60.0, 1),
                "queue_len": len(loader.queue),
                "power_kw": round(loader.power_kw),
                "health_pct": round(rel.health_pct(loader), 1),
                "hours_since_service": round(loader.service_h, 1),
                **dict(zip(("service_reason", "service_kind"), service_view(loader.service_reason, loader.service_due))),
                # When its repair is expected to end: the scenario's expected end, or the mechanics' estimate.
                "repair_end_sim_time": self._repair_end(loader),
                "repair_crews": 1 + len(loader.crews) if loader.state == "down" else 0,
                "pos_x": float(loader.pos[0]), "pos_y": float(loader.pos[1]),
            }
        for seg in sim.roads.segments.values():
            out[seg.name] = {
                "status": seg.status, "closed_by": seg.closed_by,
                "speed_limit_kmh": float(seg.speed_limit_kmh), "grade_pct": seg.grade_pct,
                "rolling_resistance_pct": float(seg.rr_pct),
                "trucks_on": on_segment.get(seg.name, 0), "surface": seg.surface,
                # A limit on top of the surface's: a road opened after an emergency repair, a treated wet ramp.
                "speed_cap_kmh": float(seg.cap_kmh),
            }
        for node in sim.roads.nodes:
            out[node] = {"trucks_waiting": waiting.get(node, 0)}
        out[self.station.name] = self.station.values()
        return out
