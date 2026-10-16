"""Everything downstream of the trucks: dump points, primary crusher, conveyor,
coarse-ore stockpile, ROM pads, waste dump, mill, flotation and the substation.

Rock moves as parcels that carry their tonnes, their copper and their
tonne-weighted hardness and fragment size, so that grade and hardness arrive
at the mill with the ore they belong to and the mass balance closes.

Two modules act here through live messages. Plant control sets the mill feed
rate (`set-feed-rate`); grade control orders reclaim from the ROM pads
(`set-reclaim`). Both withdraw their orders with `release`, and an order that
is not repeated within the decision time-out lapses: the mill returns to its
constant setpoint, the reclaim to the rule of the shift.
"""
from __future__ import annotations

from collections import deque
from typing import TYPE_CHECKING

from mine.common.physics import G
from mine.common.process import recovery_pct
from mine.sim.text import num

if TYPE_CHECKING:
    from mine.sim.fleet import Truck
    from mine.sim.model import Simulation


def short(thing_id: str) -> str:
    return thing_id.split(":", 1)[1] if ":" in thing_id else thing_id


class Stock:
    """A fully mixed heap: tonnes, contained copper, and the tonne-weighted
    sums of hardness and fragment size."""
    __slots__ = ("t", "cu", "bwi_t", "p80_t")

    def __init__(self, t: float = 0.0, grade_pct: float = 0.0, bwi: float = 0.0, p80: float = 0.0):
        self.t, self.cu, self.bwi_t, self.p80_t = t, t * grade_pct / 100.0, t * bwi, t * p80

    def add(self, t: float, cu: float, bwi_t: float, p80_t: float) -> None:
        self.t += t
        self.cu += cu
        self.bwi_t += bwi_t
        self.p80_t += p80_t

    def take(self, t: float) -> tuple[float, float, float, float]:
        """Remove `t` tonnes of the mix; return what left as (t, cu, bwi_t, p80_t)."""
        if t >= self.t - 1e-9:
            out = (self.t, self.cu, self.bwi_t, self.p80_t)
            self.t = self.cu = self.bwi_t = self.p80_t = 0.0
            return out
        share = t / self.t
        out = (t, self.cu * share, self.bwi_t * share, self.p80_t * share)
        self.t -= out[0]
        self.cu -= out[1]
        self.bwi_t -= out[2]
        self.p80_t -= out[3]
        return out

    @property
    def grade_pct(self) -> float:
        return 100.0 * self.cu / self.t if self.t > 1e-6 else 0.0

    @property
    def bwi(self) -> float:
        return self.bwi_t / self.t if self.t > 1e-6 else 0.0

    @property
    def p80(self) -> float:
        return self.p80_t / self.t if self.t > 1e-6 else 0.0


class DumpPoint:
    """A place where one truck at a time tips its load; the others queue."""

    sink_kind = "waste"                 # what the tonnes tipped here count as: crusher / rom / waste

    def __init__(self, sim: "Simulation", thing: dict, spot_s: float, dump_s: float, spread: float):
        attrs = thing["attributes"]
        self.sim = sim
        self.name = short(thing["thingId"])
        self.title = attrs["name"]
        self.node = short(attrs["links"]["access_node"])
        spec, geo = attrs["spec"], attrs["geo"]
        self.pos = (spec.get("tip_x", geo.get("x")), spec.get("tip_y", geo.get("y")), geo["z"])
        self.spot_s, self.dump_s, self.spread = spot_s, dump_s, spread
        self.queue: list["Truck"] = []
        self.truck: "Truck | None" = None
        self.tipping = False
        self.timer_s = 0.0
        self.tip_rate_tps = 0.0
        self.stock = Stock()

    def is_open(self) -> bool:
        return True

    def room_for(self, truck: "Truck") -> bool:
        return True

    def receive(self, t: float, cu: float, bwi_t: float, p80_t: float) -> None:
        self.stock.add(t, cu, bwi_t, p80_t)

    def step(self, dt: float) -> None:
        if self.truck is None and self.queue and self.is_open() and self.room_for(self.queue[0]):
            truck = self.queue.pop(0)
            truck.queue_s += self.sim.now - truck.queue_since
            truck.state, truck.queue, truck.place = "dumping", None, self.pos
            self.truck, self.tipping, self.timer_s = truck, False, self.spot_s
        truck = self.truck
        if truck is None:
            return
        self.timer_s -= dt
        if not self.tipping:
            if self.timer_s <= 0:
                tip_s = self.dump_s * truck.rng_tip.uniform(1 - self.spread, 1 + self.spread)
                self.tipping, self.tip_rate_tps = True, truck.payload_t / tip_s
            return
        tipped = min(truck.payload_t, self.tip_rate_tps * dt)
        share = tipped / truck.payload_t if truck.payload_t > 0 else 1.0
        if truck.payload_t - tipped <= 1e-6:
            tipped, share = truck.payload_t, 1.0
        self.receive(tipped, truck.payload_cu_t * share, truck.payload_bwi * share, truck.payload_p80 * share)
        self.sim.kpis.tipped(tipped, self.sink_kind)
        truck.payload_cu_t -= truck.payload_cu_t * share
        truck.payload_bwi -= truck.payload_bwi * share
        truck.payload_p80 -= truck.payload_p80 * share
        truck.payload_t -= tipped
        if share >= 1.0:
            self.truck = None
            self.sim.fleet.finish_trip(truck)


class RomPad(DumpPoint):
    """A run-of-mine ore pad of limited size. A truck tips here only while its
    whole load fits; a wheel loader takes ore back to the crusher."""

    sink_kind = "rom"

    def __init__(self, sim: "Simulation", thing: dict, spot_s: float, dump_s: float, spread: float):
        super().__init__(sim, thing, spot_s, dump_s, spread)
        self.capacity_t = float(thing["attributes"]["spec"]["capacity_t"])
        self.role = thing["features"]["role"]["properties"]["value"]
        self.ordered_tph: float | None = None      # reclaim ordered by the grade-control module or the operator
        self.ordered_at = 0.0
        self.ordered_by = ""                        # grade-control / operator
        self.hold_until = 0.0                       # an operator's order stands until then
        self.hold_noted = False
        self.reclaim_tph = 0.0                      # what the wheel loader takes now

    def room_for(self, truck: "Truck") -> bool:
        """Room for the load of this truck on top of the loads tipping or waiting here."""
        ahead = sum(t.payload_t for t in self.queue if t is not truck)
        if self.truck is not None and self.truck is not truck:
            ahead += self.truck.payload_t
        return self.stock.t + ahead + truck.payload_t <= self.capacity_t


class Crusher(DumpPoint):
    sink_kind = "crusher"

    def __init__(self, sim: "Simulation", thing: dict):
        p = sim.params["crusher"]
        super().__init__(sim, thing, p["spot_s"], p["dump_s"], p["dump_spread"])
        self.p = p
        self.hopper = self.stock                # trucks tip into the dump pocket
        self.stopped_by = ""                    # set by a scenario
        self.stopped_since = 0.0
        self.restart_at: float | None = None    # when it is expected back (the scenario's expected end)
        self.limited = False                    # held back by a full stockpile in this step
        self.rate_tph = 0.0
        self.capacity_tph = float(p["rated_tph"])
        self.feed_grade_pct = 0.0
        self.feed_p80_mm = 0.0
        self.total_t = 0.0
        self.total_cu = 0.0

    def stop(self, reason: str) -> None:
        self.stopped_by, self.stopped_since = reason, self.sim.now

    def start(self) -> None:
        self.stopped_by = ""
        self.restart_at = None

    def is_open(self) -> bool:
        return not self.stopped_by

    def room_for(self, truck: "Truck") -> bool:
        return self.hopper.t + truck.payload_t <= self.p["hopper_t"]

    def room_t(self) -> float:
        return max(0.0, self.p["hopper_t"] - self.hopper.t)

    def size_factor(self, p80_mm: float) -> float:
        """Share of the rated throughput available for feed of this fragment size."""
        ref = self.sim.params["blast"]["p80_ref_mm"]
        return min(1.0, max(self.p["size_factor_min"], 1.0 - self.p["size_slope"] * (p80_mm - ref) / ref))

    def draw(self, dt: float, room_downstream_t: float) -> tuple[float, float, float]:
        """Crush what the rating, the feed size and the space downstream allow;
        return the parcel put on the conveyor as (t, cu, bwi_t)."""
        hopper = self.hopper
        self.rate_tph, self.limited = 0.0, False
        if self.stopped_by or hopper.t <= 1e-6:
            return 0.0, 0.0, 0.0
        self.capacity_tph = self.p["rated_tph"] * self.size_factor(hopper.p80)
        wanted = min(hopper.t, self.capacity_tph / 3600.0 * dt)
        amount = min(wanted, room_downstream_t)
        # The stockpile is full and lets through only what the mill takes out of it.
        self.limited = amount < wanted
        if amount <= 1e-9:
            return 0.0, 0.0, 0.0
        self.feed_grade_pct, self.feed_p80_mm = hopper.grade_pct, hopper.p80
        t, cu, bwi_t, _ = hopper.take(amount)
        self.rate_tph = t / dt * 3600.0
        self.total_t += t
        self.total_cu += cu
        self.sim.kpis.crushed(t, cu)
        return t, cu, bwi_t

    def state(self, belt_tph: float) -> str:
        """`running` while rock it crushed is still on the belt, so that the
        state does not flicker between two trucks."""
        if self.stopped_by:
            return "stopped"
        if self.limited:
            return "blocked"
        return "running" if belt_tph > 0.5 else "idle"

    def power_kw(self, throughput_tph: float) -> float:
        if self.stopped_by:
            return 0.0
        idle = self.p["motor_kw"] * self.p["idle_load_factor"]
        return idle + (self.p["motor_kw"] - idle) * min(1.0, throughput_tph / self.p["rated_tph"])


class Conveyor:
    """Belt from the crusher to the stockpile: what is put on now arrives one
    transport time later."""

    def __init__(self, sim: "Simulation", thing: dict):
        self.p = sim.params["conveyor"]
        spec = thing["attributes"]["spec"]
        self.length_m, self.lift_m = spec["length_m"], spec["lift_m"]
        self.transport_s = self.length_m / self.p["belt_speed_ms"]
        self.steps = max(1, round(self.transport_s))        # the model advances in 1 s steps
        self.belt: deque[tuple[float, float, float]] = deque()
        self.load_t = self.load_cu = 0.0
        self.feed_tph = 0.0

    def step(self, parcel: tuple[float, float, float], dt: float) -> tuple[float, float, float]:
        """Put `parcel` on the belt; return the parcel that falls off its far end."""
        self.belt.append(parcel)
        self.load_t += parcel[0]
        self.load_cu += parcel[1]
        self.feed_tph = parcel[0] / dt * 3600.0
        if len(self.belt) <= self.steps:
            return 0.0, 0.0, 0.0
        out = self.belt.popleft()
        self.load_t -= out[0]
        self.load_cu -= out[1]
        if self.load_t < 1e-9:
            self.load_t = self.load_cu = 0.0                # an empty belt, free of rounding residue
        return out

    @property
    def carried_tph(self) -> float:
        """Mean flow over the length of the belt: what the crusher discharged
        during the last transport time."""
        return self.load_t / self.transport_s * 3600.0

    def power_kw(self) -> float:
        if self.load_t <= 1e-6:
            return 0.0                                      # the belt stops when it runs empty
        work_m = self.lift_m + self.p["friction_coefficient"] * self.length_m
        return self.p["empty_kw"] + self.carried_tph * G * work_m / 3600.0 / self.p["drive_efficiency"]


class Mill:
    """The grinding circuit: one SAG mill and two ball mills in closed circuit.
    The feeder delivers the setpoint; the circuit grinds what its installed
    power allows for ore of the current hardness (Bond: the energy per tonne
    grows in proportion to the work index). The power is shared between the
    SAG mill and the ball mills in the proportion of their specific energies.
    Ore fed faster than it can be ground builds up in the circuit (and fills
    the SAG mill's drum above its normal charge); when the build-up reaches
    the overload limit the feed is cut off until the circuit has ground
    itself out, and it is then started again like after any other stop."""

    def __init__(self, sim: "Simulation", thing: dict):
        self.sim = sim
        self.p = sim.params["mill"]
        self.name = short(thing["thingId"])
        self.title = thing["attributes"]["name"]
        self.setpoint_tph = float(self.p["design_tph"])
        self.setpoint_source = "constant"       # constant / plant-control / operator
        self.setpoint_at = 0.0
        self.hold_until = 0.0                   # an operator's setpoint stands until then
        self.hold_noted = False
        self.state = "running"                  # running / overload / idle
        self.feed_tph = 0.0                     # drawn from the stockpile
        self.ground_tph = 0.0                   # leaving for flotation
        self.power_kw = 0.0
        self.feed_bwi = float(self.p["bwi_ref_kwh_t"])
        self.charge = Stock()                   # ore in the mill above its normal load
        self.restart_at = 0.0
        self.stops = 0                          # every stop: an empty stockpile, a zero setpoint, an overload trip
        self.overloads = 0                      # of them, the feed cut off because the circuit was overloaded
        self.total_t = self.total_cu = self.energy_kwh = 0.0

    def specific_energy(self, bwi: float) -> float:
        return self.p["specific_energy_kwh_t"] * bwi / self.p["bwi_ref_kwh_t"]

    def max_feed_tph(self, bwi: float) -> float:
        return self.p["installed_mw"] * 1000.0 / self.specific_energy(bwi)

    def unit_kw(self, unit: str) -> float:
        """Power drawn now by the SAG mill (`sag`) or the ball mills (`ball`)."""
        return self.power_kw * self.p[f"{unit}_kwh_t"] / self.p["specific_energy_kwh_t"]

    def charge_pct(self) -> float:
        """Volume share of the SAG drum filled with balls and ore: from the
        charge left after grinding out, up with the feed to the charge at the
        power limit, and up again with ore held above what the circuit grinds."""
        p = self.p
        grinding = min(1.0, self.ground_tph / self.max_feed_tph(self.feed_bwi)) if self.feed_bwi > 0 else 0.0
        held = min(1.0, self.charge.t / p["overload_t"])
        return (p["charge_idle_pct"] + (p["charge_full_pct"] - p["charge_idle_pct"]) * grinding
                + (p["charge_trip_pct"] - p["charge_full_pct"]) * held)

    def _stop(self, reason: str) -> None:
        self.state, self.stops = "idle", self.stops + 1
        self.restart_at = self.sim.now + self.p["restart_min"] * 60.0
        self.sim.log("alarm", self.name, f"{self.title} остановлена: {reason}")

    def step(self, stockpile: Stock, hardness_factor: float, dt: float) -> tuple[float, float, float]:
        """Draw feed from the stockpile and grind; return the product as (t, cu, bwi).
        `hardness_factor` scales the hardness of the ore drawn (1 unless a harder zone is passing)."""
        sim = self.sim
        self.feed_tph = self.ground_tph = self.power_kw = 0.0
        if self.state == "idle":
            needed = sim.params["stockpile"]["restart_hours"] * self.setpoint_tph
            if self.setpoint_tph <= 0 or stockpile.t < needed or sim.now < self.restart_at:
                return 0.0, 0.0, 0.0
            self.state = "running"
            sim.log("info", self.name, f"{self.title} запущена: на складе {stockpile.t:.0f} т руды")
        if self.state == "running":
            if self.setpoint_tph <= 0 or stockpile.t <= 1e-6:
                if self.charge.t <= 1e-6:
                    self._stop("склад дроблёной руды пуст" if self.setpoint_tph > 0 else "уставка питания равна нулю")
                    return 0.0, 0.0, 0.0
            else:
                t, cu, bwi_t, p80_t = stockpile.take(self.setpoint_tph / 3600.0 * dt)
                self.charge.add(t, cu, bwi_t * hardness_factor, p80_t)
                self.feed_tph = t / dt * 3600.0
        out = (0.0, 0.0, 0.0)
        if self.charge.t > 1e-9:
            bwi = self.feed_bwi = self.charge.bwi
            t, cu, _, _ = self.charge.take(self.max_feed_tph(bwi) / 3600.0 * dt)
            self.ground_tph = t / dt * 3600.0
            self.power_kw = self.ground_tph * self.specific_energy(bwi)
            self.total_t += t
            self.total_cu += cu
            self.energy_kwh += self.power_kw * dt / 3600.0
            sim.kpis.mill_feed.add(sim.now, t)
            sim.kpis.mill_cu.add(sim.now, cu)
            out = (t, cu, bwi)
        if self.state == "running" and self.charge.t >= self.p["overload_t"]:
            self.state, self.stops, self.overloads = "overload", self.stops + 1, self.overloads + 1
            sim.log("alarm", self.name,
                    f"{self.title} перегружена: уставка {self.setpoint_tph:.0f} т/ч выше предельного питания "
                    f"{self.max_feed_tph(self.feed_bwi):.0f} т/ч для руды крепостью {num(self.feed_bwi)} кВт·ч/т, питание отключено")
        elif self.state == "overload" and self.charge.t <= 1e-6:
            self.state = "idle"
            self.restart_at = sim.now + self.p["restart_min"] * 60.0
            sim.log("info", self.name, f"{self.title} разгружена, пуск через {self.p['restart_min']:.0f} мин")
        return out


class ConcentrateStore:
    """Concentrate after the thickener and the filters: it piles up in the
    store and leaves by rail in lots of `concentrate.shipment_t`."""

    def __init__(self, sim: "Simulation", thing: dict):
        self.sim, self.p = sim, sim.params["concentrate"]
        self.name, self.title = short(thing["thingId"]), thing["attributes"]["name"]
        self.t = self.cu = 0.0
        self.produced_t = self.shipped_t = self.shipped_cu = 0.0
        self.shipments = 0
        self.last_shipment = None
        self.inflow_tph = 0.0

    def receive(self, t: float, cu: float, dt: float) -> None:
        self.t += t
        self.cu += cu
        self.produced_t += t
        self.inflow_tph = t / dt * 3600.0
        lot = float(self.p["shipment_t"])
        if self.t >= lot:
            share = lot / self.t
            cu_lot = self.cu * share
            self.t, self.cu = self.t - lot, self.cu - cu_lot
            self.shipped_t += lot
            self.shipped_cu += cu_lot
            self.shipments += 1
            self.last_shipment = self.sim.now
            self.sim.log("info", self.name, f"{self.title}: отгружена партия концентрата {lot:.0f} т "
                                            f"(медь {num(cu_lot, 1)} т, содержание {num(100 * cu_lot / lot, 1)} %)")

    def values(self) -> dict:
        sim = self.sim
        return {
            "level_t": round(self.t, 1),
            "level_pct": round(100.0 * self.t / self.p["store_capacity_t"], 1),
            "cu_t": round(self.cu, 2),
            "grade_pct": round(100.0 * self.cu / self.t, 2) if self.t > 1e-6 else float(sim.params["flotation"]["conc_grade_pct"]),
            "inflow_tph": round(self.inflow_tph, 1),
            "produced_t": round(self.produced_t),
            "shipped_t": round(self.shipped_t),
            "shipments_total": self.shipments,
            "last_shipment_sim_time": sim.sim_time(self.last_shipment) if self.last_shipment is not None else "",
        }


class Tailings:
    """The tailings storage facility: what the flotation does not recover."""

    def __init__(self, sim: "Simulation", thing: dict):
        self.sim = sim
        self.name = short(thing["thingId"])
        self.density = float(sim.params["concentrate"]["tails_density_t_m3"])
        self.t = self.cu = 0.0
        self.inflow_tph = self.grade_pct = 0.0

    def receive(self, t: float, cu: float, dt: float) -> None:
        self.t += t
        self.cu += cu
        self.inflow_tph = t / dt * 3600.0
        self.grade_pct = 100.0 * cu / t if t > 1e-9 else 0.0

    def values(self) -> dict:
        return {"inflow_tph": round(self.inflow_tph), "grade_pct": round(self.grade_pct, 3),
                "stored_t": round(self.t), "stored_m3": round(self.t / self.density), "cu_lost_t": round(self.cu, 2)}


class Flotation:
    def __init__(self, sim: "Simulation", concentrate: ConcentrateStore, tailings: Tailings):
        self.sim = sim
        self.concentrate, self.tailings = concentrate, tailings
        self.state = "running"
        self.feed_grade_pct = self.recovery_pct = self.cu_tph = 0.0
        self.cu_conc_t = self.cu_tails_t = 0.0
        self.conc_grade = float(sim.params["flotation"]["conc_grade_pct"]) / 100.0

    def recovery(self, grade_pct: float, bwi: float, feed_tph: float | None = None) -> float:
        return recovery_pct(self.sim.params, grade_pct, bwi, feed_tph)

    def step(self, t: float, cu: float, bwi: float, dt: float) -> None:
        if t <= 1e-9:
            self.state = "idle"
            self.feed_grade_pct = self.recovery_pct = self.cu_tph = 0.0
            self.concentrate.inflow_tph = self.tailings.inflow_tph = self.tailings.grade_pct = 0.0
            return
        self.state = "running"
        self.feed_grade_pct = 100.0 * cu / t
        self.recovery_pct = self.recovery(self.feed_grade_pct, bwi, t / dt * 3600.0)
        recovered = cu * self.recovery_pct / 100.0
        self.cu_conc_t += recovered
        self.cu_tails_t += cu - recovered
        self.cu_tph = recovered / dt * 3600.0
        self.sim.kpis.conc_cu.add(self.sim.now, recovered)
        # Concentrate is the recovered copper at the concentrate grade; the rest of the solids are tailings.
        conc_t = recovered / self.conc_grade
        self.concentrate.receive(conc_t, recovered, dt)
        self.tailings.receive(t - conc_t, cu - recovered, dt)


class Plant:
    def __init__(self, sim: "Simulation"):
        self.sim = sim
        p = sim.params
        kind = lambda k: sim.by_kind[k]
        dump = p["dump"]
        self.crusher = Crusher(sim, kind("crusher")[0])
        self.conveyor = Conveyor(sim, kind("conveyor")[0])
        self.rom_pads = {short(t["thingId"]): RomPad(sim, t, dump["spot_s"], dump["dump_s"], p["crusher"]["dump_spread"])
                         for t in kind("rom-pad")}
        for t in kind("rom-pad"):
            start = lambda name: t["features"][name]["properties"]["value"]
            self.rom_pads[short(t["thingId"])].stock = Stock(
                start("level_t"), start("grade_pct"), start("hardness_bwi"), p["blast"]["p80_ref_mm"])
        self.waste_dump = DumpPoint(sim, kind("waste-dump")[0], dump["spot_s"], dump["dump_s"], p["crusher"]["dump_spread"])
        self.sinks: dict[str, DumpPoint] = {self.crusher.name: self.crusher, **self.rom_pads,
                                            self.waste_dump.name: self.waste_dump}
        thing = kind("stockpile")[0]
        feature = lambda name: thing["features"][name]["properties"]["value"]
        self.stockpile_name = short(thing["thingId"])
        self.capacity_t = float(thing["attributes"]["spec"]["capacity_t"])
        self.stockpile = Stock(feature("level_t"), feature("grade_pct"), feature("hardness_bwi"), p["crusher"]["product_p80_mm"])
        self.hardness_factor = 1.0              # raised by the hard-ore scenario
        self.initial_stock_t = self.stockpile.t + sum(pad.stock.t for pad in self.rom_pads.values())
        self.initial_stock_cu = self.stockpile.cu + sum(pad.stock.cu for pad in self.rom_pads.values())
        self.last_divert_at = -1e9
        self.mill = Mill(sim, kind("mill")[0])
        self.units = {short(t["thingId"]): ("sag" if k == "sag-mill" else "ball")
                      for k in ("sag-mill", "ball-mill") for t in kind(k)}
        self.concentrate = ConcentrateStore(sim, kind("concentrate")[0])
        self.tailings = Tailings(sim, kind("tailings")[0])
        self.flotation = Flotation(sim, self.concentrate, self.tailings)
        self.reclaimed_t = 0.0                  # fed from the ROM pads to the crusher since the start
        self.reclaimed_cu = 0.0                 # the copper in it
        self.timeout_s = p["sim"]["decision_timeout_min"] * 60.0
        self.reclaiming = False                 # the rule of the shift: feed from the pad while the stockpile is low
        self.load_kw = self.plant_kw = self.pit_kw = 0.0
        self.energy_shift_kwh = 0.0

    # -- decisions made at the dump -----------------------------------------------

    def rom_pad_for(self, material: str) -> str:
        """The ROM pad that takes this class of ore."""
        role = "low-grade" if material == "low-grade" else "high-grade"
        return next(name for name, pad in self.rom_pads.items() if pad.role == role)

    def pad_with_room(self, truck: "Truck") -> str | None:
        """The ROM pad a truck turned away from the crusher tips on, or None
        when that pad cannot take its load and the truck has to wait."""
        name = self.rom_pad_for(truck.material)
        return name if self.rom_pads[name].room_for(truck) else None

    def divert_from_crusher(self) -> bool:
        """True when a truck arriving at the crusher should tip on a ROM pad
        instead: the queue is already long. This holds for a stopped crusher
        too: the first trucks wait for it to start."""
        return len(self.crusher.queue) >= self.sim.params["crusher"]["divert_queue_len"]

    def crusher_stop_is_long(self) -> bool:
        """True when the crusher has stood for longer than a truck would wait at it."""
        crusher = self.crusher
        return bool(crusher.stopped_by) and \
            self.sim.now - crusher.stopped_since >= self.sim.params["crusher"]["stop_wait_min"] * 60.0

    def _clear_crusher_queue(self) -> None:
        """A long stop: the trucks waiting at the crusher leave for the ROM pads,
        except those the dispatch module told to wait here."""
        fleet = self.sim.fleet
        for truck in list(self.crusher.queue):
            if truck.override_dump:
                continue
            pad = self.pad_with_room(truck)
            if pad is not None:
                truck.queue_s += self.sim.now - truck.queue_since
                self.note_divert(truck.title, pad)
                fleet.redirect(truck, pad, "overflow")

    def note_divert(self, truck_title: str, pad: str) -> None:
        """Journal the start of a spell of diverting ore, not every truck of it."""
        sim, crusher = self.sim, self.crusher
        if sim.now - self.last_divert_at > sim.params["crusher"]["divert_note_min"] * 60.0:
            why = "дробилка остановлена" if crusher.stopped_by else f"у дробилки очередь {len(crusher.queue)}"
            full = ", склад дроблёной руды заполнен" if crusher.limited else ""
            sim.log("info", crusher.name, f"{truck_title}: {why}{full}, руда идёт на «{self.sinks[pad].title}»")
        self.last_divert_at = sim.now

    # -- messages -------------------------------------------------------------------

    def set_feed_rate(self, body: dict) -> None:
        """Live message `set-feed-rate`: from the plant-control module, or an
        operator's order with `hold_min`. An operator's setpoint stands for
        that time; the module's setpoints are not taken meanwhile."""
        sim, mill = self.sim, self.mill
        tph = body.get("tph")
        limit = mill.p["design_tph"] * mill.p["setpoint_max_factor"]
        if isinstance(tph, bool) or not isinstance(tph, (int, float)) or not 0 <= tph <= limit:
            sim.refuse(mill.name, f"Уставка питания {tph!r} отклонена: допустимо от 0 до {limit:.0f} т/ч")
            return
        hold_s = sim.hold_s(body, mill.name, mill.title)
        if hold_s is False:
            return
        if hold_s:
            sim.log("info", mill.name, f"Уставка питания мельницы: {mill.setpoint_tph:.0f} → {tph:.0f} т/ч по указанию "
                                       f"оператора до {sim.sim_time(sim.now + hold_s)[11:16]}")
            mill.setpoint_tph, mill.setpoint_source, mill.setpoint_at = float(tph), "operator", sim.now
            mill.hold_until, mill.hold_noted = sim.now + hold_s, False
            return
        if mill.setpoint_source == "operator":
            if not mill.hold_noted:
                mill.hold_noted = True
                sim.log("info", mill.name, f"Уставка {tph:.0f} т/ч от модуля управления фабрикой не принята: действует "
                                           f"указание оператора до {sim.sim_time(mill.hold_until)[11:16]}")
            return
        if abs(float(tph) - mill.setpoint_tph) >= 100.0 or mill.setpoint_source != "plant-control":
            sim.log("info", mill.name, f"Уставка питания мельницы: {mill.setpoint_tph:.0f} → {tph:.0f} т/ч (модуль управления фабрикой)")
        mill.setpoint_tph, mill.setpoint_source, mill.setpoint_at = float(tph), "plant-control", sim.now

    def release_feed_rate(self, why: str = "модуль управления фабрикой выключен", operator: bool = False) -> None:
        """Back to the constant setpoint of the fixed plan. A module's release
        leaves an operator's setpoint alone."""
        mill = self.mill
        if mill.setpoint_source == "constant" or (mill.setpoint_source == "operator" and not operator):
            return
        design = float(mill.p["design_tph"])
        self.sim.log("info", mill.name, f"Уставка питания мельницы: {mill.setpoint_tph:.0f} → {design:.0f} т/ч, постоянная ({why})")
        mill.setpoint_tph, mill.setpoint_source = design, "constant"

    def set_reclaim(self, name: str, body: dict) -> None:
        """Live message `set-reclaim`: how fast the wheel loader feeds the
        crusher from this ROM pad (0 = not at all). From the grade-control
        module, or an operator's order with `hold_min` that the module's orders
        do not replace while it stands."""
        sim, pad = self.sim, self.rom_pads[name]
        tph = body.get("tph")
        limit = sim.params["rom"]["reclaim_tph"]
        if isinstance(tph, bool) or not isinstance(tph, (int, float)) or not 0 <= tph <= limit:
            sim.refuse(name, f"{pad.title}: подача {tph!r} отклонена, допустимо от 0 до {limit:.0f} т/ч")
            return
        hold_s = sim.hold_s(body, name, pad.title)
        if hold_s is False:
            return
        if hold_s:
            sim.log("info", name, f"{pad.title}: подача в дробилку {tph:.0f} т/ч по указанию оператора "
                                  f"до {sim.sim_time(sim.now + hold_s)[11:16]}")
            pad.ordered_tph, pad.ordered_at, pad.ordered_by = float(tph), sim.now, "operator"
            pad.hold_until, pad.hold_noted = sim.now + hold_s, False
            return
        if pad.ordered_by == "operator":
            if not pad.hold_noted:
                pad.hold_noted = True
                sim.log("info", name, f"{pad.title}: подача {tph:.0f} т/ч от модуля управления качеством руды не принята: "
                                      f"действует указание оператора до {sim.sim_time(pad.hold_until)[11:16]}")
            return
        if (pad.ordered_tph or 0.0) != float(tph) and (tph > 0 or (pad.ordered_tph or 0.0) > 0):
            sim.log("info", name, f"{pad.title}: подача в дробилку {tph:.0f} т/ч (модуль управления качеством руды)")
        pad.ordered_tph, pad.ordered_at, pad.ordered_by = float(tph), sim.now, "grade-control"

    def release_reclaim(self, name: str, why: str = "модуль управления качеством руды выключен", operator: bool = False) -> None:
        pad = self.rom_pads[name]
        if pad.ordered_tph is not None and (pad.ordered_by != "operator" or operator):
            pad.ordered_tph, pad.ordered_by = None, ""
            self.sim.log("info", name, f"{pad.title}: подача в дробилку снова по правилу смены ({why})")

    def expire(self) -> None:
        """Withdraw module orders that were not repeated within the time-out,
        and operator's orders whose time is up."""
        now = self.sim.now
        deadline = now - self.timeout_s
        why, over = "решение модуля не подтверждено", "срок указания оператора истёк"
        mill = self.mill
        if mill.setpoint_source == "operator":
            if now >= mill.hold_until:
                self.release_feed_rate(over, operator=True)
        elif mill.setpoint_source != "constant" and mill.setpoint_at < deadline:
            self.release_feed_rate(why)
        for name, pad in self.rom_pads.items():
            if pad.ordered_by == "operator":
                if now >= pad.hold_until:
                    self.release_reclaim(name, over, operator=True)
            elif pad.ordered_tph is not None and pad.ordered_at < deadline:
                self.release_reclaim(name, why)

    # -- one step -------------------------------------------------------------------

    def step(self, dt: float) -> None:
        sim, p = self.sim, self.sim.params
        if int(sim.now) % 30 == 0 and self.crusher_stop_is_long():
            self._clear_crusher_queue()
        for sink in self.sinks.values():
            sink.step(dt)
        self._reclaim(dt)
        room = self.capacity_t - self.stockpile.t - self.conveyor.load_t
        arrived = self.conveyor.step(self.crusher.draw(dt, max(0.0, room)), dt)
        if arrived[0] > 0:
            self.stockpile.add(arrived[0], arrived[1], arrived[2], arrived[0] * p["crusher"]["product_p80_mm"])
        product = self.mill.step(self.stockpile, self.hardness_factor, dt)
        self.flotation.step(*product, dt)

        ground_tph = self.mill.ground_tph
        aux = p["plant"]["aux_energy_kwh_t"] * (p["plant"]["aux_base_share"] * p["mill"]["design_tph"]
                                                + (1.0 - p["plant"]["aux_base_share"]) * ground_tph)
        self.plant_kw = (self.mill.power_kw + p["flotation"]["energy_kwh_t"] * ground_tph + aux
                         + self.crusher.power_kw(self.conveyor.carried_tph) + self.conveyor.power_kw())
        self.pit_kw = sum(loader.power_kw for loader in sim.fleet.loaders.values())
        self.load_kw = self.plant_kw + self.pit_kw
        kwh = self.load_kw * dt / 3600.0
        self.energy_shift_kwh += kwh
        sim.kpis.electric_kwh.add(sim.now, kwh)
        sim.kpis.electric_kwh_total += kwh
        if int(sim.now) % 60 == 0:
            self.expire()

    def _reclaim(self, dt: float) -> None:
        """A wheel loader feeds the crusher from the ROM pads: at the rates the
        grade-control module orders, or, without such an order, from the
        high-grade pad while the coarse-ore stockpile is low."""
        p = self.sim.params["rom"]
        rule_pad = self.rom_pads[self.rom_pad_for("ore")]
        ordered = any(pad.ordered_tph is not None for pad in self.rom_pads.values())
        level_pct = 100.0 * self.stockpile.t / self.capacity_t
        if ordered:
            self.reclaiming = False
        elif self.reclaiming and (level_pct >= p["reclaim_stop_pct"] or rule_pad.stock.t <= 1e-6):
            self.reclaiming = False
            self.sim.log("info", rule_pad.name, f"{rule_pad.title}: подача руды в дробилку прекращена")
        elif not self.reclaiming and level_pct < p["reclaim_below_pct"] and rule_pad.stock.t > self.crusher.p["hopper_t"]:
            self.reclaiming = True
            self.sim.log("info", rule_pad.name, f"{rule_pad.title}: погрузчик подаёт руду в дробилку, "
                                                f"склад дроблёной руды заполнен на {level_pct:.0f} %")
        budget = p["reclaim_tph"] / 3600.0 * dt             # one wheel loader for both pads
        for pad in self.rom_pads.values():
            pad.reclaim_tph = 0.0
            if self.crusher.stopped_by:
                continue
            if ordered:
                wanted_tph = pad.ordered_tph or 0.0
            else:
                wanted_tph = p["reclaim_tph"] if self.reclaiming and pad is rule_pad else 0.0
            amount = min(wanted_tph / 3600.0 * dt, budget, pad.stock.t, self.crusher.room_t())
            if amount > 1e-9:
                parcel = pad.stock.take(amount)
                self.crusher.hopper.add(*parcel)
                pad.reclaim_tph = amount / dt * 3600.0
                budget -= amount
                self.reclaimed_t += amount
                self.reclaimed_cu += parcel[1]
                self.sim.kpis.rehandle(amount)

    def new_shift(self) -> None:
        self.energy_shift_kwh = 0.0

    # -- published --------------------------------------------------------------------

    def values(self) -> dict[str, dict]:
        sim, p = self.sim, self.sim.params
        crusher, conveyor, mill, flotation, pile = self.crusher, self.conveyor, self.mill, self.flotation, self.stockpile
        # What the crusher discharged over the last belt transit is exactly what
        # the belt carries: the two twins report the same flow.
        belt_tph = conveyor.carried_tph
        crusher_state = crusher.state(belt_tph)
        pile_bwi = pile.bwi * self.hardness_factor
        stopped = crusher_state == "stopped"
        out = {
            crusher.name: {
                "state": crusher_state,
                # Zero while stopped; otherwise the mean over the rate window, which does not jump between trucks.
                "throughput_tph": 0 if stopped else round(sim.kpis.crusher_rate.per_hour(sim.now)),
                "throughput_now_tph": 0 if stopped else round(belt_tph),
                "capacity_tph": round(crusher.capacity_tph),
                "feed_grade_pct": round(crusher.feed_grade_pct, 3),
                "feed_p80_mm": round(crusher.feed_p80_mm),
                "hopper_t": round(crusher.hopper.t, 1),
                "queue_len": len(crusher.queue),
                "power_kw": round(crusher.power_kw(belt_tph)),
                "tonnes_total": round(crusher.total_t),
                # While stopped: when it is expected back, and how many crews repair it (mine/sim/response.py).
                "restart_expected_sim_time": sim.sim_time(crusher.restart_at)
                if stopped and crusher.restart_at is not None else "",
                "repair_crews": (1 + len(sim.response.working(crusher.name))) if stopped else 0,
            },
            "conveyor": {
                "state": "running" if conveyor.load_t > 1e-6 else "idle",
                "load_tph": round(belt_tph),
                "load_t": round(conveyor.load_t, 1),
                "power_kw": round(conveyor.power_kw()),
            },
            self.stockpile_name: {
                "level_t": round(pile.t),
                "level_pct": round(100.0 * pile.t / self.capacity_t, 1),
                "grade_pct": round(pile.grade_pct, 3),
                "hardness_bwi": round(pile_bwi, 2),
                "hours_of_feed": round(pile.t / mill.setpoint_tph, 1) if mill.setpoint_tph > 0 else 0.0,
            },
            mill.name: {
                "state": mill.state,
                "feed_tph": round(mill.feed_tph) if mill.state == "running" else 0,
                "feed_setpoint_tph": round(mill.setpoint_tph),
                "setpoint_source": mill.setpoint_source,
                "setpoint_hold_until": sim.sim_time(mill.hold_until) if mill.setpoint_source == "operator" else "",
                "max_feed_tph": round(mill.max_feed_tph(pile_bwi or mill.feed_bwi)),
                "throughput_tph": round(mill.ground_tph),
                "power_mw": round(mill.power_kw / 1000.0, 2),
                "specific_energy_kwh_t": round(mill.power_kw / mill.ground_tph, 2) if mill.ground_tph > 0 else 0.0,
                "feed_bwi": round(mill.feed_bwi, 2),
                "overload_t": round(mill.charge.t, 1),
                "overload_pct": round(100.0 * mill.charge.t / mill.p["overload_t"], 1),
                "stops_total": mill.stops,
                "tonnes_total": round(mill.total_t),
            },
            "flotation": {
                "state": flotation.state,
                "feed_grade_pct": round(flotation.feed_grade_pct, 3),
                "recovery_pct": round(flotation.recovery_pct, 1),
                "conc_grade_pct": float(p["flotation"]["conc_grade_pct"]),
                "conc_tph": round(flotation.cu_tph / (p["flotation"]["conc_grade_pct"] / 100.0), 1),
                "cu_tph": round(flotation.cu_tph, 2),
                "tails_grade_pct": round(self.tailings.grade_pct, 3),
                "tails_tph": round(self.tailings.inflow_tph),
                "cu_total_t": round(flotation.cu_conc_t, 2),
            },
            self.concentrate.name: self.concentrate.values(),
            self.tailings.name: self.tailings.values(),
            "substation": {
                "load_mw": round(self.load_kw / 1000.0, 2),
                "plant_mw": round(self.plant_kw / 1000.0, 2),
                "pit_mw": round(self.pit_kw / 1000.0, 2),
                "energy_mwh_shift": round(self.energy_shift_kwh / 1000.0, 1),
            },
            self.waste_dump.name: {
                "tonnes_total": round(self.waste_dump.stock.t),
                "queue_len": len(self.waste_dump.queue),
            },
        }
        for name, unit in self.units.items():
            kw = mill.unit_kw(unit)
            installed_mw = float(mill.p[f"{unit}_installed_mw"])
            out[name] = {
                "state": mill.state,
                "throughput_tph": round(mill.ground_tph),
                "power_mw": round(kw / 1000.0, 2),
                "installed_mw": installed_mw,
                "drive_load_pct": round(100.0 * kw / 1000.0 / installed_mw, 1),
                "specific_energy_kwh_t": round(kw / mill.ground_tph, 2) if mill.ground_tph > 0 else 0.0,
                "feed_bwi": round(mill.feed_bwi, 2),
            }
            if unit == "sag":
                out[name]["charge_pct"] = round(mill.charge_pct(), 1)
        by_module = any(pad.ordered_by == "grade-control" for pad in self.rom_pads.values())
        by_operator = any(pad.ordered_by == "operator" for pad in self.rom_pads.values())
        for name, pad in self.rom_pads.items():
            # Who sets this pad's reclaim: its own order, else the orders for the other pad, else the rule.
            source = pad.ordered_by or ("grade-control" if by_module else "operator" if by_operator else "rule")
            out[name] = {
                "level_t": round(pad.stock.t),
                "level_pct": round(100.0 * pad.stock.t / pad.capacity_t, 1),
                "grade_pct": round(pad.stock.grade_pct, 3),
                "hardness_bwi": round(pad.stock.bwi, 2),
                "role": pad.role,
                "queue_len": len(pad.queue),
                "reclaim_tph": round(pad.reclaim_tph),
                "reclaim_source": source,
                "reclaim_hold_until": sim.sim_time(pad.hold_until) if pad.ordered_by == "operator" else "",
            }
        return out
