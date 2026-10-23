"""Checks of the advisor module and of the operator's orders its options
send: incidents found in the twins, the options built for them, the ranking by
forecast, `apply`, and the rule that an option is not undone by the module
that owns the same lever. The advisor runs here against the simulator, the
four module cores and the forecast desk in one process, as on the stand.

    make mine-test
"""
import copy
import math
import time
import unittest
from datetime import datetime

from mine.common import envelope
from mine.model.build_twins import MARGIN_SPLIT
from mine.modules.advisor.core import NOTHING, AdvisorCore
from mine.sim.coupling import Coupled, things_now
from mine.sim.forecast import ForecastDesk
from mine.sim.reliability import Fault
from mine.tests.test_model import NS, PARAMS, THINGS, UNITS, new_sim, run, texts


class Stand:
    """Simulator, module cores, forecast desk and advisor, wired as the platform wires them."""

    def __init__(self, sim=None, warm_min: float = 60):
        self.sim = sim or new_sim()
        self.rig = Coupled(self.sim)
        self.rig.run(warm_min)
        self.desk = ForecastDesk(self.sim, THINGS)
        self.advisor = AdvisorCore(things_now(self.sim), PARAMS)
        self.sent: list = []

    def deliver(self, messages) -> None:
        for name, subject, body in messages:
            self.sent.append((name, subject, body))
            if name == "mine" and subject == "forecast":
                self.desk.request(self.sim, body)
            else:
                self.sim.handle_message(name, subject, body)

    def exchange(self) -> None:
        for name, features in (self.sim.values() | self.desk.values()).items():
            self.advisor.observe(name, features)
        self.deliver(self.advisor.step())
        self.desk.tick(self.sim)

    def run_until_ranked(self, limit_s: float = 180.0) -> dict:
        started = time.monotonic()
        while time.monotonic() - started < limit_s:
            for _ in range(20):
                self.rig.step()
            self.exchange()
            if self.advisor.status == "ready":
                return self.advisor.twin()
            if self.desk.running():
                time.sleep(0.3)         # about the stand's pace at x60: a model minute in a second
        raise AssertionError(f"the advisor did not rank its options within {limit_s} s")

    def run(self, minutes: float) -> None:
        for _ in range(int(minutes * 3)):
            for _ in range(20):
                self.rig.step()
            self.exchange()


def scripted(core: AdvisorCore, deltas: list[tuple], inventory: bool = False, se: float = 0.0) -> None:
    """Answer the core's forecast requests with the given (total, copper) differences of B against A:
    earned as copper revenue, or (`inventory`) as the fleet work held in the stocks at the end of the horizon;
    `se`, the standard error of each difference over the paired replicas, defaults to none measured."""
    for option, (delta, cu) in zip(core.options, deltas):
        part = "work" if inventory else "revenue"
        core.observe(option["slot"], {"request_id": option["request_id"], "status": "ready",
                                      "delta_margin_kzt": 0 if inventory else delta, "delta_total_kzt": delta,
                                      "delta_cu_t": cu, f"a_{part}_kzt": 1_000_000_000, f"b_{part}_kzt": 1_000_000_000 + delta,
                                      "replicas": PARAMS["advisor"]["replicas"], "delta_total_se_kzt": se,
                                      "summary_text": f"прогноз {delta}"})


class Incidents(unittest.TestCase):
    def detect(self, scenario: str | None, minutes: float = 2, body: dict | None = None):
        sim = new_sim()
        if scenario:
            sim.handle_message(f"scenario-{scenario}", "activate", body or {})
        run(sim, minutes)
        core = AdvisorCore(things_now(sim), PARAMS)
        return core.detect(core.world.now)

    def test_each_kind_of_incident(self):
        self.assertIsNone(self.detect(None))
        self.assertEqual(self.detect("shovel-failure")["key"], "loader-down:loader-01")
        self.assertEqual(self.detect("crusher-stop")["key"], "crusher-stop")
        self.assertEqual(self.detect("road-closure")["key"], "road-closed:road-segment-01")
        rain = self.detect("heavy-rain")
        self.assertEqual(rain["key"], "rain")                    # the ramp it closes is part of the rain incident
        self.assertEqual(self.detect("clay-face", body={"target": f"{NS}:loader-03"})["key"], "clay-face:loader-03")
        self.assertEqual(self.detect("clay-plant-stop")["key"], "clay-plant-stop")
        sim = new_sim()
        sim.plant.hardness_factor = 1.25                          # harder ore on the stockpile (no scenario since B1)
        run(sim, 2)
        core = AdvisorCore(things_now(sim), PARAMS)
        self.assertEqual(core.detect(core.world.now)["key"], "hard-ore")
        self.assertIsNone(self.detect("blast", minutes=5))        # a blast closes roads by plan: no incident
        for found in (rain, self.detect("crusher-stop")):
            self.assertTrue(found["text"])

    def test_the_most_serious_incident_comes_first(self):
        sim = new_sim()
        for name in ("heavy-rain", "shovel-failure", "crusher-stop"):
            sim.handle_message(f"scenario-{name}", "activate", {})
        run(sim, 2)
        core = AdvisorCore(things_now(sim), PARAMS)
        found = core.detect(core.world.now)
        self.assertEqual(found["key"], "crusher-stop")
        self.assertIn("Других инцидентов: 2", found["text"])

    def test_truck_failure_cluster_must_last(self):
        sim = new_sim()
        core = AdvisorCore(things_now(sim), PARAMS)
        trucks = core.trucks[:PARAMS["advisor"]["truck_cluster_out"]]
        for truck in trucks:
            core.observe(truck, {"state": "down"})
        self.assertNotEqual((core.detect(core.world.now) or {}).get("key"), "truck-cluster")
        later = sim.sim_time(sim.now + PARAMS["advisor"]["truck_cluster_min"] * 60 + 1)
        core.observe("mine", {"sim_time": later})
        self.assertEqual(core.detect(core.world.now)["key"], "truck-cluster")


class Options(unittest.TestCase):
    def core_for(self, scenario: str, minutes: float = 2) -> AdvisorCore:
        sim = new_sim()
        sim.handle_message(f"scenario-{scenario}", "activate", {})
        run(sim, minutes)
        core = AdvisorCore(things_now(sim), PARAMS)
        self.assertEqual(core.step(), [])           # the first round only learns the clock
        core.observe("mine", {"sim_time": sim.sim_time(sim.now + 20)})
        return core

    def test_loader_down_options_and_their_forecasts(self):
        core = self.core_for("shovel-failure")
        sent = core.step()
        labels = [o["label"] for o in core.options]
        self.assertEqual(labels[0], NOTHING)
        self.assertTrue(3 <= len(labels) <= 4, labels)
        # The shift supervisor's own resources come before the levers the modules also pull.
        by_subject = {o["actions"][0]["subject"]: o for o in core.options if o["actions"]}
        self.assertEqual(list(by_subject), ["expedite-repair", "move-to-block", "prioritise-repair"])
        self.assertEqual(by_subject["expedite-repair"]["actions"][0]["thing"], f"{NS}:loader-01")
        self.assertIn("Вторая бригада с подрядчиком на ремонт экскаватора №1", by_subject["expedite-repair"]["label"])
        self.assertIn("один бокс стоит", by_subject["prioritise-repair"]["label"])
        move = by_subject["move-to-block"]
        self.assertIn(move["actions"][0]["thing"], (f"{NS}:loader-05", f"{NS}:loader-06"))   # a loader on waste
        self.assertEqual(move["actions"][0]["body"], {"block": f"{NS}:{short_block(core, 'loader-01')}"})
        # The incident has its expected end: the scenario's, from the loader's mean repair time.
        twin = core.twin()
        self.assertEqual(twin["incident_end_sim_time"], core.world.get("loader-01", "repair_end_sim_time"))
        self.assertIn("среднее время ремонта 8 ч", twin["incident_basis"])
        self.assertIn("Ожидаемое окончание", twin["incident_text"])
        # One forecast per option, in the advisor's slots, branch A always "change nothing".
        self.assertEqual([(m[0], m[1]) for m in sent], [("mine", "forecast")] * len(labels))
        self.assertEqual([m[2]["slot"] for m in sent], list(range(2, 2 + len(labels))))
        for (_, _, body), option in zip(sent, core.options):
            self.assertEqual((body["actions_a"], body["actions_b"], body["label_b"]), ([], option["actions"], option["label"]))
        self.assertEqual(core.twin()["analysis_status"], "running")

    def test_crusher_stop_options(self):
        core = self.core_for("crusher-stop")
        core.step()
        subjects = [o["actions"][0]["subject"] for o in core.options if o["actions"]]
        self.assertEqual(subjects, ["expedite-repair", "set-destination", "set-feed-rate"])
        crew = core.options[1]
        self.assertEqual(crew["actions"], [{"thing": f"{NS}:crusher", "subject": "expedite-repair", "body": {}}])
        # The orders of an option stand until the incident's expected end (8 h for a mechanical fault, 2 min gone).
        pads = next(o for o in core.options if o["actions"] and o["actions"][0]["subject"] == "set-destination")
        self.assertTrue(all(a["body"]["destination"] == f"{NS}:rom-pad-01" and a["body"]["hold_min"] == 480
                            for a in pads["actions"]))
        self.assertIn("8 ч, до ожидаемого пуска", pads["label"])

    def test_ranking_by_margin_then_copper(self):
        core = self.core_for("shovel-failure")
        core.step()
        n = len(core.options)
        scripted(core, [(0, 0.0), (50_000_000, 1.0), (50_000_000, 2.0), (-1_000_000, 0.0)][:n])
        core.step()
        twin = core.twin()
        self.assertEqual((twin["analysis_status"], twin["recommended"]), ("ready", 3))
        self.assertEqual([twin[f"option{i}_rank"] for i in range(1, n + 1)], [3, 2, 1, 4][:n])
        self.assertEqual(twin["option3_delta_kzt"], 50_000_000)
        self.assertIn(f"Рекомендован вариант «{twin['option3_label']}» (№ 3)", twin["last_decision"])
        self.assertEqual((twin["option3_recommendable"], twin["option3_verdict"]), (True, ""))
        envelope.modify_features(NS, core.name, twin, UNITS[core.name])

    def test_margin_split_adds_up_to_the_difference(self):
        core = self.core_for("shovel-failure")
        core.step()
        for option in core.options:
            core.observe(option["slot"], {"request_id": option["request_id"], "status": "ready", "delta_cu_t": 1.2,
                                          "a_revenue_kzt": 700e6, "b_revenue_kzt": 707e6, "a_diesel_kzt": 30e6,
                                          "b_diesel_kzt": 31e6, "a_power_kzt": 40e6, "b_power_kzt": 40.5e6,
                                          "a_rehandle_kzt": 0, "b_rehandle_kzt": 1.4e6, "a_grade_kzt": 4e6, "b_grade_kzt": 3.5e6,
                                          "a_work_kzt": 3e6, "b_work_kzt": 2.6e6, "a_pads_kzt": -1e6, "b_pads_kzt": -0.9e6,
                                          "a_strip_kzt": 2e6, "b_strip_kzt": 2.2e6, "a_resources_kzt": 0,
                                          "b_resources_kzt": 1.3e6, "delta_total_kzt": 2_200_000})
        core.step()
        twin = core.twin()
        for n in range(1, twin["options_count"] + 1):
            parts = {part: twin[f"option{n}_split_{part}_kzt"] for part in MARGIN_SPLIT}
            self.assertEqual(parts, {"revenue": 7_000_000, "grade": -500_000, "work": -200_000, "rehandle": -1_300_000,
                                     "diesel": -1_000_000, "power": -500_000, "resources": -1_300_000})
            self.assertEqual(sum(parts.values()), twin[f"option{n}_delta_kzt"])
            self.assertEqual(twin[f"option{n}_cost_kzt"], 1_300_000)          # what its resources cost

    def test_a_second_crew_gaining_on_inventory_is_said_to_rest_on_the_stocks(self):
        # Beyond two standard errors, but all of it fleet work held in the stocks: the reason says so, not «не окупается».
        for scenario in ("crusher-stop", "clay-plant-stop"):
            core = self.core_for(scenario)
            core.step()
            n = len(core.options)
            crew = next(i for i in range(2, n + 1) if "expedite-repair" in core.twin()[f"option{i}_actions"])
            scripted(core, [(60_000_000, 0.1) if i == crew else (0, 0.0) if i == 1 else (-1_000_000, 0.0)
                            for i in range(2, n + 2)][:0] or
                     [(60_000_000, 0.1) if i == crew else (-1_000_000, 0.0) for i in range(1, n + 1)],
                     inventory=True, se=1_000_000)
            core.step()
            reason = core.twin()[f"option{crew}_reason"]
            self.assertIn("выигрыш держится на запасах руды, меди не прибавляет", reason, scenario)
            self.assertNotIn("не окупается", reason, scenario)

    def test_an_option_gaining_only_on_inventory_is_never_recommended(self):
        core = self.core_for("shovel-failure")
        core.step()
        n = len(core.options)
        # Option 2 "earns" 60 mln KZT, all of it in the fleet work held in the stocks, with 0.1 t of copper.
        scripted(core, [(0, 0.0), (60_000_000, 0.1)] + [(-1_000_000, 0.0)] * (n - 2), inventory=True)
        core.step()
        twin = core.twin()
        self.assertEqual((twin["option2_rank"], twin["recommended"]), (1, 1))
        self.assertFalse(twin["option2_recommendable"])
        self.assertIn("не медью", twin["option2_verdict"])
        self.assertIn("Не рекомендованы, хотя итог выше", twin["last_decision"])
        # The same gain with copper behind it is recommended.
        core = self.core_for("shovel-failure")
        core.step()
        scripted(core, [(0, 0.0), (60_000_000, 9.5)] + [(-1_000_000, 0.0)] * (n - 2))
        core.step()
        self.assertEqual(core.twin()["recommended"], 2)

    def test_small_gains_and_copper_losses_are_not_recommended(self):
        core = self.core_for("shovel-failure")
        core.step()
        n = len(core.options)
        self.assertGreaterEqual(n, 3)
        # Option 2 earns most but loses 5 t of copper; option 3 earns enough without losing copper.
        scripted(core, [(0, 0.0), (80_000_000, -5.0), (45_000_000, 0.5), (0, 0.0)][:n])
        core.step()
        twin = core.twin()
        self.assertEqual((twin["option2_rank"], twin["recommended"]), (1, 3))
        self.assertIn(f"Не рекомендованы, хотя итог выше: «{twin['option2_label']}» — теряет 5,0 т меди",
                      twin["last_decision"])
        core = self.core_for("shovel-failure")
        core.step()
        scripted(core, [(0, 0.0)] + [(5_000_000, 0.5)] * (n - 1))           # within the scatter of the forecast
        core.step()
        self.assertEqual(core.twin()["recommended"], 1)

    def test_the_gain_must_clear_its_standard_error(self):
        """An option is recommended only with a mean gain of at least `advisor.min_t` standard errors: the error
        over the paired replicas, never below the measured paired noise of one forecast for that many replicas."""
        p, noise = PARAMS["advisor"], PARAMS["forecast"]["noise_kzt"]
        floor = noise / p["replicas"] ** 0.5
        cases = [   # (gain, its standard error over the replicas, recommended, confidence)
            (5 * floor, 0.0, True, "высокая"),                       # replicas agree: the floor stands for them
            (3 * floor, 0.5 * floor, True, "средняя"),
            (6 * floor, 2.0 * floor, True, "средняя"),               # widely scattered replicas: t = 3
            (1.5 * floor, 0.0, False, "низкая"),
            (0.5 * floor, 0.0, False, "нет: в пределах разброса"),
            (-3 * floor, 0.0, False, "нет: вариант хуже"),
        ]
        for gain, se, recommended, confidence in cases:
            core = self.core_for("shovel-failure")
            core.step()
            n = len(core.options)
            scripted(core, [(0, 0.0), (round(gain), 1.0)] + [(-1_000_000, 0.0)] * (n - 2), se=se)
            core.step()
            twin = core.twin()
            self.assertEqual(twin["recommended"] == 2, recommended, (gain, se, twin["option2_verdict"]))
            self.assertEqual(twin["option2_confidence"], confidence, (gain, se))
            self.assertEqual(twin["option2_gain_se_kzt"], round(max(se, floor)))
            self.assertEqual((twin["option1_confidence"], twin["option1_gain_se_kzt"]), ("", 0))
            if not recommended and gain > 0:
                self.assertIn("в пределах разброса прогноза", twin["option2_verdict"])
            if recommended:
                self.assertIn(f"достоверность {confidence}", twin["last_decision"])
            envelope.modify_features(NS, core.name, twin, UNITS[core.name])

    def test_copper_is_a_hard_bar(self):
        """However sure its gain in money, an option that loses copper beyond `advisor.max_cu_loss_t` is not recommended."""
        core = self.core_for("shovel-failure")
        core.step()
        n = len(core.options)
        loss = PARAMS["advisor"]["max_cu_loss_t"] + 0.2
        scripted(core, [(0, 0.0), (200_000_000, -loss)] + [(-1_000_000, 0.0)] * (n - 2), se=1_000_000)
        core.step()
        twin = core.twin()
        self.assertEqual((twin["option2_rank"], twin["option2_confidence"], twin["recommended"]), (1, "высокая", 1))
        self.assertIn("теряет", twin["option2_verdict"])

    def test_nothing_recommended_names_the_modules_that_answer(self):
        """«Change nothing» recommended: the text names the modules that are on and already answer the incident."""
        core = self.core_for("crusher-stop")
        for module in ("module-dispatch", "module-plant"):
            core.observe(module, {"status": "online", "enabled": True})
        core.step()
        scripted(core, [(0, 0.0)] + [(1_000_000, 0.0)] * (len(core.options) - 1))
        core.step()
        text = core.twin()["last_decision"]
        self.assertEqual(core.twin()["recommended"], 1)
        self.assertIn("Инцидент уже отрабатывают включённые модули: модуль диспетчеризации", text)
        self.assertIn("модуль управления фабрикой снижает уставку мельницы", text)
        self.assertIn("меди за горизонт не прибавляет", text)

    def test_modules_that_are_off_are_switched_on_in_one_option(self):
        core = self.core_for("shovel-failure")
        for module in ("module-dispatch", "module-plant", "module-grade", "module-maint"):
            core.observe(module, {"status": "disabled", "enabled": False})
        core.step()
        switch = core.options[1]
        self.assertEqual(switch["label"], "Включить модули: диспетчеризации, управления фабрикой, управления качеством руды")
        self.assertEqual([(a["thing"], a["body"]) for a in switch["actions"]],
                         [(f"{NS}:{m}", {"enabled": True}) for m in ("module-dispatch", "module-plant", "module-grade")])

    def test_a_lasting_incident_is_analysed_again_when_the_stockpile_runs_down(self):
        core = self.core_for("crusher-stop")
        core.step()
        scripted(core, [(0, 0.0)] + [(1_000_000, 0.0)] * (len(core.options) - 1))
        core.step()
        self.assertEqual(core.twin()["analysis_status"], "ready")
        stock_h, fall = core.incident["stock_h"], PARAMS["advisor"]["reanalyse_stock_h"]
        clock = core.world.get("mine", "sim_time")
        core.observe("stockpile", {"hours_of_feed": stock_h - fall / 2})
        core.observe("mine", {"sim_time": clock[:-2] + "40"})
        core.step()
        self.assertEqual(core.twin()["analysis_status"], "ready")      # a small fall: the analysis stands
        core.observe("stockpile", {"hours_of_feed": stock_h - fall - 0.1})
        core.observe("mine", {"sim_time": clock[:-2] + "59"})
        sent = core.step()
        twin = core.twin()
        self.assertEqual(twin["analysis_status"], "running")
        self.assertIn("Повторный разбор", twin["incident_text"])
        self.assertTrue(any(subject == "forecast" for _, subject, _ in sent))

    def test_nothing_wins_when_every_option_loses(self):
        core = self.core_for("shovel-failure")
        core.step()
        scripted(core, [(0, 0.0)] + [(-1_000_000, 0.5)] * (len(core.options) - 1))
        core.step()
        self.assertEqual(core.twin()["recommended"], 1)
        self.assertIn("оставить как есть", core.twin()["last_decision"])

    def test_apply(self):
        core = self.core_for("shovel-failure")
        core.step()
        self.assertEqual(core.command("apply", {"option": 2}), [])               # not ranked yet
        scripted(core, [(0, 0.0), (90_000_000, 3.0), (1_000_000, 0.1), (0, 0.0)][:len(core.options)])
        core.step()
        self.assertEqual(core.command("apply", {"option": 9}), [])
        self.assertEqual(core.command("apply", {"option": "2"}), [])
        messages = core.command("apply", {"option": 2})
        option = core.options[1]
        self.assertEqual(messages[:-1], [(a["thing"].split(":")[1], a["subject"], a["body"]) for a in option["actions"]])
        journal = messages[-1]
        self.assertEqual((journal[0], journal[1]), ("journal", "record"))
        twin = core.twin()
        # The journal and the twin name the option by its label, its number as published and its place.
        self.assertIn(f"применён вариант «{option['label']}» (№ 2 в списке советника, место {twin['option2_rank']}",
                      journal[2]["text"])
        self.assertEqual((twin["applied_option"], twin["applied_label"], twin["applied_rank"]),
                         (2, option["label"], twin["option2_rank"]))
        self.assertEqual(twin["applied_text"], journal[2]["text"])
        self.assertNotRegex(journal[2]["text"], r"[a-z]{3,}")             # Russian text, no message subjects or IDs
        self.assertEqual(core.command("apply", {"option": 1})[:-1], [])        # «change nothing» sends nothing but the record
        envelope.modify_features(NS, core.name, twin, UNITS[core.name])

    def test_twin_matches_its_definition_in_every_state(self):
        sim = new_sim()
        core = AdvisorCore(things_now(sim), PARAMS)
        for status in (None, "offline"):
            envelope.modify_features(NS, core.name, core.twin(status), UNITS[core.name])
        self.assertIn("Инцидентов нет", core.twin()["effect_text"])
        core.set_enabled(False)
        self.assertEqual(core.twin()["status"], "disabled")
        envelope.modify_features(NS, core.name, core.twin(), UNITS[core.name])


def short_block(core: AdvisorCore, loader: str) -> str:
    return core.world.get(loader, "block").split(":")[1]


class EndToEnd(unittest.TestCase):
    def test_loader_failure_ranked_applied_and_visible(self):
        """The ore loader on the pit floor breaks down. Moving the waste shovel to its ore face (97 minutes on its
        tracks, stripping stopped) brings that face back; whatever is recommended must earn it with copper, and an
        applied option shows in the twins."""
        stand = Stand()
        target = stand.sim.fleet.loaders["loader-01"].block
        stand.sim.handle_message("scenario-shovel-failure", "activate", {})
        started = time.monotonic()
        twin = stand.run_until_ranked()
        print(f"\nadvisor, loader-01 down: ranked in {time.monotonic() - started:.1f} s")
        for n in range(1, twin["options_count"] + 1):
            print(f"  {n}. {twin[f'option{n}_delta_kzt'] / 1e6:+8.1f} млн тг {twin[f'option{n}_cu_delta_t']:+6.2f} т Cu "
                  f"(место {twin[f'option{n}_rank']}) {twin[f'option{n}_label']}")
        self.assertEqual(twin["option1_delta_kzt"], 0)                  # the reference against itself
        for n in range(1, twin["options_count"] + 1):
            self.assertEqual(sum(twin[f"option{n}_split_{part}_kzt"] for part in MARGIN_SPLIT), twin[f"option{n}_delta_kzt"])
        rec = twin["recommended"]
        self.assertGreaterEqual(rec, 1)
        if rec > 1:                                                     # a recommended option must earn it with copper
            self.assertGreater(twin[f"option{rec}_delta_kzt"], PARAMS["advisor"]["min_gain_kzt"])
            cash = sum(twin[f"option{rec}_split_{part}_kzt"] for part in ("revenue", "grade", "rehandle", "diesel", "power"))
            self.assertGreater(cash, 0.5 * twin[f"option{rec}_delta_kzt"])
        move = next(n for n in range(2, twin["options_count"] + 1) if "move-to-block" in twin[f"option{n}_actions"])
        mover = stand.advisor.options[move - 1]["actions"][0]["thing"].split(":")[1]
        stand.deliver(stand.advisor.command("apply", {"option": move}))
        stand.run(5)
        loader = stand.sim.fleet.loaders[mover]
        self.assertEqual((loader.block, loader.state), (target, "relocating"))
        self.assertIsNone(stand.sim.fleet.loaders["loader-01"].block)      # the broken-down loader gave its face up
        events = texts(stand.sim)
        self.assertIn("Советник: применён вариант", events)
        self.assertIn("по указанию оператора", events)
        stand.sim.handle_message("scenario-shovel-failure", "cancel", {})
        stand.run(5)
        self.assertNotIn("инцидент закончился", texts(stand.sim))         # a moved loader stays: nothing to end
        self.assertIsNotNone(stand.sim.fleet.loaders["loader-01"].block)   # repaired, it takes the next block of the plan
        self.assertEqual(stand.advisor.twin()["incident"], "")


class StockpileDecides(unittest.TestCase):
    """The same incident, a jammed crusher expected back in 3 h, and the same lever, a second repair crew with
    the contractor's specialists: with the stockpile the stand usually holds the crew does not pay, because the
    mill gets its full feed until the crusher is back anyway; after an earlier stop has drained the stockpile it
    pays many times over, because without it the mill runs short of ore at once."""

    def ranked(self, drain_h: float) -> tuple[dict, int, float]:
        stand = Stand()
        if drain_h:
            stand.sim.handle_message("scenario-crusher-stop", "activate", {"params": {"cause": "jam", "expected_h": drain_h + 1}})
            stand.run(drain_h * 60)
            stand.sim.handle_message("scenario-crusher-stop", "cancel", {})
            stand.run(20)
        pile = stand.sim.plant.stockpile
        level = 100.0 * pile.t / stand.sim.plant.capacity_t
        stand.sim.handle_message("scenario-crusher-stop", "activate", {"params": {"cause": "jam"}})
        twin = stand.run_until_ranked()
        crew = next(n for n in range(2, twin["options_count"] + 1) if "expedite-repair" in twin[f"option{n}_actions"])
        print(f"\ncrusher jammed for 3 h, stockpile {level:.0f} %: second crew {twin[f'option{crew}_delta_kzt'] / 1e6:+.1f} "
              f"± {twin[f'option{crew}_gain_se_kzt'] / 1e6:.1f} mln KZT, {twin[f'option{crew}_cu_delta_t']:+.1f} t Cu, "
              f"its cost {twin[f'option{crew}_cost_kzt'] / 1e6:.2f} mln KZT; recommended {twin['recommended']}\n  "
              f"{twin[f'option{crew}_reason']}")
        return twin, crew, level

    def test_the_stockpile_decides_whether_a_second_crew_pays(self):
        full, n, high = self.ranked(0)
        drained, m, low = self.ranked(5)
        self.assertGreater(high, 45)
        self.assertLess(low, 30)
        cost = PARAMS["response"]["contractor_people"] * PARAMS["response"]["contractor_usd_h"] \
            * PARAMS["economics"]["usd_kzt"] * PARAMS["response"]["contractor_min_h"]
        for twin, k in ((full, n), (drained, m)):
            # The crew's price is its own part of the gain, and that part is what it costs.
            self.assertEqual(twin[f"option{k}_split_resources_kzt"], -twin[f"option{k}_cost_kzt"])
            self.assertGreaterEqual(twin[f"option{k}_cost_kzt"], cost)
        # A full enough stockpile covers the stop: not recommended, and the card says why.
        self.assertNotEqual(full["recommended"], n)
        self.assertLess(full[f"option{n}_delta_kzt"], PARAMS["advisor"]["min_t"] * full[f"option{n}_gain_se_kzt"])
        self.assertIn("Склада хватает до конца ремонта дробилки", full[f"option{n}_reason"])
        self.assertIn("не окуп", full[f"option{n}_reason"])
        # A drained one does not: recommended, earned with copper, and the card says why.
        self.assertEqual(drained["recommended"], m)
        self.assertGreater(drained[f"option{m}_cu_delta_t"], 3.0)
        # Without the crew the plant module cuts the feed (or the mill stops): never «the mill runs short in 1 h»
        # next to a stockpile of several hours.
        self.assertRegex(drained[f"option{m}_reason"], r"Ускорить ремонт дробилки окупается: без этого (уже в первый час|"
                                                       r"через \d+ ч) (модуль управления фабрикой начнёт снижать подачу "
                                                       r"мельницы|мельница встанет|подача мельницы начнёт падать)")
        self.assertNotIn("не хватит руды", drained[f"option{m}_reason"])
        self.assertIn("Ускорить ремонт дробилки окупается", drained["last_decision"])


class ClayDecides(unittest.TestCase):
    """Clay ore: the same kind of incident answered differently as the situation differs. ОФ-2 stopped: a second
    crew pays when the repair has hours to go (ОФ-2 makes no copper meanwhile) and does not when it is nearly done.
    A sulphide face turns out clay-rich: moving its shovel to a free blasted block of sulphide ore pays (ОФ-1 gets
    clean ore, the clay waits in the pit for ОФ-2); with no such block free, the grade-control module already
    sends the clay to ОФ-2 and the clay-ore stockpile, and nothing the shift can add beats it."""

    def ranked(self, setup) -> dict:
        stand = Stand(warm_min=90)
        setup(stand)
        stand.run(20)
        twin = stand.run_until_ranked(240)
        print(f"\n{stand.advisor.incident['key']}: recommended {twin['recommended']}")
        for n in range(1, twin["options_count"] + 1):
            print(f"  {n}. {twin[f'option{n}_delta_kzt'] / 1e6:+8.1f} ± {twin[f'option{n}_gain_se_kzt'] / 1e6:.1f} млн тг "
                  f"{twin[f'option{n}_cu_delta_t']:+6.2f} т Cu, {twin[f'option{n}_cost_kzt'] / 1e6:.2f} млн тг: "
                  f"{twin[f'option{n}_label']}\n     {twin[f'option{n}_reason']}")
        for n in range(1, twin["options_count"] + 1):
            self.assertEqual(sum(twin[f"option{n}_split_{part}_kzt"] for part in MARGIN_SPLIT), twin[f"option{n}_delta_kzt"])
            if n > 1:
                self.assertTrue(twin[f"option{n}_reason"], n)
        return twin

    @staticmethod
    def option(twin: dict, text: str) -> int:
        return next(n for n in range(2, twin["options_count"] + 1) if text in twin[f"option{n}_actions"])

    def test_the_repair_time_decides_whether_a_second_crew_on_ofs2_pays(self):
        def stop(hours):
            return lambda stand: stand.sim.handle_message("scenario-clay-plant-stop", "activate",
                                                          {"params": {"expected_h": hours}})
        long, short_ = self.ranked(stop(6.0)), self.ranked(stop(1.5))
        for twin in (long, short_):
            self.assertEqual(twin["incident"], "clay-plant-stop")
            crew = self.option(twin, "expedite-repair")
            self.assertGreater(twin[f"option{crew}_cost_kzt"], 0)
            self.assertIn("ОФ-2 запустится раньше", twin[f"option{crew}_reason"])
        crew = self.option(long, "expedite-repair")
        self.assertEqual(long["recommended"], crew)
        self.assertGreater(long[f"option{crew}_cu_delta_t"], 3.0)
        self.assertIn("окупается: до пуска", long[f"option{crew}_reason"])
        crew = self.option(short_, "expedite-repair")
        self.assertNotEqual(short_["recommended"], crew)
        self.assertIn("не окупается", short_[f"option{crew}_reason"])

    def test_a_free_sulphide_face_decides_what_to_do_with_a_clay_face(self):
        def clay_face(stand):
            stand.sim.handle_message("scenario-clay-face", "activate", {"target": f"{NS}:loader-03"})

        def clay_face_nothing_free(stand):
            for block in stand.sim.pit.blocks.values():
                if block.status == "blasted" and block.material == "ore" and not block.reserved_by:
                    block.status = "charged"                     # no blasted sulphide block a shovel could go to
            clay_face(stand)
        free, none = self.ranked(clay_face), self.ranked(clay_face_nothing_free)
        for twin in (free, none):
            self.assertEqual(twin["incident"], "clay-face:loader-03")
            stockpile = self.option(twin, "clay-stockpile")
            self.assertIn("на складе глинистой руды свободно", twin[f"option{stockpile}_reason"])
            blend = self.option(twin, "destination=crusher")
            self.assertLess(twin[f"option{blend}_delta_kzt"], 0)        # clay into ОФ-1 above the limit loses copper
            self.assertIn("пункта извлечения ОФ-1", twin[f"option{blend}_reason"])
        move = self.option(free, "move-to-block")
        self.assertEqual(free["recommended"], move)
        self.assertGreater(free[f"option{move}_cu_delta_t"], 3.0)
        self.assertIn("ОФ-1 получает сульфидную руду", free[f"option{move}_reason"])
        self.assertFalse(any("move-to-block" in none[f"option{n}_actions"] for n in range(2, none["options_count"] + 1)))
        self.assertEqual(none["recommended"], 1)
        self.assertIn("оставить как есть", none["last_decision"])


class Wording(unittest.TestCase):
    """The reasons and labels say what the numbers next to them say."""

    core_for = Options.core_for

    def test_a_feed_cut_by_the_plant_module_is_not_called_a_lack_of_ore(self):
        core = self.core_for("crusher-stop")
        core.observe("module-plant", {"status": "online", "enabled": True})
        core.step()
        core.incident["stock_h"] = 5.9
        r = {"a_mill": [3700.0, 3700.0, 2140.0] + [2140.0] * 9, "b_mill": [3700.0] * 12, "a_stops": 0, "b_stops": 0}
        text = core._starve_text(r)
        self.assertTrue(text.startswith("через 2 ч модуль управления фабрикой начнёт снижать подачу мельницы"), text)
        self.assertIn(f"склада дроблёной руды на 5,9 ч питания не хватит до пуска дробилки в "
                      f"{core.incident['end'][11:16]}", text)
        self.assertIn("снизит её до 2 140 т/ч", text)
        self.assertNotIn("не хватит руды", text)
        stock = f" (склада дроблёной руды на 5,9 ч питания не хватит до пуска дробилки в {core.incident['end'][11:16]})"
        r["a_mill"][2:] = [0.0] * 10
        self.assertEqual(core._starve_text(r), "мельница встанет через 2 ч" + stock)
        # Cut first, stopped later: both, each at its own hour (not «встанет через 1 ч» beside a 5,9 h stockpile).
        r["a_mill"] = [3700.0, 2900.0, 2500.0, 2200.0, 2140.0, 300.0] + [0.0] * 6
        r["a_stops"] = 1
        self.assertEqual(core._starve_text(r), "через 1 ч модуль управления фабрикой начнёт снижать подачу мельницы"
                                               + stock + ", а через 6 ч мельница встанет")

    def test_copper_taken_from_the_stocks_is_not_called_no_copper(self):
        core = self.core_for("road-closure")
        core.step()
        split = {"revenue": 18_100_000, "grade": -18_100_000, "work": 11_700_000, "rehandle": 0, "diesel": -1_700_000,
                 "power": 0, "resources": 0}
        text = core._stock_reason({"cu": 2.9, "split": split})
        self.assertEqual(text, "медь +2,9 т взята из запасов богатой руды, а выигрыш держится на оценке работы парка "
                               "(+11,7 млн тг)")
        self.assertEqual(core._stock_reason({"cu": 0.1, "split": split}), "выигрыш держится на запасах руды, меди не прибавляет")

    def test_a_clay_face_the_grade_module_routes_is_said_so(self):
        core = self.core_for("clay-face")
        core.observe("module-grade", {"status": "online", "enabled": True})
        core.step()
        loader = core.incident["thing"]
        block = short_block(core, loader)
        self.assertIn("модуль управления качеством руды распределяет её между ОФ-2", core.incident["text"])
        self.assertIn("(по блочной модели)", core.incident["text"])
        # The module sends the face to ОФ-2: the incident text follows, and holding ОФ-2 is offered as a hold.
        core.observe(block, {"destination": f"{NS}:{core.clay_plant}", "destination_source": "grade-control"})
        core.observe("mine", {"sim_time": datetime.fromtimestamp(core.world.now + 60).isoformat(timespec="seconds")})
        core.step()
        self.assertIn(f"сейчас её везут на «{core.world.title(core.clay_plant)}» (решение модуля управления качеством "
                      f"руды)", core.incident["text"])
        self.assertIn(core.incident["text"], core.twin()["incident_text"])
        option = core._clay_route([(loader, block)], core.clay_plant)
        self.assertIsNotNone(option)
        self.assertIn("закрепить за ОФ-2", option["label"])
        self.assertIn("без указания модуль управления качеством руды переводит её и в шихту ОФ-1", option["label"])
        # Without the module, ore already on its way to ОФ-2 is not offered to go there.
        core.observe("module-grade", {"status": "disabled", "enabled": False})
        self.assertIsNone(core._clay_route([(loader, block)], core.clay_plant))


class Withdrawal(unittest.TestCase):
    def test_orders_of_an_applied_option_end_with_the_incident(self):
        stand = Stand(warm_min=30)
        stand.sim.handle_message("scenario-crusher-stop", "activate", {})
        stand.run_until_ranked()
        pads = next(n for n, o in enumerate(stand.advisor.options, start=1)
                    if o["actions"] and o["actions"][0]["subject"] == "set-destination")
        stand.deliver(stand.advisor.command("apply", {"option": pads}))
        stand.run(3)
        blocks = [a["thing"].split(":")[1] for a in stand.advisor.options[pads - 1]["actions"]]
        self.assertTrue(all(stand.sim.pit.blocks[b].destination_source == "operator" for b in blocks))
        stand.sim.handle_message("scenario-crusher-stop", "cancel", {})
        stand.run(5)
        self.assertIn("инцидент закончился", texts(stand.sim))
        self.assertTrue(all(stand.sim.pit.blocks[b].destination_source != "operator" for b in blocks))


class OperatorOrders(unittest.TestCase):
    """An option sets a lever with `hold_min`; the module owning the lever does not undo it."""

    def test_mill_setpoint_is_held_against_plant_control(self):
        sim = new_sim()
        rig = Coupled(sim)
        rig.run(20)
        self.assertEqual(sim.plant.mill.setpoint_source, "plant-control")
        sim.handle_message("mill", "set-feed-rate", {"tph": 1600, "hold_min": 120})
        rig.run(100)                                            # the module's control period is 5 min
        mill = sim.values()["mill"]
        self.assertEqual((mill["feed_setpoint_tph"], mill["setpoint_source"]), (1600, "operator"))
        self.assertTrue(mill["setpoint_hold_until"])
        self.assertIn("оператор", rig.cores["plant"].twin()["last_decision"])
        sim.handle_message("mill", "set-feed-rate", {"tph": 2000})          # a module's setpoint: refused
        self.assertEqual(sim.plant.mill.setpoint_tph, 1600)
        rig.run(30)                                             # the hold ends: the module takes over again
        self.assertEqual(sim.plant.mill.setpoint_source, "plant-control")
        self.assertIn("срок указания оператора истёк", texts(sim))

    def test_block_destination_is_held_against_grade_control(self):
        sim = new_sim()
        rig = Coupled(sim)
        rig.run(20)
        block = next(b for b in sim.pit.blocks.values() if b.status == "mining" and b.material == "ore")
        sim.handle_message(block.name, "set-destination", {"destination": f"{NS}:rom-pad-01", "hold_min": 60})
        rig.run(45)
        v = sim.values()[block.name]
        self.assertEqual((v["destination"], v["destination_source"]), ("rom-pad-01", "operator"))
        rig.run(20)
        self.assertNotEqual(sim.pit.blocks[block.name].destination_source, "operator")

    def test_reclaim_is_held_against_grade_control(self):
        sim = new_sim()
        rig = Coupled(sim)
        rig.run(20)
        sim.handle_message("rom-pad-01", "set-reclaim", {"tph": 600, "hold_min": 60})
        rig.run(30)
        pad = sim.values()["rom-pad-01"]
        self.assertEqual((pad["reclaim_source"], pad["reclaim_tph"]), ("operator", 600))
        rig.set_enabled("grade", False)                         # its release leaves the operator's order alone
        self.assertEqual(sim.plant.rom_pads["rom-pad-01"].ordered_tph, 600)

    def test_truck_called_back_from_planned_service(self):
        sim = new_sim()
        truck = sim.fleet.by_name["truck-05"]
        sim.handle_message("truck-05", "schedule-service", {"reason": "плановое ТО", "indicator": "hours_since_service",
                                                            "value": 241})
        self.assertTrue(truck.service_due)
        sim.handle_message("truck-05", "cancel-service", {"hold_min": 120})
        self.assertEqual(truck.service_due, "")
        self.assertTrue(sim.values()["truck-05"]["service_hold_until"])
        sim.handle_message("truck-05", "schedule-service", {"reason": "плановое ТО", "indicator": "hours_since_service",
                                                            "value": 241})
        self.assertEqual(truck.service_due, "")                 # planned service waits for the hold
        sim.handle_message("truck-05", "schedule-service", {"reason": "дефект", "indicator": "engine_temp_c", "value": 99})
        self.assertTrue(truck.service_due)                      # a defect does not

    def test_truck_waiting_at_the_workshop_goes_back_to_work(self):
        sim = new_sim()
        truck = sim.fleet.by_name["truck-07"]
        sim.handle_message("truck-07", "schedule-service", {"reason": "плановое ТО", "indicator": "hours_since_service",
                                                            "value": 241})
        # Fill both bays so that the truck has to wait for one.
        from mine.sim.reliability import Job
        for i in range(len(sim.reliability.bays)):
            sim.reliability.bays[i] = Job(sim.fleet.by_name[f"truck-0{i + 1}"], "service", 1e9, 1e9)
        for _ in range(4 * 3600):
            sim.step()
            if truck.state == "service":
                break
        self.assertEqual(truck.state, "service")
        sim.handle_message("truck-07", "cancel-service", {"hold_min": 60})
        self.assertNotIn(truck, [job.truck for job in sim.reliability.queue])
        self.assertNotEqual(truck.state, "service")

    def test_a_truck_in_a_bay_for_planned_work_is_called_back(self):
        """Planned work in a bay is broken off by an operator's call-back; a repair of a breakdown is not."""
        sim = new_sim()
        truck = sim.fleet.by_name["truck-12"]
        sim.handle_message(truck.name, "schedule-service", {"reason": "плановое ТО"})
        for _ in range(4 * 3600):
            sim.step()
            if truck.in_bay:
                break
        self.assertTrue(truck.in_bay)
        sim.handle_message(truck.name, "cancel-service", {"hold_min": 720})
        self.assertEqual(sim.refusals, [])
        values = sim.values()[truck.name]
        self.assertTrue(values["service_hold_until"].startswith("20"))
        self.assertFalse(truck.in_bay)
        self.assertNotEqual(values["state"], "service")
        self.assertIn("прервано по указанию оператора", texts(sim))
        broken = sim.fleet.by_name["truck-13"]
        broken.fault = Fault("major", "drive", 24.0, progress=0.999)
        for _ in range(3 * 3600):
            sim.step()
            if broken.state == "down":
                break
        sim.handle_message(broken.name, "cancel-service", {"hold_min": 720})
        self.assertTrue(sim.refusals)                                  # a breakdown is repaired, whatever the order
        self.assertEqual(sim.values()[broken.name]["service_hold_until"], "")

    def test_every_option_kind_shows_in_its_twin(self):
        """Each kind of lever an option pulls leaves a mark in the twin it addresses, so that an applied option
        can always be checked: the stand check reads exactly these features."""
        sim = new_sim()
        free = next(b for b in sim.pit.blocks.values() if b.status == "blasted" and not b.reserved_by)
        ore_block = next(b for b in sim.pit.blocks.values() if b.status == "mining" and b.material == "ore")
        idle = next(t for t in sim.fleet.trucks if not t.service_due and t.state != "down")
        actions = [("loader-02", "move-to-block", {"block": f"{NS}:{free.name}"}),
                   ("rom-pad-01", "set-reclaim", {"tph": 500, "hold_min": 720}),
                   ("mill", "set-feed-rate", {"tph": 3400, "hold_min": 720}),
                   (ore_block.name, "set-destination", {"destination": f"{NS}:rom-pad-01", "hold_min": 720}),
                   (idle.name, "cancel-service", {"hold_min": 720})]          # no booking: kept at work all the same
        for name, subject, body in actions:
            sim.handle_message(name, subject, body)
            self.assertEqual(sim.refusals, [], subject)
        run(sim, 3)
        v = sim.values()
        self.assertEqual(v["loader-02"]["block"], f"{NS}:{free.name}")
        self.assertEqual((v["rom-pad-01"]["reclaim_source"], v["mill"]["setpoint_source"],
                          v[ore_block.name]["destination_source"]), ("operator", "operator", "operator"))
        self.assertTrue(v[idle.name]["service_hold_until"].startswith("20"))

    def test_move_to_block(self):
        sim = new_sim()
        fleet, pit = sim.fleet, sim.pit
        free = next(b for b in pit.blocks.values() if b.status == "blasted" and not b.reserved_by)
        sim.handle_message("loader-02", "move-to-block", {"block": f"{NS}:{free.name}"})
        run(sim, 3)
        loader = fleet.loaders["loader-02"]
        self.assertEqual((loader.block, free.status, free.reserved_by), (free.name, "mining", "loader-02"))
        sim.pop_events()
        busy = fleet.loaders["loader-01"].block
        sim.handle_message("loader-02", "move-to-block", {"block": f"{NS}:{busy}"})      # another loader works there
        sim.handle_message("loader-02", "move-to-block", {"block": f"{NS}:block-99"})
        self.assertEqual(texts(sim).count("перевод на другой блок не выполнен"), 2)
        self.assertEqual(fleet.loaders["loader-02"].block, free.name)


class Reline(unittest.TestCase):
    """A planned reline of the SAG mill (scenario «Замена футеровки МПСИ»): when to begin it, and whether to take the
    ore shovels off ore for the stop. Every option is forecast over one horizon that holds the whole stop of the
    latest of them and the restart, so that a later start cannot win by putting its stop beyond the forecast."""
    NAME = "scenario-sag-reline"

    def core_for(self, params: dict):
        sim = new_sim()
        sim.handle_message(self.NAME, "activate", {"params": params})
        run(sim, 2)
        core = AdvisorCore(things_now(sim), PARAMS)
        self.assertEqual(core.step(), [])           # the first round only learns the clock
        core.observe("mine", {"sim_time": sim.sim_time(sim.now + 20)})
        return sim, core

    def test_every_option_is_forecast_over_one_horizon_that_holds_its_whole_stop(self):
        sim, core = self.core_for({"start": "next-shift"})
        sent = core.step()
        twin = core.twin()
        self.assertEqual(twin["incident"], "sag-reline:planned")
        self.assertEqual(twin["incident_end_sim_time"], sim.values()["mill-sag"]["reline_end_sim_time"])
        self.assertIn("Bradken", twin["incident_basis"])
        self.assertEqual(core.options[0]["label"], NOTHING)
        starts = [o.get("start") for o in core.options[1:]]
        self.assertEqual(starts, ["now", None, "shift-after"])           # begin now, prepare the pit, begin a shift later
        self.assertEqual([a["subject"] for a in core.options[1]["actions"]], ["plan-reline"])
        self.assertTrue(core.options[2]["actions"] and all(a["subject"] == "move-to-block" for a in core.options[2]["actions"]))
        # One horizon for all: the latest start (a shift after the next shift change) + the reline + the restart.
        hours, recovery = PARAMS["mill"]["reline_h"], PARAMS["scenario"]["reline_recovery_h"]
        latest_h = (sim.next_shift_at + PARAMS["scenario"]["reline_shift_after_h"] * 3600 - sim.now - 20) / 3600.0
        horizon = math.ceil(latest_h + hours + recovery)
        self.assertEqual({m[2]["horizon_h"] for m in sent}, {horizon})
        self.assertEqual(len(sent), len(core.options))
        self.assertEqual((twin["analysis_horizon_h"], core.horizon_h), (float(horizon), horizon))
        self.assertIn(f"на одном горизонте {horizon} ч", twin["incident_text"])
        # Each option's whole stop and the restart lie inside it: the plan, begun now, begun a shift later.
        for start_h in ((sim.next_shift_at - sim.now) / 3600.0, 0.0, latest_h):
            self.assertLessEqual(start_h + hours + recovery, horizon + 1e-9)
        self.assertGreater(horizon, PARAMS["forecast"]["max_horizon_h"])  # longer than any ordinary forecast…
        self.assertLessEqual(horizon, PARAMS["forecast"]["planned_max_horizon_h"])   # …and accepted by the engine

    def test_options_are_ranked_by_the_rule_of_every_incident(self):
        sim, core = self.core_for({"start": "next-shift"})
        core.step()
        by_start = {o.get("start"): n for n, o in enumerate(core.options) if n}
        deltas = [(0, 0.0)] * len(core.options)
        deltas[by_start["now"]] = (17_000_000, 2.5)          # as measured: the mill back sooner on a full stockpile
        deltas[by_start["shift-after"]] = (-6_800_000, -8.0)
        deltas[by_start[None]] = (-133_000_000, -2.2)
        scripted(core, deltas)
        core.step()
        twin = core.twin()
        now = by_start["now"] + 1
        self.assertEqual((twin["analysis_status"], twin["recommended"]), ("ready", now))
        self.assertEqual(twin[f"option{now}_confidence"], "средняя")          # 17 / 4.6 standard errors
        self.assertIn("Окупается", twin[f"option{now}_reason"])
        self.assertFalse(twin[f"option{by_start['shift-after'] + 1}_recommendable"])
        self.assertIn(f"за {core.horizon_h} ч", twin["last_decision"])

    def test_a_running_reline_offers_only_the_pit(self):
        sim, core = self.core_for({"start": "now", "expected_h": 24})
        sent = core.step()
        self.assertEqual(core.twin()["incident"], "sag-reline:reline")
        self.assertEqual([o.get("start") for o in core.options], [None, None])
        self.assertEqual(core.options[1]["actions"][0]["subject"], "move-to-block")
        left = (sim.scenarios[self.NAME].expected_end - sim.now - 20) / 3600.0
        self.assertEqual({m[2]["horizon_h"] for m in sent}, {math.ceil(left + PARAMS["scenario"]["reline_recovery_h"])})

    def test_a_short_window_would_favour_the_later_start(self):
        """Why one horizon must hold the whole stop of every option: over the usual 12 h the stop of the plan
        begins inside the window and that of the later start does not, and the later start looks like tens of
        tonnes of copper more; over the horizon that holds both stops the gain is gone."""
        from mine.tests.test_forecast import wait
        sim = new_sim()
        rig = Coupled(sim)
        rig.run(30)
        sim.handle_message(self.NAME, "activate", {"params": {"start": "next-shift", "expected_h": 24}})
        later = [{"thing": f"{NS}:mill-sag", "subject": "plan-reline", "body": {"start": "shift-after"}}]
        latest_h = (sim.next_shift_at + 12 * 3600 - sim.now) / 3600.0
        full = math.ceil(latest_h + 24 + PARAMS["scenario"]["reline_recovery_h"])
        desk = ForecastDesk(sim, THINGS)
        desk.request(sim, {"slot": 1, "horizon_h": 12, "actions_b": later})
        desk.request(sim, {"slot": 10, "horizon_h": full, "actions_b": later})
        values = wait(desk, sim, (1, 10), limit_s=600)
        short, whole = values["forecast-1"], values["forecast-10"]
        print(f"\nreline a shift later: {short['delta_cu_t']:+.1f} t Cu over 12 h, {whole['delta_cu_t']:+.1f} t over {full} h")
        self.assertEqual((short["status"], whole["status"]), ("ready", "ready"))
        self.assertGreater(short["delta_cu_t"], 15.0)
        self.assertLess(whole["delta_cu_t"], 0.25 * short["delta_cu_t"])


class RelineEndToEnd(unittest.TestCase):
    def test_reline_ranked_applied_and_visible(self):
        """A reline planned for the next shift change (24 h here, to keep the test short): the options are forecast
        over one horizon, ranked by the usual rule, and «Применить» on «begin now» starts the stop."""
        stand = Stand()
        stand.sim.handle_message("scenario-sag-reline", "activate", {"params": {"start": "next-shift", "expected_h": 24}})
        started = time.monotonic()
        twin = stand.run_until_ranked(limit_s=600)
        horizon = twin["analysis_horizon_h"]
        print(f"\nadvisor, reline planned: ranked in {time.monotonic() - started:.1f} s over {horizon:.0f} h")
        for n in range(1, twin["options_count"] + 1):
            print(f"  {n}. {twin[f'option{n}_delta_kzt'] / 1e6:+8.1f} ± {twin[f'option{n}_gain_se_kzt'] / 1e6:.1f} млн тг "
                  f"{twin[f'option{n}_cu_delta_t']:+6.2f} т Cu (место {twin[f'option{n}_rank']}) {twin[f'option{n}_label']}")
            self.assertEqual(sum(twin[f"option{n}_split_{part}_kzt"] for part in MARGIN_SPLIT), twin[f"option{n}_delta_kzt"])
            slot = twin[f"option{n}_forecast"].split(":")[1]
            self.assertEqual(stand.desk.values()[slot]["horizon_h"], horizon)      # every option over the same horizon
        self.assertGreater(horizon, 24)
        now = next(n for n in range(2, twin["options_count"] + 1) if "start=now" in twin[f"option{n}_actions"])
        stand.deliver(stand.advisor.command("apply", {"option": now}))
        stand.run(2)
        self.assertEqual(stand.sim.plant.mill.state, "reline")
        self.assertIn("Советник: применён вариант", texts(stand.sim))
        self.assertEqual(stand.advisor.twin()["applied_option"], now)
        self.assertEqual(stand.advisor.twin()["incident"], "sag-reline:reline")     # the stop has begun: analysed anew
        stand.sim.handle_message("scenario-sag-reline", "cancel", {})


if __name__ == "__main__":
    unittest.main()
