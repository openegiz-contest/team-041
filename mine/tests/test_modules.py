"""Checks of the four decision modules. Their cores run in one process with
the simulator (mine/tests/coupled.py): every 20 simulated seconds a core sees
the twins that changed and its messages go to the simulator, as on the stand
at speed-up 20.

The effect of each module is measured over seven simulated days, the module
alone against the fixed plan, same seed; the numbers are printed.

    make mine-test
"""
import time
import unittest

from mine.common import envelope
from mine.sim.forecast import ForecastDesk
from mine.model.build_twins import load_params
from mine.modules.base import short
from mine.sim.reliability import Fault
from mine.tests.coupled import ALL, ALL_BUT_PLANT, WEEK_CONFIGS, Coupled, shift_ceiling_tph, weeks
from mine.tests.test_model import NS, THINGS, UNITS, new_sim, run, texts

PARAMS = load_params()


def effect(module: str) -> tuple[dict, dict]:
    runs = weeks(WEEK_CONFIGS)
    return runs[()], runs[(module,)]


def show(title: str, off: dict, on: dict, keys: tuple[str, ...]) -> None:
    print(f"\n{title}: 7 simulated days, seed {off['seed']}, module off / on")
    for key in keys:
        a, b = off[key], on[key]
        change = f"{100 * (b - a) / a:+.1f} %" if a else ""
        print(f"  {key:26s} {a:12.2f} {b:12.2f}   {change}")


class Effects(unittest.TestCase):
    def test_dispatch_keeps_stripping_and_both_plants_fed(self):
        """What dispatch works for here: stripping kept up and the trucks sent where they load soonest, without
        starving ОФ-1 or ОФ-2 and without sending clay ore to the clay-ore stockpile when ОФ-2 has no room. The
        fixed plan sizes the fleet face by face, so it already queues little; dispatch trades some waiting at the
        loaders for the copper of richer, nearer faces."""
        off, on = effect("dispatch")
        show("Dispatch", off, on, ("queue_min_per_trip", "queue_load_min_per_trip", "queue_dump_min_per_trip", "cycle_min",
                                   "trips", "tonnes_moved", "ore_mined_t", "waste_mined_t", "ore_milled_t", "clay_milled_t",
                                   "clay_share_pct", "rom_pads_t", "cu_t", "use_of_available_pct"))
        self.assertGreater(on["waste_mined_t"], off["waste_mined_t"])
        self.assertGreaterEqual(on["ore_milled_t"], off["ore_milled_t"])          # ОФ-1 is not starved for it
        self.assertGreaterEqual(on["clay_milled_t"], off["clay_milled_t"])        # nor ОФ-2
        self.assertGreater(on["cu_t"], off["cu_t"])
        self.assertGreater(on["tonnes_moved"], 0.97 * off["tonnes_moved"])
        self.assertLess(on["clay_stock_t"], off["clay_stock_t"] + 5000)           # no clay sent to the stockpile for nothing
        self.assertLess(on["rom_pads_t"] - off["rom_pads_t"], 0.05 * off["ore_mined_t"])   # not bought with rehandling

    def test_grade_control_holds_the_feed_grade_in_band(self):
        off, on = effect("grade")
        show("Grade control", off, on, ("grade_in_band_pct", "head_grade_pct", "ore_milled_t", "cu_t", "rom_pads_t",
                                        "rom_pads_cu_t", "recovery_pct"))
        self.assertGreater(on["grade_in_band_pct"], off["grade_in_band_pct"] + 15)
        self.assertGreater(on["grade_in_band_pct"], 95)
        # Holding the feed in the band costs copper this week: ore richer than the band is parked on the ore pad
        # by the crusher, to be fed later. The A/B forecast on the stand says how much over 12 hours.
        self.assertLess(off["cu_t"] - on["cu_t"], 0.06 * off["cu_t"])
        # Clay: the feed of ОФ-1 keeps within the module's clay limit.
        self.assertLessEqual(on["ofs1_clay_pct"], PARAMS["grade_control"]["clay_limit_pct"])

    def test_plant_control_alone_costs_nothing(self):
        """Alone, plant control finds the stockpile at 40-65 %: the fixed plan seldom fills it to the 65 % a peak
        needs, and the mill at its working rate is not overloaded. The module keeps the sustained ceiling, which is
        the working rate itself, so the week is about the same as without it, never worse."""
        off, on = effect("plant")
        show("Plant control alone", off, on, ("cu_t", "cu_tph", "ore_milled_t", "mill_feed_pct_of_design", "recovery_pct",
                                              "mill_kwh_t", "mill_stops", "lowest_stockpile_t"))
        self.assertGreaterEqual(on["cu_t"], 0.999 * off["cu_t"])
        self.assertLessEqual(on["mill_stops"], off["mill_stops"])
        self.assertGreaterEqual(on["ore_milled_t"], 0.999 * off["ore_milled_t"])

    def test_plant_control_peaks_when_the_stockpile_is_full(self):
        """With the other modules on, the stockpile fills: the module adds short peaks up to the 2024 trial rate,
        within the peak hours of each shift, and the copper of the ore they grind."""
        runs = weeks(WEEK_CONFIGS)
        off, on = runs[ALL_BUT_PLANT], runs[ALL]
        show("Plant control with the other three modules", off, on,
             ("cu_t", "cu_tph", "ore_milled_t", "mill_feed_pct_of_design", "recovery_pct", "mill_stops", "rom_pads_t"))
        self.assertGreater(on["cu_t"], 1.003 * off["cu_t"])
        self.assertGreater(on["ore_milled_t"], 1.01 * off["ore_milled_t"])
        self.assertLessEqual(on["mill_stops"], off["mill_stops"])
        self.assertGreater(max(on["shift_peak_min"]), 60.0)

    def test_every_shift_keeps_within_the_sustained_ceiling_and_its_margin(self):
        """The shift average with plant control is at most the sustained ceiling (ОФ-1's 2024 average) plus the
        stated margin; the minutes above it are at most the shift's peak hours (plus one control period, the
        round in which the budget runs out)."""
        p = PARAMS["plant_control"]
        bound = shift_ceiling_tph(PARAMS)
        self.assertAlmostEqual(100.0 * (bound / p["sustained_tph"] - 1.0), p["shift_margin_pct"], delta=0.05)
        self.assertEqual(p["sustained_tph"], PARAMS["mill"]["design_tph"])     # the working rate, without the module
        runs = weeks(WEEK_CONFIGS)
        for config in WEEK_CONFIGS:
            if "plant" not in config:
                continue
            r = runs[config]
            print(f"\n{r['modules']}: shift averages {min(r['shift_tph']):.0f}-{max(r['shift_tph']):.0f} t/h (bound "
                  f"{bound:.0f}), minutes above {p['sustained_tph']:.0f} t/h per shift up to {max(r['shift_peak_min']):.0f}")
            self.assertGreaterEqual(len(r["shift_tph"]), 12)
            self.assertLessEqual(max(r["shift_tph"]), bound + 2.0, config)
            self.assertLessEqual(max(r["shift_peak_min"]), 60.0 * p["peak_h_shift"] + p["control_min"] + 1.0, config)

    def test_maintenance_raises_availability_and_prevents_failures(self):
        off, on = effect("maint")
        show("Maintenance", off, on, ("truck_availability_pct", "breakdowns", "workshop_jobs", "trips", "tonnes_moved"))
        self.assertGreater(on["truck_availability_pct"], off["truck_availability_pct"] + 4)
        self.assertLess(on["breakdowns"], 0.3 * off["breakdowns"])
        # Some defects give no warning (`reliability.sudden_share_pct`): the module cannot prevent every failure.
        self.assertGreater(on["breakdowns"], 0)

    def test_all_four_together(self):
        runs = weeks(WEEK_CONFIGS)
        show("All four modules", runs[()], runs[ALL],
             ("queue_min_per_trip", "tonnes_moved", "ore_milled_t", "grade_in_band_pct", "cu_t", "truck_availability_pct",
              "breakdowns", "mill_stops", "rom_pads_t"))
        on, off = runs[ALL], runs[()]
        self.assertGreater(on["cu_t"], off["cu_t"])           # plant control's copper outweighs what grade control parks
        self.assertGreater(on["truck_availability_pct"], off["truck_availability_pct"] + 4)
        # Inside the band of the best mines (92-94 %, `plan.availability`), not above it; without modules near the
        # 85 % the fleet is sized for.
        self.assertTrue(92.0 <= on["truck_availability_pct"] <= 94.0, on["truck_availability_pct"])
        self.assertTrue(84.0 <= off["truck_availability_pct"] <= 90.0, off["truck_availability_pct"])
        self.assertGreater(on["grade_in_band_pct"], off["grade_in_band_pct"] + 15)
        self.assertGreater(on["tonnes_moved"], 0.97 * off["tonnes_moved"])
        self.assertTrue(73.0 <= on["site_recovery_pct"] <= 81.5 and 73.0 <= off["site_recovery_pct"] <= 81.5)

    def test_drilling_keeps_pace_with_the_digging(self):
        """The rigs drill the holes the rock dug needs: over a week no less than the pattern requires for it
        (blocks are drilled, charged and fired ahead of the shovels), and they stand only while the stock of
        prepared blocks is full."""
        for config in ((), ALL):
            run = weeks(WEEK_CONFIGS)[config]
            print(f"\ndrilling, modules {run['modules']}: {run['drilled_m']:.0f} m drilled for {run['drill_needed_m']:.0f} m "
                  f"needed, rigs on standby {run['rig_standby_pct']:.0f} % of the time, blasts {run['blasts']}")
            self.assertGreater(run["drilled_m"], 0.95 * run["drill_needed_m"], config)
            self.assertLess(run["rig_standby_pct"], 30.0, config)
            self.assertGreaterEqual(run["blasts"], 5, config)

    def test_reference_values_are_those_of_the_run_without_modules(self):
        """The module twins compare themselves with params.yaml group `reference`; it must match the model."""
        off = weeks(WEEK_CONFIGS)[()]
        ref = PARAMS["reference"]
        self.assertAlmostEqual(ref["queue_min_per_trip"], off["queue_min_per_trip"], delta=0.05)
        self.assertAlmostEqual(ref["grade_in_band_pct"], off["grade_in_band_pct"], delta=1.0)
        self.assertAlmostEqual(ref["cu_tph"], off["cu_tph"], delta=0.05)
        self.assertAlmostEqual(ref["truck_availability_pct"], off["truck_availability_pct"], delta=0.3)


class ModuleTwins(unittest.TestCase):
    def test_features_match_the_twin_definitions_on_and_off(self):
        sim = new_sim()
        rig = Coupled(sim)
        rig.run(90)
        for name, core in rig.cores.items():
            for status in (None, "offline"):
                values = core.twin(status)
                envelope.modify_features(NS, core.name, values, UNITS[core.name])     # raises on a mismatch
            values = core.twin()
            self.assertEqual((values["enabled"], values["status"]), (True, "online"), name)
            self.assertGreater(values["decisions_total"], 0, name)
            self.assertTrue(values["last_decision"] and values["kpi_label"] and values["kpi_unit"], name)
            self.assertIn("«с модулем / без модуля»", values["effect_text"], name)
            self.assertNotIn("7-суточн", values["effect_text"], name)            # no fixed reference run
            self.assertNotIn("без модулей", values["effect_text"], name)
            rig.set_enabled(name, False)
            values = core.twin()
            self.assertEqual((values["enabled"], values["status"]), (False, "disabled"), name)
            self.assertIn("выключен", values["effect_text"], name)
            envelope.modify_features(NS, core.name, values, UNITS[core.name])

    def test_same_seed_same_run_with_modules(self):
        a, b = Coupled(new_sim(11)), Coupled(new_sim(11))
        a.run(180)
        b.run(180)
        self.assertEqual(a.sim.values(), b.sim.values())
        self.assertEqual({n: c.twin() for n, c in a.cores.items()}, {n: c.twin() for n, c in b.cores.items()})


class EffectForecast(unittest.TestCase):
    """A module measures its effect by forecasts of its own: the same mine from the same moment, with it and without it."""

    def test_plant_control_against_the_same_mine_without_it(self):
        sim = new_sim()
        rig = Coupled(sim)
        core = rig.cores["plant"]
        core.effect_live = True                         # as the service on the stand
        desk = ForecastDesk(sim, THINGS)
        requests = []
        plain = rig.deliver

        def deliver(messages):
            rest = []
            for name, subject, body in messages:
                if (name, subject) == ("mine", "forecast"):
                    requests.append(body)
                    desk.request(sim, body)
                else:
                    rest.append((name, subject, body))
            plain(rest)
        rig.deliver = deliver
        started = time.monotonic()
        while not core.effects:
            self.assertLess(time.monotonic() - started, 180, "no effect forecast within 3 minutes")
            for _ in range(20):
                rig.step()
            desk.tick(sim)
            for name, values in desk.values().items():
                core.observe(name, values)
            if desk.running():
                time.sleep(0.2)                         # the stand runs a model hour in a minute or more
        self.assertEqual(len(requests), 1)
        body = requests[0]
        self.assertEqual((body["slot"], body["actions_a"], body["requested_by"]), (8, [], "module-plant"))
        self.assertEqual(body["actions_b"], [{"thing": f"{NS}:module-plant", "subject": "set-enabled", "body": {"enabled": False}}])
        result = desk.values()["forecast-8"]
        twin = core.twin()
        envelope.modify_features(NS, core.name, twin, UNITS[core.name])
        self.assertEqual(twin["effect_count"], 1)
        self.assertAlmostEqual(twin["effect_cu_t"], result["a_cu_t"] - result["b_cu_t"], delta=0.01)
        self.assertEqual(twin["effect_margin_kzt"], result["a_margin_kzt"] - result["b_margin_kzt"])
        self.assertEqual(twin["effect_forecast"], f"{NS}:forecast-8")
        self.assertIn("«с модулем» и «без модуля»", twin["effect_basis"])
        self.assertIn("Прогноз на 12 ч", twin["effect_text"])
        self.assertIn("переработано руды", twin["effect_text"])
        print(f"\nplant control, A/B forecast: {twin['effect_text']}")
        rig.set_enabled("plant", False)
        self.assertEqual((core.twin()["effect_count"], core.twin()["effect_cu_t"]), (0, 0.0))

    def test_cores_inside_forecasts_ask_for_nothing(self):
        sim = new_sim()
        rig = Coupled(sim)
        rig.keep_log = True
        rig.run(120)
        self.assertFalse(any(subject == "forecast" for _, _, subject, _ in rig.log))


class SwitchingOff(unittest.TestCase):
    """Switching a module off sends messages that return the mine to its baseline at once."""

    def test_dispatch(self):
        sim = new_sim()
        rig = Coupled(sim, ("dispatch",))
        rig.run(60)
        self.assertGreater(sum(1 for t in sim.fleet.trucks if t.source == "dispatch"), 6)
        rig.set_enabled("dispatch", False)
        self.assertFalse(any(t.source == "dispatch" or t.override_loader or t.override_dump or t.override_route
                             for t in sim.fleet.trucks))
        rig.run(60)
        self.assertEqual({t.source for t in sim.fleet.trucks} - {"static", "fallback", "overflow"}, set())
        self.assertEqual(rig.cores["dispatch"].out, [])

    def test_grade_control(self):
        sim = new_sim()
        rig = Coupled(sim, ("grade",))
        rig.run(60)
        self.assertTrue(any(b.destination_source == "grade-control" for b in sim.pit.blocks.values()))
        rig.set_enabled("grade", False)
        values = sim.values()
        self.assertEqual({v["destination_source"] for n, v in values.items() if n.startswith("block-")}, {"cutoff"})
        self.assertEqual({values[p]["reclaim_source"] for p in ("rom-pad-01", "rom-pad-02")}, {"rule"})
        for block in sim.pit.blocks.values():
            self.assertEqual(block.destination, {"waste": "waste-dump", "low-grade": "rom-pad-02", "ore": "crusher",
                                                 "clay": "clay-plant"}[block.material])

    def test_plant_control(self):
        sim = new_sim()
        rig = Coupled(sim, ("plant",))
        rig.run(30)
        self.assertEqual(sim.values()["mill"]["setpoint_source"], "plant-control")
        rig.set_enabled("plant", False)
        values = sim.values()["mill"]
        self.assertEqual((values["setpoint_source"], values["feed_setpoint_tph"]), ("constant", PARAMS["mill"]["design_tph"]))
        rig.set_enabled("plant", True)
        rig.run(10)
        self.assertEqual(sim.values()["mill"]["setpoint_source"], "plant-control")

    def test_maintenance(self):
        sim = new_sim()
        rig = Coupled(sim, ("maint",))
        truck = sim.fleet.by_name["truck-09"]
        truck.fault = Fault("major", "engine", PARAMS["reliability"]["pf_interval_major_h"], progress=0.1)
        rig.run_until(lambda: bool(truck.service_due), 240)
        self.assertIn(truck.state, ("hauling", "queue_dump", "dumping", "returning", "loading", "queue_load", "to_service"))
        rig.set_enabled("maint", False)
        self.assertEqual((truck.service_due, sim.values()[truck.name]["service_reason"]), ("", ""))
        self.assertNotEqual(truck.state, "to_service")
        self.assertIn("заявка на обслуживание снята", texts(sim))


class StoppedService(unittest.TestCase):
    def test_decisions_lapse_when_a_module_falls_silent(self):
        """A module whose service is gone sends nothing more; the simulator drops
        its standing decisions after the time-out and the mine runs by the rules."""
        sim = new_sim()
        rig = Coupled(sim, ("grade", "plant"))
        rig.run(60)
        self.assertEqual(sim.values()["mill"]["setpoint_source"], "plant-control")
        steered = [b for b in sim.pit.blocks.values() if b.destination_source == "grade-control"]
        self.assertTrue(steered)
        sim.pop_events()
        run(sim, PARAMS["sim"]["decision_timeout_min"] + 2)             # no more exchanges: the services are gone
        values = sim.values()
        self.assertEqual((values["mill"]["setpoint_source"], values["mill"]["feed_setpoint_tph"]),
                         ("constant", PARAMS["mill"]["design_tph"]))
        self.assertEqual({v["destination_source"] for n, v in values.items() if n.startswith("block-")}, {"cutoff"})
        self.assertEqual(values["rom-pad-01"]["reclaim_source"], "rule")
        self.assertIn("решение модуля не подтверждено", texts(sim))


class Decisions(unittest.TestCase):
    def test_grade_control_classifies_a_blasted_block_with_its_reason(self):
        sim = new_sim()
        rig = Coupled(sim, ("grade",))
        charged = [b for b in sim.pit.blocks.values() if b.status == "charged"]
        rig.run_until(lambda: any(b.status == "blasted" for b in charged), 240)
        rig.run(1)
        block = next(b for b in charged if b.status == "blasted")
        values = sim.values()[block.name]
        self.assertEqual(values["destination_source"], "grade-control")
        cls = {"ore": "сульфидная руда", "low-grade": "забалансовая руда", "waste": "вскрыша",
               "clay": "глинистая руда"}[block.material]
        self.assertTrue(values["destination_reason"].startswith(cls), values["destination_reason"])
        self.assertIn("% Cu", values["destination_reason"])
        self.assertGreater(rig.cores["grade"].twin()["blocks_classified"], 0)

    def test_maintenance_books_a_truck_before_it_fails(self):
        sim = new_sim()
        rig = Coupled(sim, ("maint",))
        truck = sim.fleet.by_name["truck-16"]
        truck.fault = Fault("minor", "engine", PARAMS["reliability"]["pf_interval_minor_h"], progress=0.3)
        rig.run_until(lambda: bool(truck.service_due), 180)
        core = rig.cores["maint"]
        self.assertIn(truck.title, core.last_decision)
        self.assertIn("двигател", truck.service_due)                  # the engine temperature named the subsystem
        self.assertLess(truck.fault.progress, 0.75)                    # found with time to spare
        states = set()
        for _ in range(240):
            rig.run(1)
            states.add(truck.state)
            if truck.state not in ("to_service", "service") and "service" in states:
                break
        self.assertTrue({"to_service", "service"} <= states)
        self.assertNotIn("down", states)                             # it never broke down
        self.assertIsNone(truck.fault)
        self.assertNotIn(f"{truck.title}: неисправность", texts(sim))

    def test_dispatch_keeps_trucks_working_while_the_plant_is_full(self):
        """A full stockpile lets the crusher pass only what the mill takes: dispatch keeps
        the trucks out of the crusher queue, on waste or tipping on the ROM pad."""
        loads = {}
        for modules in ((), ("dispatch",)):
            sim = new_sim()
            plant = sim.plant
            missing = plant.capacity_t - plant.stockpile.t - plant.conveyor.load_t
            plant.stockpile.add(missing, missing * 0.0042, missing * 14.0, missing * 150.0)
            plant.initial_stock_t += missing
            plant.initial_stock_cu += missing * 0.0042
            rig = Coupled(sim, modules)
            hauled0 = sim.kpis.hauled_t
            queued = 0
            for _ in range(180):
                rig.run(1)
                queued += len(plant.crusher.queue)
            loads[modules] = (sim.kpis.hauled_t - hauled0, queued)
        (moved_off, queue_off), (moved_on, queue_on) = loads[()], loads[("dispatch",)]
        print(f"\nFull stockpile, 3 h: rock moved {moved_off:.0f} / {moved_on:.0f} t, "
              f"truck-minutes in the crusher queue {queue_off} / {queue_on} (dispatch off / on)")
        self.assertGreater(moved_on, 0.97 * moved_off)                  # as much rock moved ...
        self.assertLess(queue_on, 0.8 * queue_off)                      # ... with less standing at the crusher

    def test_dispatch_routes_only_over_open_roads(self):
        sim = new_sim()
        rig = Coupled(sim, ("dispatch",))
        sim.handle_message("scenario-road-closure", "activate", {"target": f"{NS}:road-segment-03"})
        rig.keep_log = True
        rig.run(90)
        routes = [body["route"] for _, _, subject, body in rig.log if subject == "assign" and "route" in body]
        self.assertGreater(len(routes), 20)
        for route in routes:
            nodes = route.split(">")
            self.assertFalse(any({a, b} == {"road-node-03", "road-node-04"} for a, b in zip(nodes, nodes[1:])), route)
        loaders = {short(body["loader"]) for _, _, subject, body in rig.log if subject == "assign" and "loader" in body}
        self.assertEqual(loaders, set(sim.fleet.loaders))


if __name__ == "__main__":
    unittest.main()
