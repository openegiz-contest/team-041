"""Indicators along the causal chain of a scenario.

A scenario twin shows up to four indicators, ordered along the chain of
cause and effect it starts (for hard ore: the work index at the mill, the
feed its power allows, the energy per tonne, the copper). Each one is
measured over two windows of equal length: «before» ends at the start of the
scenario, «now» ends at the present moment; the length is the time since the
start, at most `scenario.kpi_window_max_min` minutes (nothing is compared in
the first five minutes). Every figure is a sum or a mean of what the model did
(tonnes crushed, trips completed, litres burned), read from the history the
model keeps (`History`).

An indicator swings by itself: a window with one truck more or less, a
harder block on the stockpile. The swing is measured on the history before
the start: the indicator over consecutive windows of the same length, and
the root mean square of the differences between neighbours, leaving out the
windows in which another scenario was changing the mine. A change counts as
`significant` when it exceeds `scenario.kpi_noise_k` times that, and also,
whatever the swing, when it is large: a rate that falls to zero, or a change
of at least `scenario.kpi_large_change_pct` of the value before, once the
windows are `scenario.kpi_large_min_window_min` long. Short windows of a
noisy rate can swing widely, but a crusher that stops or a dig rate that
halves is not an ordinary swing.

The «before» window must show the mine in ordinary work: no other scenario
in it, nor within `scenario.kpi_recovery_min` before it (the queues, stocks
and routes settle that long after a scenario ends). When scenarios run back
to back, the latest clean window of the same length further back is taken
and `kpi_baseline_note` says where it lies, but it may end at most
`scenario.kpi_baseline_max_age_min` before the start: an older window shows
another state of the mine (other faces, another shift), not the one the
scenario changed. If there is no clean window that recent (a simulator just
started, or scenarios one after another), the «before» value is not made up
from what is there: the indicator reports that the baseline is missing
(`kpiN_baseline_known` false) and the text says «база недоступна».

A road closure changes the routes at once, but the trips completed in its
first minutes were begun on the old ones, and the first to finish after it
are the short ones; their mean would show the trip shorter while the route
has grown, and the ore tipped at the crusher over a few minutes is the loads
that were already on their way, bunched behind the closure. Its trip
indicators therefore count only the trips begun after the start, and until
`scenario.route_trips_min` of them have finished, «now» of the loaded haul,
the trip time and the ore to the crusher is the forecast from the open
routes (`kpiN_forecast` true, labelled so in the text). A clay face is read
the same way: the clay ore tipped at ОФ-2 and the clay-ore stockpile in its
first minutes is the loads begun before it, so until enough trips begun
after the start have finished, «now» of that rate is the forecast from where
the face's ore goes now.
"""
from __future__ import annotations

import bisect
import math
from collections import deque
from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable

from mine.sim.text import num

if TYPE_CHECKING:
    from mine.sim.model import Simulation

MIN_WINDOW_S = 300.0            # below five minutes there is nothing to compare yet
MIN_TRIPS = 3                   # a trip indicator needs this many completed trips in a window
MAX_NOISE_WINDOWS = 8


@dataclass
class Trip:
    finished: float
    duration_s: float
    queue_s: float
    payload_t: float
    loaded_m: float
    dump: str

    @property
    def travel_s(self) -> float:
        """The trip without its waiting at the loader and the dump point."""
        return self.duration_s - self.queue_s


class History:
    """Once a simulated minute: the running totals and a few instantaneous
    values of the mine; and every completed truck trip. Kept for
    `scenario.kpi_history_h` hours."""

    def __init__(self, sim: "Simulation"):
        self.sim = sim
        keep_h = float(sim.params["scenario"]["kpi_history_h"])
        self.times: deque[float] = deque(maxlen=int(keep_h * 60) + 2)
        self.samples: deque[dict] = deque(maxlen=int(keep_h * 60) + 2)
        self.trips: deque[Trip] = deque()
        self.keep_s = keep_h * 3600.0
        self.settle_s = float(sim.params["sim"]["history_from_min"]) * 60.0
        self.recovery_s = float(sim.params["scenario"]["kpi_recovery_min"]) * 60.0

    def trip(self, trip: Trip) -> None:
        self.trips.append(trip)
        while self.trips and trip.finished - self.trips[0].finished > self.keep_s:
            self.trips.popleft()

    def sample(self) -> None:
        sim = self.sim
        if sim.now < self.settle_s:
            return          # the start-up of the warm start (trucks setting off from their starting nodes) is no baseline
        plant, kpis, fleet = sim.plant, sim.kpis, sim.fleet
        mill = plant.mill
        moving = [t.speed_ms * 3.6 for t in fleet.trucks if t.seg is not None and t.speed_ms > 0.5]
        bwi = mill.feed_bwi if mill.ground_tph > 0 else (plant.stockpile.bwi * plant.hardness_factor or mill.feed_bwi)
        clay = plant.stockpile.clay if plant.stockpile.t > 100.0 else mill.feed_clay
        rate = mill.setpoint_tph if mill.setpoint_tph > 0 else float(sim.params["mill"]["design_tph"])
        clay_plant = plant.clay_plant
        self.times.append(sim.now)
        self.samples.append({
            "crushed_t": plant.crusher.total_t, "crushed_cu": plant.crusher.total_cu,
            "milled_t": mill.total_t, "mill_kwh": mill.energy_kwh, "cu_t": plant.flotation.cu_conc_t,
            "dug_t": kpis.ore_mined_t + kpis.waste_mined_t, "hauled_t": kpis.hauled_t,
            "to_crusher_t": kpis.tipped_at.get("crusher", 0.0), "to_rom_t": kpis.tipped_at.get("rom", 0.0),
            "diesel_l": kpis.diesel_l, "reclaim_t": plant.reclaimed_t,
            "stock_h": plant.stockpile.t / rate, "stock_t": plant.stockpile.t,
            "bwi": bwi, "max_feed": mill.max_feed_tph(bwi, clay),
            # Clay: in the feed of ОФ-1, its recovery, and the clay-ore plant ОФ-2 with its stockpiles.
            "clay_feed": clay, "recovery1": plant.flotation.recovery_pct if mill.ground_tph > 0 else None,
            "to_clay_t": kpis.tipped_at.get("clay", 0.0) + kpis.tipped_at.get("clay-stock", 0.0),
            "to_clay_stock_t": kpis.tipped_at.get("clay-stock", 0.0),
            "clay_milled_t": clay_plant.total_t, "clay_bin_t": clay_plant.bin.t, "clay_cu_t": clay_plant.cu_conc_t,
            "speed": sum(moving) / len(moving) if moving else None,
            "limit": sum(s.speed_limit_kmh for s in sim.roads.segments.values()) / len(sim.roads.segments),
            # A minute some what-if scenario was changing the mine: not part of its usual swing.
            "disturbed": any(s.status != "inactive" for s in sim.scenarios.values()),
            "broken_t": sum(b.tonnes for b in sim.pit.blocks.values() if b.status in ("blasted", "mining")),
        })

    # -- reading ------------------------------------------------------------------------

    def at(self, moment: float) -> dict | None:
        """The last sample taken at or before `moment`."""
        i = bisect.bisect_right(self.times, moment + 1e-6) - 1
        return self.samples[i] if i >= 0 else None

    def span(self, a: float, b: float) -> list[dict]:
        lo = bisect.bisect_right(self.times, a + 1e-6)
        hi = bisect.bisect_right(self.times, b + 1e-6)
        return [self.samples[i] for i in range(lo, hi)]

    def oldest(self) -> float:
        return self.times[0] if self.times else self.sim.now

    def disturbed(self, a: float, b: float) -> bool:
        return any(s["disturbed"] for s in self.span(a, b))

    def clean(self, a: float, b: float) -> bool:
        """No scenario changed the mine in the window [a, b], nor within the recovery time before it: the queues,
        the stocks and the cycle need that long to settle after a scenario ends."""
        return not self.disturbed(a - self.recovery_s, b)


@dataclass
class Indicator:
    """One indicator of a chain.

    kind   rate   the increase of a running total per hour (`key`)
           ratio  increase of `key` over increase of `per`, times `scale`
           mean   mean of an instantaneous value (`key`) over the window
           level  the value of `key` at the end of the window
           trip   mean of a trip attribute (`key`, scaled) over the trips completed in the window
    """
    label: str
    unit: str
    digits: int
    kind: str
    key: str
    per: str = ""
    scale: float = 1.0
    where: Callable[[Trip], bool] | None = None

    def value(self, h: History, a: float, b: float) -> float | None:
        if self.kind == "trip":
            values = [getattr(t, self.key) for t in h.trips if a < t.finished <= b and (self.where is None or self.where(t))]
            return sum(values) / len(values) * self.scale if len(values) >= MIN_TRIPS else None
        if self.kind == "mean":
            values = [s[self.key] for s in h.span(a, b) if s[self.key] is not None]
            return sum(values) / len(values) * self.scale if values else None
        end = h.at(b)
        if end is None:
            return None
        if self.kind == "level":
            return end[self.key] * self.scale
        start = h.at(a)
        if start is None:
            return None
        if self.kind == "rate":
            hours = (b - a) / 3600.0
            return (end[self.key] - start[self.key]) / hours * self.scale if hours > 0 else None
        base = end[self.per] - start[self.per]
        return (end[self.key] - start[self.key]) / base * self.scale if base > 1e-9 else None


def _rms(values: list[float]) -> float:
    return math.sqrt(sum(v * v for v in values) / len(values))


def baseline_end(h: History, started: float, window: float, max_age: float = math.inf) -> float | None:
    """End of the «before» window: the start itself if the window before it is clean (no other scenario, nor
    its recovery, in it), else the latest clean window of the same length further back, in steps of a quarter
    window, ending at most `max_age` seconds before the start; None if the history holds no such window."""
    step = max(window / 4.0, 60.0)
    end = started
    while end - window >= h.oldest() - 1e-6 and started - end <= max_age + 1e-6:
        if h.clean(end - window, end - 1e-6):
            return end
        end -= step
    return None


def before(sim: "Simulation", indicators: list[Indicator], started: float, window: float) -> dict:
    """What the «before» side of the comparison needs: the end of the clean window (or None), the note on it,
    the value of each indicator over it, and each indicator's usual swing over windows of the same length."""
    h = sim.kpis.history
    max_age_min = float(sim.params["scenario"]["kpi_baseline_max_age_min"])
    end = baseline_end(h, started, window, max_age_min * 60.0)
    if end is None:
        note = (f"база недоступна: за {max_age_min:.0f} мин до запуска нет чистого окна той же длины (модель "
                f"работает меньше окна, или шли другие сценарии и час восстановления после них)")
    elif end < started - 1.0:
        note = (f"«до» — окно {sim.sim_time(end - window)[11:16]}–{sim.sim_time(end)[11:16]}: позже шли другие "
                f"сценарии или восстановление после них")
    else:
        note = ""
    # Windows of the same length before the start, each a quarter window after the one before it;
    # windows in which another scenario was changing the mine, or the mine recovering from it, do not count.
    step = window / 4.0
    ends = []
    for k in range(4 * MAX_NOISE_WINDOWS):
        b = started - k * step
        if b - window < h.oldest() or len(ends) >= MAX_NOISE_WINDOWS:
            break
        if h.clean(b - window, b - 1e-6):
            ends.append(b)
    bases, swings = [], []
    for ind in indicators:
        bases.append(ind.value(h, end - window, end) if end is not None else None)
        values = [ind.value(h, b - window, b) for b in ends]
        diffs = [x - y for x, y, b1, b2 in zip(values, values[1:], ends, ends[1:])
                 if x is not None and y is not None and abs(b1 - b2 - step) < 1.0]
        # Neighbours overlapping by three quarters differ by half of what two separate windows do.
        swings.append(2.0 * _rms(diffs) if len(diffs) >= 2 else None)
    return {"end": end, "note": note, "bases": bases, "swings": swings}


def measure(sim: "Simulation", indicators: list[Indicator], started: float | None,
            expected: Callable[[Indicator, float | None], float | None] | None = None,
            memo: dict | None = None) -> dict:
    """The chain indicators of a scenario, as twin features, and the window used.

    `expected` (a road closure): the trip indicators count only the trips begun after the start, and until
    `scenario.route_trips_min` of them have finished, «now» is what `expected(indicator, before)` forecasts from
    the routes, flagged as a forecast. `memo` (the scenario's, emptied at each start) keeps the «before» side once
    the window has reached its full length: it no longer changes, and the history it was read from is kept only
    `scenario.kpi_history_h` hours."""
    h, p = sim.kpis.history, sim.params["scenario"]
    now = h.times[-1] if h.times else sim.now
    longest = float(p["kpi_window_max_min"]) * 60.0
    out, rows = {}, []
    if started is None:
        window, active = longest, False
    else:
        window, active = min(max(now - started, 0.0), longest), now - started >= MIN_WINDOW_S
    large_share = float(p["kpi_large_change_pct"]) / 100.0
    large_window = float(p["kpi_large_min_window_min"]) * 60.0
    fixed = None
    if active:
        full = window >= longest - 1e-6
        fixed = memo.get("before") if memo is not None and full else None
        if fixed is None:
            fixed = before(sim, indicators, started, window)
            if memo is not None and full:
                memo["before"] = fixed
    note = fixed["note"] if fixed else ""
    fresh = [t for t in h.trips if t.finished - t.duration_s >= started] if active and expected else []
    for n, ind in enumerate(indicators, start=1):
        baseline_known, forecast, swing = True, False, None
        if active:
            baseline_known = fixed["end"] is not None
            base, swing = fixed["bases"][n - 1], fixed["swings"][n - 1]
            routed = expected(ind, base) if expected is not None and ind.kind in ("trip", "rate") else None
            if routed is not None and len(fresh) < int(p["route_trips_min"]):
                current, forecast = routed, True
            elif routed is not None and ind.kind == "trip":
                values = [getattr(t, ind.key) for t in fresh if t.finished > now - window]
                current = sum(values) / len(values) * ind.scale if len(values) >= MIN_TRIPS else routed
                forecast = len(values) < MIN_TRIPS
            else:
                current = ind.value(h, now - window, now)
        else:
            base = current = ind.value(h, now - longest, now)
        significant, known = False, False
        if active and base is not None and current is not None:
            magnitude = abs(current - base)
            if base > 0 and (current <= 0 if ind.kind == "rate" else False):
                significant, known = True, True             # a rate that dropped to zero
            elif window >= large_window and abs(base) > 10.0 ** -ind.digits and magnitude >= large_share * abs(base):
                significant, known = True, True             # a large change is never an ordinary swing
            if swing is not None and not significant:
                floor = max(0.01 * abs(base), 10.0 ** -ind.digits)
                significant, known = magnitude > max(float(p["kpi_noise_k"]) * swing, floor), True
        if current is None:
            current = base
        if base is None:
            base = current if baseline_known else None
        current = round(current or 0.0, ind.digits)
        base = round(base or 0.0, ind.digits) if base is not None else None
        out.update({f"kpi{n}_label": ind.label, f"kpi{n}_unit": ind.unit,
                    f"kpi{n}_baseline": float(base) if base is not None else 0.0, f"kpi{n}_baseline_known": base is not None,
                    f"kpi{n}_current": float(current), f"kpi{n}_significant": bool(significant),
                    f"kpi{n}_forecast": forecast})
        rows.append((ind, base, current, significant, known, forecast))
    for n in range(len(indicators) + 1, 5):
        out.update({f"kpi{n}_label": "", f"kpi{n}_unit": "", f"kpi{n}_baseline": 0.0, f"kpi{n}_baseline_known": False,
                    f"kpi{n}_current": 0.0, f"kpi{n}_significant": False, f"kpi{n}_forecast": False})
    out["kpi_window_min"] = round(window / 60.0, 1) if active else 0.0
    out["kpi_baseline_note"] = note
    return {"features": out, "rows": rows, "active": active, "trips_after_start": len(fresh), "note": note}


def sentence(rows: list, active: bool, note: str = "", trips_after_start: int = 0,
             forecast_basis: str = "прогноз по маршрутам") -> str:
    """The chain as one Russian sentence: 'Цепочка: A 1 → 2 ед. (+5 %); B ... (в пределах колебаний).'"""
    if not active:
        return "Показатели цепочки сравниваются через 5 минут модели после запуска."
    parts = []
    for ind, base, current, significant, known, forecast in rows:
        label = ind.label.lower() if parts else ind.label
        how = (f"{forecast_basis}: рейсов, начатых после запуска, закончено {trips_after_start}"
               if forecast else "")
        if base is None:
            parts.append(f"{label} сейчас {num(current, ind.digits)} {ind.unit}" + (f" ({how})" if how else ""))
            continue
        change = f"{100 * (current - base) / base:+.0f} %" if base else ""
        tail = "" if significant else (", в пределах колебаний" if known else
                                       ", колебания не оценены: до запуска шли другие сценарии")
        inner = ", ".join(x for x in (how, f"{change}{tail}" if change else tail[2:]) if x)
        parts.append(f"{label} {num(base, ind.digits)} → {num(current, ind.digits)} {ind.unit}" + (f" ({inner})" if inner else ""))
    head = f"Цепочка ({note}): " if note.startswith("база") else "Цепочка: "
    tail = f" {note[0].upper()}{note[1:]}." if note and not note.startswith("база") else ""
    return head + "; ".join(parts) + "." + tail
