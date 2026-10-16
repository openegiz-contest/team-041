"""Checks of the mine model: haulage, pit, plant, failures, messages. They
need no platform, only the image:

    make mine-test
"""
import copy
import math
import unittest

from mine.common import envelope
from mine.model.build_twins import build_things, full_power_fuel_lph, load_params, load_world
from mine.sim.kpis import balance
from mine.sim.model import Simulation, short
from mine.sim.reliability import Fault

WORLD, PARAMS = load_world(), load_params()
THINGS = build_things(WORLD, PARAMS)
UNITS = {short(t["thingId"]): envelope.feature_units(t) for t in THINGS}
MODULE_TWINS = {short(t["thingId"]) for t in THINGS if t["attributes"]["kind"] == "module"}
FORECAST_TWINS = {short(t["thingId"]) for t in THINGS if t["attributes"]["kind"] == "forecast"}
NS = WORLD["namespace"]
CLOSURE = "scenario-road-closure"
MAIN_RAMP_TOP = "road-segment-01"          # the main ramp from the rim down to the 300 m bench
RIGS = tuple(short(t["thingId"]) for t in THINGS if t["attributes"]["kind"] == "drill-rig")
LOADERS = tuple(short(t["thingId"]) for t in THINGS if t["attributes"]["kind"] == "loader")
_WARM: list[Simulation] = []


def new_sim(seed: int | None = None) -> Simulation:
    """A model after its warm start. The default one is built once and copied."""
    if seed is not None:
        sim = Simulation(build_things(WORLD, PARAMS), WORLD, PARAMS, seed)
        sim.warm_start()
        return sim
    if not _WARM:
        _WARM.append(new_sim(PARAMS["sim"]["seed"]))
    return copy.deepcopy(_WARM[0])


def run(sim: Simulation, minutes: float) -> None:
    for _ in range(int(minutes * 60)):
        sim.step()


def run_until(sim: Simulation, condition, limit_min: float = 600) -> None:
    for _ in range(int(limit_min * 60)):
        if condition():
            return
        sim.step()
    raise AssertionError(f"condition not reached within {limit_min} simulated minutes")


def texts(sim: Simulation) -> str:
    return "\n".join(e["text"] for e in sim.pop_events())


class Telemetry(unittest.TestCase):
    def test_values_match_twin_definitions(self):
        sim = new_sim()
        for _ in range(3):
            values = sim.values()
            # The model publishes every twin but the journal (one event per update),
            # the module twins, which the module services write themselves, and the
            # forecast twins, which the runner's forecast desk writes.
            self.assertEqual(set(values) | {"journal"} | MODULE_TWINS | FORECAST_TWINS, set(UNITS))
            for name, features in values.items():
                message = envelope.modify_features(NS, name, features, UNITS[name])   # raises on a mismatch
                self.assertEqual(message["path"], "/features")
            run(sim, 180)
        events = sim.pop_events()
        self.assertTrue(events)
        for event in events:
            envelope.modify_features(NS, "journal", event, UNITS["journal"])

    def test_warm_start_is_mid_shift(self):
        values = new_sim().values()
        self.assertEqual(values["mine"]["sim_time"], "2026-10-26T10:00:00")
        self.assertEqual(values["mine"]["shift"], "day")
        self.assertGreater(values["truck-01"]["trips"], 2)
        self.assertGreater(values["crusher"]["tonnes_total"], 3000)
        self.assertEqual(values["mill"]["state"], "running")
        self.assertAlmostEqual(values["stockpile"]["level_pct"], 60, delta=15)
        self.assertFalse(any(n.startswith("module-") for n in values))
        self.assertEqual([v["status"] for n, v in values.items() if n.startswith("scenario-")], ["inactive"] * 6)

    def test_same_seed_same_run(self):
        a, b = new_sim(7), new_sim(7)
        run(a, 300)
        run(b, 300)
        self.assertEqual(a.values(), b.values())
        self.assertEqual(a.pop_events(), b.pop_events())
        other = new_sim(8)
        run(other, 300)
        self.assertNotEqual(a.values()["mine"]["ore_mined_t"], other.values()["mine"]["ore_mined_t"])


class Fork(unittest.TestCase):
    """The model holds no threads, sockets or clocks: a running model can be
    deep-copied and the copy run ahead (a forecast) without touching the original."""

    def test_a_copy_runs_ahead_and_the_original_follows_the_same_path(self):
        sim = new_sim()
        sim.handle_message("scenario-heavy-rain", "activate", {})
        run(sim, 30)
        before = sim.values()
        fork = copy.deepcopy(sim)
        run(fork, 90)
        self.assertEqual(sim.values(), before)                        # the original did not move
        self.assertNotEqual(fork.values()["mine"]["sim_time"], before["mine"]["sim_time"])
        run(sim, 90)
        self.assertEqual(sim.values(), fork.values())                 # the same seed state gives the same future
        self.assertEqual(sim.pop_events(), fork.pop_events())


class Haulage(unittest.TestCase):
    def test_passes_fill_the_truck(self):
        sim = new_sim()
        nominal = PARAMS["truck"]["payload_t"]
        payloads, passes = [], set()
        for _ in range(4 * 3600):
            sim.step()
            for loader in sim.fleet.loaders.values():
                passes.add(loader.passes)
            payloads += [t.hauled_t for t in sim.fleet.trucks if t.state == "hauling" and t.seg is not None]
        self.assertEqual(max(passes), 4)
        self.assertGreater(len(set(payloads)), 20)                  # the payload varies from trip to trip
        for payload in payloads:
            self.assertAlmostEqual(payload, nominal, delta=0.1 * nominal)
            self.assertLessEqual(payload, nominal * PARAMS["truck"]["overload_limit"] + 1e-6)

    def test_trucks_carry_the_material_and_grade_of_their_block(self):
        sim = new_sim()
        run(sim, 30)
        values = sim.values()
        loaded = [v for n, v in values.items() if n.startswith("truck-") and v["state"] == "hauling"]
        self.assertTrue(loaded)
        for v in loaded:
            self.assertIn(v["material"], ("ore", "low-grade", "waste"))
            self.assertGreater(v["grade_pct"], 0)
            expected = "waste-dump" if v["material"] == "waste" else ("crusher", "rom-pad-01", "rom-pad-02")
            self.assertIn(short(v["assigned_dump"]), expected)
            self.assertEqual(v["assignment_source"] in ("static", "overflow"), True)

    def test_fuel_follows_engine_power(self):
        sim = new_sim()
        full = full_power_fuel_lph(PARAMS)
        idle = full * PARAMS["truck"]["idle_load_factor"]
        climbing, standing = [], []
        for _ in range(3600):
            sim.step()
            for t in sim.fleet.trucks:
                if t.state == "hauling" and t.seg is not None and t.seg.grade_at(t.dist, t.seg_from) > 7.5 and t.speed_ms > 3:
                    climbing.append(t.fuel_rate_lph)
                elif t.state in ("queue_load", "queue_dump"):
                    standing.append(t.fuel_rate_lph)
        climbing.sort()
        self.assertGreater(climbing[len(climbing) // 2], 0.9 * full)  # a loaded climb takes all the engine has
        self.assertAlmostEqual(max(standing), idle, delta=0.01)
        truck = PARAMS["truck"]
        self.assertTrue(truck["cycle_load_factor_min"] * full >= 85 and truck["cycle_load_factor_max"] * full <= 130)

    def test_loaded_climb_is_slow_and_empty_road_is_fast(self):
        sim = new_sim()
        climb, flat = [], []
        for _ in range(3600):
            sim.step()
            for t in sim.fleet.trucks:
                if t.seg is None:
                    continue
                grade = t.seg.grade_at(t.dist, t.seg_from)
                if t.state == "hauling" and grade > 7.5:
                    climb.append(t.speed_ms * 3.6)
                elif t.state == "returning" and abs(grade) < 1 and t.seg.road_class == "surface":
                    flat.append(t.speed_ms * 3.6)
        self.assertTrue(9 <= sorted(climb)[len(climb) // 2] <= 14, "a loaded 785C on the 8 % ramp: about 11 km/h")
        self.assertLessEqual(max(flat), PARAMS["road"]["speed_limit_surface_kmh"] + 1e-6)
        self.assertGreater(sorted(flat)[len(flat) // 2], 30)

    def test_refuelling_at_the_fuel_station(self):
        sim = new_sim()
        truck = sim.fleet.by_name["truck-07"]
        truck.fuel_l = 0.16 * PARAMS["truck"]["tank_l"]
        run_until(sim, lambda: truck.state == "to_fuel")              # on its way: a state of its own
        self.assertEqual(truck.goal[0], "refuel")
        run_until(sim, lambda: truck.speed_ms > 5, 30)
        self.assertEqual(sim.values()[truck.name]["state"], "to_fuel")
        run_until(sim, lambda: truck.state == "refuel")               # at the station: standing
        self.assertEqual(sim.values()[truck.name]["speed_kmh"], 0.0)
        run_until(sim, lambda: truck.place == sim.fleet.station.pos)
        self.assertEqual(sim.values()[truck.name]["speed_kmh"], 0.0)
        self.assertIn(truck, sim.fleet.station.pumps)
        self.assertGreaterEqual(sim.values()["fuel-station"]["trucks_fuelling"], 1)
        run_until(sim, lambda: truck.state != "refuel")
        self.assertGreater(100 * truck.fuel_l / PARAMS["truck"]["tank_l"], 95)
        self.assertGreater(sim.values()["fuel-station"]["litres_issued"], 1500)

    def test_shift_change(self):
        sim = new_sim()
        mine = sim.values()["mine"]
        # In the first shift the figures of the shift are those since the start.
        self.assertEqual((mine["cu_shift_t"], mine["ore_shift_t"]), (round(mine["cu_produced_t"], 2), round(mine["ore_mined_t"])))
        self.assertEqual(mine["shift_start_sim_time"], "2026-10-26T08:00:00")
        run_until(sim, lambda: sim.shift_index == 1, limit_min=700)
        before = sim.values()["mine"]
        self.assertEqual(sim.sim_time(), "2026-10-26T20:00:00")
        self.assertEqual(sim.values()["mine"]["shift"], "night")
        self.assertLess(sim.values()["substation"]["energy_mwh_shift"], 1.0)
        # The figures «за смену» start again from zero at the shift change; the totals since the start go on.
        self.assertEqual(before["shift_start_sim_time"], "2026-10-26T20:00:00")
        self.assertLess(before["cu_shift_t"], 0.5)
        self.assertLess(before["ore_shift_t"], 500)
        self.assertGreater(before["cu_produced_t"], 100.0)
        self.assertIn("ночная смена", texts(sim))
        stood = set()
        for _ in range(3600):
            sim.step()
            stood.update(t.name for t in sim.fleet.trucks if t.state == "standby" and not t.waiting_route)
        self.assertGreaterEqual(len(stood), 8)                        # crews change at the first dump of the shift
        after = sim.values()["mine"]
        self.assertAlmostEqual(after["cu_shift_t"], after["cu_produced_t"] - before["cu_produced_t"] + before["cu_shift_t"], delta=0.02)
        self.assertAlmostEqual(after["ore_shift_t"], after["ore_mined_t"] - before["ore_mined_t"] + before["ore_shift_t"], delta=2)
        self.assertTrue(8.0 < after["cu_shift_t"] < 18.0, after["cu_shift_t"])     # the first hour of one line
        self.assertLessEqual(sim.kpis.longest_standby_s, 45 * 60)                  # the meal break is the longest

    def test_closing_any_single_segment_strands_no_truck(self):
        for segment in new_sim().roads.segments:
            sim = new_sim()
            trips = {t.name: t.trips for t in sim.fleet.trucks}
            sim.handle_message(CLOSURE, "activate", {"target": f"{NS}:{segment}"})
            self.assertEqual(sim.values()[segment]["status"], "closed", segment)
            for _ in range(100 * 60):
                sim.step()
                self.assertFalse(any(t.waiting_route for t in sim.fleet.trucks), f"{segment} closed: a truck has no route")
            self.assertNotIn("нет открытого маршрута", texts(sim), segment)
            for t in sim.fleet.trucks:
                if t.state != "down" and "breakdown" not in t.service_reason:
                    self.assertGreater(t.trips, trips[t.name], f"{segment} closed: {t.name} made no trip")


class RoadClosure(unittest.TestCase):
    def test_closure_reroutes_and_cancel_restores(self):
        sim = new_sim()
        before = sim.values()[CLOSURE]
        self.assertEqual(before["status"], "inactive")
        self.assertEqual(before["baseline_cycle_min"], before["current_cycle_min"])
        self.assertGreater(before["baseline_feed_tph"], 2000)

        sim.handle_message(CLOSURE, "activate", {})
        after = sim.values()
        self.assertEqual(after[CLOSURE]["status"], "active")
        self.assertEqual(after[CLOSURE]["target"], f"{NS}:{MAIN_RAMP_TOP}")
        self.assertEqual(after[MAIN_RAMP_TOP]["status"], "closed")
        self.assertEqual(after[MAIN_RAMP_TOP]["closed_by"], CLOSURE)
        self.assertGreater(after[CLOSURE]["current_cycle_min"], after[CLOSURE]["baseline_cycle_min"] + 2)
        self.assertLess(after[CLOSURE]["current_feed_tph"], 0.9 * after[CLOSURE]["baseline_feed_tph"])

        run(sim, 60)
        active = sim.values()
        closed = sim.roads.segments[MAIN_RAMP_TOP]
        self.assertFalse(any(t.seg is closed for t in sim.fleet.trucks))
        west = [n for n, v in active.items() if n.startswith("truck-") and "road-node-09" in v["route"]]
        self.assertGreaterEqual(len(west), 4, "trucks leave the pit by the west ramp")
        self.assertGreater(active[CLOSURE]["current_cycle_min"], active[CLOSURE]["baseline_cycle_min"] + 2)
        self.assertIn("сменил маршрут", texts(sim))

        sim.handle_message(CLOSURE, "cancel", {})
        self.assertEqual(sim.values()[CLOSURE]["status"], "inactive")
        self.assertEqual(sim.values()[MAIN_RAMP_TOP]["status"], "open")
        run(sim, 60)
        hauling = [v for n, v in sim.values().items() if n.startswith("truck-") and v["state"] == "hauling"]
        self.assertTrue(hauling)
        for v in hauling:
            self.assertNotIn("road-node-09", v["route"])

    def test_closing_an_unused_road_changes_nothing(self):
        sim = new_sim()
        sim.handle_message(CLOSURE, "activate", {"target": f"{NS}:road-segment-18"})      # the west perimeter road
        values = sim.values()[CLOSURE]
        self.assertEqual(values["status"], "active")
        self.assertEqual(values["current_cycle_min"], values["baseline_cycle_min"])
        self.assertEqual(values["current_feed_tph"], values["baseline_feed_tph"])

    def test_truck_on_closed_segment_finishes_it(self):
        sim = new_sim()
        target = sim.roads.segments[MAIN_RAMP_TOP]
        run_until(sim, lambda: any(t.seg is target and t.state == "hauling" for t in sim.fleet.trucks), 60)
        truck = next(t for t in sim.fleet.trucks if t.seg is target and t.state == "hauling")
        sim.handle_message(CLOSURE, "activate", {})
        run_until(sim, lambda: truck.seg is not target, 30)
        self.assertEqual(truck.node, "road-node-01")             # it drove up to the rim
        self.assertEqual(truck.state, "hauling")

    def test_truck_heading_for_a_closed_segment_turns_back(self):
        sim = new_sim()
        lower = sim.roads.segments["road-segment-02"]
        run_until(sim, lambda: any(t.seg is lower and t.state == "hauling" for t in sim.fleet.trucks), 60)
        truck = next(t for t in sim.fleet.trucks if t.seg is lower and t.state == "hauling")
        sim.pop_events()
        sim.handle_message(CLOSURE, "activate", {})                  # closes the top of the ramp ahead of it
        run_until(sim, lambda: truck.state != "hauling", 90)
        self.assertIn(truck.state, ("queue_dump", "dumping"))        # it reached the crusher the other way
        self.assertIn(f"{truck.title} сменил маршрут", texts(sim))


class Pit(unittest.TestCase):
    def test_routine_blast_with_exclusion_zone(self):
        sim = new_sim()
        pit, radius = sim.pit, PARAMS["blast"]["exclusion_radius_m"]
        # The charged block nearest to a road: its zone closes the road.
        first = min((b for b in pit.blocks.values() if b.status == "charged"),
                    key=lambda b: min(s.distance_to(*b.centre) for s in sim.roads.segments.values()))
        self.assertLess(min(s.distance_to(*first.centre) for s in sim.roads.segments.values()), radius)
        for other in pit.blocks.values():
            other.charged_at = 0.0 if other is first else 1.0         # the oldest charge is fired first
        run_until(sim, lambda: pit.blast_phase == "clearing", 200)
        self.assertEqual(sim.sim_time(), "2026-10-26T12:40:00")       # 20 minutes before the 13:00 window
        block = pit.blast_block
        self.assertIs(block, first)
        values = sim.values()
        self.assertEqual((values["pit"]["blast_state"], values["pit"]["blast_block"], values["pit"]["blast_radius_m"]),
                         ("clearing", f"{NS}:{block.name}", float(radius)))
        closed = [s for s in sim.roads.segments.values() if s.status == "closed"]
        self.assertEqual({s.name for s in closed},
                         {s.name for s in sim.roads.segments.values() if s.distance_to(*block.centre) < radius})
        self.assertTrue(closed)                                       # a road runs through the zone
        for segment in closed:
            self.assertEqual(segment.closed_by, "blast")
        run_until(sim, lambda: block.status == "blasted", 60)
        self.assertGreaterEqual(sim.sim_time(), "2026-10-26T13:00:00")
        for truck in sim.fleet.trucks:                                # nobody is inside the zone when it fires
            self.assertGreaterEqual(math.dist(sim.fleet.position(truck)[:2], block.centre), radius, truck.name)
        for loader in sim.fleet.loaders.values():
            self.assertGreaterEqual(math.dist(loader.pos, block.centre), PARAMS["blast"]["equipment_withdrawal_m"])
        self.assertTrue(PARAMS["blast"]["p80_min_mm"] <= block.frag_p80_mm <= PARAMS["blast"]["p80_max_mm"])
        self.assertTrue(0.7 <= block.powder_factor <= 0.8)
        fired = sim.now
        run_until(sim, lambda: pit.blast_phase == "idle", 60)
        self.assertAlmostEqual(sim.now - fired, PARAMS["blast"]["reentry_min"] * 60, delta=2)
        self.assertFalse(any(s.status == "closed" for s in sim.roads.segments.values()))
        journal = texts(sim)
        for phrase in ("Подготовка к взрыву", "Взрыв:", "Опасная зона снята"):
            self.assertIn(phrase, journal)
        run(sim, 60)
        self.assertFalse(any(t.waiting_route or t.hold for t in sim.fleet.trucks))

    def test_fragmentation_follows_powder_factor_and_hardness(self):
        pit = new_sim().pit
        reference = pit.fragmentation(0.75, PARAMS["geology"]["bwi_mean_kwh_t"])
        self.assertEqual(reference, PARAMS["blast"]["p80_ref_mm"])
        self.assertLess(pit.fragmentation(0.80, 14.0), reference)     # more explosive, finer muck
        self.assertGreater(pit.fragmentation(0.75, 16.0), reference)  # harder rock, coarser muck

    def test_drilling_charging_and_block_life_cycle(self):
        sim = new_sim()
        rig = sim.pit.rigs[0]
        block = sim.pit.blocks[rig.block]
        self.assertEqual(block.status, "drilling")                    # the block a rig stands on is being drilled
        done, position = rig.holes_done, rig.pos
        run(sim, 180)
        self.assertGreater(rig.holes_done, done + 3)
        self.assertNotEqual(rig.pos, position)
        gross = (rig.holes_done - done) * PARAMS["drill"]["hole_depth_m"] / 3.0
        self.assertAlmostEqual(gross, PARAMS["drill"]["gross_rate_m_h"], delta=8)
        run_until(sim, lambda: block.status == "drilled", 4500)
        self.assertEqual(block.powder_factor, 0.0)
        run_until(sim, lambda: block.status == "charged", 3000)
        charge = block.powder_factor                                  # designed when the block is charged
        self.assertTrue(0.7 <= charge <= 0.8, charge)
        self.assertEqual(sim.values()[block.name]["powder_factor_kg_m3"], charge)
        block.charged_at = -1.0                                       # first in line: the oldest charge is fired first
        run_until(sim, lambda: block.status == "blasted", 1500)      # at the next daily window
        self.assertGreater(block.frag_p80_mm, 0)
        self.assertEqual(block.powder_factor, charge)                 # it is fired with the charge it was given

    def test_loader_moves_to_the_next_blasted_block(self):
        sim = new_sim()
        loader = sim.fleet.loaders["loader-02"]
        first = sim.pit.blocks[loader.block]
        first.tonnes = 400.0
        run_until(sim, lambda: first.status == "depleted", 120)
        self.assertEqual(loader.state, "relocating")
        nxt = sim.pit.blocks[loader.block]
        self.assertEqual((nxt.status, nxt.is_ore), ("mining", True))
        run_until(sim, lambda: loader.state == "loading", 180)
        self.assertEqual(loader.pos, nxt.face)
        journal = texts(sim)
        self.assertIn("отработан", journal)
        self.assertIn("переезжает", journal)

    def test_pushback_restores_an_exhausted_bench(self):
        sim = new_sim()
        blocks = [b for b in sim.pit.blocks.values() if b.bench == "bench-05"]
        old = [b.grade_pct for b in blocks]
        for block in blocks:
            block.status, block.tonnes = "depleted", 0.0
        self.assertEqual(sim.values()["bench-05"]["blocks_remaining"], 0)
        run(sim, 2)
        values = sim.values()
        self.assertEqual((values["bench-05"]["blocks_remaining"], values["bench-05"]["pushback"]), (10, 2))
        self.assertEqual(values["bench-01"]["pushback"], 1)          # a bench of the final wall has no blocks to push back
        self.assertEqual({b.status for b in blocks}, {"insitu"})
        self.assertNotEqual([b.grade_pct for b in blocks], old)
        self.assertEqual(sum(b.tonnes for b in blocks), sum(b.tonnes_initial for b in blocks))
        self.assertIn("разноска борта №2", texts(sim))

    def test_mine_keeps_running_when_blocks_run_out(self):
        """Tiny blocks with few holes: every block is drilled, blasted and dug
        out within days, a bench is exhausted and pushed back, and work goes on."""
        sim = new_sim()
        for block in sim.pit.blocks.values():
            block.tonnes_initial, block.holes_planned = 2500.0, 2
            block.tonnes = min(block.tonnes, 2500.0)
        for rig in sim.pit.rigs:
            rig.holes_done, rig.holes_planned = 0, 2
        mined = sim.kpis.ore_mined_t + sim.kpis.waste_mined_t
        run(sim, 13 * 24 * 60)
        self.assertGreaterEqual(max(b["pushback"] for b in sim.pit.benches.values()), 2)
        self.assertIn("разноска борта", texts(sim))
        self.assertGreater(sim.kpis.ore_mined_t + sim.kpis.waste_mined_t, mined + 50000)
        self.assertGreater(sum(1 for b in sim.pit.blocks.values() if b.status != "depleted"), 4)
        b = balance(sim)
        self.assertAlmostEqual(b["rock_sources"], b["rock_sinks"], delta=1.0)


class Plant(unittest.TestCase):
    def test_every_tonne_is_accounted_for(self):
        sim = new_sim()
        start = sum(b.tonnes for b in sim.pit.blocks.values()) + sim.kpis.ore_mined_t + sim.kpis.waste_mined_t
        run(sim, 24 * 60)
        b = balance(sim)
        self.assertGreater(b["milled"], 50000)
        self.assertAlmostEqual(b["rock_sources"], b["rock_sinks"], delta=0.01)
        self.assertAlmostEqual(b["copper_sources"], b["copper_sinks"], delta=1e-4)
        self.assertAlmostEqual(b["mill_copper_in"], b["mill_copper_out"], delta=1e-6)      # concentrate + tailings
        # What the mill ground left it as concentrate (in store and shipped) and tailings solids.
        self.assertAlmostEqual(b["milled"], b["mill_out_t"], delta=0.01)
        values, conc, tails = sim.values(), sim.plant.concentrate, sim.plant.tailings
        self.assertAlmostEqual(conc.produced_t, sim.plant.flotation.cu_conc_t / 0.28, delta=0.01)
        self.assertAlmostEqual(conc.t + conc.shipped_t, conc.produced_t, delta=0.01)
        self.assertAlmostEqual(conc.cu + conc.shipped_cu, sim.plant.flotation.cu_conc_t, delta=1e-6)
        self.assertAlmostEqual(tails.cu, sim.plant.flotation.cu_tails_t, delta=1e-6)
        self.assertEqual(values["tailings"]["stored_t"], round(tails.t))
        self.assertLessEqual(values["concentrate"]["level_t"], PARAMS["concentrate"]["shipment_t"])
        # A full lot leaves by rail and its copper with it.
        lot, cu_before = PARAMS["concentrate"]["shipment_t"], conc.cu
        added = lot - conc.t
        conc.receive(added, added * 0.28, 1.0)
        self.assertEqual((conc.shipments, round(conc.shipped_t)), (1, lot))
        self.assertAlmostEqual(conc.cu + conc.shipped_cu, cu_before + added * 0.28, delta=1e-6)
        self.assertIn("отгружена партия концентрата", texts(sim))
        in_blocks = sum(blk.tonnes for blk in sim.pit.blocks.values())
        self.assertAlmostEqual(start - in_blocks, sim.kpis.ore_mined_t + sim.kpis.waste_mined_t, delta=0.01)
        for place, tonnes in b["rock"].items():
            self.assertGreaterEqual(tonnes, -1e-6, place)
        self.assertGreater(b["rock"]["waste_dump"], 10000)
        self.assertGreater(b["copper"]["concentrate"], 100)

    def test_conveyor_delays_the_ore(self):
        sim = new_sim()
        plant = sim.plant
        self.assertEqual(plant.conveyor.steps, round(plant.conveyor.length_m / PARAMS["conveyor"]["belt_speed_ms"]))
        plant.crusher.stopped_by = "test"
        run(sim, plant.conveyor.steps / 60 + 1)                       # the belt runs empty
        self.assertEqual(plant.conveyor.load_t, 0.0)
        level = plant.stockpile.t
        plant.crusher.stopped_by = ""
        plant.crusher.hopper.add(200.0, 1.0, 2800.0, 70000.0)
        plant.initial_stock_t += 200.0
        plant.initial_stock_cu += 1.0
        for _ in range(plant.conveyor.steps - 5):
            before = plant.stockpile.t
            sim.step()
            self.assertLessEqual(plant.stockpile.t, before)           # still on the belt: the pile only falls
        self.assertGreater(plant.conveyor.load_t, 50)
        run(sim, 2)
        self.assertGreater(plant.stockpile.t + PARAMS["mill"]["design_tph"] / 60 * 2 + 1, level - 5000)

    def test_mill_is_limited_by_power_on_hard_ore(self):
        sim = new_sim()
        mill, pile = sim.plant.mill, sim.plant.stockpile
        design = PARAMS["mill"]["design_tph"]
        run(sim, 10)
        self.assertAlmostEqual(mill.feed_tph, design, delta=1)
        self.assertAlmostEqual(sim.values()["mill"]["specific_energy_kwh_t"],
                               PARAMS["mill"]["specific_energy_kwh_t"] * pile.bwi / 14.0, delta=0.05)
        sim.plant.hardness_factor = 17.5 / pile.bwi                   # a hard zone reaches the mill
        run(sim, 2)
        values = sim.values()
        self.assertEqual(values["mill"]["feed_tph"], design)          # the feeder still delivers the setpoint
        self.assertLess(values["mill"]["throughput_tph"], 0.95 * design)  # but the mill cannot grind that much
        self.assertAlmostEqual(values["mill"]["max_feed_tph"], values["mill"]["throughput_tph"], delta=2)
        self.assertAlmostEqual(values["mill"]["power_mw"], PARAMS["mill"]["installed_mw"], delta=0.3)
        self.assertGreater(values["mill"]["specific_energy_kwh_t"], 20)
        self.assertLess(values["flotation"]["recovery_pct"], 85)
        self.assertGreater(values["mill"]["overload_t"], 0)           # the surplus builds up in the mill
        self.assertGreater(values["mill-sag"]["charge_pct"], PARAMS["mill"]["charge_full_pct"])   # and fills the SAG drum
        # Both units run at their installed power, and together they are the circuit.
        for unit in ("sag", "ball"):
            self.assertAlmostEqual(values[f"mill-{unit}"]["power_mw"], PARAMS["mill"][f"{unit}_installed_mw"], delta=0.2)
        self.assertAlmostEqual(values["mill-sag"]["power_mw"] + values["mill-ball"]["power_mw"], values["mill"]["power_mw"],
                               delta=0.02)

    def test_overfeeding_overloads_the_mill_and_a_lower_setpoint_does_not(self):
        sim = new_sim()
        mill, pile = sim.plant.mill, sim.plant.stockpile
        sim.plant.hardness_factor = 17.5 / pile.bwi
        run_until(sim, lambda: mill.state == "overload", 240)
        values = sim.values()["mill"]
        self.assertEqual((values["state"], values["feed_tph"], values["stops_total"]), ("overload", 0, 1))
        self.assertGreater(values["throughput_tph"], 2000)            # it keeps grinding what it holds
        self.assertIn("перегружена", texts(sim))
        run_until(sim, lambda: mill.state == "idle", 30)              # ground out, then a regular restart
        started = sim.now
        run_until(sim, lambda: mill.state == "running", 60)
        self.assertAlmostEqual((sim.now - started) / 60, PARAMS["mill"]["restart_min"], delta=1)
        b = balance(sim)
        self.assertAlmostEqual(b["rock_sources"], b["rock_sinks"], delta=0.01)
        self.assertAlmostEqual(b["copper_sources"], b["copper_sinks"], delta=1e-4)

        calm = new_sim()
        calm.plant.hardness_factor = 17.5 / calm.plant.stockpile.bwi
        calm.handle_message("mill", "set-feed-rate", {"tph": 3300})   # below the power limit of about 3500 t/h
        for _ in range(10):
            run(calm, 25)
            calm.handle_message("mill", "set-feed-rate", {"tph": 3300})
        self.assertEqual((calm.plant.mill.stops, calm.plant.mill.state), (0, "running"))
        self.assertGreater(calm.plant.mill.total_t, sim.plant.mill.total_t)

    def test_mill_stops_when_the_stockpile_is_empty(self):
        sim = new_sim()
        plant = sim.plant
        plant.crusher.stopped_by = "test"
        plant.initial_stock_t -= plant.stockpile.t - 300.0
        plant.initial_stock_cu -= plant.stockpile.cu * (1 - 300.0 / plant.stockpile.t)
        plant.stockpile.take(plant.stockpile.t - 300.0)
        plant.crusher.stopped_since = sim.now
        run(sim, 30)
        values = sim.values()
        self.assertEqual((values["mill"]["state"], values["mill"]["feed_tph"], values["mill"]["power_mw"],
                          values["flotation"]["state"], values["flotation"]["cu_tph"], values["flotation"]["recovery_pct"]),
                         ("idle", 0, 0.0, "idle", 0.0, 0.0))
        self.assertEqual((values["stockpile"]["level_t"], values["mill"]["stops_total"]), (0, 1))
        self.assertGreaterEqual(plant.stockpile.t, 0.0)
        self.assertIn("остановлена: склад дроблёной руды пуст", texts(sim))
        self.assertIn("overflow", {t.source for t in sim.fleet.trucks})   # ore goes to the ROM pad meanwhile
        plant.crusher.stopped_by = ""
        run_until(sim, lambda: plant.mill.state == "running", 240)
        self.assertGreaterEqual(plant.stockpile.t, 0.0)

    def test_full_stockpile_blocks_the_crusher_and_ore_goes_to_the_rom_pad(self):
        sim = new_sim()
        plant = sim.plant
        missing = plant.capacity_t - plant.stockpile.t - plant.conveyor.load_t
        plant.stockpile.add(missing, missing * 0.0042, missing * 14.0, missing * 150.0)
        plant.initial_stock_t += missing
        plant.initial_stock_cu += missing * 0.0042
        pad = plant.rom_pads["rom-pad-01"].stock.t
        plant.last_divert_at = -1e9                                   # a new spell of diverting is journalled
        seen = set()
        for _ in range(3 * 3600):
            sim.step()
            seen.add(plant.crusher.state(plant.conveyor.carried_tph))
            self.assertLessEqual(plant.stockpile.t + plant.conveyor.load_t, plant.capacity_t + 1e-6)
        self.assertIn("blocked", seen)
        self.assertGreater(plant.rom_pads["rom-pad-01"].stock.t, pad + 500)
        self.assertIn("руда идёт на", texts(sim))

    def test_low_stockpile_is_fed_from_the_rom_pad(self):
        sim = new_sim()
        plant = sim.plant
        take = plant.stockpile.t - 0.2 * plant.capacity_t
        plant.initial_stock_t -= take
        plant.initial_stock_cu -= plant.stockpile.cu * take / plant.stockpile.t
        plant.stockpile.take(take)
        reclaimed = plant.reclaimed_t
        run(sim, 60)
        self.assertTrue(plant.reclaiming)
        self.assertGreater(plant.reclaimed_t, reclaimed + 300)
        b = balance(sim)
        self.assertAlmostEqual(b["rock_sources"], b["rock_sinks"], delta=0.01)

    def test_recovery_rises_with_grade_and_falls_with_hardness(self):
        flotation = new_sim().plant.flotation
        reference = flotation.recovery(PARAMS["geology"]["mean_head_grade_pct"], 14.0)
        self.assertAlmostEqual(reference, PARAMS["flotation"]["recovery_ref_pct"])
        self.assertGreater(flotation.recovery(0.55, 14.0), reference)
        self.assertLess(flotation.recovery(0.30, 14.0), reference)
        self.assertLess(flotation.recovery(0.42, 16.0), reference)
        for grade in (0.30, 0.41, 0.60):                             # Aktogay 2020-2025: 84.9-87.9 % at 0.40-0.56 %
            self.assertTrue(84 <= flotation.recovery(grade, 14.0) <= 89, grade)

    def test_substation_load_is_the_sum_of_its_consumers(self):
        sim = new_sim()
        run(sim, 30)
        values = sim.values()
        plant = sim.plant
        loaders = sum(values[n]["power_kw"] for n in LOADERS)
        self.assertAlmostEqual(values["substation"]["pit_mw"] * 1000, loaders, delta=5 * len(LOADERS))
        self.assertAlmostEqual(values["substation"]["load_mw"],
                               values["substation"]["plant_mw"] + values["substation"]["pit_mw"], delta=0.02)
        self.assertGreater(values["substation"]["plant_mw"], values["mill"]["power_mw"] + 5)
        self.assertTrue(22 <= values["mine"]["energy_kwh_per_t"] <= 28, values["mine"]["energy_kwh_per_t"])
        self.assertAlmostEqual(plant.load_kw, plant.plant_kw + plant.pit_kw)

    def test_wet_roads_slow_the_trucks(self):
        sim = new_sim()
        dry_cycle, dry_feed = sim.fleet.forecast(queueing=False)
        sim.weather.set_heavy(True)
        values = sim.values()
        self.assertEqual((values["weather"]["condition"], values["road-segment-09"]["surface"],
                          values["road-segment-09"]["status"]), ("rain", "wet", "restricted"))
        self.assertLess(values["road-segment-09"]["speed_limit_kmh"], PARAMS["road"]["speed_limit_surface_kmh"])
        self.assertEqual(values["road-segment-04"]["rolling_resistance_pct"],
                         PARAMS["road"]["rr_ramp_pct"] + PARAMS["road"]["wet_rr_added_pct"])
        wet_cycle, wet_feed = sim.fleet.forecast(queueing=False)
        self.assertGreater(wet_cycle, dry_cycle * 1.05)
        self.assertLess(wet_feed, dry_feed)
        sim.weather.set_heavy(False)                                 # the roads stay wet while they dry
        self.assertEqual((sim.values()["weather"]["condition"], sim.values()["weather"]["road_surface"]), ("dry", "wet"))
        self.assertEqual(sim.fleet.forecast(queueing=False)[0], wet_cycle)
        run(sim, PARAMS["road"]["drying_min"] + 1)
        self.assertEqual(sim.values()["road-segment-09"]["status"], "open")
        self.assertEqual(sim.fleet.forecast(queueing=False)[0], dry_cycle)


class Consistency(unittest.TestCase):
    """A twin's numbers must agree with its state, and linked twins with each other."""

    MOVING = {"hauling", "returning", "to_fuel", "to_service", "approach"}

    def test_states_and_numbers_agree_over_two_days(self):
        sim = new_sim()
        seen_states, loader_states = set(), set()
        p_loader, p_mill = PARAMS["loader"], PARAMS["mill"]
        shift_fuel, shift_mark = [], (sim.kpis.diesel_l, sim.kpis.engine_s, sim.shift_index)
        blasts = []
        for minute in range(2 * 24 * 60):
            run(sim, 1)
            for event in sim.pop_events():
                if event["text"].startswith("Взрыв: "):
                    blasts.append(event["sim_time"])
            if sim.shift_index != shift_mark[2]:
                # Fleet diesel per hour of engine running, over the shift just ended.
                litres, engine_s = sim.kpis.diesel_l - shift_mark[0], sim.kpis.engine_s - shift_mark[1]
                shift_fuel.append(litres / (engine_s / 3600.0))
                shift_mark = (sim.kpis.diesel_l, sim.kpis.engine_s, sim.shift_index)
            if minute % 7:
                continue
            values = sim.values()
            for name, v in values.items():
                kind = sim.things[name]["attributes"]["kind"]
                where = f"{name} at {sim.sim_time()}: {v}"
                if kind == "truck":
                    seen_states.add(v["state"])
                    if v["state"] not in self.MOVING:
                        self.assertEqual(v["speed_kmh"], 0.0, where)
                    # Driving on a segment means moving, except at the very start of it.
                    driving = v["state"] in ("hauling", "returning", "to_fuel", "to_service") and v["segment"]
                    if driving and sim.fleet.by_name[name].dist > 5:
                        self.assertGreater(v["speed_kmh"], 0.0, where)
                    if v["state"] == "down":
                        self.assertEqual((v["fuel_rate_lph"], v["engine_load_pct"]), (0, 0), where)
                    self.assertEqual(v["payload_t"] > 0, v["material"] != "none", where)
                    self.assertEqual(v["payload_t"] > 0, v["grade_pct"] > 0, where)
                    if v["state"] == "loading":
                        # Under the bucket: standing, and the loader it is assigned to has it.
                        loader = values[v["assigned_loader"].split(":")[1]]
                        self.assertEqual((v["speed_kmh"], loader["truck"], loader["state"]),
                                         (0.0, f"{NS}:{name}", "loading"), where)
                    self.assertEqual(bool(v["service_reason"]), bool(v["service_kind"]), where)
                    self.assertNotRegex(v["service_reason"], r"[a-z]{3,}", where)            # Russian, not codes
                elif kind == "loader":
                    loader_states.add(v["state"])
                    if v["state"] in ("down", "idle", "relocating", "setup", "standby"):
                        self.assertEqual((v["dig_rate_tph"], v["truck"], v["passes"]), (0, "", 0), where)
                    if v["state"] == "loading":
                        # A truck under the bucket, a pass under way, the drive digging.
                        self.assertTrue(v["truck"] and v["passes"] > 0, where)
                        self.assertEqual(v["power_kw"], round(p_loader["power_rated_kw"] * p_loader["load_factor_digging"]), where)
                    if v["state"] == "relocating":
                        self.assertEqual(v["power_kw"], round(p_loader["power_rated_kw"] * p_loader["load_factor_travel"]), where)
                    if v["state"] in ("waiting", "setup", "standby", "idle"):
                        self.assertEqual(v["power_kw"], round(p_loader["power_rated_kw"] * p_loader["load_factor_idle"]), where)
                    if v["state"] == "down":
                        self.assertEqual(v["power_kw"], 0, where)
                    # A loader ordered out of a blast zone reports when it will be back, also while it
                    # still loads the trucks that were waiting.
                    self.assertEqual(v["ready_in_min"] > 0,
                                     v["state"] in ("relocating", "setup") or sim.fleet.loaders[name].stood_down, where)
                    self.assertNotRegex(v["service_reason"], r"[a-z]{3,}", where)
                elif kind == "drill-rig":
                    self.assertEqual(v["penetration_m_h"] > 0, v["state"] == "drilling", where)
                    self.assertEqual(bool(v["block"]), v["state"] != "standby" or v["holes_planned"] > 0, where)
                    # Metres and holes of the block being drilled agree: whole holes plus the hole under way.
                    depth = PARAMS["drill"]["hole_depth_m"]
                    self.assertTrue(v["holes_done"] * depth - 0.1 <= v["metres_drilled"] <= (v["holes_done"] + 1) * depth + 0.1,
                                    where)
                    if v["block"]:
                        self.assertEqual(values[v["block"].split(":")[1]]["status"], "drilling", where)
                elif kind == "block":
                    # The charge is designed when the block is charged; a pushback starts it afresh.
                    self.assertEqual(v["powder_factor_kg_m3"] > 0, v["status"] in ("charged", "blasted", "mining", "depleted"),
                                     where)
                    if v["status"] == "drilling":
                        self.assertIn(f"{NS}:{name}", {values[r]["block"] for r in RIGS}, where)
                elif kind == "crusher":
                    belt = values["conveyor"]
                    # The belt carries exactly what the crusher discharged over the last transit.
                    self.assertEqual(v["throughput_now_tph"], belt["load_tph"], where)
                    self.assertLessEqual(belt["load_tph"], PARAMS["crusher"]["rated_tph"] + 1, where)
                    self.assertLessEqual(v["capacity_tph"], PARAMS["crusher"]["rated_tph"], where)
                    self.assertEqual(v["state"] in ("running", "blocked"), v["throughput_now_tph"] > 0, where)
                    # The published rate is a mean over the rate window: no jumps between trucks, zero when stopped.
                    self.assertLessEqual(v["throughput_tph"], PARAMS["crusher"]["rated_tph"] * 1.005, where)   # a minute's bucket
                    if v["state"] == "stopped":
                        self.assertEqual(v["throughput_tph"], 0, where)
                    self.assertEqual(belt["state"] == "running", belt["load_t"] > 0, where)
                    self.assertEqual(belt["power_kw"] > 0, belt["load_t"] > 0, where)
                    self.assertLessEqual(v["hopper_t"], PARAMS["crusher"]["hopper_t"] + 1e-6, where)
                elif kind == "mill":
                    self.assertEqual(v["state"] == "running", v["feed_tph"] > 0, where)
                    self.assertEqual(v["power_mw"] > 0, v["throughput_tph"] > 0, where)
                    self.assertEqual(v["specific_energy_kwh_t"] > 0, v["throughput_tph"] > 0, where)
                    self.assertLessEqual(v["throughput_tph"], v["max_feed_tph"] + 2, where)
                    flotation = values["flotation"]
                    self.assertEqual(flotation["state"] == "running", v["throughput_tph"] > 0, where)
                    self.assertEqual(flotation["cu_tph"] > 0, flotation["state"] == "running", where)
                    self.assertEqual(flotation["recovery_pct"] > 0, flotation["state"] == "running", where)
                    # The two units are the circuit: same state and throughput, powers adding up.
                    sag, ball = values["mill-sag"], values["mill-ball"]
                    self.assertEqual((sag["state"], ball["state"]), (v["state"], v["state"]), where)
                    self.assertAlmostEqual(sag["power_mw"] + ball["power_mw"], v["power_mw"], delta=0.02, msg=where)
                    self.assertLessEqual(sag["power_mw"], sag["installed_mw"] + 0.01, where)
                    self.assertLessEqual(ball["power_mw"], ball["installed_mw"] + 0.01, where)
                    self.assertTrue(p_mill["charge_idle_pct"] <= sag["charge_pct"] <= p_mill["charge_trip_pct"], where)
                    self.assertTrue(0 <= v["overload_pct"] <= 100, where)
                    # Flotation splits the ground ore into concentrate and tailings.
                    if flotation["state"] == "running":
                        self.assertAlmostEqual(flotation["conc_tph"] + flotation["tails_tph"], v["throughput_tph"], delta=2,
                                               msg=where)
                        self.assertAlmostEqual(values["concentrate"]["inflow_tph"], flotation["conc_tph"], delta=0.2, msg=where)
                elif kind == "rom-pad":
                    capacity = sim.things[name]["attributes"]["spec"]["capacity_t"]
                    self.assertLessEqual(v["level_t"], capacity, where)
                    self.assertAlmostEqual(v["level_pct"], 100 * v["level_t"] / capacity, delta=0.06, msg=where)
                elif kind == "stockpile":
                    self.assertLessEqual(v["level_t"], PARAMS["stockpile"]["capacity_t"], where)
                elif kind == "road-segment":
                    self.assertEqual(v["status"] == "closed", bool(v["closed_by"]), where)
                elif kind == "workshop":
                    self.assertLessEqual(v["bays_busy"], v["bays_total"], where)
                    in_bays = sum(1 for t in sim.fleet.trucks if t.in_bay)
                    self.assertEqual(v["bays_busy"], in_bays, where)
                elif kind == "fuel-station":
                    self.assertEqual(v["queue_len"] + v["trucks_fuelling"],
                                     sum(1 for t in sim.fleet.trucks if t.state == "refuel"), where)
            waiting = sum(v["trucks_waiting"] for n, v in values.items() if n.startswith("road-node-"))
            on_road = sum(v["trucks_on"] for n, v in values.items() if n.startswith("road-segment-"))
            self.assertLessEqual(waiting + on_road, len(sim.fleet.trucks))
        for state in ("to_fuel", "refuel", "queue_load", "approach", "loading", "queue_dump", "down", "standby"):
            self.assertIn(state, seen_states)
        for state in ("waiting", "loading"):
            self.assertIn(state, loader_states)
        # Mass blasts in daylight only and at most one a day while the broken stock is ample.
        print(f"\ntwo days: blasts at {', '.join(b[5:16] for b in blasts)}; fleet diesel per engine hour by shift: "
              f"{', '.join(f'{f:.0f}' for f in shift_fuel)} l/h")
        self.assertTrue(blasts)
        for at in blasts:
            self.assertTrue("10:00" <= at[11:16] <= "17:00", at)
        days = [b[:10] for b in blasts]
        self.assertEqual(len(days), len(set(days)), blasts)
        # Diesel per engine hour within the band of the Turija measurements over every whole shift.
        full = full_power_fuel_lph(PARAMS)
        # A single shift may run up to 3 points above the band: the long 8 % climbs of a pit 255 m deep are
        # heavier going than the Turija pit's (the week's mean stays inside the band, test_soak).
        for rate in shift_fuel:
            self.assertTrue(PARAMS["truck"]["cycle_load_factor_min"] * full <= rate
                            <= (PARAMS["truck"]["cycle_load_factor_max"] + 0.03) * full, shift_fuel)

    def test_drill_rig_reports_positioning_between_holes(self):
        sim = new_sim()
        rig = sim.pit.rigs[0]
        states = {}
        for _ in range(90 * 60):
            sim.step()
            states.setdefault(rig.state, set()).add(rig.rate_m_h > 0)
        self.assertEqual(states["drilling"], {True})
        self.assertEqual(states["positioning"], {False})

    def test_rom_pad_has_a_capacity(self):
        sim = new_sim()
        plant = sim.plant
        pad = plant.rom_pads["rom-pad-01"]
        spec = next(r for r in WORLD["rom_pads"] if r["id"] == "rom-pad-01")
        self.assertAlmostEqual(pad.capacity_t, 300 * 170 * spec["stack_height_m"] * PARAMS["rock"]["loose_density_t_m3"], delta=1)
        room = 3 * PARAMS["truck"]["payload_t"]
        added = pad.capacity_t - room - pad.stock.t
        pad.stock.add(added, added * 0.0046, added * 14.0, added * 350.0)
        plant.initial_stock_t += added
        plant.initial_stock_cu += added * 0.0046
        plant.crusher.stop("test")                                    # every ore truck now wants the pad
        run(sim, 240)
        self.assertLessEqual(pad.stock.t, pad.capacity_t)
        self.assertGreater(pad.stock.t, pad.capacity_t - 2 * PARAMS["truck"]["payload_t"] * 1.1)
        self.assertEqual(sim.values()["rom-pad-01"]["queue_len"], 0)  # nobody waits at a pad that is full
        waiting = [t for t in plant.crusher.queue if t.material == "ore"]
        self.assertGreaterEqual(len(waiting), 3)                      # the ore trucks wait at the crusher instead
        plant.crusher.start()
        trips = sum(t.trips for t in waiting)
        run(sim, 30)
        self.assertGreater(sum(t.trips for t in waiting), trips)


class Reliability(unittest.TestCase):
    def test_minor_fault_shows_in_the_indicators_then_stops_the_truck(self):
        sim = new_sim()
        truck = sim.fleet.by_name["truck-06"]
        healthy = sim.values()[truck.name]
        truck.fault = Fault("minor", "engine", PARAMS["reliability"]["pf_interval_minor_h"], progress=0.5)
        run(sim, 60)
        developing = sim.values()[truck.name]
        self.assertLess(developing["health_pct"], healthy["health_pct"] - 4)
        self.assertEqual(developing["state"] == "down", False)
        run_until(sim, lambda: truck.state == "down", 300)
        values = sim.values()
        self.assertEqual(values[truck.name]["service_kind"], "breakdown-minor")
        self.assertEqual(values[truck.name]["service_reason"], "Неисправность: система охлаждения двигателя, ремонт на месте")
        self.assertEqual((values[truck.name]["speed_kmh"], values[truck.name]["fuel_rate_lph"]), (0.0, 0))
        self.assertEqual(values["workshop"]["bays_busy"], 0)          # fixed where it stands
        self.assertIn(f"{truck.title}: неисправность", texts(sim))
        run_until(sim, lambda: truck.state != "down", 1500)
        self.assertEqual((sim.values()[truck.name]["service_reason"], sim.values()[truck.name]["service_kind"]), ("", ""))

    def test_engine_fault_raises_engine_temperature(self):
        a, b = new_sim(), new_sim()
        b.fleet.by_name["truck-02"].fault = Fault("major", "engine", 24.0, progress=0.9)
        run(a, 20)
        run(b, 20)
        self.assertGreater(b.fleet.by_name["truck-02"].engine_temp_c, a.fleet.by_name["truck-02"].engine_temp_c + 5)

    def test_major_failure_is_repaired_in_a_workshop_bay(self):
        sim = new_sim()
        truck = sim.fleet.by_name["truck-03"]
        truck.fault = Fault("major", "drive", 24.0, progress=0.999)
        run_until(sim, lambda: truck.state == "down", 120)
        run_until(sim, lambda: truck.in_bay, 30)
        values = sim.values()
        self.assertEqual(values["workshop"]["bays_busy"], 1)
        self.assertEqual((values[truck.name]["pos_x"], values[truck.name]["pos_y"]), sim.reliability.pos[:2])
        run_until(sim, lambda: truck.state != "down", 4000)
        self.assertEqual(truck.service_h < 1, True)                   # a major repair includes the service
        self.assertEqual(sim.values()["workshop"]["jobs_done"], 1)
        run_until(sim, lambda: truck.trips > values[truck.name]["trips"], 240)

    def test_bays_are_a_finite_resource(self):
        sim = new_sim()
        bays = PARAMS["workshop"]["bays"]
        broken = [t.name for t in sim.fleet.trucks[:bays + 1]]
        for name in broken:
            sim.fleet.by_name[name].fault = Fault("major", "tyre", 24.0, progress=0.999)
        run_until(sim, lambda: all(sim.fleet.by_name[n].state == "down" for n in broken), 240)
        run(sim, 5)
        values = sim.values()
        self.assertEqual((values["workshop"]["bays_busy"], values["workshop"]["bays_total"]), (bays, bays))
        self.assertGreaterEqual(values["workshop"]["jobs_queued"], 1)        # one more than there are bays waits
        self.assertLessEqual(values["mine"]["trucks_active"], len(sim.fleet.trucks) - len(broken))

    def test_loader_failure_sends_its_trucks_to_the_other_loaders(self):
        sim = new_sim()
        loader = sim.fleet.loaders["loader-01"]
        loader.fault = Fault("major", "drive", 24.0, progress=0.9999)
        run_until(sim, lambda: loader.state == "down", 30)
        self.assertEqual(sim.values()["loader-01"]["power_kw"], 0)
        run(sim, 40)
        homeless = [t for t in sim.fleet.trucks if t.home_loader == "loader-01" and t.state not in ("down", "service")]
        self.assertTrue(homeless)
        for truck in homeless:
            self.assertNotEqual(truck.loader_target, "loader-01")
        self.assertIn("fallback", {t.source for t in homeless})
        run_until(sim, lambda: loader.state != "down", 3000)
        run(sim, 60)
        self.assertTrue(all(t.loader_target == "loader-01" for t in homeless if t.state not in ("down", "service")))


class Messages(unittest.TestCase):
    def test_bad_messages_change_nothing_but_the_journal(self):
        sim = new_sim()
        sim.pop_events()
        before = sim.values()
        bad = [
            (CLOSURE, "activate", {"target": f"{NS}:crusher"}),
            (CLOSURE, "cancel", {}),
            ("scenario-blast", "activate", {"target": f"{NS}:{sim.fleet.loaders['loader-01'].block}"}),
            ("scenario-shovel-failure", "activate", {"target": f"{NS}:truck-01"}),
            ("scenario-heavy-rain", "activate", {"params": {"rain_mm_h": -3}}),
            ("scenario-hard-ore", "activate", {"params": {"bwi": 9}}),
            ("scenario-crusher-stop", "cancel", {}),
            ("mine", "set-speed", {"speedup": 7}),
            ("truck-01", "explode", {}),
            ("truck-01", "assign", {"loader": f"{NS}:loader-09"}),
            ("truck-01", "assign", {"route": "road-node-01>road-node-09"}),
            ("truck-01", "assign", {}),
            ("block-20", "set-destination", {"destination": "moon"}),
            ("mill", "set-feed-rate", {"tph": 99999}),
            ("mill", "set-feed-rate", {"tph": "fast"}),
            ("truck-01", "schedule-service", {}),
            ("rom-pad-01", "set-reclaim", {"tph": 5000}),
            ("no-such-twin", "activate", {}),
        ]
        for message in bad:
            sim.handle_message(*message)
        self.assertEqual(sim.values(), before)
        self.assertEqual([e["severity"] for e in sim.pop_events()], ["warning"] * len(bad))

    def test_messages_for_module_services_are_passed_over_silently(self):
        """`apply` to the advisor and other commands to a module twin are the module's business: the simulator
        receives them on the same topic, changes nothing and writes nothing about them."""
        sim = new_sim()
        sim.pop_events()
        before = sim.values()
        for name, subject, body in (("module-advisor", "apply", {"option": 2}), ("module-grade", "recalculate", {}),
                                    ("module-dispatch", "set-enabled", {"enabled": "yes"})):
            sim.handle_message(name, subject, body)
            self.assertEqual(sim.refusals, [], subject)
        self.assertEqual(sim.values(), before)
        self.assertEqual(sim.pop_events(), [])
        sim.handle_message("module-plant", "set-enabled", {"enabled": False})       # the switch itself is journalled
        self.assertIn("команда выключить", texts(sim))

    def test_speed_and_pause(self):
        sim = new_sim()
        sim.handle_message("mine", "set-speed", {"speedup": 60})
        sim.handle_message("mine", "pause", {})
        values = sim.values()["mine"]
        self.assertEqual((values["speedup"], values["sim_state"]), (60, "paused"))
        sim.handle_message("mine", "resume", {})
        self.assertEqual(sim.values()["mine"]["sim_state"], "running")

    def test_assign_sends_a_truck_to_another_loader_once(self):
        sim = new_sim()
        truck = sim.fleet.by_name["truck-30"]                         # its home loader is loader-05, in waste
        run_until(sim, lambda: truck.state == "returning", 60)
        sim.handle_message(truck.name, "assign", {"loader": f"{NS}:loader-02"})
        values = sim.values()[truck.name]
        self.assertEqual((values["assigned_loader"], values["assignment_source"]), (f"{NS}:loader-02", "dispatch"))
        run_until(sim, lambda: truck.state == "loading", 60)
        self.assertIs(sim.fleet.loaders["loader-02"].truck, truck)
        run_until(sim, lambda: truck.state == "hauling", 20)
        self.assertEqual((truck.material, truck.dump_target), ("ore", "crusher"))
        trips = truck.trips
        run_until(sim, lambda: truck.trips > trips and truck.state == "returning", 90)
        values = sim.values()[truck.name]                             # the order is spent: back to the fixed plan
        self.assertEqual((values["assigned_loader"], values["assignment_source"]), (f"{NS}:loader-05", "static"))

    def test_assign_dump_and_route(self):
        sim = new_sim()
        truck = next(t for t in sim.fleet.trucks if t.home_loader == "loader-01")
        ramp_top = sim.roads.segments["road-segment-01"]
        run_until(sim, lambda: truck.state == "hauling" and truck.seg is ramp_top, 90)     # climbing to the rim
        route = "road-node-01>road-node-12>road-node-11"
        sim.handle_message(truck.name, "assign", {"dump": f"{NS}:rom-pad-01", "route": route})
        values = sim.values()[truck.name]
        self.assertEqual((values["assigned_dump"], values["assignment_source"]), (f"{NS}:rom-pad-01", "dispatch"))
        pad = sim.plant.rom_pads["rom-pad-01"].stock.t
        run_until(sim, lambda: truck.state == "dumping", 90)
        self.assertTrue(sim.values()[truck.name]["route"].endswith(route))      # the rest of the leg as ordered
        run_until(sim, lambda: truck.state == "returning", 10)
        self.assertGreater(sim.plant.rom_pads["rom-pad-01"].stock.t, pad + 100)

    def test_set_destination_steers_a_block(self):
        sim = new_sim()
        loader = sim.fleet.loaders["loader-02"]
        block = loader.block
        sim.handle_message(block, "set-destination", {"destination": "rom-pad-02"})
        values = sim.values()[block]
        self.assertEqual((values["destination"], values["destination_source"]), ("rom-pad-02", "grade-control"))
        pad = sim.plant.rom_pads["rom-pad-02"].stock.t
        run(sim, 60)
        self.assertGreater(sim.plant.rom_pads["rom-pad-02"].stock.t, pad + 500)
        other = next(n for n, b in sim.pit.blocks.items() if n != block and b.is_ore)
        self.assertEqual(sim.values()[other]["destination_source"], "cutoff")

    def test_set_feed_rate_changes_the_mill_setpoint(self):
        sim = new_sim()
        values = sim.values()["mill"]
        self.assertEqual((values["feed_setpoint_tph"], values["setpoint_source"]),
                         (PARAMS["mill"]["design_tph"], "constant"))
        sim.handle_message("mill", "set-feed-rate", {"tph": 1800})
        run(sim, 2)
        values = sim.values()["mill"]
        self.assertEqual((values["feed_setpoint_tph"], values["setpoint_source"], values["feed_tph"]),
                         (1800, "plant-control", 1800))

    def test_schedule_service_takes_a_truck_to_the_workshop(self):
        sim = new_sim()
        truck = sim.fleet.by_name["truck-04"]
        truck.fault = Fault("major", "engine", 24.0, progress=0.3)   # a defect the service will remove
        hours = truck.service_h
        sim.handle_message(truck.name, "schedule-service", {"reason": "рост температуры двигателя",
                                                            "indicator": "engine_temp_c", "value": 99.5})
        self.assertEqual((sim.values()[truck.name]["service_reason"], sim.values()[truck.name]["service_kind"]),
                         ("Обслуживание назначено: рост температуры двигателя", "planned"))
        run_until(sim, lambda: truck.in_bay, 120)
        values = sim.values()
        self.assertEqual((values[truck.name]["state"], values["workshop"]["bays_busy"]), ("service", 1))
        self.assertEqual(values[truck.name]["fuel_rate_lph"], 0)
        started = sim.now
        run_until(sim, lambda: truck.state != "service", 400)
        self.assertAlmostEqual((sim.now - started) / 3600, PARAMS["reliability"]["pm_duration_h"], delta=0.05)
        self.assertIsNone(truck.fault)
        self.assertLess(truck.service_h, hours)
        self.assertGreater(sim.values()[truck.name]["health_pct"], 99)
        self.assertEqual(sim.values()[truck.name]["service_reason"], "")

    def test_the_workshop_does_what_the_machine_needs(self):
        """A booked truck gets a short fix for a minor defect, an inspection
        when nothing is wrong, a full service when one is nearly due."""
        rel = PARAMS["reliability"]
        cases = [("truck-06", Fault("minor", "tyre", rel["pf_interval_minor_h"], progress=0.3), rel["fix_minor_h"], False),
                 ("truck-11", None, rel["inspect_h"], False),
                 ("truck-30", None, rel["pm_duration_h"], True)]         # 215 h since service: due
        for name, fault, hours, renewed in cases:
            sim = new_sim()
            truck = sim.fleet.by_name[name]
            truck.fault, before = fault, truck.service_h
            sim.handle_message(name, "schedule-service", {"reason": "проверка"})
            run_until(sim, lambda: truck.state == "to_service", 120)
            self.assertGreater(sim.values()[name]["speed_kmh"] + 1, 0)
            run_until(sim, lambda: truck.in_bay, 120)
            started = sim.now
            run_until(sim, lambda: not truck.in_bay, 400)
            self.assertAlmostEqual((sim.now - started) / 3600, hours, delta=0.05, msg=name)
            self.assertIsNone(truck.fault, name)
            self.assertEqual(truck.service_h < 1, renewed, name)
            if not renewed:
                self.assertGreater(truck.service_h, before)

    def test_cancel_service_withdraws_a_booking(self):
        sim = new_sim()
        truck = sim.fleet.by_name["truck-07"]
        sim.handle_message(truck.name, "schedule-service", {"reason": "плановое ТО"})
        run_until(sim, lambda: truck.state == "to_service", 120)
        sim.handle_message(truck.name, "cancel-service", {})
        values = sim.values()[truck.name]
        self.assertEqual((values["service_reason"], values["state"]), ("", "returning"))
        trips = truck.trips
        run_until(sim, lambda: truck.trips > trips, 60)

    def test_schedule_service_stops_a_loader_in_place(self):
        sim = new_sim()
        sim.handle_message("loader-03", "schedule-service", {"reason": "плановое ТО"})
        run_until(sim, lambda: sim.fleet.loaders["loader-03"].state == "down", 30)
        self.assertEqual((sim.values()["loader-03"]["service_reason"], sim.values()["loader-03"]["service_kind"]),
                         ("Обслуживание по заявке: плановое ТО", "planned"))
        run_until(sim, lambda: sim.fleet.loaders["loader-03"].state != "down", 300)
        self.assertLess(sim.fleet.loaders["loader-03"].service_h, 1)


class LiveMessages(unittest.TestCase):
    def test_parse(self):
        payload = ('{"topic":"kz.aktogay/scenario-road-closure/things/live/messages/activate",'
                   '"headers":{"ditto-message-subject":"activate"},'
                   '"path":"/inbox/messages/activate","value":{"target":"kz.aktogay:road-segment-02"}}')
        self.assertEqual(envelope.parse_live_message(payload),
                         ("scenario-road-closure", "activate", {"target": "kz.aktogay:road-segment-02"}))
        self.assertIsNone(envelope.parse_live_message("not json"))
        self.assertIsNone(envelope.parse_live_message('{"topic":"kz.aktogay/mine/things/twin/events/modified"}'))
        empty = '{"topic":"kz.aktogay/mine/things/live/messages/pause","headers":{},"path":"/inbox/messages/pause"}'
        self.assertEqual(envelope.parse_live_message(empty), ("mine", "pause", {}))


if __name__ == "__main__":
    unittest.main()
