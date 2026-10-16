"""The simulator and the module cores in one process, without a broker:
`Coupled` lives in mine/sim/coupling.py (the forecast engine uses it too);
this file adds the 7-day runs the module tests compare.
"""
from __future__ import annotations

import re

from mine.model.build_twins import build_things, load_params, load_world
from mine.sim.coupling import ALL, CORES, Coupled, things_now  # noqa: F401  (re-exported for the tests)
from mine.sim.kpis import balance
from mine.sim.model import Simulation

# A feature name, a thing ID or a key=value pair in a Russian text: "hours_since_service", "truck-09", "hold_min=720".
LATIN = re.compile(r"[a-z]+_[a-z_]+|\b[a-z]+-\d{2}\b|\w=\w")


def week(modules: tuple[str, ...] = (), days: int = 7, seed: int | None = None) -> dict:
    """Run the mine for `days` with the given modules on; return what was measured.
    The same seed gives the same geology, failures and driving in every run."""
    world, params = load_world(), load_params()
    sim = Simulation(build_things(world, params), world, params, seed)
    rig = Coupled(sim, modules)
    plant, kpis, band = sim.plant, sim.kpis, params["grade_control"]
    in_band_s = fed_s = queue_load_s = queue_dump_s = 0
    trucks = sim.fleet.trucks
    lowest = plant.stockpile.t
    severities: dict[str, int] = {}
    latin: list[str] = []                   # Russian texts with an identifier or a key=value in them
    warnings: list[str] = []
    longest_message_gap = 0
    drilled_at_start = sum(r.metres for r in sim.pit.rigs)
    rig_standby_s = 0
    for step in range(days * 24 * 3600):
        sim.step()
        rig_standby_s += sum(1 for r in sim.pit.rigs if r.state == "standby")
        if int(sim.now) % rig.period_s == 0:
            rig.exchange()
        if plant.mill.ground_tph > 0:
            fed_s += 1
            if abs(plant.flotation.feed_grade_pct - band["target_pct"]) <= band["band_pct"]:
                in_band_s += 1
        if plant.stockpile.t < lowest:
            lowest = plant.stockpile.t
        for truck in trucks:
            if truck.state == "queue_load":
                queue_load_s += 1
            elif truck.state == "queue_dump":
                queue_dump_s += 1
        if step % 3600 == 0:
            for event in sim.pop_events():
                severities[event["severity"]] = severities.get(event["severity"], 0) + 1
                if LATIN.search(event["text"]):
                    latin.append(event["text"])
                if event["severity"] == "warning":
                    warnings.append(event["text"])
            for core in rig.cores.values():
                latin += [f"{core.name}.{k}: {v}" for k, v in core.twin().items()
                          if k in ("last_decision", "effect_text") and isinstance(v, str) and LATIN.search(v)]
            for name, scenario in sim.scenarios.items():
                text = scenario.values()["impact_text"]
                if LATIN.search(text):
                    latin.append(f"{name}: {text}")
    hours = sim.now / 3600.0
    mill = plant.mill
    b = balance(sim)
    return {
        "modules": "+".join(modules) or "none",
        "seed": sim.seed,
        "hours": hours,
        "trips": kpis.cycle_count,
        "queue_min_per_trip": kpis.queue_s / kpis.cycle_count / 60.0,
        "queue_load_min_per_trip": queue_load_s / kpis.cycle_count / 60.0,
        "queue_dump_min_per_trip": queue_dump_s / kpis.cycle_count / 60.0,
        "cycle_min": kpis.cycle_s / kpis.cycle_count / 60.0,
        "tonnes_moved": kpis.hauled_t,
        "ore_mined_t": kpis.ore_mined_t,
        "waste_mined_t": kpis.waste_mined_t,
        "strip_ratio": kpis.waste_mined_t / kpis.ore_mined_t,
        "rom_pads_t": sum(pad.stock.t for pad in plant.rom_pads.values()),
        "rom_pads_cu_t": sum(pad.stock.cu for pad in plant.rom_pads.values()),
        "pad_t": {name: round(pad.stock.t) for name, pad in plant.rom_pads.items()},
        "crusher_tph": plant.crusher.total_t / hours,
        "mill_mw": mill.energy_kwh / hours / 1000.0,
        "diesel_l_per_t": kpis.diesel_l / kpis.hauled_t,
        "stockpile_end_t": plant.stockpile.t,
        "ore_milled_t": mill.total_t,
        "mill_feed_pct_of_design": 100.0 * mill.total_t / hours / params["mill"]["design_tph"],
        "head_grade_pct": 100.0 * mill.total_cu / mill.total_t,
        "grade_in_band_pct": 100.0 * in_band_s / fed_s if fed_s else 0.0,
        "recovery_pct": 100.0 * plant.flotation.cu_conc_t / mill.total_cu,
        "cu_t": plant.flotation.cu_conc_t,
        "cu_tph": plant.flotation.cu_conc_t / hours,
        "mill_stops": mill.stops,
        "mill_kwh_t": mill.energy_kwh / mill.total_t,
        "truck_availability_pct": 100.0 * (1.0 - kpis.unavailable_s / kpis.truck_s),
        "use_of_available_pct": 100.0 * kpis.productive_s / (kpis.truck_s - kpis.unavailable_s),
        "breakdowns": sim.reliability.breakdowns,
        "workshop_jobs": sim.reliability.jobs_done,
        "fuel_lph": kpis.diesel_l / (kpis.engine_s / 3600.0),
        "lowest_stockpile_t": lowest,
        "longest_standby_min": kpis.longest_standby_s / 60.0,
        "blasts": sim.pit.blasts_fired,
        # Drilling against the rock dug: metres drilled and the metres the rock dug needed (holes of the pattern).
        "drilled_m": sum(rig.metres for rig in sim.pit.rigs) - drilled_at_start,
        "drill_needed_m": (kpis.ore_mined_t + kpis.waste_mined_t) / (params["drill"]["burden_m"] * params["drill"]["spacing_m"]
                          * params["drill"]["bench_height_m"] * params["rock"]["insitu_density_t_m3"]) * params["drill"]["hole_depth_m"],
        "rig_standby_pct": 100.0 * rig_standby_s / (len(sim.pit.rigs) * days * 24 * 3600),
        "blocks_dug_out": sum(1 for blk in sim.pit.blocks.values() if blk.status == "depleted"),
        "events": severities,
        "latin_texts": sorted(set(latin))[:20],
        "warning_texts": warnings[:40],
        "messages": rig.messages,
        "decisions": {name: core.decisions for name, core in rig.cores.items()},
        "rock_balance_t": b["rock_sources"] - b["rock_sinks"],
        "copper_balance_t": b["copper_sources"] - b["copper_sinks"],
        "mill_copper_balance_t": b["mill_copper_in"] - b["mill_copper_out"],
    }


_WEEKS: dict[tuple[str, ...], dict] = {}


def weeks(configs: list[tuple[str, ...]]) -> dict[tuple[str, ...], dict]:
    """7-day runs for several module sets, computed once per test process,
    side by side on the available processors."""
    import os
    from concurrent.futures import ProcessPoolExecutor
    missing = [c for c in configs if c not in _WEEKS]
    if missing:
        with ProcessPoolExecutor(max_workers=max(1, min(len(missing), os.cpu_count() or 1))) as pool:
            for config, result in zip(missing, pool.map(week, missing)):
                _WEEKS[config] = result
    return {c: _WEEKS[c] for c in configs}


# The module sets the test suite runs for a week: none, each module alone, all four.
WEEK_CONFIGS = [(), *((name,) for name in ALL), ALL]
