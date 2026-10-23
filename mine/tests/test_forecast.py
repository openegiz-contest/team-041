"""Checks of the money and CO2 figures and of the forecast engine: the
arithmetic against values computed by hand, the forked copy that runs ahead
without touching the live model, the same forecast for the same state, and
the forecast twins. They need no platform, only the image:

    make mine-test
"""
import copy
import os
import time
import unittest

from mine.common import envelope
from mine.common.economics import Economics
from mine.sim.coupling import ALL, Coupled
from mine.sim.forecast import ForecastDesk, apply_action, explain, run_branch, timeout_s
from mine.tests.test_model import NS, PARAMS, THINGS, UNITS, new_sim, run

ROAD_CLOSURE = [{"thing": f"{NS}:scenario-road-closure", "subject": "activate", "body": {}}]
ON = {name: True for name in ALL}


def wait(desk: ForecastDesk, sim, slots, limit_s: float = 120.0) -> dict:
    """Tick the desk (the live model stepping on) until the slots are ready or failed."""
    started = time.monotonic()
    while time.monotonic() - started < limit_s:
        desk.tick(sim)
        for _ in range(20):
            sim.step()
        values = desk.values()
        if all(values[f"forecast-{n}"]["status"] in ("ready", "error") for n in slots):
            return values
        time.sleep(0.05)
    raise AssertionError(f"forecasts {slots} not ready within {limit_s} s")


class Money(unittest.TestCase):
    def test_prices_against_hand_arithmetic(self):
        econ = Economics(PARAMS)
        # 1 t of copper in concentrate: 14 474 USD/t x 95.5 % payable (Bozshakol 2024) x 448.45 KZT/USD, TC and RC 0 in 2026.
        self.assertAlmostEqual(econ.revenue_kzt(1.0), 14474 * 0.955 * 448.45, places=3)
        self.assertAlmostEqual(econ.revenue_kzt(1.0), 6198776.36, delta=0.01)
        self.assertAlmostEqual(econ.diesel_kzt(1000.0), 352000.0)
        self.assertAlmostEqual(econ.power_kzt(1000.0), 40290.0)
        # 1000 l of diesel and 1 MWh: 2.58354 t + 0.603 t of CO2.
        self.assertAlmostEqual(econ.co2_t(1000.0, 1000.0), 3.18654, places=6)
        self.assertAlmostEqual(econ.margin_kzt(1.0, 1000.0, 1000.0), 14474 * 0.955 * 448.45 - 352000.0 - 40290.0, places=3)

    def test_smelter_charges_are_deducted(self):
        params = copy.deepcopy(PARAMS)
        params["economics"].update(tc_usd_t=20.0, rc_usc_lb=2.0)
        # (0.955 x 14474 − 20 / 0.28 − 0.955 x 2204.62 lb x 0.02 USD) x 448.45.
        self.assertAlmostEqual(Economics(params).revenue_kzt(1.0),
                               (0.955 * 14474 - 20 / 0.28 - 0.955 * 2204.62 * 0.02) * 448.45, places=2)

    def test_mine_twin_money_follows_its_own_figures(self):
        sim = new_sim()
        run(sim, 75)
        mine = sim.values()["mine"]
        envelope.modify_features(NS, "mine", mine, UNITS["mine"])
        revenue = mine["cu_tph"] * 14474 * 0.955 * 448.45
        diesel = mine["diesel_l_h"] * 352
        power = mine["energy_mwh_h"] * 1000 * 40.29
        self.assertAlmostEqual(mine["revenue_kzt_h"], revenue, delta=1)
        self.assertAlmostEqual(mine["diesel_cost_kzt_h"], diesel, delta=1)
        self.assertAlmostEqual(mine["power_cost_kzt_h"], power, delta=1)
        self.assertAlmostEqual(mine["margin_kzt_h"], revenue - diesel - power, delta=1)
        self.assertAlmostEqual(mine["co2_t_h"], (mine["diesel_l_h"] + mine["rehandle_t_h"] * 0.25) * 2.58354 / 1000
                               + mine["energy_mwh_h"] * 0.603, delta=0.006)
        self.assertAlmostEqual(mine["rehandle_cost_kzt_h"], mine["rehandle_t_h"] * (0.25 * 352 + 120), delta=1)
        # Bozshakol, ОФ-1 at 3557 t/h and ОФ-2 at 755 t/h: about 13-14 t of copper (110 kt a year), some 80 mln tenge an hour.
        self.assertTrue(11.0 < mine["cu_tph"] < 16.0, mine["cu_tph"])
        self.assertTrue(55e6 < mine["margin_kzt_h"] < 105e6, mine["margin_kzt_h"])
        self.assertTrue(40.0 < mine["co2_t_h"] < 95.0, mine["co2_t_h"])

    def test_scenario_margin_pair(self):
        sim = new_sim()
        run(sim, 30)
        values = sim.values()
        for name in sim.scenarios:
            v = values[name]
            self.assertEqual(v["baseline_margin_kzt_h"], v["current_margin_kzt_h"], name)
            self.assertEqual(v["baseline_margin_kzt_h"], values["mine"]["margin_kzt_h"], name)
        sim.handle_message("scenario-crusher-stop", "activate", {})
        start = sim.values()["scenario-crusher-stop"]["baseline_margin_kzt_h"]
        run(sim, 12 * 60)                           # long enough for the stockpile to run out
        v = sim.values()["scenario-crusher-stop"]
        envelope.modify_features(NS, "scenario-crusher-stop", v, UNITS["scenario-crusher-stop"])
        self.assertEqual(v["baseline_margin_kzt_h"], start)
        self.assertLess(v["current_margin_kzt_h"], 0.75 * start)
        self.assertIn("Цепочка:", v["impact_text"])


class Branch(unittest.TestCase):
    def test_a_branch_leaves_the_live_model_alone(self):
        sim = new_sim()
        before = sim.values()
        result = run_branch(copy.deepcopy(sim), [("scenario-road-closure", "activate", {})], ON, 2)
        self.assertEqual(sim.values(), before)
        self.assertEqual(sim.scenarios["scenario-road-closure"].status, "inactive")
        self.assertEqual(len(result["series_mill_tph"].split(",")), 2)
        self.assertEqual(len(result["series_stockpile_pct"].split(",")), 2)

    def test_same_state_same_forecast(self):
        sim = new_sim()
        run(sim, 20)
        actions = [("scenario-shovel-failure", "activate", {})]
        a = run_branch(copy.deepcopy(sim), actions, ON, 3)
        b = run_branch(copy.deepcopy(sim), actions, ON, 3)
        self.assertEqual(a, b)

    def test_margin_adds_up(self):
        sim = new_sim()
        r = run_branch(copy.deepcopy(sim), [], ON, 2)
        econ = Economics(PARAMS)
        self.assertAlmostEqual(r["revenue_kzt"], econ.revenue_kzt(r["cu_t"]), delta=0.005 * 6.2e6 + 1)
        self.assertAlmostEqual(r["diesel_kzt"], r["diesel_l"] * 352, delta=352)
        # The margin is exactly the mine twin's: revenue less diesel and power.
        self.assertEqual(r["margin_kzt"], r["revenue_kzt"] - r["diesel_kzt"] - r["power_kzt"])
        # The adjustment: stocks and stripping at the mining cost of 2 USD/t, less the rehandling.
        self.assertEqual(r["adjust_kzt"], r["stock_kzt"] + r["strip_kzt"] - r["rehandle_kzt"])
        self.assertEqual(r["total_kzt"], r["margin_kzt"] + r["adjust_kzt"])
        self.assertEqual(econ.stock_kzt_t, 897)
        self.assertAlmostEqual(r["strip_kzt"], r["waste_t"] * 897, delta=897)
        self.assertAlmostEqual(r["rehandle_kzt"], r["rehandle_t"] * (0.25 * 352 + 120), delta=50)
        self.assertAlmostEqual(r["co2_t"], (r["diesel_l"] + 0.25 * r["rehandle_t"]) * 2.58354e-3 + r["energy_mwh"] * 0.603,
                               delta=0.2)

    def test_moving_ore_between_heaps_changes_the_total_only_by_the_move(self):
        """Every ore inventory is valued on one basis (mine/common/economics.py): moving ore between heaps changes
        the value only by the rehandling a ROM pad still needs, and mixing heaps changes nothing."""
        sim = new_sim()
        econ = Economics(PARAMS)
        plant = sim.plant
        pad, pile, pocket = plant.rom_pads["rom-pad-01"].stock, plant.stockpile, plant.crusher.hopper
        self.assertGreater(pad.grade_pct, pile.grade_pct + 0.01)          # a rich pad and a poorer stockpile
        before = econ.stock_value_kzt(sim)
        # The wheel loader feeds 5000 t of the rich pad into the crusher's pocket: the rehandling the ore still
        # needed is no longer owed, and exactly that much is paid for the loader in the branch (rehandle_kzt).
        pocket.add(*pad.take(5000.0))
        self.assertAlmostEqual(econ.stock_value_kzt(sim) - before, 5000.0 * econ.rehandle_kzt_t, delta=1)
        # From the pocket onto the stockpile (crushing, the belt) and into the mill: the same tonne is worth the same.
        before = econ.stock_value_kzt(sim)
        pile.add(*pocket.take(5000.0))
        plant.mill.charge.add(*pile.take(3000.0))
        self.assertAlmostEqual(econ.stock_value_kzt(sim), before, delta=1)
        # Ore tipped on a pad instead of the crusher owes its rehandling at once.
        before = econ.stock_value_kzt(sim)
        pad.add(*pile.take(2000.0))
        self.assertAlmostEqual(econ.stock_value_kzt(sim) - before, -2000.0 * econ.rehandle_kzt_t, delta=1)

    def test_drawing_down_a_rich_stock_earns_only_the_mill_time(self):
        """Milling a tonne turns its value into revenue: at the reference recovery the copper above the mine's average
        grade is exactly what the stocks give up, so a rich tonne and an average tonne milled earn the same."""
        econ = Economics(PARAMS)
        ref = PARAMS["geology"]["mean_head_grade_pct"] / 100.0
        recovery = PARAMS["flotation"]["recovery_ref_pct"] / 100.0
        net = []
        for grade in (ref, 0.0044, 0.0055, 0.0030):
            cu = 1000.0 * grade
            value = econ.grade_value_kzt(1000.0, cu) + econ.stock_kzt_t * 1000.0        # what the stocks lose
            net.append(econ.revenue_kzt(cu * recovery) - value)
        for n in net[1:]:
            self.assertAlmostEqual(n, net[0], delta=1)
        # The mill time is worth the average ore's copper less the fleet work it took: about 19 thousand tenge a tonne.
        self.assertAlmostEqual(net[0] / 1000.0, econ.revenue_kzt(ref * recovery) - econ.stock_kzt_t, delta=0.01)
        self.assertTrue(17_500 < net[0] / 1000.0 < 20_500, net[0])

    def test_the_ore_in_the_pit_counts_by_what_is_loaded(self):
        """Loading ore moves its copper from the pit to a truck: the value changes only by the fleet work."""
        sim = new_sim()
        econ = Economics(PARAMS)
        before = econ.stock_parts_kzt(sim)
        truck = next(t for t in sim.fleet.trucks if t.payload_t < 1e-6)
        truck.payload_t, truck.payload_cu_t, truck.material = 136.0, 136.0 * 0.0052, "ore"
        sim.kpis.mined(136.0, 136.0 * 0.0052, "ore")
        after = econ.stock_parts_kzt(sim)
        self.assertAlmostEqual(after["grade"], before["grade"], delta=1)
        self.assertAlmostEqual(after["work"] - before["work"], 136.0 * econ.stock_kzt_t, delta=1)

    def test_copper_and_margin_apart_are_explained(self):
        a = {"cu_t": 100.0, "margin_kzt": 600e6, "revenue_kzt": 640e6, "diesel_kzt": 20e6, "power_kzt": 20e6,
             "total_kzt": 605e6, "stock_kzt": 3e6, "strip_kzt": 2e6, "rehandle_kzt": 0}
        b = dict(a, cu_t=101.0, revenue_kzt=646e6, diesel_kzt=33e6, margin_kzt=593e6, total_kzt=598e6)
        text = explain("B", a, b)
        self.assertIn("Меди в «B» больше на 1,0 т", text)
        self.assertIn("но маржа ниже", text)
        self.assertEqual(explain("B", a, dict(a)), "")

    def test_a_refused_action_is_reported(self):
        sim = new_sim()
        r = run_branch(copy.deepcopy(sim), [("loader-02", "move-to-block", {"block": f"{NS}:block-99"})], ON, 1)
        self.assertIn("перевод на другой блок не выполнен", r["refused"])
        self.assertEqual(run_branch(copy.deepcopy(sim), [], ON, 1)["refused"], "")

    def test_a_module_switched_in_a_branch_follows_the_switch(self):
        sim = new_sim()
        rig = Coupled(sim, ALL, enabled={"plant": True})
        apply_action(sim, rig, "module-plant", "set-enabled", {"enabled": False})
        self.assertFalse(rig.cores["plant"].enabled)
        rig.run(10)
        self.assertEqual(sim.plant.mill.setpoint_source, "constant")
        rig = Coupled(sim, ALL, enabled={"plant": False})
        self.assertFalse(rig.cores["plant"].enabled)
        apply_action(sim, rig, "module-plant", "set-enabled", {"enabled": True})
        rig.run(10)
        self.assertEqual(sim.plant.mill.setpoint_source, "plant-control")


class Desk(unittest.TestCase):
    def test_road_closure_round_trip_in_worker_processes(self):
        sim = new_sim()
        desk = ForecastDesk(sim, THINGS)
        desk.request(sim, {"slot": 1, "horizon_h": 12, "label_a": "Как есть", "label_b": "Закрыть съезд",
                           "actions_b": ROAD_CLOSURE, "request_id": "t-1"})
        started = time.monotonic()
        values = wait(desk, sim, [1])
        latency = time.monotonic() - started
        twin = values["forecast-1"]
        envelope.modify_features(NS, "forecast-1", twin, UNITS["forecast-1"])
        print(f"\nforecast of 12 h, two branches in worker processes: {latency:.1f} s; {twin['summary_text']}")
        self.assertEqual((twin["status"], twin["request_id"], twin["horizon_h"]), ("ready", "t-1", 12))
        # The longer haul moves less rock; the stripping and the stocks it did not make count against it.
        self.assertLess(twin["b_ore_t"] + twin["b_waste_t"], 0.9 * (twin["a_ore_t"] + twin["a_waste_t"]))
        self.assertLess(twin["b_adjust_kzt"], twin["a_adjust_kzt"])
        for branch in ("a", "b"):
            self.assertEqual(twin[f"{branch}_margin_kzt"],
                             twin[f"{branch}_revenue_kzt"] - twin[f"{branch}_diesel_kzt"] - twin[f"{branch}_power_kzt"])
            self.assertEqual(twin[f"{branch}_total_kzt"], twin[f"{branch}_margin_kzt"] + twin[f"{branch}_adjust_kzt"])
        self.assertEqual(twin["delta_margin_kzt"], twin["b_margin_kzt"] - twin["a_margin_kzt"])
        self.assertEqual(twin["delta_total_kzt"], twin["b_total_kzt"] - twin["a_total_kzt"])
        self.assertEqual(len(twin["a_series_mill_tph"].split(",")), 12)
        for phrase in ("маржа", "поправка", "Маржа — переменная: выручка за медь минус дизель и электроэнергия", "млн тг"):
            self.assertIn(phrase, twin["summary_text"])
        cu, margin = twin["delta_cu_t"], twin["delta_margin_kzt"]
        if (cu > 0.05 and margin < -50_000) or (cu < -0.05 and margin > 50_000):
            self.assertIn("но маржа", twin["summary_text"])       # copper and margin apart: the text says why
        # Closing the main ramp for 12 hours costs money in total, whatever the copper does: the fleet moves less
        # rock, and copper from richer stocks is copper the stocks give up.
        self.assertLess(twin["delta_total_kzt"], -5e6)
        self.assertTrue(twin["verdict_text"].startswith("Итог с поправкой: «Закрыть съезд» хуже на"), twin["verdict_text"])
        self.assertTrue(twin["summary_text"].startswith("Прогноз на 12 ч от"), twin["summary_text"][:60])
        self.assertIn(twin["verdict_text"], twin["summary_text"])
        # The copper difference is explained: tonnes milled and head grade, the sources of the mill's ore, the fleet work.
        # The tonnes milled are named when they differ: with the stockpile the stand holds, the plant-control module
        # at its sustained ceiling may feed the mill alike in both branches through a closure of 6 hours.
        for phrase in ("Медь ", "Руда на мельницу", "Добыто руды", "работа парка"):
            self.assertIn(phrase, twin["copper_text"])
        if abs(twin["b_milled_t"] - twin["a_milled_t"]) >= 50:
            self.assertIn("переработано руды", twin["copper_text"])
        self.assertIn(twin["copper_text"], twin["summary_text"])
        self.assertEqual(sim.scenarios["scenario-road-closure"].status, "inactive")    # only the branch closed the road
        self.assertLess(latency, 60.0)

    def test_the_page_does_not_wait_for_background_forecasts(self):
        sim = new_sim()
        desk = ForecastDesk(sim, THINGS)
        try:
            # The advisor and a module fill every worker the background may use...
            for slot in (2, 3, 4, 5, 6):
                desk.request(sim, {"slot": slot, "horizon_h": 1, "request_id": f"bg-{slot}", "replicas": 3 if slot < 6 else 1, "actions_b": [
                    {"thing": f"{NS}:mill", "subject": "set-feed-rate", "body": {"tph": 3000 + 100 * slot}}]})
            desk.tick(sim)
            # (no more workers than the usable processors less two for the live simulator and the services)
            cap = max(1, min(int(PARAMS["forecast"]["max_parallel"]), len(os.sched_getaffinity(0)) - 2))
            self.assertEqual(desk.running(), cap)
            # ...and the page's two branches still start at once, ahead of the module branch that waits.
            desk.request(sim, {"slot": 1, "horizon_h": 1, "request_id": "page", "actions_b": ROAD_CLOSURE})
            desk.tick(sim)
            page = [job for job in desk.jobs.values() if 1 in job.users]
            self.assertEqual(len(page), 2)
            self.assertTrue(all(job.pid > 0 for job in page))
            self.assertEqual(desk.running(), cap + 2)
            # The advisor's branches go before the module's: its own branch is the one still waiting.
            module = [job for job in desk.jobs.values() if job.users == {6}]
            self.assertEqual(len(module), 1)
            self.assertEqual(module[0].pid, 0)
        finally:
            desk.close()

    def test_shared_branches_and_the_reference_option(self):
        sim = new_sim()
        desk = ForecastDesk(sim, THINGS)
        for slot in (2, 3):
            desk.request(sim, {"slot": slot, "horizon_h": 2, "actions_b": [], "request_id": f"same-{slot}"})
        desk.tick(sim)
        self.assertEqual(len(desk.jobs), 1)                  # A and B of both slots: one branch
        values = wait(desk, sim, [2, 3])
        for slot in (2, 3):
            twin = values[f"forecast-{slot}"]
            self.assertEqual(twin["delta_margin_kzt"], 0)
            self.assertEqual(twin["a_series_mill_tph"], twin["b_series_mill_tph"])
        self.assertEqual(values["forecast-2"]["a_margin_kzt"], values["forecast-3"]["a_margin_kzt"])

    def test_worker_and_inline_forecasts_agree(self):
        sim = new_sim()
        results = []
        for fork in (True, False):
            live = copy.deepcopy(sim)
            desk = ForecastDesk(live, THINGS, fork=fork)
            desk.request(live, {"slot": 4, "horizon_h": 2, "actions_b": ROAD_CLOSURE})
            results.append(wait(desk, live, [4])["forecast-4"])
        keys = [k for k in results[0] if k[:2] in ("a_", "b_") or k.startswith("delta")]
        self.assertEqual({k: results[0][k] for k in keys}, {k: results[1][k] for k in keys})

    def test_a_newer_request_replaces_the_older(self):
        sim = new_sim()
        desk = ForecastDesk(sim, THINGS)
        desk.request(sim, {"slot": 1, "horizon_h": 12, "actions_b": ROAD_CLOSURE, "request_id": "old"})
        desk.tick(sim)
        old_pids = [job.pid for job in desk.jobs.values()]
        sim.step()
        desk.request(sim, {"slot": 1, "horizon_h": 2, "actions_b": [], "request_id": "new"})
        values = wait(desk, sim, [1])
        self.assertEqual((values["forecast-1"]["request_id"], values["forecast-1"]["horizon_h"]), ("new", 2))
        self.assertTrue(all(pid > 0 for pid in old_pids))
        self.assertEqual(desk.jobs, {})

    def test_bad_requests_are_refused_and_journalled(self):
        sim = new_sim()
        desk = ForecastDesk(sim, THINGS)
        sim.pop_events()
        desk.request(sim, {"slot": len(desk.names) + 1})          # beyond the last slot
        desk.request(sim, {"slot": 2, "actions_b": [{"thing": "kz.bozshakol:nothing-here", "subject": "x", "body": {}}]})
        desk.request(sim, {"slot": 3, "horizon_h": 100})
        desk.tick(sim)
        texts = [e["text"] for e in sim.pop_events()]
        self.assertEqual(sum("Запрос прогноза отклонён" in t for t in texts), 3, texts)
        self.assertEqual(desk.values()["forecast-2"]["status"], "error")
        self.assertIn("nothing-here", desk.values()["forecast-2"]["error_text"])
        self.assertEqual(desk.jobs, {})

    def test_a_long_horizon_only_while_a_reline_is_planned(self):
        """Beyond a day only while a reline of the SAG mill is planned or under way (the advisor must see the whole
        stop of its latest option), and a branch that long may compute for longer in proportion."""
        sim = new_sim()
        desk = ForecastDesk(sim, THINGS)
        desk.request(sim, {"slot": 2, "horizon_h": 97})
        self.assertEqual(desk.values()["forecast-2"]["status"], "error")
        sim.handle_message("scenario-sag-reline", "activate", {})
        desk.request(sim, {"slot": 2, "horizon_h": 97})
        self.assertEqual([r.horizon_h for r in desk.new], [97])
        desk.request(sim, {"slot": 3, "horizon_h": PARAMS["forecast"]["planned_max_horizon_h"] + 1})
        self.assertEqual(desk.values()["forecast-3"]["status"], "error")
        self.assertEqual(timeout_s(PARAMS, 12), PARAMS["forecast"]["timeout_s"])
        self.assertAlmostEqual(timeout_s(PARAMS, 96), 4 * PARAMS["forecast"]["timeout_s"])

    def test_modules_in_a_forecast_follow_the_stand(self):
        sim = new_sim()
        desk = ForecastDesk(sim, THINGS)
        self.assertEqual(desk.enabled(), ON)
        desk.observe_module("module-plant", {"enabled": False, "status": "disabled"})
        desk.observe_module("module-maint", {"status": "offline"})
        self.assertEqual(desk.enabled(), {"dispatch": True, "grade": True, "plant": False, "maint": False})

    def test_the_advisor_does_not_wait_for_a_module_measurement(self):
        """A module's background branches that hold workers when the advisor's branches arrive are stopped and
        started again later: the options of an incident get every worker at once."""
        sim = new_sim()
        desk = ForecastDesk(sim, THINGS)
        mill = lambda tph: [{"thing": f"{NS}:mill", "subject": "set-feed-rate", "body": {"tph": tph}}]
        try:
            desk.request(sim, {"slot": 6, "horizon_h": 2, "request_id": "module", "actions_b": mill(3000)})
            desk.tick(sim)
            module = [job for job in desk.jobs.values() if job.users == {6}]
            self.assertTrue(module and all(job.pid > 0 for job in module))
            sim.step()
            for slot in (2, 3, 4, 5):
                desk.request(sim, {"slot": slot, "horizon_h": 2, "replicas": 3, "request_id": f"adv-{slot}",
                                   "actions_b": mill(3100 + 10 * slot)})
            desk.tick(sim)
            limit = max(1, min(int(PARAMS["forecast"]["max_parallel"]), len(os.sched_getaffinity(0)) - 2))
            advisor = [job for job in desk.jobs.values() if 6 not in job.users]
            self.assertEqual(len(advisor), 15)                   # 3 replicas: one shared «change nothing» + 4 options
            self.assertEqual(sum(1 for job in advisor if job.pid > 0), min(len(advisor), limit))
            self.assertEqual(desk.requeued, len(module))
            self.assertTrue(all(job.pid == 0 for job in module))
            values = wait(desk, sim, [2, 3, 4, 5, 6], limit_s=240)
            self.assertTrue(all(values[f"forecast-{n}"]["status"] == "ready" for n in (2, 3, 4, 5, 6)))
        finally:
            desk.close()

    def test_paired_replicas(self):
        """`replicas`: each replica runs A and B on the same streams, drawn anew per replica; the twin shows the
        means and the standard error of the difference over the replicas."""
        sim = new_sim()
        desk = ForecastDesk(sim, THINGS)
        desk.request(sim, {"slot": 2, "horizon_h": 2, "replicas": 3, "request_id": "rep",
                           "actions_b": [{"thing": f"{NS}:mill", "subject": "set-feed-rate", "body": {"tph": 3400, "hold_min": 120}}]})
        desk.tick(sim)
        self.assertEqual(len(desk.jobs), 6)
        self.assertEqual(sorted({job.salt for job in desk.jobs.values()}), ["", f"{sim.sim_time()}/1", f"{sim.sim_time()}/2"])
        twin = wait(desk, sim, [2])["forecast-2"]
        self.assertEqual(twin["replicas"], 3)
        deltas = [int(v) for v in twin["delta_total_replicas_kzt"].split(",")]
        self.assertEqual(len(deltas), 3)
        self.assertAlmostEqual(twin["delta_total_kzt"], sum(deltas) / 3, delta=8)       # means of rounded parts
        mean = sum(deltas) / 3
        se = (sum((d - mean) ** 2 for d in deltas) / 2 / 3) ** 0.5
        self.assertAlmostEqual(twin["delta_total_se_kzt"], se, delta=1)
        for branch in ("a", "b"):
            self.assertEqual(twin[f"{branch}_total_kzt"], twin[f"{branch}_margin_kzt"] + twin[f"{branch}_adjust_kzt"])
        self.assertIn("посчитаны 3 раза", twin["summary_text"])
        envelope.modify_features(NS, "forecast-2", twin, UNITS["forecast-2"])
        for bad in (0, 7, True, 2.5):
            desk.request(sim, {"slot": 3, "replicas": bad})
            self.assertEqual(desk.values()["forecast-3"]["status"], "error", bad)


class CommonRandomNumbers(unittest.TestCase):
    """Every random process draws from a stream of its own, keyed by its entity: branches with the same actions
    are identical, and a small change perturbs only what it causes."""

    def test_every_entity_has_streams_of_its_own(self):
        sim = new_sim()
        streams = [s for t in sim.fleet.trucks for s in (t.rng_drive, t.rng_tip, t.rng_fail, t.rng_repair)]
        streams += [s for l in sim.fleet.loaders.values() for s in (l.rng_fill, l.rng_pass, l.rng_fail, l.rng_repair)]
        self.assertEqual(len({id(s) for s in streams}), len(streams))
        # What one truck draws leaves every other stream where it was.
        other = copy.deepcopy(sim)
        for _ in range(50):
            other.fleet.trucks[0].rng_drive.random()
        self.assertEqual([t.rng_drive.random() for t in sim.fleet.trucks[1:]],
                         [t.rng_drive.random() for t in other.fleet.trucks[1:]])

    def test_identical_actions_identical_branches_on_any_replica(self):
        sim = new_sim()
        actions = [("rom-pad-01", "set-reclaim", {"tph": 600, "hold_min": 120})]
        live = [run_branch(copy.deepcopy(sim), actions, ON, 2) for _ in range(2)]
        self.assertEqual(live[0], live[1])
        replica = [run_branch(copy.deepcopy(sim), actions, ON, 2, salt="2026-10-26T10:00:00/1") for _ in range(2)]
        self.assertEqual(replica[0], replica[1])
        self.assertNotEqual(replica[0]["total_kzt"], live[0]["total_kzt"])        # another draw of the future

    def test_a_negligible_change_without_modules_changes_almost_nothing(self):
        """One truck has 5 m more to drive: without the modules (fixed plan) the branches stay within a fraction
        of a truckload of each other; before the streams were keyed by entity, every later draw shifted."""
        sim = new_sim()
        off = {name: False for name in ALL}
        a = run_branch(copy.deepcopy(sim), [], off, 6)
        nudged = copy.deepcopy(sim)
        truck = next(t for t in nudged.fleet.trucks if t.seg is not None and t.dist > 10)
        truck.dist -= 5.0
        b = run_branch(nudged, [], off, 6)
        self.assertLess(abs(b["total_kzt"] - a["total_kzt"]), 1_000_000)
        self.assertLess(abs(b["cu_t"] - a["cu_t"]), 0.1)

    def test_paired_noise_with_modules_is_within_its_stated_bound(self):
        """The noise the advisor's rule is built on (`forecast.noise_kzt`, a standard deviation): with every module
        on, a truck that refuels earlier (300 l less fuel) moves the 12-hour total of four fork moments two hours
        apart by no more than three of it, and by no more than one and a half of it in root mean square."""
        from concurrent.futures import ProcessPoolExecutor
        sim = new_sim()
        rig = Coupled(sim)
        moments = []
        for _ in range(4):
            rig.run(120)
            moments.append(copy.deepcopy(sim))
        jobs = [(m, nudge) for m in moments for nudge in (False, True)]
        with ProcessPoolExecutor(max_workers=min(8, os.cpu_count() or 1)) as pool:
            totals = list(pool.map(_noise_branch, jobs, range(len(jobs))))
        deltas = [b - a for a, b in zip(totals[0::2], totals[1::2])]
        noise = float(PARAMS["forecast"]["noise_kzt"])
        print(f"\npaired noise, 300 l less fuel on one truck: {[round(d / 1e6, 1) for d in deltas]} mln KZT")
        self.assertLessEqual(max(abs(d) for d in deltas), 3 * noise)
        self.assertLessEqual((sum(d * d for d in deltas) / len(deltas)) ** 0.5, 1.5 * noise)


class ExpectedEnds(unittest.TestCase):
    """A forecast takes a running incident to end at its expected end (mine/sim/durations.py), and an action
    that shortens it moves that end; what the action's resources cost is part of the branch's total."""

    def test_the_incident_ends_in_the_forecast_and_runs_on_the_stand(self):
        sim = new_sim()
        sim.handle_message("scenario-crusher-stop", "activate", {"params": {"cause": "jam"}})
        branch = copy.deepcopy(sim)
        r = run_branch(branch, [], ON, 6)
        self.assertEqual(branch.scenarios["scenario-crusher-stop"].status, "inactive")
        self.assertFalse(branch.plant.crusher.stopped_by)
        self.assertIn("«Остановка дробилки» — до", r["ends"])
        self.assertIn("заклинивание", r["ends"])
        # The mill gets its full feed again once the crusher is back (the jam is 3 h; the stockpile covers it).
        mill = [int(v) for v in r["series_mill_tph"].split(",")]
        self.assertGreater(min(mill[3:]), 0.9 * max(mill))
        # The live model is the stand: the scenario still runs.
        self.assertEqual(sim.scenarios["scenario-crusher-stop"].status, "active")

    def test_a_second_crew_moves_the_end_and_costs_its_price(self):
        sim = new_sim()
        sim.handle_message("scenario-crusher-stop", "activate", {})          # mechanical fault, 8 h
        a = run_branch(copy.deepcopy(sim), [], ON, 12)
        b = run_branch(copy.deepcopy(sim), [("crusher", "expedite-repair", {})], ON, 12)
        r = PARAMS["response"]
        hours = PARAMS["crusher"]["repair_mechanical_h"] * r["second_crew_factor"]
        contractor = r["contractor_people"] * r["contractor_usd_h"] * PARAMS["economics"]["usd_kzt"]
        price = r["own_crew_kzt_h"] * hours + contractor * max(hours, r["contractor_min_h"])
        self.assertEqual(a["resources_kzt"], 0)
        self.assertAlmostEqual(b["resources_kzt"], price, delta=300)
        for branch in (a, b):
            self.assertEqual(branch["total_kzt"], branch["margin_kzt"] + branch["adjust_kzt"] - branch["resources_kzt"])
        self.assertNotEqual(a["ends"], b["ends"])
        # The crusher is back 3.2 h earlier: the mill gets more ore over the horizon.
        self.assertGreater(b["milled_t"], a["milled_t"])
        self.assertGreater(b["cu_t"], a["cu_t"])

    def test_the_forecast_twin_says_what_it_assumes(self):
        sim = new_sim()
        sim.handle_message("scenario-shovel-failure", "activate", {})
        desk = ForecastDesk(sim, THINGS, fork=False)
        desk.request(sim, {"slot": 1, "horizon_h": 2, "label_a": "Как есть", "label_b": "Вторая бригада",
                           "actions_b": [{"thing": f"{NS}:loader-01", "subject": "expedite-repair", "body": {}}]})
        twin = wait(desk, sim, [1])["forecast-1"]
        self.assertEqual(twin["status"], "ready")
        self.assertIn("Прогноз считает окончание инцидента", twin["assumption_text"])
        self.assertIn("«Отказ экскаватора» — до", twin["assumption_text"])
        self.assertGreater(twin["b_resources_kzt"], 0)
        self.assertEqual(twin["a_resources_kzt"], 0)
        envelope.modify_features(NS, "forecast-1", twin, UNITS["forecast-1"])


def _noise_branch(job, index):
    sim, nudge = job
    if nudge:
        truck = sim.fleet.trucks[index // 2 % len(sim.fleet.trucks)]
        truck.fuel_l = max(0.0, truck.fuel_l - 300.0)
    return run_branch(sim, [], ON, 12)["total_kzt"]


if __name__ == "__main__":
    unittest.main()
