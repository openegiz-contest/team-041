"""Checks of the what-if scenarios: each one is started, left to run for a
simulated period, its chain of consequences is read from the twins it is
linked to, then it is cancelled and the mine must return to normal work.
Every scenario is also run with the four modules on, to see them respond.

    make mine-test
"""
import math
import unittest

from mine.common import envelope
from mine.sim.kpis import balance
from mine.tests.coupled import Coupled
from mine.tests.test_model import MAIN_RAMP_TOP, NS, PARAMS, UNITS, new_sim, run, run_until, texts

WEST_RAMP = "road-segment-09"          # 10 %: the top section of the west ramp, which heavy rain closes

SCALARS = ("status", "target", "started_sim_time", "elapsed_min", "cancel_remaining_min", "baseline_feed_tph", "current_feed_tph",
           "baseline_cycle_min", "current_cycle_min", "baseline_margin_kzt_h", "current_margin_kzt_h", "kpi_label", "kpi_unit", "kpi_baseline", "kpi_current",
           *(f"kpi{n}_{f}" for n in range(1, 5)
             for f in ("label", "unit", "baseline", "baseline_known", "current", "significant", "forecast")),
           "kpi_window_min", "kpi_baseline_note", "effect_delay_text",
           "expected_end_sim_time", "expected_duration_h", "expected_remaining_h", "duration_basis", "response_text",
           "response_cost_kzt", "affected", "impact_text")


def chain(values: dict) -> list[tuple]:
    """The chain indicators of a scenario twin: (label, baseline, current, significant)."""
    return [(values[f"kpi{n}_label"], values[f"kpi{n}_baseline"], values[f"kpi{n}_current"], values[f"kpi{n}_significant"])
            for n in range(1, 5) if values[f"kpi{n}_label"]]


def twin(sim, scenario: str) -> dict:
    values = sim.values()[scenario]
    envelope.modify_features(NS, scenario, values, UNITS[scenario])      # raises if a feature is missing or not scalar
    return values


def names(values: dict) -> list[str]:
    return values["affected"].split(",") if values["affected"] else []


class ScenarioTwins(unittest.TestCase):
    def test_every_scenario_twin_carries_the_common_features(self):
        sim = new_sim()
        for name, scenario in sim.scenarios.items():
            values = twin(sim, name)
            self.assertEqual(tuple(values), SCALARS + (("valid_targets",) if name == "scenario-blast" else ()), name)
            # Inactive: the chain shows the last hour, nothing compared, and when an effect would show.
            self.assertEqual(len(chain(values)), 4, name)
            for label, base, current, significant in chain(values):
                self.assertEqual((base, significant), (current, False), (name, label))
            self.assertIn("×60", values["effect_delay_text"], name)
            self.assertIn("Прогноз на 12 ч", values["effect_delay_text"], name)
            self.assertEqual((values["status"], values["target"], values["affected"], values["elapsed_min"]),
                             ("inactive", "", "", 0.0), name)
            self.assertTrue(values["kpi_label"] and values["kpi_unit"] and values["impact_text"], name)
            self.assertEqual(values["kpi_baseline"], values["kpi_current"], name)
            self.assertIn(f"{NS}:{scenario.default_target}", sim.things[name]["attributes"]["links"]["targets"])

    def test_several_scenarios_at_once(self):
        sim = new_sim()
        for name in ("scenario-road-closure", "scenario-heavy-rain", "scenario-clay-face", "scenario-shovel-failure"):
            sim.handle_message(name, "activate", {"target": f"{NS}:road-segment-12"} if "road" in name else {})
        run(sim, 90)
        values = sim.values()
        self.assertEqual([values[n]["status"] for n in sim.scenarios],
                         ["active", "active", "inactive", "active", "inactive", "active", "inactive"])
        self.assertEqual(values["road-segment-12"]["closed_by"], "scenario-road-closure")
        self.assertEqual(values[WEST_RAMP]["closed_by"], "scenario-heavy-rain")
        self.assertFalse(any(t.waiting_route for t in sim.fleet.trucks))
        for name in sim.scenarios:
            if values[name]["status"] == "active":
                sim.handle_message(name, "cancel", {})
        run(sim, 60)
        values = sim.values()
        self.assertEqual({values[n]["status"] for n in sim.scenarios}, {"inactive"})
        self.assertEqual({v["status"] for n, v in values.items() if n.startswith("road-segment-")}, {"open"})
        self.assertEqual(values["loader-01"]["state"] == "down", False)
        self.assertLess(max(b.clay_pct - b.clay_plan_pct for b in sim.pit.blocks.values()), 1e-9)   # the clay face is gone
        b = balance(sim)
        self.assertAlmostEqual(b["rock_sources"], b["rock_sinks"], delta=0.01)


class RoadClosure(unittest.TestCase):
    NAME = "scenario-road-closure"

    def test_chain_starts_with_the_longer_haul(self):
        sim = new_sim()
        run(sim, 60)
        sim.handle_message(self.NAME, "activate", {"target": f"{NS}:{MAIN_RAMP_TOP}"})
        early = twin(sim, self.NAME)
        self.assertEqual(early["kpi_window_min"], 0.0)                # nothing compared in the first minutes
        self.assertIn("через 5 минут", early["impact_text"])
        run(sim, 90)
        values = twin(sim, self.NAME)
        links = chain(values)
        self.assertEqual([c[0] for c in links], ["Путь с грузом, среднее по рейсам", "Время рейса", "Руда, разгруженная в дробилку",
                                                 "Запас склада дроблёной руды"])
        self.assertEqual(values["kpi_window_min"], 60.0)
        self.assertTrue(links[0][3] and links[0][2] > links[0][1] * 1.2, links[0])     # the detour
        self.assertTrue(links[1][3] and links[1][2] > links[1][1], links[1])           # longer trips
        self.assertIn("склад дроблёной руды держит", values["effect_delay_text"])
        sim.handle_message(self.NAME, "cancel", {})


class ShovelFailure(unittest.TestCase):
    NAME = "scenario-shovel-failure"

    def test_chain_and_recovery(self):
        sim = new_sim()
        run(sim, 60)
        before = sim.values()
        crew = [t for t in sim.fleet.trucks if t.home_loader == "loader-01"]
        sim.handle_message(self.NAME, "activate", {"target": f"{NS}:loader-01"})
        values = sim.values()
        self.assertEqual((values[self.NAME]["status"], values[self.NAME]["target"]), ("active", f"{NS}:loader-01"))
        self.assertEqual((values["loader-01"]["state"], values["loader-01"]["service_kind"], values["loader-01"]["power_kw"]),
                         ("down", "breakdown-scenario", 0))
        longest = {name: 0 for name in sim.fleet.loaders if name != "loader-01"}
        for _ in range(180):
            run(sim, 1)
            for name in longest:
                longest[name] = max(longest[name], len(sim.fleet.loaders[name].queue))
        active = sim.values()
        # Trucks reassigned ...
        moved = [t for t in crew if t.state not in ("down", "service", "to_service")]
        self.assertTrue(moved)
        for truck in moved:
            self.assertNotEqual(active[truck.name]["assigned_loader"], f"{NS}:loader-01")
            self.assertEqual(active[truck.name]["assignment_source"], "fallback")
            self.assertIn(truck.name, names(active[self.NAME]))
        self.assertIn("loader-01", names(active[self.NAME]))
        # ... queues at the other units ...
        self.assertGreaterEqual(max(longest.values()), 3)
        # ... and less rock dug: one face of five is out.
        self.assertLess(active[self.NAME]["kpi_current"], 0.95 * active[self.NAME]["kpi_baseline"])
        self.assertAlmostEqual(active[self.NAME]["elapsed_min"], 180, delta=0.1)
        self.assertIn("Переведено самосвалов", active[self.NAME]["impact_text"])
        # The chain: rock dug falls first, significantly.
        links = chain(active[self.NAME])
        self.assertEqual([c[0] for c in links], ["Выемка горной массы", "Очередь на рейс", "Руда, разгруженная в дробилку",
                                                 "Содержание меди в питании дробилки"])
        self.assertLess(links[0][2], links[0][1])
        self.assertTrue(links[0][3])
        self.assertIn("Цепочка: Выемка горной массы", active[self.NAME]["impact_text"])

        sim.handle_message(self.NAME, "cancel", {})
        after = sim.values()
        self.assertEqual((after[self.NAME]["status"], after[self.NAME]["affected"]), ("inactive", ""))
        self.assertIn(after["loader-01"]["state"], ("waiting", "loading"))
        # The loader digs again at its usual rate: its 15-minute rate, sampled over the next 90 minutes (a single
        # sample can catch two of its trucks on their meal break).
        rates = []
        for _ in range(6):
            run(sim, 15)
            rates.append(sim.values()["loader-01"]["dig_rate_tph"])
        after = sim.values()
        for truck in crew:
            if truck.state not in ("down", "service", "to_service"):
                self.assertEqual(after[truck.name]["assigned_loader"], f"{NS}:loader-01")
        self.assertGreater(sum(rates) / len(rates), 800, rates)

    def test_modules_respond(self):
        """Dispatch spreads the trucks over the loaders that work, grade control answers the lower feed grade."""
        plain, sim = new_sim(), new_sim()
        rig = Coupled(sim)
        rig.run(60)
        run(plain, 60)
        for s in (plain, sim):
            s.handle_message(self.NAME, "activate", {})
        reclaim = 0.0
        for _ in range(240):
            rig.run(1)
            run(plain, 1)
            reclaim += sim.plant.rom_pads["rom-pad-01"].reclaim_tph / 60.0
        values = sim.values()
        moved = [n for n in names(values[self.NAME]) if n.startswith("truck-")]
        self.assertGreaterEqual(len(moved), 2)
        self.assertEqual({values[n]["assignment_source"] for n in moved}, {"dispatch"})
        self.assertIn("модуль диспетчеризации", values[self.NAME]["impact_text"])
        self.assertFalse(any(values[n]["assigned_loader"] == f"{NS}:loader-01" for n in values
                             if n.startswith("truck-") and values[n]["state"] in ("returning", "queue_load")))
        # The blend corrects: rich ore is fed from ROM pad 1, and the mill feed stays closer to plan.
        self.assertGreater(reclaim, 300)
        self.assertEqual(values["rom-pad-01"]["reclaim_source"], "grade-control")
        target = PARAMS["grade_control"]["target_pct"]
        self.assertLessEqual(abs(values["stockpile"]["grade_pct"] - target), PARAMS["grade_control"]["band_pct"])


class CrusherStop(unittest.TestCase):
    NAME = "scenario-crusher-stop"

    def test_chain_and_recovery(self):
        sim = new_sim()
        plant = sim.plant
        # Start from a quarter-full stockpile so that the mill runs it down within the test.
        take = plant.stockpile.t - 0.25 * plant.capacity_t
        plant.initial_stock_t -= take
        plant.initial_stock_cu -= plant.stockpile.cu * take / plant.stockpile.t
        plant.stockpile.take(take)
        pad = plant.rom_pads["rom-pad-01"]                            # the ore pad by the crusher
        pads = pad.stock.t
        sim.handle_message(self.NAME, "activate", {})
        values = sim.values()
        self.assertEqual((values["crusher"]["state"], values["crusher"]["throughput_tph"], values["crusher"]["power_kw"]),
                         ("stopped", 0, 0))
        # Trucks queue first ...
        run_until(sim, lambda: len(plant.crusher.queue) >= 2, 30)
        self.assertEqual(pad.stock.t, pads)
        self.assertGreaterEqual(sim.values()["crusher"]["queue_len"], 2)
        # ... then divert to the ROM pads.
        run(sim, 30)
        values = sim.values()
        diverted = [n for n, v in values.items() if n.startswith("truck-") and v["assignment_source"] == "overflow"]
        self.assertTrue(diverted)
        self.assertEqual(values["crusher"]["queue_len"], 0)
        self.assertGreater(pad.stock.t, pads + 500)
        self.assertEqual((values["conveyor"]["state"], values["conveyor"]["load_tph"]), ("idle", 0))
        self.assertLess(values[self.NAME]["kpi_current"], values[self.NAME]["kpi_baseline"])
        self.assertEqual(values[self.NAME]["current_feed_tph"], 0)
        self.assertIn("crusher", names(values[self.NAME]))
        self.assertIn(diverted[0], names(values[self.NAME]))
        links = chain(values[self.NAME])
        self.assertEqual([c[0] for c in links], ["Производительность дробилки", "Руда на рудные склады",
                                                 "Запас склада дроблёной руды", "Питание мельницы"])
        self.assertTrue(links[0][3] and links[0][2] < 0.5 * links[0][1])          # the crusher stands
        self.assertTrue(links[1][3] and links[1][2] > links[1][1])                # the ore goes to the pads
        self.assertTrue(links[2][2] < links[2][1])                                # the stockpile runs down
        self.assertIn("руды хватит на", values[self.NAME]["effect_delay_text"])
        # The mill runs the stockpile down and stops when it is empty.
        self.assertEqual(values["mill"]["state"], "running")
        run_until(sim, lambda: plant.mill.state == "idle", 240)
        values = sim.values()
        self.assertEqual((values["stockpile"]["level_t"], values["mill"]["feed_tph"], values["flotation"]["state"],
                          values["mill"]["stops_total"]), (0, 0, "idle", 1))
        self.assertEqual(values[self.NAME]["kpi_current"], 0.0)
        self.assertIn("Мельница остановлена", values[self.NAME]["impact_text"])
        self.assertIn("mill", names(values[self.NAME]))

        sim.handle_message(self.NAME, "cancel", {})
        self.assertEqual((sim.values()[self.NAME]["status"], plant.crusher.stopped_by), ("inactive", ""))
        run_until(sim, lambda: plant.mill.state == "running", 120)
        run(sim, 120)
        values = sim.values()
        self.assertGreater(values["crusher"]["tonnes_total"], 3000)
        self.assertGreater(values["stockpile"]["level_t"], 1000)
        self.assertEqual(values["mill"]["feed_tph"], PARAMS["mill"]["design_tph"])
        b = balance(sim)
        self.assertAlmostEqual(b["rock_sources"], b["rock_sinks"], delta=0.01)

    def test_plant_control_slows_the_mill_and_keeps_it_running(self):
        """Same stop with the modules: the feed is cut early, the stockpile lasts
        longer and the mill is still running when the fixed setpoint has emptied it."""
        runs = {}
        for with_modules in (False, True):
            sim = new_sim()
            rig = Coupled(sim, ("dispatch", "grade", "plant", "maint") if with_modules else ())
            rig.run(120)
            sim.handle_message(self.NAME, "activate", {})
            start = sim.now
            stopped_after_h = None
            setpoints = []
            for minute in range(13 * 60):
                rig.run(1)
                setpoints.append(sim.plant.mill.setpoint_tph)
                if stopped_after_h is None and sim.plant.mill.state == "idle":
                    stopped_after_h = (sim.now - start) / 3600.0
                if minute == 8 * 60:
                    values = sim.values()                             # eight hours into the stop
            runs[with_modules] = (stopped_after_h, setpoints, values)
            print(f"\nCrusher stop, modules {'on' if with_modules else 'off'}: mill ran "
                  f"{'the whole 13 h' if stopped_after_h is None else f'{stopped_after_h:.1f} h'}, "
                  f"lowest setpoint {min(setpoints):.0f} t/h, {values['stockpile']['level_t']} t left after 8 h")
        off_stop, _, off_values = runs[False]
        on_stop, setpoints, on_values = runs[True]
        self.assertGreater(on_values["stockpile"]["level_t"], off_values["stockpile"]["level_t"] + 1000)
        self.assertIsNotNone(off_stop)                                # the fixed setpoint empties the stockpile
        self.assertLess(off_stop, 12.5)
        self.assertTrue(on_stop is None or on_stop > off_stop + 1.0)
        self.assertEqual(on_values["mill"]["setpoint_source"], "plant-control")
        self.assertLess(min(setpoints), PARAMS["mill"]["design_tph"] - 300)
        self.assertGreaterEqual(min(setpoints), PARAMS["mill"]["design_tph"] * PARAMS["mill"]["min_feed_factor"])
        self.assertIn("модуль управления фабрикой", on_values[self.NAME]["impact_text"])
        # Dispatch does not leave ore trucks standing at the stopped crusher.
        self.assertEqual(on_values["crusher"]["queue_len"], 0)


class HeavyRain(unittest.TestCase):
    NAME = "scenario-heavy-rain"

    def test_chain_and_recovery(self):
        sim = new_sim()
        run(sim, 60)
        before = sim.values()
        dry_limits = {n: v["speed_limit_kmh"] for n, v in before.items() if n.startswith("road-segment-")}
        sim.handle_message(self.NAME, "activate", {"params": {"rain_mm_h": 6}})
        values = sim.values()
        self.assertEqual((values["weather"]["condition"], values["weather"]["rain_mm_h"], values["weather"]["road_surface"],
                          values["weather"]["precip_class"]), ("rain", 6.0, "wet", "heavy"))
        # Speed limits drop on all segments, the steep ramp closes.
        for name, limit in dry_limits.items():
            self.assertLess(values[name]["speed_limit_kmh"], limit, name)
            self.assertEqual(values[name]["surface"], "wet", name)
            self.assertGreater(values[name]["rolling_resistance_pct"], before[name]["rolling_resistance_pct"], name)
        closed = {n for n in dry_limits if values[n]["status"] == "closed"}
        self.assertEqual(closed, {WEST_RAMP})                          # the west ramp at 10 %; the main ramp, 8 %, stays open
        self.assertEqual(values[WEST_RAMP]["closed_by"], self.NAME)
        self.assertEqual({values[n]["status"] for n in dry_limits if n not in closed}, {"restricted"})
        self.assertEqual(set(names(values[self.NAME])), {"weather", *dry_limits})
        run(sim, 240)
        active = sim.values()
        # The chain: slower trucks, longer trips, more diesel per tonne, all beyond their usual swing.
        links = chain(active[self.NAME])
        self.assertEqual([c[0] for c in links], ["Среднее ограничение скорости на дорогах", "Время рейса без ожидания",
                                                 "Расход дизельного топлива на тонну", "Руда, разгруженная в дробилку"])
        self.assertTrue(links[0][3] and links[0][2] < links[0][1], links[0])
        self.assertTrue(links[1][3] and links[1][2] > links[1][1], links[1])
        self.assertGreater(links[2][2], links[2][1], links[2])                 # more diesel a tonne
        # Over the trips completed in the last hour; the trucks drive slower, and wait less at the crusher.
        self.assertEqual(active[self.NAME]["kpi_window_min"], 60.0)
        self.assertGreater(active[self.NAME]["kpi_current"], active[self.NAME]["kpi_baseline"] * 1.05)
        self.assertGreater(active["mine"]["diesel_l_per_t"], before["mine"]["diesel_l_per_t"] * 1.05)
        self.assertLess(active[self.NAME]["current_feed_tph"], active[self.NAME]["baseline_feed_tph"])
        self.assertFalse(any(t.waiting_route for t in sim.fleet.trucks))
        self.assertFalse(any(t.seg is sim.roads.segments[WEST_RAMP] for t in sim.fleet.trucks))

        sim.handle_message(self.NAME, "cancel", {})
        drying = sim.values()
        self.assertEqual((drying[self.NAME]["status"], drying["weather"]["condition"], drying["weather"]["road_surface"]),
                         ("cancelling", "dry", "wet"))
        self.assertEqual(drying[WEST_RAMP]["status"], "closed")
        run(sim, PARAMS["road"]["drying_min"] + 1)
        after = sim.values()
        self.assertEqual((after[self.NAME]["status"], after["weather"]["road_surface"]), ("inactive", "dry"))
        for name, limit in dry_limits.items():
            self.assertEqual((after[name]["status"], after[name]["speed_limit_kmh"], after[name]["surface"]),
                             ("open", limit, "dry"), name)
        run(sim, 240)
        self.assertLess(sim.values()["mine"]["avg_cycle_min"], active[self.NAME]["current_cycle_min"])

    def test_dispatch_routes_around_the_closed_ramp(self):
        sim = new_sim()
        rig = Coupled(sim)
        rig.run(60)
        sim.handle_message(self.NAME, "activate", {})
        rig.keep_log = True
        rig.run(60)
        routes = [body["route"] for _, name, subject, body in rig.log if subject == "assign" and "route" in body]
        self.assertGreater(len(routes), 20)
        for route in routes:
            nodes = route.split(">")
            self.assertFalse(any({a, b} == {"road-node-09", "road-node-10"} for a, b in zip(nodes, nodes[1:])), route)
        self.assertFalse(any("отклонено" in e["text"] for e in sim.pop_events()))


class Blast(unittest.TestCase):
    NAME = "scenario-blast"

    def start(self, sim):
        """Without a target the scenario fires the first valid block: one whose zone crosses a road and holds a
        working machine. It is charged when it is fired."""
        run(sim, 30)
        before = sim.values()
        valid = before[self.NAME]["valid_targets"].split(",")
        sim.handle_message(self.NAME, "activate", {})
        self.assertEqual(sim.values()[self.NAME]["target"], valid[0])
        block = sim.pit.blocks[valid[0].split(":")[1]]
        self.assertIn(before[block.name]["status"], ("drilled", "charged"))
        self.assertEqual(block.status, "charged")
        self.block = block.name
        return block, before

    def test_chain_and_end(self):
        sim = new_sim()
        block, before = self.start(sim)
        radius = PARAMS["blast"]["exclusion_radius_m"]
        values = sim.values()
        # Exclusion zone, nearby segments closed for the clearance time.
        self.assertEqual((values[self.NAME]["status"], values["pit"]["blast_state"], values["pit"]["blast_block"],
                          values["pit"]["blast_radius_m"], values["pit"]["blast_source"]),
                         ("active", "clearing", f"{NS}:{block.name}", float(radius), self.NAME))
        closed = {n for n, v in values.items() if n.startswith("road-segment-") and v["status"] == "closed"}
        self.assertEqual(closed, {s.name for s in sim.roads.segments.values() if s.distance_to(*block.centre) < radius})
        self.assertTrue(closed)                                               # a road runs through the zone
        self.assertEqual({values[n]["closed_by"] for n in closed}, {"blast"})
        self.assertTrue(closed <= set(names(values[self.NAME])))
        self.assertIn(block.name, names(values[self.NAME]))
        out = [l.name for l in sim.fleet.loaders.values() if l.stood_down]
        rigs = [r.name for r in sim.pit.rigs if r.stood_down]
        self.assertTrue(out or rigs, "a working machine stands in the zone and is withdrawn")
        for name in out + rigs:
            self.assertIn(name, names(values[self.NAME]))
        loader = out[0] if out else None
        # Equipment leaves.
        run_until(sim, lambda: block.status == "blasted", 60)
        fired = sim.now
        for truck in sim.fleet.trucks:
            self.assertGreaterEqual(math.dist(sim.fleet.position(truck)[:2], block.centre), radius, truck.name)
        values = sim.values()
        for rig in rigs:
            self.assertEqual(values[rig]["state"], "standby")
        if loader is None:
            loader = next(iter(sim.fleet.loaders))          # no loader in the zone: the checks below hold trivially
            self.skip_loader = True
        # Out of the zone on its own tracks, a loader stands by there with its drive idling.
        if not getattr(self, "skip_loader", False):
            self.assertEqual((values[loader]["state"], values[loader]["dig_rate_tph"]), ("standby", 0))
        if not getattr(self, "skip_loader", False):
            self.assertEqual(values[loader]["power_kw"], round(PARAMS["loader"]["power_rated_kw"] * PARAMS["loader"]["load_factor_idle"]))
            self.assertGreater(values[loader]["ready_in_min"], 0)
            self.assertGreaterEqual(math.dist(sim.fleet.loaders[loader].pos, block.centre),
                                    PARAMS["blast"]["equipment_withdrawal_m"])
            # Its trucks load elsewhere until the zone is lifted.
            self.assertEqual(values[loader]["queue_len"], 0)
            self.assertFalse(any(v["assigned_loader"] == f"{NS}:{loader}" and v["state"] in ("returning", "queue_load")
                                 for n, v in values.items() if n.startswith("truck-")))
        # The block becomes muck with its fragmentation.
        self.assertEqual((values[block.name]["status"], values["pit"]["blast_state"]), ("blasted", "reentry"))
        self.assertTrue(PARAMS["blast"]["p80_min_mm"] <= values[block.name]["frag_p80_mm"] <= PARAMS["blast"]["p80_max_mm"])
        self.assertGreater(values["pit"]["broken_stock_t"], before["pit"]["broken_stock_t"] + 80000)
        self.assertGreater(values[self.NAME]["kpi_current"], values[self.NAME]["kpi_baseline"] + 80000)
        self.assertIn("взорван", values[self.NAME]["impact_text"])
        # The scenario ends by itself when the zone is lifted.
        run_until(sim, lambda: sim.scenarios[self.NAME].status == "inactive", 30)
        self.assertAlmostEqual((sim.now - fired) / 60, PARAMS["blast"]["reentry_min"], delta=0.1)
        values = sim.values()
        self.assertEqual((values["pit"]["blast_state"], values["pit"]["blast_source"]), ("idle", ""))
        self.assertFalse(any(v["status"] == "closed" for n, v in values.items() if n.startswith("road-segment-")))
        self.assertIn("Последний запуск", values[self.NAME]["impact_text"])
        run(sim, 60)
        self.assertFalse(any(t.waiting_route or t.hold for t in sim.fleet.trucks))
        self.assertTrue(all(l.state in ("loading", "waiting") for l in sim.fleet.loaders.values() if not l.fault))

    def test_cancel_before_the_blast_calls_it_off(self):
        sim = new_sim()
        block, _ = self.start(sim)
        run(sim, 10)
        sim.handle_message(self.NAME, "cancel", {})
        values = sim.values()
        self.assertEqual((values[self.NAME]["status"], values["pit"]["blast_state"], values[block.name]["status"]),
                         ("inactive", "idle", "charged"))
        self.assertFalse(any(v["status"] == "closed" for n, v in values.items() if n.startswith("road-segment-")))
        self.assertIn("не состоялся", values[self.NAME]["impact_text"])
        self.assertIn("отменён до подрыва", texts(sim))
        run(sim, 60)
        self.assertFalse(any(t.waiting_route or t.hold for t in sim.fleet.trucks))
        self.assertTrue(all(not l.stood_down for l in sim.fleet.loaders.values()))

    def test_cancel_after_the_blast_lifts_the_zone_early(self):
        sim = new_sim()
        block, _ = self.start(sim)
        run_until(sim, lambda: block.status == "blasted", 60)
        run(sim, 3)
        sim.handle_message(self.NAME, "cancel", {})
        values = sim.values()
        self.assertEqual((values[self.NAME]["status"], values["pit"]["blast_state"], values[block.name]["status"]),
                         ("inactive", "idle", "blasted"))
        self.assertIn("Досрочный допуск", texts(sim))

    def test_only_drilled_or_charged_blocks_are_valid_targets(self):
        sim = new_sim()
        valid = [t.split(":")[1] for t in sim.values()[self.NAME]["valid_targets"].split(",")]
        self.assertTrue(valid)
        self.assertEqual({sim.pit.blocks[b].status for b in valid} - {"drilled", "charged"}, set())
        self.assertEqual(set(valid), {b.name for b in sim.pit.blocks.values() if b.status in ("drilled", "charged")})
        sim.pop_events()
        for refused in (sim.pit.rigs[0].block,                       # being drilled
                        next(b.name for b in sim.pit.blocks.values() if b.status == "blasted"),
                        sim.fleet.loaders["loader-01"].block):        # being dug
            sim.handle_message(self.NAME, "activate", {"target": f"{NS}:{refused}"})
            self.assertEqual(sim.values()[self.NAME]["status"], "inactive", refused)
        journal = texts(sim)
        for phrase in ("бурится", "уже взорван", "в отработке", "взорвать можно только обуренный или заряженный блок"):
            self.assertIn(phrase, journal)
        sim.handle_message(self.NAME, "activate", {})                 # no target: the first valid one
        self.assertEqual(sim.values()[self.NAME]["target"], f"{NS}:{valid[0]}")
        run_until(sim, lambda: sim.scenarios[self.NAME].status == "inactive", 60)
        self.assertNotIn(f"{NS}:{valid[0]}", sim.values()[self.NAME]["valid_targets"])   # blasted: no longer offered


class ClayFace(unittest.TestCase):
    """A face the block model took for sulphide ore turns out clay-rich: clay in the ОФ-1 feed, its recovery and
    throughput down; the grade-control module reroutes the face to ОФ-2 or the clay-ore stockpile."""
    NAME = "scenario-clay-face"

    def start(self, sim, target="loader-03"):
        block = sim.pit.blocks[sim.fleet.loaders[target].block]
        sim.handle_message(self.NAME, "activate", {"target": f"{NS}:{target}"})
        return block

    def test_chain_and_recovery(self):
        sim = new_sim()
        run(sim, 30)
        before = sim.values()
        block = self.start(sim)
        clay_was = block.clay_plan_pct
        self.assertEqual(block.clay_pct, PARAMS["scenario"]["clay_face_pct"])
        self.assertEqual(block.material, "ore")                      # by the block model it is still sulphide ore
        self.assertEqual(block.destination, "crusher")               # and without the module it goes on to ОФ-1
        values = sim.values()[self.NAME]
        self.assertEqual(values["status"], "active")
        self.assertAlmostEqual(values["expected_duration_h"], PARAMS["scenario"]["clay_face_expected_h"])
        self.assertIn("каолинизации", values["duration_basis"])
        run(sim, 4 * 60)
        values = sim.values()
        self.assertGreater(values["stockpile"]["clay_pct"], before["stockpile"]["clay_pct"] + 3)
        self.assertGreater(values["mill"]["feed_clay_pct"], PARAMS["flotation"]["clay_ref_pct"] + 3)
        self.assertLess(values["mill"]["max_feed_tph"], before["mill"]["max_feed_tph"] * 0.95)
        self.assertLess(values["flotation"]["recovery_pct"], before["flotation"]["recovery_pct"] - 1.5)
        self.assertGreater(values["flotation"]["clay_loss_pct"], 1.5)
        self.assertGreater(values["mine"]["ofs1_clay_pct"], before["mine"]["ofs1_clay_pct"])
        links = chain(values[self.NAME])
        self.assertEqual([c[0] for c in links], ["Глинистость питания ОФ-1", "Извлечение меди на ОФ-1",
                                                 "Предел мельницы ОФ-1 (среднее за окно)",
                                                 "Глинистая руда на ОФ-2 и склад глинистой руды"])
        self.assertTrue(all(c[3] for c in links[:3]), links)
        self.assertGreater(links[0][2], links[0][1])
        self.assertLess(links[1][2], links[1][1])
        self.assertLess(links[2][2], links[2][1])
        self.assertIn("Глинистая руда в забое", values[self.NAME]["impact_text"])
        self.assertTrue({"stockpile", "mill", "flotation"} <= set(names(values[self.NAME])))
        b = balance(sim)
        self.assertAlmostEqual(b["copper_sources"], b["copper_sinks"], delta=1e-4)

        sim.handle_message(self.NAME, "cancel", {})
        self.assertEqual(sim.values()[self.NAME]["status"], "inactive")
        self.assertEqual(block.clay_pct, clay_was)

    def test_grade_control_reroutes_the_face(self):
        sim = new_sim()
        rig = Coupled(sim)
        rig.run(60)
        tipped = sim.kpis.tipped_at.get("clay", 0.0) + sim.kpis.tipped_at.get("clay-stock", 0.0)
        block = self.start(sim)
        rig.run(30)
        self.assertIn(block.destination, ("clay-plant", "clay-stockpile"))
        self.assertEqual(block.destination_source, "grade-control")
        self.assertIn("глинистая руда", block.destination_reason)
        rig.run(4 * 60)
        values = sim.values()
        # The face's ore goes to ОФ-2 or its stockpile: the clay of the ОФ-1 feed stays within the module's limit.
        self.assertLessEqual(values["stockpile"]["clay_pct"], PARAMS["grade_control"]["clay_limit_pct"] + 0.5)
        to_clay = sim.kpis.tipped_at.get("clay", 0.0) + sim.kpis.tipped_at.get("clay-stock", 0.0) - tipped
        self.assertGreater(to_clay, 4.5 * 0.9 * PARAMS["clay_plant"]["design_tph"])  # more than the clay face alone brings
        self.assertGreater(rig.cores["grade"].twin()["clay_blocks"], 0)
        b = balance(sim)
        self.assertAlmostEqual(b["rock_sources"], b["rock_sinks"], delta=0.01)
        sim.handle_message(self.NAME, "cancel", {})
        rig.run(30)
        self.assertEqual(block.destination, "crusher")               # sulphide ore again: back to ОФ-1

    def test_the_clay_ore_rate_is_forecast_until_trips_begun_after_the_start_have_finished(self):
        # In the first minutes the clay ore tipped at ОФ-2 is the loads begun before the start, sometimes none
        # in a short window («1666 → 0 т/ч (−100 %)» while the face's ore goes there): «now» is a forecast instead.
        sim = new_sim()
        run(sim, 30)
        block = self.start(sim)
        sim.handle_message(block.name, "set-destination", {"destination": f"{NS}:clay-plant", "hold_min": 600})
        run(sim, 12)
        v = twin(sim, self.NAME)
        self.assertEqual(v["kpi4_label"], "Глинистая руда на ОФ-2 и склад глинистой руды")
        self.assertTrue(v["kpi4_forecast"])
        self.assertTrue(v["kpi4_baseline_known"])
        self.assertGreater(v["kpi4_current"], v["kpi4_baseline"] + 300)      # the face's ore now goes there
        self.assertIn("прогноз по назначению руды забоя", v["impact_text"])
        self.assertFalse(v["kpi1_forecast"] or v["kpi2_forecast"] or v["kpi3_forecast"])
        run(sim, 60)
        v = twin(sim, self.NAME)
        self.assertFalse(v["kpi4_forecast"])                                # enough trips since: measured
        self.assertGreater(v["kpi4_current"], v["kpi4_baseline"])

    def test_only_a_sulphide_face_can_turn_out_clay_rich(self):
        sim = new_sim()
        clay_loader = next(l for l in sim.fleet.loaders.values() if l.material == "clay")
        sim.handle_message(self.NAME, "activate", {"target": f"{NS}:{clay_loader.name}"})
        self.assertEqual(sim.values()[self.NAME]["status"], "inactive")
        sim.handle_message(self.NAME, "activate", {"target": f"{NS}:loader-03", "params": {"clay_pct": 60}})
        self.assertEqual(sim.values()[self.NAME]["status"], "inactive")
        self.assertIn("глинистость", texts(sim))


class ClayPlantStop(unittest.TestCase):
    """ОФ-2 stops: its feed stockpile fills, then the clay ore goes to the clay-ore stockpile; no ОФ-2 copper."""
    NAME = "scenario-clay-plant-stop"

    def test_chain_and_restart(self):
        sim = new_sim()
        run(sim, 30)
        clay = sim.plant.clay_plant
        # The feed stockpile nearly full: the clay ore soon has to go to the clay-ore stockpile.
        f = (clay.room_t() - 800.0) / clay.bin.t
        added = (clay.bin.t * f, clay.bin.cu * f, clay.bin.bwi_t * f, clay.bin.p80_t * f, clay.bin.clay_t * f)
        clay.bin.add(*added)
        sim.plant.initial_stock_t += added[0]
        sim.plant.initial_stock_cu += added[1]
        bin_before = clay.bin.t
        pile = sim.plant.clay_stock.stock.t
        sim.handle_message(self.NAME, "activate", {"params": {"cause": "slurry"}})
        values = sim.values()
        self.assertEqual(values["clay-plant"]["state"], "stopped")
        self.assertAlmostEqual(values[self.NAME]["expected_duration_h"], PARAMS["clay_plant"]["repair_slurry_h"])
        self.assertTrue(values["clay-plant"]["restart_expected_sim_time"])
        run(sim, 120)
        values = sim.values()
        self.assertEqual((values["clay-plant"]["feed_tph"], values["clay-plant"]["cu_tph"]), (0, 0.0))
        self.assertEqual(values["mine"]["ofs2_feed_tph"], 0)
        self.assertGreater(values["clay-plant"]["bin_t"], bin_before)
        self.assertGreater(sim.plant.clay_stock.stock.t, pile + 500)      # the full feed stockpile sends trucks next door
        links = chain(values[self.NAME])
        self.assertEqual([c[0] for c in links], ["Переработка ОФ-2", "Запас приёмного склада ОФ-2",
                                                 "Глинистая руда на склад глинистой руды", "Медь в концентрате ОФ-2"])
        self.assertEqual((links[0][2], links[3][2]), (0.0, 0.0))
        self.assertTrue(links[0][3] and links[3][3])
        self.assertGreater(links[2][2], links[2][1])
        self.assertIn("ОФ-2 стоит", values[self.NAME]["impact_text"])
        self.assertIn(f"clay-stockpile", names(values[self.NAME]))
        sim.handle_message(self.NAME, "cancel", {})
        values = sim.values()
        self.assertEqual((values[self.NAME]["status"], values["clay-plant"]["state"]), ("inactive", "starting"))
        run(sim, PARAMS["clay_plant"]["restart_min"] + 2)
        values = sim.values()
        self.assertEqual(values["clay-plant"]["state"], "running")
        self.assertGreater(values["clay-plant"]["cu_tph"], 0.5)
        b = balance(sim)
        self.assertAlmostEqual(b["copper_sources"], b["copper_sinks"], delta=1e-4)


class Windows(unittest.TestCase):
    """The «before» window of a scenario card shows the mine in ordinary work, and a road closure's trip
    indicators are forecast from the routes until enough trips begun after it have finished."""

    def test_a_clean_before_window_ends_at_the_start(self):
        sim = new_sim()
        sim.handle_message("scenario-road-closure", "activate", {})
        run(sim, 10)
        v = twin(sim, "scenario-road-closure")
        self.assertEqual(v["kpi_baseline_note"], "")
        self.assertTrue(all(v[f"kpi{n}_baseline_known"] for n in range(1, 5)))

    def test_back_to_back_scenarios_take_the_last_clean_window(self):
        sim = new_sim()
        crusher_from = sim.now
        sim.handle_message("scenario-crusher-stop", "activate", {})
        run(sim, 20)
        sim.handle_message("scenario-crusher-stop", "cancel", {})
        run(sim, 10)
        sim.handle_message("scenario-road-closure", "activate", {})
        run(sim, 10)
        v = twin(sim, "scenario-road-closure")
        # The window before the start held the crusher stop and its recovery: the last clean one ends where it began.
        self.assertEqual(v["kpi_baseline_note"], f"«до» — окно {sim.sim_time(crusher_from - 600)[11:16]}–"
                                                 f"{sim.sim_time(crusher_from)[11:16]}: позже шли другие сценарии "
                                                 f"или восстановление после них")
        self.assertTrue(v["kpi1_baseline_known"])
        self.assertIn("«до» — окно", v["impact_text"])

    def test_a_clean_window_long_before_the_start_is_no_baseline(self):
        # The last clean window ends more than `scenario.kpi_baseline_max_age_min` before the start: another state
        # of the mine, not a baseline (a blast after an hour of other scenarios showed the dig rate up by a third).
        self.assertEqual(PARAMS["scenario"]["kpi_baseline_max_age_min"], 60)
        sim = new_sim()
        sim.handle_message("scenario-crusher-stop", "activate", {})
        run(sim, 60)
        sim.handle_message("scenario-crusher-stop", "cancel", {})
        run(sim, 10)
        sim.handle_message("scenario-blast", "activate", {})
        run(sim, 10)
        v = twin(sim, "scenario-blast")
        self.assertTrue(v["kpi_baseline_note"].startswith("база недоступна: за 60 мин до запуска"), v["kpi_baseline_note"])
        self.assertFalse(any(v[f"kpi{n}_baseline_known"] for n in range(1, 4)))
        self.assertFalse(any(v[f"kpi{n}_significant"] for n in range(1, 4)))
        self.assertIn("Цепочка (база недоступна", v["impact_text"])
        self.assertNotIn("%)", v["impact_text"].split("Цепочка", 1)[1])

    def test_no_clean_window_means_no_baseline(self):
        sim = new_sim()
        sim.handle_message("scenario-crusher-stop", "activate", {})
        run(sim, 6.5 * 60)                       # longer than the history the model keeps
        sim.handle_message("scenario-crusher-stop", "cancel", {})
        run(sim, 10)
        sim.handle_message("scenario-shovel-failure", "activate", {})
        run(sim, 10)
        v = twin(sim, "scenario-shovel-failure")
        self.assertTrue(v["kpi_baseline_note"].startswith("база недоступна"))
        self.assertFalse(any(v[f"kpi{n}_baseline_known"] for n in range(1, 5)))
        self.assertIn("Цепочка (база недоступна", v["impact_text"])

    def test_a_long_scenario_keeps_its_baseline(self):
        """Once the window has its full length, the «before» side is kept: the history it was read from is
        dropped after `scenario.kpi_history_h` hours, the baseline is not."""
        sim = new_sim()
        sim.handle_message("scenario-crusher-stop", "activate", {})
        run(sim, 90)
        early = twin(sim, "scenario-crusher-stop")
        run(sim, 7 * 60)
        late = twin(sim, "scenario-crusher-stop")
        self.assertTrue(late["kpi1_baseline_known"])
        self.assertEqual([late[f"kpi{n}_baseline"] for n in range(1, 5)], [early[f"kpi{n}_baseline"] for n in range(1, 5)])
        self.assertIn("Цепочка: ", late["impact_text"])

    def test_road_closure_trips_are_forecast_from_the_routes_at_first(self):
        sim = new_sim()
        sim.handle_message("scenario-road-closure", "activate", {})
        run(sim, 6)
        v = twin(sim, "scenario-road-closure")
        self.assertTrue(v["kpi1_forecast"] and v["kpi2_forecast"] and v["kpi3_forecast"])   # haul, trip, ore to crusher
        self.assertFalse(v["kpi4_forecast"])                             # the stockpile level: measured
        self.assertGreater(v["kpi1_current"], v["kpi1_baseline"])         # the route has grown at once
        self.assertGreater(v["kpi2_current"], v["kpi2_baseline"])
        self.assertLessEqual(v["kpi3_current"], v["kpi3_baseline"])      # no jump of the ore tipped in a few minutes
        self.assertIn("прогноз по маршрутам", v["impact_text"])
        run(sim, 84)
        v = twin(sim, "scenario-road-closure")
        self.assertFalse(v["kpi1_forecast"] or v["kpi2_forecast"] or v["kpi3_forecast"])   # enough trips since
        self.assertGreater(v["kpi1_current"], v["kpi1_baseline"])


class Cancelling(unittest.TestCase):
    """A cancel that takes time is bounded, and the twin says how long is left."""

    def test_heavy_rain_ends_when_the_roads_have_dried(self):
        sim = new_sim()
        sim.handle_message("scenario-heavy-rain", "activate", {})
        run(sim, 20)
        sim.handle_message("scenario-heavy-rain", "cancel", {})
        v = twin(sim, "scenario-heavy-rain")
        self.assertEqual(v["status"], "cancelling")
        self.assertAlmostEqual(v["cancel_remaining_min"], PARAMS["road"]["drying_min"], delta=0.1)
        self.assertIn("мин модели", v["impact_text"])
        run(sim, PARAMS["road"]["drying_min"] + 1)
        v = twin(sim, "scenario-heavy-rain")
        self.assertEqual((v["status"], v["cancel_remaining_min"]), ("inactive", 0.0))

    def test_clay_plant_restarts_after_its_start_up_time(self):
        sim = new_sim()
        sim.handle_message("scenario-clay-plant-stop", "activate", {})
        run(sim, 20)
        sim.handle_message("scenario-clay-plant-stop", "cancel", {})
        v = twin(sim, "scenario-clay-plant-stop")
        self.assertEqual((v["status"], v["cancel_remaining_min"]), ("inactive", 0.0))
        self.assertEqual(sim.values()["clay-plant"]["state"], "starting")
        run(sim, PARAMS["clay_plant"]["restart_min"] + 1)
        self.assertEqual(sim.values()["clay-plant"]["state"], "running")


if __name__ == "__main__":
    unittest.main()
