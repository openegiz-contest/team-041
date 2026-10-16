"""The pit: blast blocks and their life cycle, drill rigs, routine blasts with
their exclusion zone, the short-term plan that sends loaders and rigs to
their next block, and the pushbacks that keep the mine supplied with rock.

    insitu -> drilling -> drilled -> charged -> blasted -> mining -> depleted -> (pushback) -> insitu

The powder factor of a block is set when it is charged (the charge is
designed then); its fragment size when it is blasted. Mass blasts are fired
in daylight only, at most one a day: the routine window (world.yaml,
`clock.blast_times`) every day, the second daylight window
(`clock.extra_blast_times`) only on a day when the broken stock in the pit is
below `blast.extra_window_below_h` hours of digging at the planned fleet rate.

Without the grade-control module ore goes to the crusher, low-grade ore to
the low-grade stockpile and waste to the dump, by the class of the block
(mine/model/geology.py). The module changes the destination of a block with
`set-destination` and gives it back with `release`; a destination that is not
repeated within the decision time-out returns to the cut-off rule.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from mine.model.geology import BlockModel
from mine.common.roads import BLAST
from mine.sim.text import num

if TYPE_CHECKING:
    from mine.sim.fleet import Loader
    from mine.sim.model import Simulation

BROKEN = ("blasted", "mining")
PREPARED = ("drilled", "charged", "blasted")


def short(thing_id: str) -> str:
    return thing_id.split(":", 1)[1] if ":" in thing_id else thing_id


def initial(thing: dict, feature: str):
    return thing["features"][feature]["properties"]["value"]


@dataclass
class Block:
    name: str
    title: str
    bench: str
    node: str
    centre: tuple[float, float]
    face: tuple[float, float]
    outline: list
    tonnes_initial: float
    holes_planned: int
    order: int
    status: str
    material: str
    grade_pct: float
    hardness_bwi: float
    tonnes: float
    frag_p80_mm: float
    powder_factor: float
    destination: str
    destination_source: str = "cutoff"      # cutoff / grade-control / operator
    destination_reason: str = ""
    destination_at: float = 0.0
    hold_until: float = 0.0                 # an operator's destination stands until then
    hold_noted: bool = False
    charged_holes: float = 0.0
    charged_at: float = 0.0                 # when charging finished; the oldest charge is fired first
    reserved_by: str = ""                   # the loader or rig working on the block

    @property
    def is_ore(self) -> bool:
        return self.material != "waste"


@dataclass
class DrillRig:
    name: str
    title: str
    block: str | None
    holes_done: int
    holes_planned: int
    pos: tuple[float, float]
    state: str = "drilling"                 # drilling / positioning / tramming / standby
    depth_m: float = 0.0                    # of the hole being drilled
    move_s: float = 0.0                     # left of the move to the next hole
    tram_s: float = 0.0
    tram_total_s: float = 0.0
    tram_from: tuple[float, float] = (0.0, 0.0)
    rate_m_h: float = 0.0
    metres: float = 0.0
    stood_down: bool = False


class Pit:
    def __init__(self, sim: "Simulation"):
        self.sim = sim
        p = sim.params
        self.p_drill, self.p_blast = p["drill"], p["blast"]
        self.bwi_ref = p["geology"]["bwi_mean_kwh_t"]
        self.pf_ref = (self.p_blast["powder_factor_min_kg_m3"] + self.p_blast["powder_factor_max_kg_m3"]) / 2

        self.benches: dict[str, dict] = {}
        for t in sim.by_kind["bench"]:
            self.benches[short(t["thingId"])] = {"title": t["attributes"]["name"], "pushback": initial(t, "pushback"),
                                                 "elevation_m": initial(t, "elevation_m")}
        self.blocks: dict[str, Block] = {}
        for t in sim.by_kind["block"]:
            a, spec = t["attributes"], t["attributes"]["spec"]
            self.blocks[short(t["thingId"])] = Block(
                name=short(t["thingId"]), title=a["name"], bench=short(a["links"]["bench"]),
                node=short(a["links"]["access_node"]), centre=(spec["centre_x"], spec["centre_y"]),
                face=(spec["face_x"], spec["face_y"]), outline=a["geo"]["polygon"],
                tonnes_initial=float(spec["tonnes_initial"]), holes_planned=spec["holes_planned"],
                order=spec["mining_order"], status=initial(t, "status"), material=initial(t, "material"),
                grade_pct=initial(t, "grade_pct"), hardness_bwi=initial(t, "hardness_bwi"),
                tonnes=initial(t, "tonnes_remaining"), frag_p80_mm=initial(t, "frag_p80_mm"),
                powder_factor=initial(t, "powder_factor_kg_m3"), destination=initial(t, "destination"))
        for block in self.blocks.values():
            if block.status in BROKEN:
                # Blocks blasted before the start get the fragmentation of their own hardness.
                block.frag_p80_mm = self.fragmentation(block.powder_factor, block.hardness_bwi)
            if block.status == "charged":
                block.charged_holes = float(block.holes_planned)
                block.powder_factor = block.powder_factor or self.pf_ref
        self.model = BlockModel(sim.world, p, [{"bench": b.bench, "x": b.centre[0], "y": b.centre[1]}
                                               for b in self.blocks.values()])

        self.rigs: list[DrillRig] = []
        for t in sim.by_kind["drill-rig"]:
            block = self.blocks[short(initial(t, "block"))]
            rig = DrillRig(name=short(t["thingId"]), title=t["attributes"]["name"], block=block.name,
                           holes_done=initial(t, "holes_done"), holes_planned=block.holes_planned, pos=block.centre)
            block.reserved_by, block.status = rig.name, "drilling"
            rig.pos = self._hole_position(block, rig.holes_done)
            self.rigs.append(rig)
        self.charging: Block | None = None

        # Routine blasts: one daylight window a day, a second one when the broken stock runs short.
        hm = lambda text: tuple(int(part) for part in text.split(":"))
        self.blast_times = [(*hm(text), False) for text in sim.clock["blast_times"]]
        self.blast_times += [(*hm(text), True) for text in sim.clock.get("extra_blast_times", [])]
        self.short_stock_t = float(p["blast"]["extra_window_below_h"]) * p["plan"]["rock_tph"]
        self.blast_at, self.blast_extra = self._next_window(0.0)
        self.blast_phase = "idle"               # idle / clearing / reentry
        self.blast_block: Block | None = None
        self.blast_by = ""                      # the scenario that ordered the blast; '' for a routine one
        self.last_blast: Block | None = None
        self.timeout_s = p["sim"]["decision_timeout_min"] * 60.0
        self.zone_until = 0.0
        self.zone_segments: list = []
        self.delay_logged = False
        self.blasts_fired = 0

    # -- geometry ---------------------------------------------------------------

    def _hole_position(self, block: Block, index: int) -> tuple[float, float]:
        """Where the rig stands for hole number `index` of the block's pattern."""
        # A block on a curved bench has six points (a middle point on each long side), one on the floor four.
        outline = block.outline
        p0, p1, p3 = (outline[0], outline[2], outline[5]) if len(outline) == 6 else (outline[0], outline[1], outline[3])
        width, depth = math.dist(p0, p1), math.dist(p0, p3)
        columns = max(1, round(width / self.p_drill["spacing_m"]))
        rows = max(1, math.ceil(block.holes_planned / columns))
        index = min(index, block.holes_planned - 1)
        row, column = divmod(index, columns)
        if row % 2:
            column = columns - 1 - column       # the rig works back and forth along the rows
        u, v = (column + 0.5) / columns, (row + 0.5) / rows
        return (round(p0[0] + (p1[0] - p0[0]) * u + (p3[0] - p0[0]) * v, 1),
                round(p0[1] + (p1[1] - p0[1]) * u + (p3[1] - p0[1]) * v, 1))

    def travel_m(self, pos: tuple[float, float], node: str, block: Block) -> float:
        """Distance a tracked machine covers from `pos` (served by road node `node`) to a block."""
        if node == block.node:
            return math.dist(pos, block.face)
        nodes = self.sim.roads.nodes
        return (math.dist(pos, nodes[node][:2]) + self.sim.roads.shortest_length(node, block.node)
                + math.dist(nodes[block.node][:2], block.face))

    # -- fragmentation ------------------------------------------------------------

    def fragmentation(self, powder_factor: float, hardness_bwi: float) -> float:
        """P80 of the muck pile: finer with more explosive per cubic metre
        (Kuznetsov's exponent), coarser in harder rock."""
        p = self.p_blast
        p80 = (p["p80_ref_mm"] * (powder_factor / self.pf_ref) ** p["p80_powder_exponent"]
               * (hardness_bwi / self.bwi_ref) ** p["p80_hardness_exponent"])
        return round(min(p["p80_max_mm"], max(p["p80_min_mm"], p80)))

    # -- the short-term plan --------------------------------------------------------

    def next_block(self, loader: "Loader") -> Block | None:
        """The blasted block a free loader should move to: keep the planned
        number of loaders in ore, then prefer the same bench and the shortest move."""
        candidates = [b for b in self.blocks.values() if b.status == "blasted" and not b.reserved_by]
        if not candidates:
            return None
        others_in_ore = sum(1 for other in self.sim.fleet.loaders.values()
                            if other is not loader and other.block and self.blocks[other.block].is_ore)
        want_ore = others_in_ore < self.sim.params["loader"]["ore_faces"]
        preferred = [b for b in candidates if b.is_ore == want_ore] or candidates
        return min(preferred, key=lambda b: (self.travel_m(loader.pos, loader.node, b), b.name))

    def _next_drill_block(self, rig: DrillRig) -> Block | None:
        """The in-situ block a free rig should drill: keep ore and waste ready in
        the proportion of the plan, and stop when enough blocks are prepared."""
        blocks = self.blocks.values()
        insitu = [b for b in blocks if b.status == "insitu" and not b.reserved_by]
        ahead = [b for b in blocks if b.status in PREPARED or b.status == "drilling"]
        if not insitu or len(ahead) >= self.p_drill["drill_ahead_blocks"]:
            return None
        ore_share = self.sim.params["plan"]["ore_share"]
        ore_t = sum(b.tonnes for b in ahead if b.is_ore)
        waste_t = sum(b.tonnes for b in ahead if not b.is_ore)
        want_ore = ore_t / ore_share <= waste_t / (1.0 - ore_share)
        preferred = [b for b in insitu if b.is_ore == want_ore] or insitu
        return min(preferred, key=lambda b: (math.dist(rig.pos, b.centre), b.name))

    def depleted(self, block: Block) -> None:
        block.status, block.tonnes, block.reserved_by = "depleted", 0.0, ""
        self.sim.log("info", block.name, f"{block.title} отработан")

    # -- drilling and charging ------------------------------------------------------

    def _step_rig(self, rig: DrillRig, dt: float) -> None:
        sim, p = self.sim, self.p_drill
        rig.rate_m_h = 0.0
        if rig.stood_down:
            rig.state = "standby"
            return
        if rig.block is None:
            rig.state = "standby"
            if int(sim.now) % 60:
                return                          # look for work once a minute
            block = self._next_drill_block(rig)
            if block is None:
                return
            block.reserved_by, block.status = rig.name, "drilling"
            rig.block, rig.holes_done, rig.holes_planned, rig.depth_m = block.name, 0, block.holes_planned, 0.0
            target = self._hole_position(block, 0)
            rig.tram_from = rig.pos
            rig.tram_total_s = rig.tram_s = math.dist(rig.pos, target) / (p["tram_kmh"] / 3.6)
            rig.state = "tramming"
            sim.log("info", rig.name, f"{rig.title} переезжает на {block.title}")
            return
        block = self.blocks[rig.block]
        if rig.state == "tramming":
            rig.tram_s -= dt
            target = self._hole_position(block, 0)
            f = 1.0 - max(rig.tram_s, 0.0) / rig.tram_total_s if rig.tram_total_s > 0 else 1.0
            rig.pos = (round(rig.tram_from[0] + (target[0] - rig.tram_from[0]) * f, 1),
                       round(rig.tram_from[1] + (target[1] - rig.tram_from[1]) * f, 1))
            if rig.tram_s > 0:
                return
        if rig.move_s > 0:
            rig.state = "positioning"           # moving to the next hole of the pattern
            rig.move_s -= dt
            return
        rig.state = "drilling"
        # Harder rock drills slower, in inverse proportion to the work index.
        rig.rate_m_h = p["penetration_m_h"] * self.bwi_ref / block.hardness_bwi
        drilled = rig.rate_m_h * dt / 3600.0
        rig.depth_m += drilled
        rig.metres += drilled
        if rig.depth_m < p["hole_depth_m"]:
            return
        rig.depth_m, rig.holes_done = 0.0, rig.holes_done + 1
        if rig.holes_done >= block.holes_planned:
            block.status, block.reserved_by, rig.block = "drilled", "", None
            sim.log("info", block.name, f"{block.title} обурен: {block.holes_planned} скважин, "
                                        f"{block.holes_planned * p['hole_depth_m']:.0f} м")
            return
        rig.move_s = p["move_min_per_hole"] * 60.0
        rig.pos = self._hole_position(block, rig.holes_done)

    def _step_charging(self, dt: float) -> None:
        if self.charging is None:
            self.charging = min((b for b in self.blocks.values() if b.status == "drilled"),
                                key=lambda b: b.name, default=None)
            if self.charging is None:
                return
        block = self.charging
        block.charged_holes += self.p_blast["charging_holes_per_h"] * dt / 3600.0
        if block.charged_holes >= block.holes_planned:
            block.status, block.charged_holes, self.charging = "charged", float(block.holes_planned), None
            block.charged_at = self.sim.now
            block.powder_factor = self._design_charge(block)
            self.sim.log("info", block.name, f"{block.title} заряжен: {block.holes_planned} скважин, удельный расход ВВ "
                                             f"{num(block.powder_factor, 2)} кг/м3 (около "
                                             f"{self.p_blast['charge_per_hole_kg'] * block.powder_factor / self.pf_ref:.0f} кг "
                                             f"на скважину), ждёт взрыва")

    def _design_charge(self, block) -> float:
        """Powder factor of the charge put into a block, kg/m3: within the range for hard rock. Drawn from the
        block's own stream, so that it does not depend on what was drawn before."""
        p = self.p_blast
        return round(self.sim.rng(f"block/{block.name}/charge").uniform(p["powder_factor_min_kg_m3"], p["powder_factor_max_kg_m3"]), 3)

    # -- blasting -------------------------------------------------------------------

    def _next_window(self, after_s: float) -> tuple[float, bool]:
        """Simulated second of the first blast window later than `after_s`, and whether it is the extra one."""
        moment = self.sim.epoch + timedelta(seconds=after_s)
        best = None
        for day in (0, 1):
            for hour, minute, extra in self.blast_times:
                candidate = datetime(moment.year, moment.month, moment.day, hour, minute) + timedelta(days=day)
                if candidate > moment and (best is None or candidate < best[0]):
                    best = (candidate, extra)
        return (best[0] - self.sim.epoch).total_seconds(), best[1]

    def expected_window(self) -> float:
        """The next window a routine blast is expected in: an extra window counts only while the broken stock is short."""
        at, extra = self.blast_at, self.blast_extra
        if self.blast_phase != "idle":
            return at
        for _ in range(4):
            if not extra or self.broken_stock_t() < self.short_stock_t:
                return at
            at, extra = self._next_window(at)
        return at

    def broken_stock_t(self) -> float:
        return sum(b.tonnes for b in self.blocks.values() if b.status in BROKEN)

    def in_zone(self, x: float, y: float) -> bool:
        """True while a blast is being prepared or fired and the point is inside its exclusion zone."""
        block = self.blast_block
        return block is not None and math.dist((x, y), block.centre) < self.p_blast["exclusion_radius_m"]

    def node_in_zone(self, node: str) -> bool:
        return self.blast_block is not None and self.in_zone(*self.sim.roads.nodes[node][:2])

    def blast_now(self, block: Block, by: str) -> None:
        """An unscheduled blast ordered by a scenario: the zone is declared at
        once and the block is fired after the usual clearance time. A block
        that is not charged yet is taken as drilled and charged ahead of plan."""
        sim = self.sim
        if block.status != "charged":
            for rig in self.rigs:
                if rig.block == block.name:
                    rig.block, rig.holes_done, rig.move_s = None, block.holes_planned, 0.0
            if self.charging is block:
                self.charging = None
            block.status, block.reserved_by = "charged", ""
            block.charged_holes, block.charged_at = float(block.holes_planned), sim.now
            block.powder_factor = self._design_charge(block)
            sim.log("info", block.name, f"{block.title}: по сценарию обурен и заряжен вне плана, {block.holes_planned} скважин")
        self.blast_at = sim.now + self.p_blast["clearance_lead_min"] * 60.0
        self.start_blast(block, by)

    def abort_blast(self) -> None:
        """Call off a blast that has not been fired: the zone is lifted, the block stays charged."""
        block = self.blast_block
        self.sim.log("info", block.name, f"Взрыв отменён до подрыва: {block.title} остаётся заряженным, опасная зона снята")
        self._all_clear(fired=False)

    def lift_zone(self) -> None:
        """Let people and machines back in before the re-entry time has passed."""
        self.sim.log("info", self.blast_block.name, "Досрочный допуск в зону взрыва")
        self._all_clear()

    def zone_left_s(self) -> float:
        """Seconds until the exclusion zone of the blast under way is expected to be lifted."""
        reentry_s = self.p_blast["reentry_min"] * 60.0
        if self.blast_phase == "clearing":
            return max(self.blast_at - self.sim.now, 0.0) + reentry_s
        return max(self.zone_until - self.sim.now, 0.0) if self.blast_phase == "reentry" else 0.0

    def start_blast(self, block: Block, by: str = "") -> None:
        """Declare the exclusion zone around a charged block: close the roads
        through it and withdraw the machines that stand too close."""
        sim, p = self.sim, self.p_blast
        self.blast_phase, self.blast_block, self.delay_logged, self.blast_by = "clearing", block, False, by
        cx, cy = block.centre
        self.zone_segments = [s for s in sim.roads.segments.values()
                              if s.distance_to(cx, cy) < p["exclusion_radius_m"]]
        for segment in self.zone_segments:
            sim.roads.close(segment, BLAST)
        for loader in sim.fleet.loaders.values():
            if math.dist(loader.pos, block.centre) < p["equipment_withdrawal_m"]:
                loader.stood_down = True
        for rig in self.rigs:
            if math.dist(rig.pos, block.centre) < p["equipment_withdrawal_m"]:
                rig.stood_down = True
        closed = ", ".join(f"«{s.title}»" for s in self.zone_segments) or "нет"
        sim.log("alarm", block.name,
                f"Подготовка к взрыву: {block.title}, опасная зона {p['exclusion_radius_m']:.0f} м, "
                f"взрыв в {sim.sim_time(self.blast_at)[11:16]}. Закрыты участки: {closed}")
        sim.fleet.roads_changed()

    def _zone_clear(self) -> bool:
        fleet = self.sim.fleet
        for truck in fleet.trucks:
            if truck.state != "down" and self.in_zone(*fleet.position(truck)[:2]):
                return False
        return all(loader.withdrawn or loader.state == "down"
                   for loader in fleet.loaders.values() if loader.stood_down)

    def _fire(self) -> None:
        sim, p, block = self.sim, self.p_blast, self.blast_block
        block.powder_factor = block.powder_factor or self._design_charge(block)     # the charge put in when it was charged
        block.frag_p80_mm = self.fragmentation(block.powder_factor, block.hardness_bwi)
        block.status, block.charged_holes = "blasted", 0.0
        self.blasts_fired += 1
        self.blast_phase, self.zone_until = "reentry", sim.now + p["reentry_min"] * 60.0
        sim.log("alarm", block.name,
                f"Взрыв: {block.title}, {block.tonnes:.0f} т, удельный расход ВВ {num(block.powder_factor, 2)} кг/м3, "
                f"крупность P80 {block.frag_p80_mm:.0f} мм. Допуск в зону через {p['reentry_min']:.0f} мин")

    def _all_clear(self, fired: bool = True) -> None:
        sim, block = self.sim, self.blast_block
        for segment in self.zone_segments:
            sim.roads.reopen(segment, BLAST)
        self.blast_phase, self.blast_block, self.zone_segments, self.blast_by = "idle", None, [], ""
        self.last_blast = block if fired else None
        for loader in sim.fleet.loaders.values():
            sim.fleet.stand_up(loader)
        for rig in self.rigs:
            rig.stood_down = False
        self.blast_at, self.blast_extra = self._next_window(sim.now)
        if fired:
            sim.log("info", block.name, f"Опасная зона снята: {block.title} взорван, участки дорог открыты")
        sim.fleet.roads_changed()
        sim.fleet.release_holds()

    def _step_blast(self) -> None:
        sim, p = self.sim, self.p_blast
        if self.blast_phase == "idle":
            if sim.now < self.blast_at - p["clearance_lead_min"] * 60.0:
                return
            charged = [b for b in self.blocks.values() if b.status == "charged"]
            # The extra window is used only when the faces are running short of broken rock.
            needed = not self.blast_extra or self.broken_stock_t() < self.short_stock_t
            if charged and needed:
                self.start_blast(min(charged, key=lambda b: (b.charged_at, b.name)))
            else:
                self.blast_at, self.blast_extra = self._next_window(self.blast_at)
        elif self.blast_phase == "clearing":
            if int(sim.now) % 20 == 0:
                sim.fleet.clear_zone()
            if sim.now < self.blast_at:
                return
            if self._zone_clear():
                self._fire()
            elif sim.now >= self.blast_at + p["clearance_lead_min"] * 60.0:
                # The zone could not be cleared: no blast in this window.
                sim.log("warning", self.blast_block.name,
                        f"Взрыв отменён: опасная зона блока {self.blast_block.title} не освобождена, "
                        f"блок остаётся заряженным до следующего окна")
                self._all_clear(fired=False)
            elif not self.delay_logged:
                self.delay_logged = True
                sim.log("warning", self.blast_block.name,
                        f"Взрыв задержан: в опасной зоне блока {self.blast_block.title} ещё есть техника")
        elif sim.now >= self.zone_until:
            self._all_clear()

    # -- pushbacks ------------------------------------------------------------------

    def _step_pushbacks(self) -> None:
        for name, bench in self.benches.items():
            blocks = [b for b in self.blocks.values() if b.bench == name]
            if not blocks or any(b.status != "depleted" for b in blocks):
                continue                        # a bench of the final wall has no blocks
            bench["pushback"] += 1
            for block in blocks:
                block.__dict__.update(self.model.block(name, *block.centre, generation=bench["pushback"] - 1))
                block.status, block.tonnes = "insitu", block.tonnes_initial
                block.frag_p80_mm = block.powder_factor = 0.0
                block.destination_source, block.reserved_by, block.charged_holes = "cutoff", "", 0.0
                block.destination_reason, block.hold_until = "", 0.0
            ore = [b for b in blocks if b.is_ore]
            grade = sum(b.grade_pct for b in ore) / len(ore) if ore else 0.0
            self.sim.log("info", name,
                         f"{bench['title']}: уступ отработан, начата разноска борта №{bench['pushback']}: "
                         f"{len(blocks)} новых блоков, из них рудных {len(ore)}, среднее содержание {num(grade, 2)} % Cu")

    # -- messages -------------------------------------------------------------------

    def _sink(self, name: str) -> str:
        sink = self.sim.plant.sinks.get(name)
        return sink.title if sink is not None else name

    def default_destination(self, block: Block) -> str:
        """The base rule: ore to the crusher, low-grade ore to the low-grade stockpile, waste to the dump."""
        return self.model.destination(block.material)

    def set_destination(self, name: str, body: dict) -> None:
        """Live message `set-destination`: from the grade-control module, or an
        operator's order with `hold_min` that stands for that time; the
        module's destinations for the block are not taken meanwhile."""
        sim, block = self.sim, self.blocks[name]
        destination = body.get("destination")
        destination = short(destination) if isinstance(destination, str) else destination
        if destination not in sim.plant.sinks:
            sim.refuse(name, f"{block.title}: назначение {destination!r} не существует, "
                                     f"допустимо: {', '.join(sim.plant.sinks)}")
            return
        hold_s = sim.hold_s(body, name, block.title)
        if hold_s is False:
            return
        reason = body.get("reason") if isinstance(body.get("reason"), str) else ""
        if hold_s:
            until = sim.sim_time(sim.now + hold_s)[11:16]
            sim.log("info", name, f"{block.title}: назначение «{self._sink(block.destination)}» → «{self._sink(destination)}» по указанию оператора "
                                  f"до {until}{': ' + reason if reason else ''}")
            block.destination, block.destination_source = destination, "operator"
            block.destination_reason, block.destination_at = (reason or f"указание оператора до {until}")[:160], sim.now
            block.hold_until, block.hold_noted = sim.now + hold_s, False
            return
        if block.destination_source == "operator":
            if not block.hold_noted:
                block.hold_noted = True
                sim.log("info", name, f"{block.title}: назначение «{self._sink(destination)}» от модуля управления качеством руды "
                                      f"не принято: действует указание оператора до {sim.sim_time(block.hold_until)[11:16]}")
            return
        if destination != block.destination:
            sim.log("info", name, f"{block.title}: назначение «{self._sink(block.destination)}» → «{self._sink(destination)}» "
                                  f"(модуль управления качеством руды{': ' + reason if reason else ''})")
        block.destination, block.destination_source = destination, "grade-control"
        block.destination_reason, block.destination_at = reason[:160], sim.now

    def release_destination(self, name: str, why: str = "модуль управления качеством руды выключен",
                            operator: bool = False) -> None:
        """Back to the cut-off rule for this block. A module's release leaves an
        operator's destination alone."""
        block = self.blocks[name]
        if block.destination_source == "cutoff" or (block.destination_source == "operator" and not operator):
            return
        default = self.default_destination(block)
        if default != block.destination:
            self.sim.log("info", name, f"{block.title}: назначение «{self._sink(block.destination)}» → «{self._sink(default)}» "
                                       f"по бортовому содержанию ({why})")
        block.destination, block.destination_source, block.destination_reason = default, "cutoff", ""

    def expire(self) -> None:
        """Withdraw destinations the module has not repeated within the time-out,
        and operator's destinations whose time is up."""
        now = self.sim.now
        deadline = now - self.timeout_s
        for block in self.blocks.values():
            if block.destination_source == "operator":
                if now >= block.hold_until:
                    self.release_destination(block.name, "срок указания оператора истёк", operator=True)
            elif block.destination_source != "cutoff" and block.destination_at < deadline:
                self.release_destination(block.name, "решение модуля не подтверждено")

    # -- one step -------------------------------------------------------------------

    def step(self, dt: float) -> None:
        for rig in self.rigs:
            self._step_rig(rig, dt)
        self._step_charging(dt)
        self._step_blast()
        if int(self.sim.now) % 60 == 0:
            self._step_pushbacks()
            self.expire()

    # -- published --------------------------------------------------------------------

    def values(self) -> dict[str, dict]:
        sim = self.sim
        out: dict[str, dict] = {}
        for b in self.blocks.values():
            out[b.name] = {
                "status": b.status, "grade_pct": b.grade_pct, "hardness_bwi": b.hardness_bwi,
                "tonnes_remaining": round(b.tonnes), "material": b.material,
                "frag_p80_mm": float(b.frag_p80_mm), "powder_factor_kg_m3": float(b.powder_factor),
                "destination": b.destination, "destination_source": b.destination_source,
                "destination_reason": b.destination_reason,
                "destination_hold_until": sim.sim_time(b.hold_until) if b.destination_source == "operator" else "",
            }
        for name, bench in self.benches.items():
            blocks = [b for b in self.blocks.values() if b.bench == name]
            out[name] = {
                "elevation_m": bench["elevation_m"],
                "blocks_remaining": sum(1 for b in blocks if b.status != "depleted"),
                "tonnes_remaining": round(sum(b.tonnes for b in blocks)),
                "pushback": bench["pushback"],
            }
        depth = self.p_drill["hole_depth_m"]
        for rig in self.rigs:
            out[rig.name] = {
                "state": rig.state,
                "block": sim.tid(rig.block) if rig.block else "",
                "holes_done": rig.holes_done if rig.block else 0,
                "holes_planned": rig.holes_planned if rig.block else 0,
                "penetration_m_h": round(rig.rate_m_h, 1),
                # In the block being drilled: whole holes times the hole depth, plus the hole under way.
                "metres_drilled": round(rig.holes_done * depth + rig.depth_m, 1) if rig.block else 0.0,
                "metres_total": round(rig.metres),
                "pos_x": float(rig.pos[0]), "pos_y": float(rig.pos[1]),
            }
        thing = sim.things["pit"]
        loaders = sim.fleet.loaders.values()
        out["pit"] = {
            "active_faces": sum(1 for l in loaders if l.block and l.state in ("loading", "waiting")),
            "depth_m": initial(thing, "depth_m"),
            "broken_stock_t": round(self.broken_stock_t()),
            "blast_state": self.blast_phase,
            "blast_block": sim.tid(self.blast_block.name) if self.blast_block else "",
            "blast_radius_m": float(self.p_blast["exclusion_radius_m"]) if self.blast_block else 0.0,
            "blast_source": (self.blast_by or "plan") if self.blast_block else "",
            "next_blast_sim_time": sim.sim_time(self.expected_window()),
        }
        return out
