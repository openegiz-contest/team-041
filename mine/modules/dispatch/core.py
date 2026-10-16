"""Dispatch: which loader an empty truck goes to, where a loaded truck tips,
and by which road.

For a truck that is about to become empty the module estimates, for every
loader that can work, the time until the truck would leave it loaded:

    travel on the roads open now  +  waiting behind the trucks that get there first  +  loading

and, for an ore face, the wait at the crusher the load would meet. The loader
with the smallest sum wins, after a correction that keeps ore and waste in the
planned proportion: the class that is ahead of the plan is made to look as
many minutes longer as its lead is worth. Ore is not counted as behind while
the plant cannot take it (stockpile full, crusher stopped), so trucks strip
waste instead of standing at the crusher.

A loaded truck goes where its block is destined (the grade-control module may
have set that), unless the crusher is stopped or the wait there would cost
more than tipping on a ROM pad and rehandling the ore later, and the pad is
below its high mark. Every order names the route over the segments open now.

Without the module the simulator keeps each truck on its home loader and
sends an ore truck to a ROM pad only once it has reached a crusher with two
trucks already waiting.
"""
from __future__ import annotations

import math
from collections import deque

from mine.common.haulage import TravelTimes
from mine.common.roads import RoadNetwork
from mine.modules.base import Core, Message, num, short

EMPTY_STATES = ("returning", "queue_load")
MATERIAL_TITLES = {"waste": "вскрыша", "ore": "руда", "low-grade": "забалансовая руда", "clay": "глинистая руда"}
LOADED_STATES = ("hauling", "queue_dump")
RESEND_S = 90.0             # an order that did not show in the twin is repeated after this time


class DispatchCore(Core):
    name = "module-dispatch"
    kpi_label = "Очередь на один рейс самосвала"
    kpi_unit = "мин"
    # What the module works for: stripping kept to the plan while the plant takes all the ore it can, and
    # trucks moved to where they load soonest. Rock moved in total is shown beside it, not as the goal: with a
    # full coarse-ore stockpile the module sends trucks to the waste face, and the total may fall.
    effect_kpi = ("waste_t", "снято вскрыши", "т", 0, "more")
    effect_kpi_extra = ("ore_t+waste_t", "перевезено горной массы", "т", 0, "more")
    effect_note = ("Модуль держит соотношение руды и вскрыши по плану и не ставит самосвалы в очередь к дробилке, "
                   "когда склад дроблёной руды полон: тогда он отправляет их на вскрышу, и перевезённой горной массы "
                   "может стать меньше, а вскрыши — больше. Эффект оценивается по меди, марже и снятой вскрыше.")

    def __init__(self, things: list[dict], params: dict, enabled: bool = True):
        super().__init__(things, params, enabled)
        w = self.world
        self.p = params["dispatch"]
        self.roads = RoadNetwork.from_things(things, params["road"])      # the module's own copy of the road graph
        self.travel = TravelTimes(self.roads, params["truck"])
        for segment in w.by_kind["road-segment"]:
            self._road_state(segment)
        self.trucks = w.by_kind["truck"]
        self.loaders = w.by_kind["loader"]
        self.crusher = w.by_kind["crusher"][0]
        self.waste_dump = w.by_kind["waste-dump"][0]
        self.pads = {w.get(pad, "role"): pad for pad in w.by_kind["rom-pad"]}
        # The clay-ore plant ОФ-2 and the clay-ore stockpile beside it: a load destined there goes there.
        self.clay_sinks = [*w.by_kind.get("clay-plant", []), *w.by_kind.get("clay-stockpile", [])]
        self.sink_node = {name: w.link(name, "access_node")
                          for name in (self.crusher, self.waste_dump, *self.pads.values(), *self.clay_sinks)}
        self.payload_t = float(params["truck"]["payload_t"])
        self.clay_min = float(params["clay"]["clay_ore_min_pct"])
        self.passes = max(1, int(w.spec(self.loaders[0], "passes_per_truck", 4)))
        nominal_s = params["loader"]["spot_s"] + self.passes * params["loader"]["pass_s"] + 20.0
        self.service_s = {loader: nominal_s for loader in self.loaders}     # learnt from the loadings observed
        self.dump_s = params["crusher"]["spot_s"] + params["crusher"]["dump_s"]
        self.plan_ore = float(params["plan"]["ore_share"])
        self.window_s = self.p["proportion_window_h"] * 3600.0
        self.restart()

    def restart(self) -> None:
        super().restart()
        self.loads: deque[tuple[float, bool, float]] = deque()       # (time, is ore, tonnes) of every load seen
        self.trips: deque[tuple[float, float, float]] = deque()      # (time, queueing s, tonnes) of every trip finished
        self.since: dict[str, float] = {}                            # when each truck entered its present state
        self.served_from: dict[str, float] = {}                      # when the truck under service reached its loader
        self.queue_s: dict[str, float] = {}                          # queueing in the trip under way
        self.payload: dict[str, float] = {}
        self.next_loader: dict[str, str] = {}                        # ordered for the next load, until the truck loads there
        self.dump_order: dict[str, str] = {}                         # ordered for the load on board
        self.sent_at: dict[tuple[str, str], float] = {}
        self.reassigned = self.diverted = 0
        self.last_replan = 0.0

    # -- the picture of the mine ------------------------------------------------------

    def _road_state(self, name: str) -> None:
        w, seg = self.world, self.roads.segments[name]
        seg.closures = {"closed"} if w.get(name, "status") == "closed" else set()
        seg.speed_limit_kmh = float(w.get(name, "speed_limit_kmh", seg.dry_limit_kmh))
        seg.rr_pct = float(w.get(name, "rolling_resistance_pct", seg.dry_rr_pct))
        self.roads.version += 1

    def changed(self, name: str, old: dict, new: dict) -> None:
        kind = self.world.attrs.get(name, {}).get("kind")
        if kind == "road-segment":
            if any(old.get(k) != new.get(k) for k in ("status", "speed_limit_kmh", "rolling_resistance_pct")):
                self._road_state(name)
        elif kind == "truck" and old.get("state") != new.get("state"):
            self._truck_moved_on(name, old.get("state"), new)

    def _truck_moved_on(self, truck: str, was: str | None, new: dict) -> None:
        """Keep the books of a trip from the truck's state changes."""
        now, state = self.world.now, new.get("state")
        spent = now - self.since.get(truck, now)
        self.since[truck] = now
        if was in ("queue_load", "queue_dump"):
            self.queue_s[truck] = self.queue_s.get(truck, 0.0) + spent
        if state == "approach" or (state == "loading" and was != "approach"):
            self.served_from[truck] = now           # the service at the loader starts with the drive to its face
        if was == "loading" and state == "hauling":
            loader = short(new.get("assigned_loader", ""))
            spent = now - self.served_from.pop(truck, now - spent)
            if loader in self.service_s and 60.0 <= spent <= 900.0:
                self.service_s[loader] += 0.2 * (spent - self.service_s[loader])
            tonnes = float(new.get("payload_t") or 0.0)
            self.payload[truck] = tonnes
            self.loads.append((now, new.get("material") != "waste", tonnes))
        if state in ("approach", "loading"):
            self.next_loader.pop(truck, None)           # the order for this load is carried out
        if was == "dumping" and state != "down":
            tonnes = self.payload.pop(truck, 0.0)
            if tonnes > 0:
                queue_s = self.queue_s.get(truck, 0.0)
                self.trips.append((now, queue_s, tonnes))
                self.tally.add(self.enabled, queue_s / 60.0, 1.0)        # minutes of queueing per trip
            self.queue_s[truck] = 0.0
            self.dump_order.pop(truck, None)

    def _node_of(self, truck: str) -> tuple[str, float]:
        """The node a truck will be at next and the seconds until it is there."""
        w = self.world
        v = w.values[truck]
        loaded = float(v.get("payload_t") or 0.0) > 1.0
        route = v.get("route").split(">") if v.get("route") else []
        seg = self.roads.segments.get(short(v.get("segment") or ""))
        pos = (float(v.get("pos_x") or 0.0), float(v.get("pos_y") or 0.0))
        if seg is not None:
            ahead = seg.b
            for a, b in zip(route, route[1:]):
                if {a, b} == {seg.a, seg.b}:
                    ahead = b
            behind = seg.other(ahead)
            span = math.dist(self.roads.nodes[behind][:2], self.roads.nodes[ahead][:2]) or 1.0
            left = min(1.0, math.dist(pos, self.roads.nodes[ahead][:2]) / span)
            return ahead, left * self.travel.cost(loaded)(seg, behind)
        state = v.get("state")
        if state in ("queue_dump", "dumping") and short(v.get("assigned_dump") or "") in self.sink_node:
            return self.sink_node[short(v["assigned_dump"])], 0.0
        if state in ("queue_load", "approach", "loading") and short(v.get("assigned_loader") or "") in self.loaders \
                and self._loader_node(short(v["assigned_loader"])):
            return self._loader_node(short(v["assigned_loader"])), 0.0
        return min(self.roads.nodes, key=lambda n: math.dist(pos, self.roads.nodes[n][:2])), 0.0

    def _loader_node(self, loader: str) -> str:
        block = short(self.world.get(loader, "block", ""))
        return self.world.link(block, "access_node") if block in self.world.attrs else ""

    def _eta_s(self, truck: str, goal: str) -> float | None:
        """Seconds until the truck could stand at node `goal`."""
        if goal not in self.roads.nodes:
            return None                     # a loader without a block has no face to drive to
        node, lead_s = self._node_of(truck)
        v = self.world.values[truck]
        if v.get("state") == "dumping":
            lead_s += self.dump_s / 2
        found = self.travel.route(node, goal, float(v.get("payload_t") or 0.0) > 1.0 and v.get("state") != "dumping")
        return None if found is None else lead_s + found[1]

    def _loader_works(self, loader: str) -> bool:
        v = self.world.values[loader]
        return v.get("state") in ("loading", "waiting", "relocating", "setup") and bool(v.get("block")) \
            and float(v.get("ready_in_min") or 0.0) <= 30.0

    def _headed_for(self, loader: str, but: str) -> list[float]:
        """Arrival times (s from now) of the other trucks committed to a loader:
        those waiting there, those driving there, and those the module has
        already ordered there for their next load (still tipping, standing for
        a crew change or refuelling)."""
        w, node, etas = self.world, self._loader_node(loader), []
        for truck in self.trucks:
            if truck == but:
                continue
            v = w.values[truck]
            state = v.get("state")
            ordered = self.next_loader.get(truck)
            if state == "queue_load" and short(v.get("assigned_loader") or "") == loader:
                etas.append(0.0)
            elif state in ("down", "service", "to_service", "approach", "loading", "hauling", "queue_load"):
                continue
            elif ordered == loader or (ordered is None and state == "returning"
                                       and short(v.get("assigned_loader") or "") == loader):
                eta = self._eta_s(truck, node)
                if eta is not None:
                    etas.append(eta)
        return sorted(etas)

    def _crusher_arrivals(self, but: str) -> list[float]:
        """When (seconds from now) every other load that is on its way to the
        crusher, or will be once its truck is loaded, gets there."""
        w = self.world
        node = self.sink_node[self.crusher]
        arrivals = [0.0] * int(w.get(self.crusher, "queue_len", 0))
        for truck in self.trucks:
            v = w.values[truck]
            if truck != but and v.get("state") == "hauling" and short(v.get("assigned_dump") or "") == self.crusher:
                eta = self._eta_s(truck, node)
                if eta is not None:
                    arrivals.append(eta)
        for loader in self.loaders:
            v = w.values[loader]
            block = short(v.get("block") or "")
            if not self._loader_works(loader) or w.get(block, "destination") != self.crusher:
                continue
            haul = self.travel.route(self._loader_node(loader), node, True)
            if haul is None:
                continue
            service_s = self.service_s[loader]
            free_s = float(v.get("ready_in_min") or 0.0) * 60.0
            if v.get("truck") and short(v["truck"]) != but:
                free_s += service_s * (1.0 - min(int(v.get("passes") or 0), self.passes) / self.passes)
                arrivals.append(free_s + haul[1])
            for eta in self._headed_for(loader, but):
                free_s = max(free_s, eta) + service_s
                arrivals.append(free_s + haul[1])
        return sorted(arrivals)

    def _crusher_wait_s(self, arrive_s: float, arrivals: list[float]) -> float:
        """Expected wait at the crusher for a load that gets there in `arrive_s`
        seconds: every load that arrives earlier is served first."""
        w = self.world
        if w.get(self.crusher, "state") == "stopped":
            return 1800.0
        # A full stockpile lets the crusher pass only what the mill draws; else its own rating limits it.
        held_back = w.get(self.crusher, "state") == "blocked" or self._stockpile_level() >= self.p["stockpile_full_pct"]
        rate = float(w.get("mill", "feed_tph", 0.0)) if held_back else float(w.get(self.crusher, "capacity_tph", 0.0))
        per_truck_s = max(self.dump_s, self.payload_t / max(rate, 600.0) * 3600.0)
        free_s = 0.0
        for eta in arrivals:
            if eta >= arrive_s:
                break
            free_s = max(free_s, eta) + per_truck_s
        return max(0.0, free_s - arrive_s)

    def _stockpile_level(self) -> float:
        return float(self.world.get("stockpile", "level_pct", 0.0))

    def ore_need(self) -> float:
        """How much the plant wants ore, 0 to 1: nothing while the stockpile is
        full or the crusher stands, everything once the stockpile is down to its low mark."""
        if self.world.get(self.crusher, "state") == "stopped":
            return 0.0
        full, low = self.p["stockpile_full_pct"], self.p["stockpile_low_pct"]
        return min(1.0, max(0.0, (full - self._stockpile_level()) / (full - low)))

    def ore_share(self) -> float | None:
        now = self.world.now
        while self.loads and now - self.loads[0][0] > self.window_s:
            self.loads.popleft()
        total = sum(t for _, _, t in self.loads)
        if len(self.loads) < 12 or total <= 0:
            return None
        return sum(t for _, ore, t in self.loads if ore) / total

    # -- choosing a loader --------------------------------------------------------------

    def _options(self, truck: str) -> list[dict]:
        """Every loader that can work, with the minutes it would cost this truck."""
        w = self.world
        share = self.ore_share()
        lead = 0.0 if share is None else share - self.plan_ore
        need = self.ore_need()
        crusher_arrivals = self._crusher_arrivals(truck)
        gain_s = self.p["proportion_gain_min"] * 60.0
        options = []
        for loader in self.loaders:
            if not self._loader_works(loader):
                continue
            node = self._loader_node(loader)
            eta = self._eta_s(truck, node)
            if eta is None:
                continue                                 # no open road to this loader
            v = w.values[loader]
            service_s = self.service_s[loader]
            free_s = float(v.get("ready_in_min") or 0.0) * 60.0
            if v.get("truck") and short(v["truck"]) != truck:
                free_s += service_s * (1.0 - min(int(v.get("passes") or 0), self.passes) / self.passes)
            for other in self._headed_for(loader, truck):
                if other < eta:
                    free_s = max(free_s, other) + service_s
            wait_s = max(0.0, free_s - eta)
            is_ore = v.get("material") != "waste"
            block = short(v.get("block") or "")
            dump_wait_s = 0.0
            if is_ore and w.get(block, "destination") == self.crusher:
                haul = self.travel.route(node, self.sink_node[self.crusher], True)
                if haul is not None:
                    dump_wait_s = self._crusher_wait_s(eta + wait_s + service_s + haul[1], crusher_arrivals)
                    if self._pad_for(v.get("material")):
                        # The load can tip on a ROM pad instead: the wait is worth no more than that costs.
                        dump_wait_s = min(dump_wait_s, self.p["divert_wait_min"] * 60.0)
            if is_ore:
                balance_s = gain_s * max(0.0, lead)             # ore is ahead of the plan: stripping must not fall behind
            else:
                balance_s = gain_s * max(0.0, -lead) * need     # waste is ahead, and the plant can take the ore
            if self._face_is_clay(block):
                balance_s += self._clay_face_cost_s(need)
            options.append({"loader": loader, "node": node, "ore": is_ore, "travel": eta, "wait": wait_s,
                            "load": service_s, "dump_wait": dump_wait_s, "balance": balance_s,
                            "total": eta + wait_s + service_s + dump_wait_s + balance_s})
        return sorted(options, key=lambda o: (o["total"], o["loader"]))

    def _face_is_clay(self, block: str) -> bool:
        """The face digs clay ore (by its class or by the clay the face shows): it feeds ОФ-2, not the crusher."""
        w = self.world
        return block in w.attrs and (w.get(block, "material") == "clay"
                                     or float(w.get(block, "clay_pct", 0.0)) >= self.clay_min)

    def _clay_face_cost_s(self, ofs1_need: float) -> float:
        """What a trip to a clay face is made to cost on top of its time: up to `dispatch.clay_full_min` while the
        feed stockpile of ОФ-2 has no room for it, and up to that again while ОФ-1 needs ore, which clay ore does
        not bring it."""
        if not self.clay_sinks:
            return 0.0
        w = self.world
        plant = self.clay_sinks[0]
        capacity = float(w.spec(plant, "feed_bin_t", 1.0)) or 1.0
        room = float(w.get(plant, "room_t", capacity))
        clay_need = min(1.0, max(0.0, room / (0.4 * capacity)))
        return self.p["clay_full_min"] * 60.0 * (1.0 - clay_need)

    def _order_loader(self, truck: str, why: str = "") -> None:
        w = self.world
        options = self._options(truck)
        if not options:
            return
        best = options[0]
        loader = best["loader"]
        self.next_loader[truck] = loader
        self.sent_at[(truck, "load")] = w.now
        node, _ = self._node_of(truck)
        found = self.travel.route(node, best["node"], False)
        body = {"loader": w.tid(loader)}
        if found is not None:
            body["route"] = ">".join(found[0])
        home = w.link(truck, "home_loader")
        if loader != home:
            self.reassigned += 1
        minutes = lambda o: (o["travel"] + o["wait"] + o["load"]) / 60.0
        text = (f"{w.title(truck)} → {w.title(loader)}: путь {num(best['travel'] / 60)} мин, очередь {num(best['wait'] / 60)}, "
                f"погрузка {num(best['load'] / 60)}")
        if best["dump_wait"] > 30:
            text += f", ожидание у дробилки {num(best['dump_wait'] / 60)}"
        if len(options) > 1:
            other = options[1]
            text += f"; у {w.title(other['loader']).split()[-1]} было бы {num(minutes(other))} мин"
            if other["dump_wait"] > 30:
                text += f" и {num(other['dump_wait'] / 60)} мин у дробилки"
        share = self.ore_share()
        if share is not None:
            text += f". Доля руды {100 * share:.0f} % при плане {100 * self.plan_ore:.0f} %"
            if not best["ore"] and share < self.plan_ore and self.ore_need() < 0.5:
                text += f", склад дроблёной руды заполнен на {self._stockpile_level():.0f} %: вскрыша сверх плана"
        self.send(truck, "assign", body, f"{text}.{' ' + why if why else ''}")

    # -- choosing a dump ----------------------------------------------------------------

    def _pad_for(self, material: str) -> str | None:
        """The ROM pad for this class of ore, if a load still fits on it."""
        pad = self.pads.get("low-grade" if material == "low-grade" else "high-grade")
        if pad is None:
            return None
        w = self.world
        room = self.p["pad_max_pct"] / 100.0 * float(w.spec(pad, "capacity_t", 0.0)) - float(w.get(pad, "level_t", 0.0)) \
            - (int(w.get(pad, "queue_len", 0)) + 2) * self.payload_t * 1.1
        return pad if room > 0 else None

    def _clay_sink(self) -> str | None:
        """Where a load of clay ore turned away from the crusher goes: ОФ-2 while its feed stockpile has room, else
        the clay-ore stockpile."""
        w = self.world
        for sink in self.clay_sinks:
            if float(w.get(sink, "room_t", 0.0)) > 3 * self.payload_t:
                return sink
        return None

    def _order_dump(self, truck: str, loader_node: str | None = None) -> None:
        w = self.world
        v = w.values[truck]
        loader = short(v.get("assigned_loader") or "")
        block = short(w.get(loader, "block", "")) if v.get("state") in ("approach", "loading") else ""
        material = w.get(block, "material") if block else v.get("material")
        if material in (None, "none"):
            return
        planned = w.get(block, "destination") if block else short(v.get("assigned_dump") or "")
        if material == "waste" or planned not in self.sink_node:
            planned = self.waste_dump if material == "waste" else self.crusher
        dump, why = planned, ""
        node, lead_s = (self._loader_node(loader), self.service_s[loader] / 2) if block else self._node_of(truck)
        if planned == self.crusher:
            haul = self.travel.route(node, self.sink_node[self.crusher], True)
            wait_s = self._crusher_wait_s(lead_s + (haul[1] if haul else 0.0), self._crusher_arrivals(truck))
            pad = self._pad_for(material) if material != "clay" else self._clay_sink()
            stopped = w.get(self.crusher, "state") == "stopped"
            if pad and (stopped or wait_s > self.p["divert_wait_min"] * 60.0):
                dump = pad
                why = "дробилка остановлена" if stopped else f"у дробилки ждать {num(wait_s / 60)} мин"
                self.diverted += 1
        found = self.travel.route(node, self.sink_node[dump], True)
        body = {"dump": w.tid(dump)}
        if found is not None:
            body["route"] = ">".join(found[0])
        self.dump_order[truck] = dump
        self.sent_at[(truck, "dump")] = w.now
        minutes = f", {num((found[1] if found else 0.0) / 60)} мин пути" if found else ""
        self.send(truck, "assign", body,
                  f"{w.title(truck)} ({MATERIAL_TITLES.get(material, 'руда')}) → {w.title(dump)}{minutes}"
                  f"{': ' + why if why else ''}.")

    # -- one round ----------------------------------------------------------------------

    def _due(self, truck: str, kind: str) -> bool:
        return self.world.now - self.sent_at.get((truck, kind), -1e9) >= RESEND_S

    def decide(self, now: float) -> None:
        w = self.world
        crusher_stopped = w.get(self.crusher, "state") == "stopped"
        for truck in self.trucks:
            v = w.values[truck]
            state, source = v.get("state"), v.get("assignment_source")
            if state == "dumping":
                if truck not in self.next_loader:
                    self._order_loader(truck)
            elif state in EMPTY_STATES:
                target = short(v.get("assigned_loader") or "")
                if target in self.loaders and not self._loader_works(target):
                    if self._due(truck, "load"):
                        self._order_loader(truck, f"{w.title(target)} не работает.")
                elif source != "dispatch" and self._due(truck, "load"):
                    self._order_loader(truck)
            elif state in ("approach", "loading"):
                if truck not in self.dump_order:
                    self._order_dump(truck)
            elif state in LOADED_STATES:
                at_crusher = short(v.get("assigned_dump") or "") == self.crusher
                if (source != "dispatch" or (crusher_stopped and at_crusher and self.dump_order.get(truck) == self.crusher)) \
                        and self._due(truck, "dump"):
                    self._order_dump(truck)
        if now - self.last_replan >= self.p["replan_min"] * 60.0:
            self.last_replan = now
            self._review_queues()

    def _review_queues(self) -> None:
        """Move a truck out of a queue when another loader would have it loaded clearly sooner."""
        w = self.world
        gain_s = self.p["requeue_gain_min"] * 60.0
        for truck in self.trucks:
            v = w.values[truck]
            if v.get("state") != "queue_load" or not self._due(truck, "load"):
                continue
            here = short(v.get("assigned_loader") or "")
            options = self._options(truck)
            staying = next((o for o in options if o["loader"] == here), None)
            if options and staying and options[0]["loader"] != here and options[0]["total"] + gain_s < staying["total"]:
                self._order_loader(truck, f"Переведён из очереди у {w.title(here)}.")

    def revert(self) -> list[Message]:
        self.next_loader.clear()
        self.dump_order.clear()
        self.sent_at.clear()
        return [(truck, "release", {}) for truck in self.trucks]

    def switched_on(self) -> None:
        self.reassigned = self.diverted = 0

    # -- indicators -----------------------------------------------------------------------

    def measure(self, now: float, dt: float) -> None:
        while self.trips and now - self.trips[0][0] > 3600.0:
            self.trips.popleft()

    def kpi_value(self) -> float:
        return round(sum(q for _, q, _ in self.trips) / len(self.trips) / 60.0, 2) if self.trips else 0.0

    def own_features(self) -> dict:
        share = self.ore_share()
        return {
            "avg_queue_min": self.kpi_value(),
            "tonnes_per_h": round(sum(t for _, _, t in self.trips)),
            "ore_share_pct": round(100.0 * share, 1) if share is not None else 0.0,
            "plan_ore_share_pct": round(100.0 * self.plan_ore, 1),
            "reassigned_total": self.reassigned,
            "diverted_total": self.diverted,
        }

    def effect_text(self) -> str:
        text = self.effect_sentence() + self.since_text("очередь на рейс", "мин", 2)
        if self.enabled and self.tally.hours(True) > 0:
            text += (f" Направлено к незакреплённому экскаватору: {self.reassigned} раз, "
                     f"на рудный склад вместо очереди у дробилки: {self.diverted} рейсов.")
        return text
