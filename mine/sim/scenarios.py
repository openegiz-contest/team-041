"""What-if scenarios. Each one is a twin: it is started and cancelled by live
messages and reports its state and its effect through its own features.

    activate {target?, params?}     start; the target defaults to the one named in world.yaml
    cancel {}                       stop and return the mine to normal operation

A scenario changes the mine only through the subsystem it belongs to (a road
segment is closed, a loader breaks down, the crusher stops, it rains, a block
is fired, harder ore reaches the mill); everything further down the chain
follows from the model. Its twin shows up to four indicators along the
scenario's own chain of cause and effect (mine/sim/chain.py), each over equal
windows before the start and up to now, flagged when the change exceeds the
indicator's usual swing; says when the effect will reach the plant
(`effect_delay_text`); and lists the twins it is changing at the moment. The
older comparison rows (ore feed, truck cycle, margin, one indicator) are kept
for compatibility. Several scenarios can be active at once.
"""
from __future__ import annotations

import math
from typing import TYPE_CHECKING

from mine.sim.chain import Indicator, before, measure, sentence
from mine.sim.durations import causes as cause_table
from mine.sim.kpis import UNAVAILABLE_STATES
from mine.sim.text import money, num

if TYPE_CHECKING:
    from mine.sim.model import Simulation

BLEND_S = 900.0     # a rate measured since the start replaces the baseline over this time


def _short(thing_id: str) -> str:
    return thing_id.split(":", 1)[1] if ":" in thing_id else thing_id


def _change(before: float, after: float) -> str:
    """Relative change of the values as they are published, e.g. '+24 %'."""
    if before <= 0:
        return "нет данных"
    return f"{100 * (after - before) / before:+.0f} %"


def _hhmm(sim: "Simulation", at: float) -> str:
    return sim.sim_time(at)[11:16]


def _real(sim: "Simulation", model_h: float) -> str:
    """How long `model_h` simulated hours take on the stand at its present speed, in Russian."""
    seconds = model_h * 3600.0 / max(sim.speedup, 1)
    return f"{seconds / 60:.0f} мин" if seconds >= 90 else f"{seconds:.0f} с"


def _speed_hint(sim: "Simulation") -> str:
    faster = "ускорьте до ×60 (1 мин на час модели) или нажмите" if sim.speedup != 60 else "нажмите"
    return (f"Час модели идёт {_real(sim, 1.0)} при ×{sim.speedup}; {faster} «Прогноз на 12 ч», чтобы увидеть "
            f"итог сразу.")


def _stock_hours(sim: "Simulation") -> float:
    plant = sim.plant
    rate = plant.mill.setpoint_tph or float(sim.params["mill"]["design_tph"])
    return plant.stockpile.t / rate


STATUS_TITLES = {"insitu": "в массиве", "drilling": "бурится", "drilled": "обурен", "charged": "заряжен",
                 "blasted": "уже взорван", "mining": "в отработке", "depleted": "отработан"}

TRIP_CYCLE = Indicator("Время рейса", "мин", 1, "trip", "duration_s", scale=1 / 60.0)
ORE_TO_CRUSHER = Indicator("Руда, разгруженная в дробилку", "т/ч", 0, "rate", "to_crusher_t")
DIG_RATE = Indicator("Выемка горной массы", "т/ч", 0, "rate", "dug_t")
STOCK_HOURS = Indicator("Запас склада дроблёной руды", "ч питания", 1, "level", "stock_h")


class Scenario:
    """Common part of every scenario: the messages, the snapshot taken at the
    start, the rates measured since, and the features of the twin."""

    kpi_label = ""
    kpi_unit = ""
    kpi_digits = 1
    feed_label = "подача руды в дробилку"
    idle = "Сценарий не активен."

    def __init__(self, sim: "Simulation", thing: dict):
        self.sim = sim
        self.name = _short(thing["thingId"])
        self.title = thing["attributes"]["name"]
        links = thing["attributes"]["links"]
        self.targets = [_short(t) for t in links["targets"]]
        self.default_target = _short(links["default_target"])
        self.status = "inactive"
        self.target = ""
        self.started: float | None = None
        self.last_text = ""                     # what the last run came to, kept after it ends
        self.mark: dict = {}
        self._chain_cache: tuple | None = None
        self._before_memo: dict = {}            # the «before» side of the chain, kept once its window is full
        # The expected end (mine/sim/durations.py): what the supervisor is told, what every forecast assumes.
        self.causes = cause_table(sim.params).get(self.name, [])
        self.cause = self.causes[0] if self.causes else None
        self.expected_end: float | None = None
        self.basis = ""
        self.responses: list[str] = []          # what was done to end it sooner, in Russian
        self.overruns = 0

    # -- messages ---------------------------------------------------------------

    def activate(self, body: dict) -> None:
        sim = self.sim
        if self.status != "inactive":
            sim.refuse(self.name, f"Сценарий «{self.title}» уже активен, сначала отмените его")
            return
        requested = body.get("target") or self.pick_target()
        target = _short(requested) if isinstance(requested, str) else ""
        params = body.get("params") if isinstance(body.get("params"), dict) else {}
        problem = f"цель {requested!r} не подходит" if target not in self.targets else (
            self.check_duration(params) or self.check(target, params))
        if problem:
            sim.refuse(self.name, f"Сценарий «{self.title}» не запущен: {problem}")
            return
        self.take_mark()
        # The «before» side for the full window is fixed at the start: kept, it outlives the history it is read from.
        longest = float(sim.params["scenario"]["kpi_window_max_min"]) * 60.0
        self._before_memo = {"before": before(sim, self.chain(), sim.now, longest)}
        self.status, self.target, self.started = "active", target, sim.now
        self.plan(params)
        self.start(target, params)

    def cancel(self) -> None:
        if self.status != "active":
            what = "уже отменяется" if self.status == "cancelling" else "не активен, отменять нечего"
            self.sim.refuse(self.name, f"Сценарий «{self.title}» {what}")
            return
        self.stop()

    def finish(self, text: str = "") -> None:
        self.last_text = text
        self.status, self.target, self.started = "inactive", "", None
        self.expected_end = None
        self.on_expected()

    # -- the expected end -----------------------------------------------------------

    def check_duration(self, params: dict) -> str | None:
        """`params.cause` must be one of the scenario's causes, `params.expected_h` a duration it may take."""
        cause, hours = params.get("cause"), params.get("expected_h")
        if cause is not None and cause not in [c["id"] for c in self.causes]:
            known = ", ".join(c["id"] for c in self.causes) or "нет"
            return f"причина {cause!r} не подходит (возможны: {known})"
        if hours is not None and (isinstance(hours, bool) or not isinstance(hours, (int, float)) or not 0.25 <= hours <= 24):
            return f"ожидаемая длительность {hours!r} ч вне диапазона 0,25–24 ч"
        return None

    def plan(self, params: dict) -> None:
        """The expected end from the cause: its typical duration, or the one given at the start."""
        self.responses, self.overruns = [], 0
        if not self.causes:
            self.expected_end, self.basis = None, ""
            return
        self.cause = next((c for c in self.causes if c["id"] == params.get("cause")), self.causes[0])
        hours, self.basis = self.cause["hours"], self.cause["basis"]
        if params.get("expected_h") is not None:
            hours = float(params["expected_h"])
            self.basis = f"задано при запуске: {num(hours)} ч ({self.cause['title']})"
        self.expected_end = self.sim.now + hours * 3600.0
        self.on_expected()

    def on_expected(self) -> None:
        """The expected end changed: the twins that show it (the crusher's restart) follow."""

    def shorten(self, factor: float, what: str) -> None:
        """A resource shortens the rest of the incident (a second crew on the repair)."""
        sim = self.sim
        old = self.expected_end
        if old is None:
            return
        self.expected_end = sim.now + max(0.0, old - sim.now) * factor
        self.responses.append(f"{what} с {_hhmm(sim, sim.now)}: окончание {_hhmm(sim, old)} → {_hhmm(sim, self.expected_end)}")
        self.on_expected()

    def note_response(self, text: str) -> None:
        self.responses.append(text)

    def end_expected(self) -> None:
        """A forecast reached the expected end: the incident is over there (on the stand the operator ends it)."""
        self.sim.log("info", self.name, f"«{self.title}»: ожидаемое окончание {_hhmm(self.sim, self.expected_end)}")
        self.stop()

    def overrun(self) -> None:
        """On the stand the expected end has passed and the scenario still runs: the repair takes longer than
        expected, and the estimate moves on by a share of the original duration."""
        sim = self.sim
        hours = (self.cause or {}).get("hours", 1.0) * float(sim.params["scenario"]["overrun_extend_share"])
        self.expected_end = sim.now + hours * 3600.0
        self.overruns += 1
        self.responses.append(f"ремонт затянулся, новая оценка — до {_hhmm(sim, self.expected_end)}")
        sim.log("warning", self.name, f"«{self.title}»: ожидаемое окончание прошло, сценарий продолжается; новая оценка — "
                                      f"до {_hhmm(sim, self.expected_end)}")
        self.on_expected()

    def expected_values(self) -> dict:
        sim = self.sim
        running = self.status != "inactive"
        default = self.causes[0] if self.causes else None
        end = self.expected_end if running else None
        if end is not None and self.started is not None:
            duration = (end - self.started) / 3600.0
        else:
            duration = default["hours"] if default and not running else 0.0
        basis = self.basis if running else (default["basis"] if default else "")
        return {
            "expected_end_sim_time": sim.sim_time(end) if end is not None else "",
            "expected_duration_h": round(duration, 1),
            "expected_remaining_h": round(max(0.0, end - sim.now) / 3600.0, 1) if end is not None and self.status == "active" else 0.0,
            "duration_basis": basis,
            "response_text": "; ".join(self.responses) if running else "",
            "response_cost_kzt": round(sim.response.cost_for(scenario=self.name, since=self.started or 0.0)) if running else 0,
        }

    # -- for the subclasses -------------------------------------------------------

    def pick_target(self) -> str:
        return self.default_target

    def check(self, target: str, params: dict) -> str | None:
        """A reason not to start, or None."""
        return None

    def start(self, target: str, params: dict) -> None:
        raise NotImplementedError

    def stop(self) -> None:
        raise NotImplementedError

    def step(self) -> None:
        """Called once per simulated second while the scenario is not inactive."""

    def kpi_now(self) -> float:
        return 0.0

    def kpi_current(self) -> float:
        return self.kpi_now()

    def affected(self) -> list[str]:
        return [self.target]

    def cancel_remaining_s(self) -> float:
        """Simulated seconds until a scenario that is cancelling has finished; 0 for any other."""
        return 0.0

    def text(self, v: dict) -> str:
        return ""

    def chain(self) -> list[Indicator]:
        """The indicators along the scenario's chain of cause and effect, first link first."""
        return []

    def delay_text(self) -> str:
        """When the effect reaches the plant, in model hours, in Russian."""
        return _speed_hint(self.sim)

    # A scenario that changes the routes (a road closure): its trip indicators count only the trips begun after
    # the start and are forecast from the routes until enough of those have finished (chain.measure).
    route_based = False

    def expected(self, ind: Indicator, before: float | None) -> float | None:
        return None

    def chain_values(self) -> dict:
        """`measure` of the chain, computed again only when the history has a new sample
        (for an inactive scenario every fifth one: its figures only describe the last hour)."""
        h = self.sim.kpis.history
        samples = len(h.times) // (1 if self.status != "inactive" else 5)
        key = (samples, h.times[-1] if h.times and self.status != "inactive" else 0, self.status, self.started)
        if self._chain_cache is None or self._chain_cache[0] != key:
            self._chain_cache = (key, measure(self.sim, self.chain(), self.started if self.status != "inactive" else None,
                                              self.expected if self.route_based else None, self._before_memo))
        return self._chain_cache[1]

    # -- measurements -----------------------------------------------------------

    def feed_live(self) -> float:
        """Ore passing the crusher, t/h, over the KPI window."""
        return self.sim.kpis.crusher_feed.per_hour(self.sim.now)

    def feed_total(self) -> float:
        return self.sim.plant.crusher.total_t

    def cycle_live(self) -> float:
        """Mean truck cycle, s: measured over the KPI window, else forecast from the routes."""
        return self.sim.kpis.measured_cycle_s(minimum=4) or self.sim.fleet.forecast()[0] or 0.0

    def take_mark(self) -> None:
        """Remember the mine as it is just before the scenario changes it."""
        kpis = self.sim.kpis
        self.mark = {"feed": self.feed_live(), "feed_total": self.feed_total(), "cycle": self.cycle_live(),
                     "kpi": self.kpi_now(), "diesel": kpis.diesel_l, "hauled": kpis.hauled_t,
                     "dug": kpis.ore_mined_t + kpis.waste_mined_t, "crushed_cu": self.sim.plant.crusher.total_cu,
                     "margin": self.margin_live(), "margin_total": kpis.margin_total_kzt()}

    def elapsed_s(self) -> float:
        return self.sim.now - self.started if self.started is not None else 0.0

    def rate_since(self, total_now: float, total_then: float, baseline_tph: float) -> float:
        """Mean hourly rate since the start. Over the first minutes it is blended
        with the baseline, because a rate over a few minutes is one truck more or less."""
        elapsed = self.elapsed_s()
        amount = total_now - total_then
        if elapsed >= BLEND_S:
            return amount / elapsed * 3600.0
        return (baseline_tph * (BLEND_S - elapsed) / 3600.0 + amount) / BLEND_S * 3600.0

    def feed_current(self) -> float:
        return self.rate_since(self.feed_total(), self.mark["feed_total"], self.mark["feed"])

    def cycle_current(self) -> float:
        measured = self.sim.kpis.measured_cycle_s(started_after=self.started, minimum=3)
        return measured or self.mark["cycle"]

    def margin_live(self) -> float:
        """Variable margin of the mine, KZT/h, over the KPI window."""
        return float(self.sim.kpis.money()["margin_kzt_h"])

    def margin_current(self) -> float:
        """Variable margin per hour since the start, blended with the baseline at first like the feed."""
        return self.rate_since(self.sim.kpis.margin_total_kzt(), self.mark["margin_total"], self.mark["margin"])

    # -- published ----------------------------------------------------------------

    def values(self) -> dict:
        sim = self.sim
        if self.status == "inactive":
            feed, cycle, kpi, margin = self.feed_live(), self.cycle_live(), self.kpi_now(), self.margin_live()
            v = {"feed0": feed, "feed": feed, "cycle0": cycle, "cycle": cycle, "kpi0": kpi, "kpi": kpi,
                 "margin0": margin, "margin": margin}
            text, affected = (f"{self.idle} Последний запуск: {self.last_text}" if self.last_text else self.idle), []
        chain = self.chain_values()
        if self.status != "inactive":
            v = {"feed0": self.mark["feed"], "feed": self.feed_current(), "cycle0": self.mark["cycle"],
                 "cycle": self.cycle_current(), "kpi0": self.mark["kpi"], "kpi": self.kpi_current(),
                 "margin0": self.mark["margin"], "margin": self.margin_current()}
            text, affected = (f"{self.text(v)} {sentence(chain['rows'], chain['active'], chain['note'], chain['trips_after_start'])}",
                              self.affected())
        digits = self.kpi_digits
        out = {
            "status": self.status,
            "target": sim.tid(self.target) if self.target else "",
            "started_sim_time": sim.sim_time(self.started) if self.started is not None else "",
            "elapsed_min": round(self.elapsed_s() / 60.0, 1),
            # A cancel that takes time (wet roads drying, hard ore passing): model minutes until the mine is back.
            "cancel_remaining_min": round(self.cancel_remaining_s() / 60.0, 1) if self.status == "cancelling" else 0.0,
            "baseline_feed_tph": round(v["feed0"]),
            "current_feed_tph": round(v["feed"]),
            "baseline_cycle_min": round(v["cycle0"] / 60.0, 1),
            "current_cycle_min": round(v["cycle"] / 60.0, 1),
            "baseline_margin_kzt_h": round(v["margin0"]),
            "current_margin_kzt_h": round(v["margin"]),
            "kpi_label": self.kpi_label,
            "kpi_unit": self.kpi_unit,
            "kpi_baseline": round(float(v["kpi0"]), digits),
            "kpi_current": round(float(v["kpi"]), digits),
            **chain["features"],
            "effect_delay_text": self.delay_text(),
            **self.expected_values(),
            "affected": ",".join(dict.fromkeys(a for a in affected if a)),
            "impact_text": text,
        }
        return out


class RoadClosure(Scenario):
    """Close one road segment until cancelled.

    Trucks already on the segment drive it to the end; every other truck
    chooses its route again at the next node it reaches. The scenario twin
    compares the cycle time and ore feed the open roads allowed with the
    current ones: a forecast for the changed roads at first, then the change
    in the trips actually driven since the closure.
    """

    kpi_label = "Путь с грузом до дробилки"
    kpi_unit = "км"
    kpi_digits = 2
    idle = "Сценарий не активен: участки дорог открыты."

    def __init__(self, sim: "Simulation", thing: dict):
        super().__init__(sim, thing)
        self.measured_before_s: float | None = None

    def kpi_now(self) -> float:
        """Mean loaded haul from the ore faces to the crusher over the fastest open routes, km."""
        sim = self.sim
        loaded = sim.fleet.cost(True)
        lengths = []
        for loader in sim.fleet.loaders.values():
            if loader.block is None or sim.pit.blocks[loader.block].material == "waste":
                continue
            route = sim.roads.route(loader.node, sim.plant.crusher.node, loaded)
            if route is not None:
                lengths.append(sim.roads.route_length(route))
        return sum(lengths) / len(lengths) / 1000.0 if lengths else 0.0

    def take_mark(self) -> None:
        # Taken before the road state changes. The baseline is the cycle the
        # open roads allow, not the recent average, which an earlier closure
        # would still weigh on.
        super().take_mark()
        cycle_s, feed = self.sim.fleet.forecast()
        self.mark["cycle"], self.mark["feed"] = cycle_s or 0.0, feed
        self.measured_before_s = self.sim.kpis.measured_cycle_s(minimum=6)

    def start(self, target: str, params: dict) -> None:
        sim = self.sim
        segment = sim.roads.segments[target]
        self.full_end = None
        sim.roads.close(segment, self.name)
        sim.fleet.roads_changed()
        sim.log("alarm", target, f"Участок «{segment.title}» закрыт (сценарий «{self.title}»)")

    def stop(self) -> None:
        sim = self.sim
        segment = sim.roads.segments[self.target]
        sim.roads.reopen(segment, self.name)
        closed, full = self.target, self.full_end
        self.full_end = None
        restricted = ""
        if full is not None and full > sim.now:
            # Opened after an emergency repair: one lane on a temporary topping until the full repair is due.
            kmh = float(sim.params["response"]["repaired_kmh"])
            sim.response.cap_until(segment, full, kmh)
            restricted = f" с ограничением {num(kmh)} км/ч до {_hhmm(sim, full)} (аварийный ремонт)"
        self.finish()
        sim.fleet.roads_changed()
        sim.log("info", closed, f"Участок «{segment.title}» открыт{restricted}, сценарий «{self.title}» отменён")

    full_end: float | None = None               # the full repair, when an emergency crew opens the road before it

    def repair_now(self, at: float, what: str) -> None:
        """An emergency crew opens the segment at `at`, before the full repair is due."""
        sim = self.sim
        if self.expected_end is None or at >= self.expected_end:
            return
        self.full_end = self.expected_end
        self.expected_end = at
        self.responses.append(f"{what} с {_hhmm(sim, sim.now)}: участок откроют в {_hhmm(sim, at)} вместо "
                              f"{_hhmm(sim, self.full_end)}, до него — с ограничением скорости")

    def affected(self) -> list[str]:
        """The segment and the trucks that are driving another way because of it."""
        sim, fleet = self.sim, self.sim.fleet
        names = [self.target]
        for truck in fleet.trucks:
            if not truck.route or truck.goal is None or truck.state in UNAVAILABLE_STATES or truck.waiting_route:
                continue
            free = sim.roads.route(truck.route[0], truck.route[-1], fleet.cost(truck.payload_t > 1.0),
                                   frozenset({self.name}))
            if free is not None and free != truck.route:
                names.append(truck.name)
        return names

    def values(self) -> dict:
        if self.status == "inactive":
            # The forecast for the roads as they are: the same figures the scenario starts from.
            cycle_s, feed = self.sim.fleet.forecast()
            out = super().values()
            out.update(baseline_feed_tph=round(feed), current_feed_tph=round(feed),
                       baseline_cycle_min=round((cycle_s or 0.0) / 60, 1), current_cycle_min=round((cycle_s or 0.0) / 60, 1))
            return out
        return super().values()

    route_based = True

    def expected(self, ind: Indicator, before: float | None) -> float | None:
        """An indicator «now» as the open routes give it: the value before, times the ratio of the route-based
        figure now to the same figure at the start (the loaded haul to the crusher, the expected cycle, the ore
        the fleet can bring to the crusher); the route-based figure itself when there is no value before."""
        if ind.key == "loaded_m":
            now, then = self.kpi_now(), self.mark["kpi"]
        elif ind.key == "duration_s":
            now, then = (self.sim.fleet.forecast()[0] or 0.0) / 60.0, self.mark["cycle"] / 60.0
        elif ind.key == "to_crusher_t":
            now, then = self.sim.fleet.forecast()[1], self.mark["feed"]     # ore the fleet can bring, t/h
        else:
            return None
        if before is not None and then > 0:
            return before * now / then
        return now

    def feed_current(self) -> float:
        return self.sim.fleet.forecast()[1]

    def cycle_current(self) -> float:
        forecast_s, _ = self.sim.fleet.forecast()
        if forecast_s is None:
            return 0.0
        measured_s = self.sim.kpis.measured_cycle_s(started_after=self.started, minimum=3)
        if measured_s and self.measured_before_s:
            return self.mark["cycle"] * measured_s / self.measured_before_s
        return forecast_s

    def text(self, v: dict) -> str:
        segment = self.sim.roads.segments[self.target]
        if self.sim.fleet.forecast()[0] is None:
            return f"Закрыт участок «{segment.title}»: часть самосвалов осталась без проезда."
        return (f"Закрыт участок «{segment.title}», самосвалы едут в объезд: кратчайший открытый путь с грузом от "
                f"рудных забоев до дробилки {num(v['kpi0'], 2)} → {num(v['kpi'], 2)} км.")

    def chain(self) -> list[Indicator]:
        return [Indicator("Путь с грузом, среднее по рейсам", "км", 2, "trip", "loaded_m", scale=0.001), TRIP_CYCLE,
                ORE_TO_CRUSHER, STOCK_HOURS]

    def delay_text(self) -> str:
        return (f"Рейсы удлиняются сразу. На фабрике закрытие скажется, только если руды в дробилку станет приходить "
                f"меньше, чем берёт мельница: склад дроблёной руды держит {num(_stock_hours(self.sim))} ч питания, "
                f"так что питание мельницы изменится не раньше чем через несколько часов модели. {_speed_hint(self.sim)}")


class ShovelFailure(Scenario):
    """Break a loader down until cancelled. Its trucks go to the other
    loaders (by the fallback rule of the shift, or where the dispatch module
    sends them), queues grow there, less rock is dug and the mix of ore
    reaching the crusher changes."""

    kpi_label = "Выемка горной массы"
    kpi_unit = "т/ч"
    kpi_digits = 0
    idle = "Сценарий не активен: экскаваторы работают."

    def __init__(self, sim: "Simulation", thing: dict):
        super().__init__(sim, thing)
        self.crew: list[str] = []

    def kpi_now(self) -> float:
        return self.sim.kpis.dug.per_hour(self.sim.now)

    def kpi_current(self) -> float:
        kpis = self.sim.kpis
        return self.rate_since(kpis.ore_mined_t + kpis.waste_mined_t, self.mark["dug"], self.mark["kpi"])

    def take_mark(self) -> None:
        super().take_mark()
        kpis, now = self.sim.kpis, self.sim.now
        feed = kpis.crusher_feed.total(now)
        self.mark["grade"] = 100.0 * kpis.crusher_cu.total(now) / feed if feed > 0 else 0.0

    def start(self, target: str, params: dict) -> None:
        sim = self.sim
        loader = sim.fleet.loaders[target]
        self.crew = [t.name for t in sim.fleet.trucks if (t.loader_target or t.home_loader) == target]
        sim.reliability.force_down(loader, self.name)
        sim.log("alarm", target, f"{loader.title}: отказ (сценарий «{self.title}»), "
                                 f"его самосвалы ({len(self.crew)}) уходят к другим экскаваторам")

    def stop(self) -> None:
        sim = self.sim
        loader = sim.fleet.loaders[self.target]
        sim.reliability.force_up(loader)
        sim.log("info", self.target, f"{loader.title}: ремонт окончен, сценарий «{self.title}» отменён")
        self.finish()

    def _moved(self) -> list:
        fleet = self.sim.fleet
        return [t for t in fleet.trucks if t.name in self.crew and t.loader_target != self.target
                and t.state not in UNAVAILABLE_STATES]

    def affected(self) -> list[str]:
        moved = self._moved()
        return [self.target, *(t.name for t in moved), *sorted({t.loader_target for t in moved})]

    def text(self, v: dict) -> str:
        sim, fleet = self.sim, self.sim.fleet
        loader = fleet.loaders[self.target]
        moved = self._moved()
        others = [l for l in fleet.loaders.values() if l is not loader]
        queues = ", ".join(f"{l.title.split()[-1]}: {len(l.queue)}" for l in others)
        crushed = sim.plant.crusher.total_t - self.mark["feed_total"]
        grade = 100.0 * (sim.plant.crusher.total_cu - self.mark["crushed_cu"]) / crushed if crushed > 500 else self.mark["grade"]
        by = "модуль диспетчеризации" if any(t.source == "dispatch" for t in moved) else "правило смены"
        return (f"{loader.title} в ремонте {self.elapsed_s() / 60:.0f} мин. Переведено самосвалов: {len(moved)} ({by}); "
                f"очередь у экскаваторов {queues}.")

    def chain(self) -> list[Indicator]:
        return [DIG_RATE, Indicator("Очередь на рейс", "мин", 1, "trip", "queue_s", scale=1 / 60.0), ORE_TO_CRUSHER,
                Indicator("Содержание меди в питании дробилки", "% Cu", 3, "ratio", "crushed_cu", per="crushed_t", scale=100.0)]

    def delay_text(self) -> str:
        return (f"Выемка и очереди меняются сразу; содержание меди в питании дробилки — с первыми рейсами от других забоев. "
                f"Мельница почувствует отказ, только если руды станет приходить меньше, чем она берёт: склад дроблёной "
                f"руды держит {num(_stock_hours(self.sim))} ч питания. {_speed_hint(self.sim)}")


class CrusherStop(Scenario):
    """Stop the primary crusher until cancelled. The first trucks wait at it,
    the following ones tip on the ROM pads; the mill runs the coarse-ore
    stockpile down and stops when it is empty."""

    kpi_label = "Запас руды на складе в часах питания мельницы"
    kpi_unit = "ч"
    idle = "Сценарий не активен: дробилка работает."

    def __init__(self, sim: "Simulation", thing: dict):
        super().__init__(sim, thing)
        self.mill_stopped_at: float | None = None

    def kpi_now(self) -> float:
        plant = self.sim.plant
        rate = plant.mill.feed_tph if plant.mill.feed_tph > 0 else plant.mill.setpoint_tph
        return plant.stockpile.t / rate if rate > 0 else 0.0

    def take_mark(self) -> None:
        super().take_mark()
        plant = self.sim.plant
        self.mark.update(level=plant.stockpile.t, pads=sum(p.stock.t for p in plant.rom_pads.values()),
                         stops=plant.mill.stops)
        self.mill_stopped_at = None

    def start(self, target: str, params: dict) -> None:
        sim = self.sim
        sim.plant.crusher.stop(self.name)
        sim.log("alarm", target, f"{sim.plant.crusher.title} остановлена (сценарий «{self.title}»): самосвалы ждут, "
                                 f"затем везут руду на рудные склады")

    def stop(self) -> None:
        sim = self.sim
        sim.plant.crusher.start()
        sim.log("info", self.target, f"{sim.plant.crusher.title} запущена, сценарий «{self.title}» отменён")
        self.finish()

    def on_expected(self) -> None:
        # The crusher's twin shows when it is expected back: the plant module plans the stockpile up to then.
        crusher = self.sim.plant.crusher
        crusher.restart_at = self.expected_end if self.status != "inactive" else None

    def step(self) -> None:
        mill = self.sim.plant.mill
        if self.mill_stopped_at is None and mill.stops > self.mark["stops"] and mill.state == "idle":
            self.mill_stopped_at = self.sim.now

    def feed_current(self) -> float:
        return 0.0

    def _diverted(self) -> list:
        return [t for t in self.sim.fleet.trucks if t.source == "overflow" or (t.dump_target in self.sim.plant.rom_pads
                                                                               and t.state in ("hauling", "queue_dump", "dumping"))]

    def affected(self) -> list[str]:
        plant = self.sim.plant
        names = [self.target, "conveyor", plant.stockpile_name]
        if plant.mill.setpoint_source != "constant" or plant.mill.state != "running":
            names += [plant.mill.name, "flotation"]
        names += [t.name for t in plant.crusher.queue] + [t.name for t in self._diverted()]
        names += [name for name, pad in plant.rom_pads.items() if pad.queue or pad.truck]
        return names

    def text(self, v: dict) -> str:
        sim, plant = self.sim, self.sim.plant
        mill = plant.mill
        to_pads = max(0.0, sum(p.stock.t for p in plant.rom_pads.values()) - self.mark["pads"])
        if self.mill_stopped_at is not None:
            mill_text = f"Мельница остановлена в {_hhmm(sim, self.mill_stopped_at)}: склад пуст"
        elif mill.setpoint_source != "constant":
            mill_text = (f"Мельница работает, уставка {mill.setpoint_tph:.0f} т/ч (модуль управления фабрикой), "
                         f"руды хватит на {num(v['kpi'])} ч")
        else:
            mill_text = f"Мельница работает на постоянной уставке {mill.setpoint_tph:.0f} т/ч, руды хватит на {num(v['kpi'])} ч"
        return (f"Дробилка стоит {self.elapsed_s() / 60:.0f} мин. В очереди у дробилки {len(plant.crusher.queue)} самосвалов, "
                f"на рудные склады ушло {to_pads:.0f} т. Склад дроблёной руды {self.mark['level']:.0f} → {plant.stockpile.t:.0f} т. "
                f"{mill_text}.")

    def chain(self) -> list[Indicator]:
        return [Indicator("Производительность дробилки", "т/ч", 0, "rate", "crushed_t"),
                Indicator("Руда на рудные склады", "т/ч", 0, "rate", "to_rom_t"), STOCK_HOURS,
                Indicator("Питание мельницы", "т/ч", 0, "rate", "milled_t")]

    def delay_text(self) -> str:
        sim, mill = self.sim, self.sim.plant.mill
        hours = _stock_hours(sim)
        if self.mill_stopped_at is not None:
            return f"Мельница уже остановлена: склад пуст. {_speed_hint(sim)}"
        at60 = f", {hours:.0f} мин при ×60" if sim.speedup != 60 else ""
        head = (f"Дробилка и руда на рудные склады меняются сразу. Мельница работает со склада: при нынешней подаче "
                f"{mill.setpoint_tph:.0f} т/ч руды хватит на {num(hours)} ч модели ({_real(sim, hours)} стенда при "
                f"×{sim.speedup}{at60})")
        if mill.setpoint_source == "plant-control":
            tail = (", но модуль управления фабрикой снижает подачу по мере того, как склад пустеет (не ниже "
                    f"{float(sim.params['mill']['min_feed_factor']) * float(sim.params['mill']['design_tph']):.0f} т/ч), "
                    "и склада хватает дольше; когда остановится мельница, покажет «Прогноз на 12 ч»")
        else:
            tail = ", потом мельница остановится"
        return f"{head}{tail}.{self._cover_text(hours)} {_speed_hint(sim)}"

    def _cover_text(self, stock_h: float) -> str:
        """Whether the stockpile covers the repair as it is expected now, in one sentence."""
        sim = self.sim
        if self.status != "active" or self.expected_end is None:
            return ""
        left_h = max(0.0, self.expected_end - sim.now) / 3600.0
        when = f"Пуск дробилки ожидается в {_hhmm(sim, self.expected_end)}, через {num(left_h)} ч"
        if stock_h >= left_h:
            return f" {when}: склада хватает до пуска при полной подаче."
        return (f" {when}: склада при нынешней подаче не хватит на {num(left_h - stock_h)} ч — мельнице придётся "
                f"снизить подачу или встать, если ремонт не ускорить.")


class HeavyRain(Scenario):
    """Rain until cancelled: every road is wet (lower speed limits, more
    rolling resistance) and the steepest ramp is closed. After the cancel the
    roads stay wet for the drying time; the twin is `cancelling` meanwhile."""

    kpi_label = "Расход дизельного топлива на тонну"
    kpi_unit = "л/т"
    kpi_digits = 3
    idle = "Сценарий не активен: сухо."

    def __init__(self, sim: "Simulation", thing: dict):
        super().__init__(sim, thing)
        self.closed: list = []

    def kpi_now(self) -> float:
        kpis, now = self.sim.kpis, self.sim.now
        hauled = kpis.hauled.total(now)
        return kpis.diesel.total(now) / hauled if hauled > 0 else 0.0

    def kpi_current(self) -> float:
        kpis = self.sim.kpis
        hauled = kpis.hauled_t - self.mark["hauled"]
        litres = kpis.diesel_l - self.mark["diesel"]
        # Litres per tonne over a few trips is noise: until twenty loads have been
        # tipped, the missing tonnes count at the baseline rate.
        full = 20 * self.sim.params["truck"]["payload_t"]
        if hauled >= full:
            return litres / hauled
        return (litres + self.mark["kpi"] * (full - hauled)) / full

    def check(self, target: str, params: dict) -> str | None:
        p = self.sim.params["weather"]
        rain = params.get("rain_mm_h", p["rain_heavy_mm_h"])
        if isinstance(rain, bool) or not isinstance(rain, (int, float)) or not 1 <= rain <= p["rain_heavy_max_mm_h"]:
            return f"интенсивность осадков {rain!r} вне диапазона 1–{p['rain_heavy_max_mm_h']:.0f} мм/ч"
        return None

    def start(self, target: str, params: dict) -> None:
        sim, road = self.sim, self.sim.params["road"]
        sim.weather.set_heavy(True, float(params.get("rain_mm_h", sim.params["weather"]["rain_heavy_mm_h"])))
        self.closed = [s for s in sim.roads.segments.values()
                       if s.road_class == "ramp" and abs(s.grade_pct) >= road["wet_closed_grade_pct"]]
        for segment in self.closed:
            sim.roads.close(segment, self.name)
            sim.log("alarm", segment.name, f"Участок «{segment.title}» закрыт: уклон {num(abs(segment.grade_pct))} % "
                                           f"слишком крут для мокрой дороги")
        sim.fleet.roads_changed()

    def stop(self) -> None:
        self.sim.weather.set_heavy(False)
        self.status = "cancelling"

    def step(self) -> None:
        sim = self.sim
        # The closures end when the roads have dried, or when ordinary precipitation takes over (it closes no ramp).
        if self.status != "cancelling" or (sim.weather.condition == "dry" and sim.weather.wet):
            return
        for segment in self.closed:
            sim.roads.reopen(segment, self.name)
            sim.log("info", segment.name, f"Участок «{segment.title}» открыт: дорога просохла")
        self.closed = []
        sim.fleet.roads_changed()
        self.finish()

    def open_treated(self, segment, kmh: float) -> None:
        """A grader and a topping made the closed ramp passable: it opens at a restricted speed while wet."""
        sim = self.sim
        if segment in self.closed:
            self.closed.remove(segment)
        sim.roads.reopen(segment, self.name)
        sim.roads.set_cap(segment, kmh)
        sim.fleet.roads_changed()
        sim.log("info", segment.name, f"Участок «{segment.title}» открыт после грейдера и подсыпки с ограничением "
                                      f"{num(kmh)} км/ч, пока дорога мокрая")

    def affected(self) -> list[str]:
        return ["weather", *self.sim.roads.segments]

    def cancel_remaining_s(self) -> float:
        dry_at = self.sim.weather.dry_at
        return max(0.0, dry_at - self.sim.now) if dry_at is not None else 0.0

    def text(self, v: dict) -> str:
        sim = self.sim
        closed = ", ".join(f"«{s.title}»" for s in self.closed) or "нет"
        limits = sorted({s.speed_limit_kmh for s in sim.roads.segments.values()})
        if self.status == "active":
            state = f"Сильный дождь с мокрым снегом, {sim.weather.rain_mm_h:.0f} мм/ч"
        elif sim.weather.dry_at is not None:
            state = (f"Сильные осадки прекратились, дороги просохнут к {_hhmm(sim, sim.weather.dry_at)} "
                     f"(через {self.cancel_remaining_s() / 60:.0f} мин модели, {_real(sim, self.cancel_remaining_s() / 3600)} стенда)")
        else:
            state = "Сильные осадки прекратились"
        return (f"{state}: все {len(sim.roads.segments)} участков мокрые, ограничения скорости {num(limits[0])}–{num(limits[-1])} км/ч, "
                f"закрыт крутой съезд: {closed}.")

    def chain(self) -> list[Indicator]:
        # The trip without waiting: with a spare fleet and a full crusher, slower driving first shortens the queues.
        return [Indicator("Среднее ограничение скорости на дорогах", "км/ч", 1, "level", "limit"),
                Indicator("Время рейса без ожидания", "мин", 1, "trip", "travel_s", scale=1 / 60.0),
                Indicator("Расход дизельного топлива на тонну", "л/т", 3, "ratio", "diesel_l", per="hauled_t"),
                ORE_TO_CRUSHER]

    def delay_text(self) -> str:
        return (f"Ограничения скорости и расход топлива меняются сразу, время рейса — с первыми рейсами под дождём. Фабрика "
                f"почувствует дождь, только если руды в дробилку станет приходить меньше, чем берёт мельница: склад "
                f"дроблёной руды держит {num(_stock_hours(self.sim))} ч питания. {_speed_hint(self.sim)}")


class Blast(Scenario):
    """Fire one block outside the blast plan. The scenario runs by itself:
    exclusion zone and clearance, the blast, the re-entry wait, the all-clear.

    Cancelling it before the blast calls the blast off (the zone is lifted,
    the block stays charged); cancelling it after the blast lifts the zone
    without waiting for the re-entry time. The blasted block stays blasted.
    """

    kpi_label = "Взорванная горная масса в забоях"
    kpi_unit = "т"
    kpi_digits = 0
    idle = "Сценарий не активен: взрывы идут по графику."

    def __init__(self, sim: "Simulation", thing: dict):
        super().__init__(sim, thing)
        self.fired_at: float | None = None

    def kpi_now(self) -> float:
        return sum(b.tonnes for b in self.sim.pit.blocks.values() if b.status in ("blasted", "mining"))

    def pick_target(self) -> str:
        """Without a target: a block in place whose zone both crosses a road
        and holds a working machine, so that the whole chain can be seen;
        among those a charged block before a drilled one before one that is
        still in situ, and the one nearest to a working face first."""
        valid = self.valid_targets()
        return valid[0] if valid else self.default_target

    def valid_targets(self) -> list[str]:
        """Blocks that can be fired now: drilled or charged, none in the way of a loader. A block whose zone
        both crosses a road and holds a working machine comes first, so that the whole chain can be seen;
        then a charged block before a drilled one, the one nearest to a working face first."""
        sim, pit, p = self.sim, self.sim.pit, self.sim.params["blast"]
        machines = [l.pos for l in sim.fleet.loaders.values() if l.block and l.state != "down"]
        machines += [r.pos for r in pit.rigs if r.block]
        rank = {"charged": 0, "drilled": 1}
        dug = {l.block for l in sim.fleet.loaders.values()}

        def key(block):
            nearest = min((math.dist(block.centre, m) for m in machines), default=0.0)
            road = any(s.distance_to(*block.centre) < p["exclusion_radius_m"] for s in sim.roads.segments.values())
            return (not (road and nearest < p["equipment_withdrawal_m"]), rank[block.status], nearest, block.name)

        blocks = [b for b in pit.blocks.values() if b.status in rank and b.name not in dug]
        return [b.name for b in sorted(blocks, key=key)]

    def check(self, target: str, params: dict) -> str | None:
        pit = self.sim.pit
        block = pit.blocks[target]
        if pit.blast_phase != "idle":
            return f"уже идёт взрыв блока {pit.blast_block.title}"
        if target not in self.valid_targets():
            valid = ", ".join(pit.blocks[b].title for b in self.valid_targets()[:4]) or "таких сейчас нет"
            return (f"{block.title} {STATUS_TITLES.get(block.status, block.status)}: взорвать можно только обуренный "
                    f"или заряженный блок ({valid})")
        return None

    def values(self) -> dict:
        out = super().values()
        out["valid_targets"] = ",".join(self.sim.tid(b) for b in self.valid_targets())
        return out

    def start(self, target: str, params: dict) -> None:
        self.fired_at = None
        self.sim.pit.blast_now(self.sim.pit.blocks[target], self.name)

    def stop(self) -> None:
        pit = self.sim.pit
        if pit.blast_by != self.name:
            self.finish()
        elif pit.blast_phase == "clearing":
            pit.abort_blast()
        else:
            pit.lift_zone()
        # `step` sees the zone gone and closes the scenario with the right summary.
        self.step()

    def step(self) -> None:
        sim, pit = self.sim, self.sim.pit
        if self.status == "inactive":
            return
        block = pit.blocks[self.target]
        if pit.blast_by == self.name:
            if self.fired_at is None and block.status == "blasted":
                self.fired_at = sim.now
            return
        if self.fired_at is None and block.status == "blasted":
            self.fired_at = sim.now
        if self.fired_at is not None:
            self.finish(f"{block.title} взорван в {_hhmm(sim, self.fired_at)}: {block.tonnes_initial:.0f} т, "
                        f"P80 {block.frag_p80_mm:.0f} мм; зона снята в {_hhmm(sim, sim.now)}.")
        else:
            self.finish(f"взрыв блока {block.title} не состоялся, блок остался заряженным.")

    def affected(self) -> list[str]:
        sim, pit, fleet = self.sim, self.sim.pit, self.sim.fleet
        names = [self.target, "pit", *(s.name for s in pit.zone_segments)]
        names += [l.name for l in fleet.loaders.values() if l.stood_down]
        names += [r.name for r in pit.rigs if r.stood_down]
        names += [t.name for t in fleet.trucks if t.hold or (t.goal and t.goal[0] == "hold")]
        return names

    def text(self, v: dict) -> str:
        sim, pit, fleet = self.sim, self.sim.pit, self.sim.fleet
        block = pit.blocks[self.target]
        closed = ", ".join(f"«{s.title}»" for s in pit.zone_segments) or "нет"
        out = [l.title for l in fleet.loaders.values() if l.stood_down] + [r.title for r in pit.rigs if r.stood_down]
        held = sum(1 for t in fleet.trucks if t.hold or (t.goal and t.goal[0] == "hold"))
        zone = (f"Опасная зона {sim.params['blast']['exclusion_radius_m']:.0f} м, закрыты участки: {closed}; "
                f"выведено из зоны: {', '.join(out) or 'никого'}; самосвалов ждёт у границы зоны: {held}")
        if self.fired_at is None:
            head = f"{block.title}: подготовка к взрыву, подрыв в {_hhmm(sim, pit.blast_at)}"
        else:
            head = (f"{block.title} взорван в {_hhmm(sim, self.fired_at)}: {block.tonnes:.0f} т горной массы, "
                    f"крупность P80 {block.frag_p80_mm:.0f} мм; допуск в зону в {_hhmm(sim, pit.zone_until)}")
        return f"{head}. {zone}."

    def chain(self) -> list[Indicator]:
        return [DIG_RATE, TRIP_CYCLE, Indicator("Взорванная горная масса в забоях", "т", 0, "level", "broken_t"),
                ORE_TO_CRUSHER]

    def delay_text(self) -> str:
        sim, pit, p = self.sim, self.sim.pit, self.sim.params["blast"]
        if self.status == "inactive":
            head = (f"Взрыв через {p['clearance_lead_min']:.0f} мин модели после запуска (оцепление зоны), допуск в зону "
                    f"через {p['reentry_min']:.0f} мин после взрыва.")
        elif self.fired_at is None:
            head = f"Взрыв в {_hhmm(sim, pit.blast_at)}, допуск в зону через {p['reentry_min']:.0f} мин после него."
        else:
            head = f"Блок взорван в {_hhmm(sim, self.fired_at)}, допуск в зону в {_hhmm(sim, pit.zone_until)}."
        return (f"{head} Выемка и рейсы меняются, пока стоит оцепление; на фабрике взрыв не скажется, пока руда этого "
                f"блока не пойдёт в дробилку (часы модели). {_speed_hint(sim)}")


class HardOre(Scenario):
    """A zone of harder ore reaches the mill and stays until cancelled. The
    work index of the mill feed rises over the transition time; the mill can
    grind less per hour with its installed power, uses more energy per tonne,
    and recovery falls. After the cancel the zone passes over the same time."""

    kpi_label = "Удельный расход энергии мельницы"
    kpi_unit = "кВт·ч/т"
    kpi_digits = 2
    feed_label = "питание мельницы"
    idle = "Сценарий не активен: крепость руды обычная."

    def __init__(self, sim: "Simulation", thing: dict):
        super().__init__(sim, thing)
        self.factor = 1.0                       # the hardness factor the scenario is heading for
        self.rate_per_s = 0.0

    def _bwi(self) -> float:
        plant = self.sim.plant
        return (plant.stockpile.bwi or plant.mill.feed_bwi) * plant.hardness_factor

    def kpi_now(self) -> float:
        return self.sim.plant.mill.specific_energy(self._bwi())

    def feed_live(self) -> float:
        return self.sim.kpis.mill_feed.per_hour(self.sim.now)

    def feed_total(self) -> float:
        return self.sim.plant.mill.total_t

    def take_mark(self) -> None:
        super().take_mark()
        plant = self.sim.plant
        mill, flotation = plant.mill, plant.flotation
        self.mark.update(bwi=self._bwi(), max_feed=mill.max_feed_tph(self._bwi()), stops=mill.stops,
                         overloads=mill.overloads, recovery=flotation.recovery(plant.stockpile.grade_pct, self._bwi(), mill.setpoint_tph))

    def check(self, target: str, params: dict) -> str | None:
        bwi = params.get("bwi", self.sim.params["scenario"]["hard_ore_bwi"])
        low = self._bwi() + 0.5
        if isinstance(bwi, bool) or not isinstance(bwi, (int, float)) or not low <= bwi <= 22:
            return f"индекс Бонда {bwi!r} должен быть от {num(low)} до 22 кВт·ч/т"
        return None

    def start(self, target: str, params: dict) -> None:
        sim = self.sim
        bwi = float(params.get("bwi", sim.params["scenario"]["hard_ore_bwi"]))
        ramp_s = sim.params["scenario"]["hard_ore_ramp_min"] * 60.0
        self.factor = bwi / self.mark["bwi"]
        self.rate_per_s = (self.factor - 1.0) / ramp_s
        sim.log("alarm", target, f"Зона крепкой руды (сценарий «{self.title}»): индекс Бонда питания мельницы "
                                 f"растёт с {num(self.mark['bwi'])} до {num(bwi)} кВт·ч/т за {ramp_s / 60:.0f} мин")

    def stop(self) -> None:
        self.status = "cancelling"
        self.sim.log("info", self.target, f"Зона крепкой руды проходит, сценарий «{self.title}» отменён: "
                                          f"крепость вернётся к обычной за {self.sim.params['scenario']['hard_ore_ramp_min']:.0f} мин")

    def step(self) -> None:
        plant = self.sim.plant
        if self.status == "active":
            plant.hardness_factor = min(self.factor, plant.hardness_factor + self.rate_per_s)
        elif self.status == "cancelling":
            plant.hardness_factor = max(1.0, plant.hardness_factor - self.rate_per_s)
            if plant.hardness_factor <= 1.0:
                self.finish()

    def cancel_remaining_s(self) -> float:
        excess = self.sim.plant.hardness_factor - 1.0
        return excess / self.rate_per_s if self.rate_per_s > 0 and excess > 0 else 0.0

    def affected(self) -> list[str]:
        plant = self.sim.plant
        names = [plant.stockpile_name, plant.mill.name, "flotation", "substation"]
        if plant.crusher.limited:
            names.append(plant.crusher.name)
        return names

    def text(self, v: dict) -> str:
        plant = self.sim.plant
        mill, flotation = plant.mill, plant.flotation
        bwi = self._bwi()
        recovery = flotation.recovery_pct or flotation.recovery(plant.stockpile.grade_pct, bwi, mill.setpoint_tph)
        stops = mill.overloads - self.mark["overloads"]
        if mill.setpoint_source != "constant":
            control = f"Уставку питания ведёт модуль управления фабрикой: {mill.setpoint_tph:.0f} т/ч"
        else:
            control = f"Уставка питания постоянная, {mill.setpoint_tph:.0f} т/ч"
        if stops:
            control += f"; остановок мельницы из-за перегруза: {stops}"
        elif mill.setpoint_tph > mill.max_feed_tph(bwi):
            control += (f"; уставка выше предела по мощности: в мельницах накопилось {mill.charge.t:.0f} т руды сверх "
                        f"измельчённой, питание отключится при {mill.p['overload_t']:.0f} т")
        head = "Зона крепкой руды" if self.status == "active" else (
            f"Зона крепкой руды проходит (крепость вернётся к обычной через {self.cancel_remaining_s() / 60:.0f} мин "
            f"модели, {_real(self.sim, self.cancel_remaining_s() / 3600)} стенда)")
        return (f"{head}: индекс Бонда {num(self.mark['bwi'])} → {num(bwi)} кВт·ч/т. Предел мельницы по мощности "
                f"{self.mark['max_feed']:.0f} → {mill.max_feed_tph(bwi):.0f} т/ч, извлечение {num(self.mark['recovery'])} → "
                f"{num(recovery)} %. {control}.")

    def chain(self) -> list[Indicator]:
        return [Indicator("Индекс Бонда питания мельницы", "кВт·ч/т", 2, "mean", "bwi"),
                Indicator("Предел мельницы по мощности (среднее за окно)", "т/ч", 0, "mean", "max_feed"),
                Indicator("Удельный расход энергии измельчения", "кВт·ч/т", 2, "ratio", "mill_kwh", per="milled_t"),
                Indicator("Медь в концентрате", "т/ч", 2, "rate", "cu_t")]

    def delay_text(self) -> str:
        ramp = self.sim.params["scenario"]["hard_ore_ramp_min"]
        what = "уходит" if self.status == "cancelling" else "приходит"
        return (f"Крепкая руда {what} в питание мельницы за {ramp:.0f} мин модели; мельница ограничена мощностью сразу, "
                f"медь меняется в течение того же времени, ещё через 30 мин сравнение по окнам устоится. "
                f"{_speed_hint(self.sim)}")


KINDS = {
    "scenario-road-closure": RoadClosure,
    "scenario-shovel-failure": ShovelFailure,
    "scenario-crusher-stop": CrusherStop,
    "scenario-heavy-rain": HeavyRain,
    "scenario-blast": Blast,
    "scenario-hard-ore": HardOre,
}


def build(sim: "Simulation") -> dict[str, Scenario]:
    """One object per scenario twin."""
    return {_short(t["thingId"]): KINDS[_short(t["thingId"])](sim, t) for t in sim.by_kind["scenario"]}
