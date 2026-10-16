"""Mine indicators and the material balance, computed from what the model did.

Rates and averages cover the last KPI window of simulated time (one hour by
default); totals run from the start of the simulation. Nothing here is set
by hand: every figure is a sum of tonnes, litres, kilowatt-hours or seconds
that the other subsystems report as they happen.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import TYPE_CHECKING

from mine.common.economics import Economics
from mine.sim.chain import History, Trip

if TYPE_CHECKING:
    from mine.sim.model import Simulation

PRODUCTIVE_STATES = frozenset({"approach", "loading", "hauling", "dumping", "returning"})
# A truck on its way to the workshop is already withdrawn from production.
UNAVAILABLE_STATES = frozenset({"down", "service", "to_service"})


class Rolling:
    """Total of the amounts recorded over the last `window_s` simulated seconds,
    kept in one-minute buckets."""

    def __init__(self, window_s: float):
        self.window_s = window_s
        self.buckets: deque[list[float]] = deque()
        self.sum = 0.0                      # of the buckets kept, updated as they come and go

    def _drop_old(self, now: float) -> None:
        oldest = (now - self.window_s) // 60
        while self.buckets and self.buckets[0][0] < oldest:
            self.sum -= self.buckets.popleft()[1]
        if not self.buckets:
            self.sum = 0.0                  # no rounding residue on an empty window

    def add(self, now: float, amount: float) -> None:
        minute = now // 60
        if self.buckets and self.buckets[-1][0] == minute:
            self.buckets[-1][1] += amount
        else:
            self.buckets.append([minute, amount])
            self._drop_old(now)
        self.sum += amount

    def total(self, now: float) -> float:
        self._drop_old(now)
        return self.sum

    def per_hour(self, now: float) -> float:
        """Mean hourly rate over the time the kept buckets cover."""
        total = self.total(now)
        if not self.buckets:
            return 0.0
        span_s = now - max((now - self.window_s) // 60, 0.0) * 60.0
        return total * 3600.0 / span_s if span_s > 0 else 0.0


@dataclass
class Cycle:
    finished: float
    started: float
    queue_s: float
    payload_t: float

    @property
    def duration_s(self) -> float:
        return self.finished - self.started


class Kpis:
    def __init__(self, sim: "Simulation"):
        self.sim = sim
        self.window_s = sim.params["sim"]["kpi_window_min"] * 60.0
        roll = lambda: Rolling(self.window_s)
        self.cycles: deque[Cycle] = deque()
        self.mill_feed, self.mill_cu, self.conc_cu = roll(), roll(), roll()
        self.diesel, self.hauled, self.electric_kwh = roll(), roll(), roll()
        self.busy_s, self.fleet_s, self.available_s = roll(), roll(), roll()
        # Availability over a day: over one hour it jumps with the trucks that happen to be in the workshop.
        day = float(sim.params["sim"]["availability_window_h"]) * 3600.0
        self.fleet_day_s, self.available_day_s = Rolling(day), Rolling(day)
        self.engine_on_s = roll()                               # truck seconds with the engine running
        self.crusher_feed, self.crusher_cu, self.dug = roll(), roll(), roll()
        self.rehandled = roll()                                 # tonnes fed from the ROM pads to the crusher
        # The crusher's published rate: a mean over the shorter rate window, so that it does not jump between trucks.
        self.crusher_rate = Rolling(sim.params["sim"]["rate_window_min"] * 60.0)
        self.tipped_at: dict[str, float] = {}                   # tonnes tipped since the start: crusher / rom / waste
        self.rehandle_l = 0.0                                   # diesel of the wheel loader on the ROM pads
        self.history = History(sim)
        # Totals since the start of the simulation.
        self.ore_mined_t = 0.0          # material loaded as ore or low-grade ore
        self.waste_mined_t = 0.0
        self.cu_mined_t = 0.0
        self.ore_cu_mined_t = 0.0       # copper in the material loaded as ore or low-grade ore
        # The same totals at the start of the current shift: the figures "за смену" are the totals less these.
        self.shift_marks = {"cu": 0.0, "ore": 0.0, "waste": 0.0, "milled": 0.0}
        self.diesel_l = 0.0
        self.hauled_t = 0.0             # tipped at any dump point
        self.engine_s = 0.0             # truck time with the engine running
        self.truck_s = 0.0              # truck calendar time
        self.unavailable_s = 0.0        # truck time down or in service
        self.productive_s = 0.0
        self.cycle_count = 0
        self.cycle_s = 0.0
        self.queue_s = 0.0
        self.longest_standby_s = 0.0
        self.electric_kwh_total = 0.0
        self.econ = Economics(sim.params)

    # -- reported by the subsystems ---------------------------------------------

    def mined(self, tonnes: float, cu_t: float, material: str) -> None:
        self.dug.add(self.sim.now, tonnes)
        if material == "waste":
            self.waste_mined_t += tonnes
        else:
            self.ore_mined_t += tonnes
            self.ore_cu_mined_t += cu_t
        self.cu_mined_t += cu_t

    def shift_totals(self) -> dict:
        """Copper, ore, waste and milled ore since the model started (the shift marks are taken from these)."""
        plant = self.sim.plant
        return {"cu": plant.flotation.cu_conc_t, "ore": self.ore_mined_t, "waste": self.waste_mined_t,
                "milled": plant.mill.total_t}

    def new_shift(self) -> None:
        """A shift begins: the figures "за смену" start again from zero."""
        self.shift_marks = self.shift_totals()

    def tipped(self, tonnes: float, where: str = "") -> None:
        self.hauled.add(self.sim.now, tonnes)
        self.hauled_t += tonnes
        if where:
            self.tipped_at[where] = self.tipped_at.get(where, 0.0) + tonnes

    def crushed(self, tonnes: float, cu_t: float) -> None:
        self.crusher_feed.add(self.sim.now, tonnes)
        self.crusher_cu.add(self.sim.now, cu_t)
        self.crusher_rate.add(self.sim.now, tonnes)

    def rehandle(self, tonnes: float) -> None:
        """The wheel loader fed `tonnes` from a ROM pad to the crusher."""
        self.rehandled.add(self.sim.now, tonnes)
        self.rehandle_l += tonnes * self.sim.params["rom"]["rehandle_diesel_l_t"]

    def fleet_step(self, dt: float, trucks: int, unavailable: int, productive: int,
                   engines_on: int, burned_l: float) -> None:
        now = self.sim.now
        self.fleet_s.add(now, trucks * dt)
        self.available_s.add(now, (trucks - unavailable) * dt)
        self.fleet_day_s.add(now, trucks * dt)
        self.available_day_s.add(now, (trucks - unavailable) * dt)
        self.busy_s.add(now, productive * dt)
        self.engine_on_s.add(now, engines_on * dt)
        self.diesel.add(now, burned_l)
        self.truck_s += trucks * dt
        self.unavailable_s += unavailable * dt
        self.productive_s += productive * dt
        self.engine_s += engines_on * dt
        self.diesel_l += burned_l

    def cycle_done(self, started: float, queue_s: float, payload_t: float, loaded_m: float = 0.0, dump: str = "") -> Cycle:
        now = self.sim.now
        cycle = Cycle(finished=now, started=started, queue_s=queue_s, payload_t=payload_t)
        self.cycles.append(cycle)
        while self.cycles and now - self.cycles[0].finished > self.window_s:
            self.cycles.popleft()
        self.cycle_count += 1
        self.cycle_s += cycle.duration_s
        self.queue_s += queue_s
        self.history.trip(Trip(now, cycle.duration_s, queue_s, payload_t, loaded_m, dump))
        return cycle

    # -- cycle time -------------------------------------------------------------

    def measured_cycle_s(self, started_after: float | None = None, minimum: int = 1) -> float | None:
        """Mean duration of the trips finished within the KPI window; None if
        there are fewer than `minimum`. `started_after` keeps only trips that
        began after that moment."""
        now = self.sim.now
        durations = [c.duration_s for c in self.cycles
                     if now - c.finished <= self.window_s
                     and (started_after is None or c.started >= started_after)]
        return sum(durations) / len(durations) if len(durations) >= minimum else None

    def mean_queue_s(self) -> float:
        now = self.sim.now
        recent = [c.queue_s for c in self.cycles if now - c.finished <= self.window_s]
        return sum(recent) / len(recent) if recent else 0.0

    def cycle_estimate_s(self) -> float:
        return self.measured_cycle_s(minimum=3) or self.sim.fleet.forecast()[0] or 0.0

    # -- money and CO2 ------------------------------------------------------------

    def money(self) -> dict:
        """Copper, diesel and electricity per hour over the KPI window, and what
        they are worth. The amounts are rounded first and the money is computed
        from the rounded amounts, so the published figures can be checked by hand."""
        now, econ = self.sim.now, self.econ
        cu_tph = round(self.conc_cu.per_hour(now), 3)
        diesel_l_h = round(self.diesel.per_hour(now), 1)
        energy_mwh_h = round(self.electric_kwh.per_hour(now) / 1000.0, 3)
        rehandle_t_h = round(self.rehandled.per_hour(now), 1)
        revenue = econ.revenue_kzt(cu_tph)
        diesel = econ.diesel_kzt(diesel_l_h)
        power = econ.power_kzt(energy_mwh_h * 1000.0)
        return {
            "cu_tph": cu_tph,
            "diesel_l_h": diesel_l_h,
            "energy_mwh_h": energy_mwh_h,
            "revenue_kzt_h": round(revenue),
            "diesel_cost_kzt_h": round(diesel),
            "power_cost_kzt_h": round(power),
            "margin_kzt_h": round(revenue - diesel - power),
            "rehandle_t_h": rehandle_t_h,
            "rehandle_cost_kzt_h": round(econ.rehandle_kzt(rehandle_t_h)),
            # Trucks, the wheel loader on the ROM pads and the grid.
            "co2_t_h": round(econ.co2_t(diesel_l_h + econ.rehandle_l(rehandle_t_h), energy_mwh_h * 1000.0), 2),
        }

    def margin_total_kzt(self) -> float:
        """Variable margin earned since the start of the simulation."""
        return self.econ.margin_kzt(self.sim.plant.flotation.cu_conc_t, self.diesel_l, self.electric_kwh_total)

    # -- published --------------------------------------------------------------

    def values(self) -> dict:
        sim, now = self.sim, self.sim.now
        fleet_s = self.fleet_s.total(now)
        feed_t, feed_cu = self.mill_feed.total(now), self.mill_cu.total(now)
        hauled = self.hauled.total(now)
        fleet = sim.fleet
        totals, marks = self.shift_totals(), self.shift_marks
        return {
            # Since the shift began (08:00 or 20:00), and since the model started.
            "cu_shift_t": round(totals["cu"] - marks["cu"], 2),
            "ore_shift_t": round(totals["ore"] - marks["ore"]),
            "waste_shift_t": round(totals["waste"] - marks["waste"]),
            "milled_shift_t": round(totals["milled"] - marks["milled"]),
            "shift_start_sim_time": sim.sim_time(sim.shift_started_at),
            "ore_mined_t": round(self.ore_mined_t, 1),
            "waste_mined_t": round(self.waste_mined_t, 1),
            "strip_ratio": round(self.waste_mined_t / self.ore_mined_t, 3) if self.ore_mined_t > 0 else 0.0,
            "mill_feed_tph": round(self.mill_feed.per_hour(now)),
            "head_grade_pct": round(100 * feed_cu / feed_t, 3) if feed_t > 0 else 0.0,
            "recovery_pct": round(100 * self.conc_cu.total(now) / feed_cu, 1) if feed_cu > 0 else 0.0,
            "cu_produced_t": round(sim.plant.flotation.cu_conc_t, 2),
            "avg_cycle_min": round(self.cycle_estimate_s() / 60, 1),
            "avg_queue_min": round(self.mean_queue_s() / 60, 1),
            "fleet_util_pct": round(100 * self.busy_s.total(now) / fleet_s, 1) if fleet_s else 0.0,
            "fleet_avail_pct": round(100 * self.available_day_s.total(now) / self.fleet_day_s.total(now), 1)
            if self.fleet_day_s.total(now) else 0.0,
            "trucks_active": sum(1 for t in fleet.trucks if t.state not in UNAVAILABLE_STATES),
            "diesel_l_per_t": round(self.diesel.total(now) / hauled, 3) if hauled > 0 else 0.0,
            # Diesel per truck and hour with the engine running (in repair or at the pump it is off).
            "diesel_l_per_engine_h": round(self.diesel.total(now) / (self.engine_on_s.total(now) / 3600.0), 1)
            if self.engine_on_s.total(now) > 0 else 0.0,
            "energy_kwh_per_t": round(self.electric_kwh.total(now) / feed_t, 2) if feed_t > 0 else 0.0,
            **self.money(),
        }


def balance(sim: "Simulation") -> dict:
    """Where every tonne of rock and of copper is, for the conservation checks.

    `sources` = what was in the stockpile at the start plus everything dug;
    `sinks` = everything that still exists somewhere plus what was milled.
    The two must be equal, in tonnes of rock and in tonnes of copper."""
    plant, fleet = sim.plant, sim.fleet
    conc, tails = plant.concentrate, plant.tailings
    rock = {
        "trucks": sum(t.payload_t for t in fleet.trucks),
        "hopper": plant.crusher.hopper.t,
        "conveyor": plant.conveyor.load_t,
        "stockpile": plant.stockpile.t,
        "rom_pads": sum(pad.stock.t for pad in plant.rom_pads.values()),
        "waste_dump": plant.waste_dump.stock.t,
        "mill_charge": plant.mill.charge.t,
        # What left the mill: concentrate (in store and shipped) and tailings solids.
        "concentrate": conc.t + conc.shipped_t,
        "tailings": tails.t,
    }
    copper = {
        "trucks": sum(t.payload_cu_t for t in fleet.trucks),
        "hopper": plant.crusher.hopper.cu,
        "conveyor": plant.conveyor.load_cu,
        "stockpile": plant.stockpile.cu,
        "rom_pads": sum(pad.stock.cu for pad in plant.rom_pads.values()),
        "waste_dump": plant.waste_dump.stock.cu,
        "mill_charge": plant.mill.charge.cu,
        "concentrate": conc.cu + conc.shipped_cu,
        "tailings": tails.cu,
    }
    return {
        "rock_sources": plant.initial_stock_t + sim.kpis.ore_mined_t + sim.kpis.waste_mined_t,
        "rock_sinks": sum(rock.values()),
        "rock": rock,
        "copper_sources": plant.initial_stock_cu + sim.kpis.cu_mined_t,
        "copper_sinks": sum(copper.values()),
        "copper": copper,
        "milled": plant.mill.total_t,
        "mill_out_t": conc.t + conc.shipped_t + tails.t,
        "mill_copper_in": plant.mill.total_cu,
        "mill_copper_out": plant.flotation.cu_conc_t + plant.flotation.cu_tails_t,
    }
