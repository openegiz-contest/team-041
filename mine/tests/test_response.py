"""Checks of the incidents' expected ends and of the shift supervisor's response resources
(mine/sim/durations.py, mine/sim/response.py): each scenario says when it is expected to end and why, a
forecast ends it then, and each resource changes what it should and costs what params.yaml says.

    make mine-test
"""
import unittest

from mine.common import envelope
from mine.sim.reliability import Fault
from mine.tests.test_model import NS, PARAMS, UNITS, new_sim, run, run_until, texts

R = PARAMS["response"]
FX = PARAMS["economics"]["usd_kzt"]
CONTRACTOR_KZT_H = R["contractor_people"] * R["contractor_usd_h"] * FX
CALLOUT_KZT = CONTRACTOR_KZT_H * R["contractor_min_h"]
WEST_RAMP = "road-segment-08"
MAIN_RAMP_TOP = "road-segment-01"


def crew_cost(hours: float) -> float:
    """A second crew working `hours`: own fitters on overtime, the contractor paid at least its call-out."""
    return R["own_crew_kzt_h"] * hours + CONTRACTOR_KZT_H * max(hours, R["contractor_min_h"])


def forecasting():
    """A model that behaves as a forecast branch does: incidents end at their expected end."""
    sim = new_sim()
    sim.auto_end = True
    return sim


class ExpectedEnd(unittest.TestCase):
    def test_each_incident_scenario_says_when_it_ends_and_why(self):
        expected = {"scenario-crusher-stop": PARAMS["crusher"]["repair_mechanical_h"],
                    "scenario-shovel-failure": PARAMS["reliability"]["loader_mttr_h"],
                    "scenario-road-closure": PARAMS["road"]["closure_expected_h"],
                    "scenario-heavy-rain": PARAMS["weather"]["heavy_event_h"],
                    "scenario-hard-ore": PARAMS["scenario"]["hard_ore_expected_h"]}
        for name, hours in expected.items():
            sim = new_sim()
            inactive = sim.values()[name]
            # Before the start the card already shows how long the incident would last.
            self.assertEqual((inactive["expected_duration_h"], inactive["expected_end_sim_time"]), (hours, ""), name)
            self.assertTrue(inactive["duration_basis"], name)
            sim.handle_message(name, "activate", {})
            run(sim, 1)
            v = sim.values()[name]
            envelope.modify_features(NS, name, v, UNITS[name])
            self.assertEqual(v["expected_end_sim_time"], sim.sim_time(sim.scenarios[name].started + hours * 3600), name)
            self.assertEqual(v["expected_duration_h"], hours, name)
            self.assertAlmostEqual(v["expected_remaining_h"], hours - 1 / 60, delta=0.06)
            self.assertRegex(v["duration_basis"], r"[а-я]{4}", name)
        # The blast runs by itself: no estimate.
        sim = new_sim()
        sim.handle_message("scenario-blast", "activate", {})
        self.assertEqual(sim.values()["scenario-blast"]["expected_end_sim_time"], "")

    def test_the_cause_and_a_given_duration(self):
        sim = new_sim()
        sim.handle_message("scenario-crusher-stop", "activate", {"params": {"cause": "jam"}})
        v = sim.values()
        self.assertEqual(v["scenario-crusher-stop"]["expected_duration_h"], PARAMS["crusher"]["repair_jam_h"])
        self.assertIn("заклинивание", v["scenario-crusher-stop"]["duration_basis"])
        self.assertEqual(v["crusher"]["restart_expected_sim_time"], v["scenario-crusher-stop"]["expected_end_sim_time"])
        sim.handle_message("scenario-crusher-stop", "cancel", {})
        self.assertEqual(sim.values()["crusher"]["restart_expected_sim_time"], "")
        sim.handle_message("scenario-crusher-stop", "activate", {"params": {"cause": "bearing"}})
        self.assertIn("причина", sim.refusals[0])
        sim.handle_message("scenario-crusher-stop", "activate", {"params": {"expected_h": 30}})
        self.assertIn("0,25–24", sim.refusals[0])
        sim.handle_message("scenario-crusher-stop", "activate", {"params": {"expected_h": 2.5}})
        self.assertEqual(sim.values()["scenario-crusher-stop"]["expected_duration_h"], 2.5)
        self.assertIn("задано при запуске", sim.values()["scenario-crusher-stop"]["duration_basis"])

    def test_a_forecast_ends_the_incident_at_its_expected_end(self):
        sim = forecasting()
        sim.handle_message("scenario-crusher-stop", "activate", {"params": {"cause": "jam"}})
        end = sim.scenarios["scenario-crusher-stop"].expected_end
        run_until(sim, lambda: not sim.plant.crusher.stopped_by, limit_min=4 * 60)
        self.assertAlmostEqual(sim.now, end, delta=1.0)
        self.assertEqual(sim.scenarios["scenario-crusher-stop"].status, "inactive")

    def test_on_the_stand_the_operator_ends_it_and_an_overrun_gets_a_new_estimate(self):
        sim = new_sim()                         # auto_end is off, as on the stand
        sim.handle_message("scenario-crusher-stop", "activate", {"params": {"cause": "jam"}})
        scenario = sim.scenarios["scenario-crusher-stop"]
        first = scenario.expected_end
        run(sim, PARAMS["crusher"]["repair_jam_h"] * 60 + 5)
        self.assertEqual(scenario.status, "active")
        self.assertTrue(sim.plant.crusher.stopped_by)
        share = PARAMS["scenario"]["overrun_extend_share"]
        self.assertAlmostEqual(scenario.expected_end - first, share * PARAMS["crusher"]["repair_jam_h"] * 3600 + 1, delta=2)
        self.assertIn("ремонт затянулся", sim.values()["scenario-crusher-stop"]["response_text"])
        self.assertIn("новая оценка", texts(sim))


class Resources(unittest.TestCase):
    def test_a_second_crew_on_the_crusher(self):
        sim = forecasting()
        sim.handle_message("crusher", "expedite-repair", {})
        self.assertIn("дробилка работает", sim.refusals[0])
        sim.handle_message("scenario-crusher-stop", "activate", {})       # mechanical fault, 8 h
        run(sim, 60)
        scenario = sim.scenarios["scenario-crusher-stop"]
        before = scenario.expected_end
        sim.handle_message("crusher", "expedite-repair", {})
        self.assertEqual(sim.refusals, [])
        left_h = (before - sim.now) / 3600
        self.assertAlmostEqual(scenario.expected_end - sim.now, left_h * R["second_crew_factor"] * 3600, delta=1)
        v = sim.values()
        self.assertEqual(v["crusher"]["repair_crews"], 2)
        self.assertEqual(v["crusher"]["restart_expected_sim_time"], sim.sim_time(scenario.expected_end))
        self.assertAlmostEqual(v["scenario-crusher-stop"]["response_cost_kzt"], CALLOUT_KZT, delta=100)  # the call-out at once
        sim.handle_message("crusher", "expedite-repair", {})
        self.assertIn("уже на ремонте", sim.refusals[0])
        start = sim.now
        run_until(sim, lambda: not sim.plant.crusher.stopped_by, limit_min=8 * 60)
        hours = (sim.now - start) / 3600
        self.assertAlmostEqual(hours, left_h * R["second_crew_factor"], delta=0.01)
        run(sim, 1)
        self.assertAlmostEqual(sim.response.spent_kzt, crew_cost(hours), delta=200)
        self.assertEqual(sim.values()["mine"]["response_cost_kzt"], round(sim.response.spent_kzt))

    def test_a_second_crew_and_the_workshop_crew_on_a_broken_loader(self):
        sim = forecasting()
        loader = sim.fleet.loaders["loader-02"]
        sim.handle_message("loader-02", "expedite-repair", {})
        self.assertIn("не в ремонте", sim.refusals[0])
        loader.fault = Fault("major", "drive", 24.0, progress=0.9999)        # a natural breakdown
        run_until(sim, lambda: loader.state == "down", limit_min=60)
        estimate = loader.repair_s
        self.assertEqual(sim.values()["loader-02"]["repair_end_sim_time"], sim.sim_time(sim.now + estimate))
        sim.handle_message("loader-02", "prioritise-repair", {})
        self.assertAlmostEqual(loader.repair_s, estimate * R["workshop_lend_factor"], delta=1)
        self.assertEqual(sim.values()["workshop"]["bays_lent"], 1)
        sim.handle_message("loader-02", "expedite-repair", {})
        self.assertAlmostEqual(loader.repair_s, estimate * R["workshop_lend_factor"] * R["second_crew_factor"], delta=2)
        self.assertEqual(sim.values()["loader-02"]["repair_crews"], 3)
        start, left = sim.now, loader.repair_s
        run_until(sim, lambda: loader.state != "down", limit_min=24 * 60)
        self.assertAlmostEqual(sim.now - start, left, delta=2)
        run(sim, 1)
        self.assertEqual(sim.values()["workshop"]["bays_lent"], 0)             # the bay's crew is back
        self.assertEqual(sim.values()["loader-02"]["repair_crews"], 0)
        # The workshop crew costs nothing extra; the second crew its hours and the call-out.
        self.assertAlmostEqual(sim.response.spent_kzt, crew_cost(left / 3600), delta=200)

    def test_a_lent_bay_takes_no_new_job(self):
        sim = forecasting()
        sim.handle_message("scenario-shovel-failure", "activate", {})
        sim.handle_message("loader-01", "prioritise-repair", {})
        rel = sim.reliability
        self.assertEqual(rel.lent, 1)
        trucks = [t for t in sim.fleet.trucks if t.state not in ("down", "service")][:len(rel.bays)]
        for truck in trucks:
            sim.handle_message(truck.name, "schedule-service", {"reason": "плановое ТО"})
        run_until(sim, lambda: sum(job is not None for job in rel.bays) >= len(rel.bays) - 1, limit_min=120)
        run(sim, 30)
        self.assertLessEqual(sum(job is not None for job in rel.bays), len(rel.bays) - 1)
        self.assertIsNone(rel.bays[-1])

    def test_an_emergency_road_crew(self):
        sim = forecasting()
        sim.handle_message(MAIN_RAMP_TOP, "repair-road", {})
        self.assertIn("не закрыт", sim.refusals[0])
        sim.handle_message("scenario-road-closure", "activate", {})        # the main ramp, 6 h
        scenario = sim.scenarios["scenario-road-closure"]
        full = scenario.expected_end
        sim.handle_message(MAIN_RAMP_TOP, "repair-road", {})
        self.assertEqual(sim.refusals, [])
        self.assertAlmostEqual(scenario.expected_end - sim.now, R["road_repair_h"] * 3600, delta=1)
        segment = sim.roads.segments[MAIN_RAMP_TOP]
        run(sim, R["road_repair_h"] * 60 + 2)
        self.assertEqual(scenario.status, "inactive")
        v = sim.values()[MAIN_RAMP_TOP]
        self.assertEqual((v["status"], v["speed_limit_kmh"], v["speed_cap_kmh"]), ("restricted", R["repaired_kmh"], R["repaired_kmh"]))
        run_until(sim, lambda: segment.cap_kmh == 0.0, limit_min=8 * 60)
        self.assertAlmostEqual(sim.now, full, delta=2)                          # the full repair as first expected
        self.assertEqual(sim.values()[MAIN_RAMP_TOP]["status"], "open")
        self.assertAlmostEqual(sim.response.spent_kzt, R["road_crew_kzt_h"] * R["road_repair_h"], delta=200)

    def test_a_grader_and_a_topping_on_the_ramp_the_rain_closed(self):
        sim = forecasting()
        sim.handle_message(WEST_RAMP, "treat-surface", {})
        self.assertIn("не закрыт дождём", sim.refusals[0])
        sim.handle_message("scenario-heavy-rain", "activate", {})
        segment = sim.roads.segments[WEST_RAMP]
        self.assertEqual(segment.status, "closed")
        sim.handle_message(WEST_RAMP, "treat-surface", {})
        self.assertEqual(sim.refusals, [])
        tonnes = segment.length * PARAMS["road"]["width_m"] * R["topping_m"] * PARAMS["rock"]["loose_density_t_m3"]
        material = tonnes * PARAMS["economics"]["stock_kzt_t"]
        self.assertAlmostEqual(sim.response.spent_kzt, material, delta=1)       # the topping is paid at once
        run(sim, R["grading_h"] * 60 + 1)
        v = sim.values()[WEST_RAMP]
        self.assertEqual((v["status"], v["speed_limit_kmh"]), ("restricted", R["treated_wet_kmh"]))
        run_until(sim, lambda: sim.scenarios["scenario-heavy-rain"].status == "inactive", limit_min=6 * 60)
        run(sim, 1)
        self.assertEqual(segment.cap_kmh, 0.0)                                 # dry: the limit is lifted
        rain_h = PARAMS["weather"]["heavy_event_h"] + PARAMS["road"]["drying_min"] / 60
        self.assertAlmostEqual(sim.response.spent_kzt, material + R["grader_kzt_h"] * rain_h, delta=R["grader_kzt_h"] * 0.05)

    def test_workshop_overtime_shortens_truck_repairs(self):
        sim = forecasting()
        sim.handle_message("workshop", "expedite-repair", {})
        self.assertIn("нет", sim.refusals[0])
        truck = next(t for t in sim.fleet.trucks if t.state == "hauling")
        truck.fault = Fault("major", "drive", 24.0, progress=0.9999)
        run_until(sim, lambda: truck.state == "down", limit_min=60)
        job = next(j for j in sim.reliability.repairs() if j.truck is truck)
        duration = job.duration_s
        sim.handle_message("workshop", "expedite-repair", {})
        self.assertEqual(sim.refusals, [])
        self.assertAlmostEqual(job.duration_s, duration * R["second_crew_factor"], delta=1)
        self.assertTrue(sim.values()["workshop"]["overtime_until"])
        run_until(sim, lambda: not sim.reliability.repairs(), limit_min=24 * 60)
        paid_h = sim.response.spent_kzt / R["own_crew_kzt_h"]
        run(sim, 60)                    # no repair left: the overtime crew is not paid meanwhile
        self.assertAlmostEqual(sim.response.spent_kzt / R["own_crew_kzt_h"], paid_h, delta=0.01)
        self.assertLessEqual(paid_h, R["workshop_overtime_h"])

    def test_a_postponed_service_raises_the_failure_rate_as_documented(self):
        """The call-back keeps a truck at work; past its service interval it ages faster by
        `reliability.overdue_hazard_slope` per interval overdue (the advisor's label states the rate per hour)."""
        sim = new_sim()
        rel, p = sim.reliability, PARAMS["reliability"]
        due, late = sim.fleet.trucks[0], sim.fleet.trucks[1]
        due.service_h, late.service_h = p["pm_interval_h"], 1.5 * p["pm_interval_h"]
        due.fault = late.fault = None
        due.hazard, late.hazard = {"major": 1e9}, {"major": 1e9}
        rel._age(due, 1.0)
        rel._age(late, 1.0)
        used_due, used_late = 1e9 - due.hazard["major"], 1e9 - late.hazard["major"]
        self.assertAlmostEqual(used_late / used_due, 1 + p["overdue_hazard_slope"] * (0.5 * p["pm_interval_h"] + 1) / p["pm_interval_h"]
                               / (1 + p["overdue_hazard_slope"] * 1 / p["pm_interval_h"]), delta=0.01)


if __name__ == "__main__":
    unittest.main()
