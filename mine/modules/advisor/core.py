"""Advisor: what to do about an incident, decided by forecasting each answer.

The module watches the twins for an incident:

    crusher-stop        the primary crusher of ОФ-1 is stopped
    clay-plant-stop     the clay-ore plant ОФ-2 is stopped
    loader-down:<id>    a loader has broken down (not a planned service)
    clay-face:<id>      a loader digs a block the block model took for sulphide ore, and the face shows clay ore
                        (at least `clay.clay_ore_min_pct` of clay)
    road-closed:<id>    a haul-road segment is closed, other than for a blast or by the rain
    rain                it rains: wet roads, the steep ramp closed
    truck-cluster       `advisor.truck_cluster_out` trucks or more are out of work at once
                        for `advisor.truck_cluster_min` simulated minutes (down, in service, on the way there)
    hard-ore            the ore on the stockpile is harder than `advisor.hard_ore_bwi`

When several hold at once, the first of this list is analysed. Each incident
has an expected end (`incident_end_sim_time`, `incident_basis`): the expected
end of its scenario (mine/sim/durations.py), the mechanics' estimate of a
natural repair; the forecasts assume it ends then unless an option changes it.

For the incident it builds up to four response options, always starting with
"Ничего не менять", the reference; an option is offered only where its lever
applies at that moment, and the first of the list below win the places. First
comes, as one option, switching on those of the modules that answer this kind
of incident and are off (`HANDLES`). Then the shift supervisor's own resources
(mine/sim/response.py), which no module pulls and which cost money of their
own, before the levers the modules also pull:

    crusher-stop    a second repair crew with the contractor's specialists (`expedite-repair` to the crusher);
                    send the ore faces straight to the ROM pads (`set-destination`);
                    lower the mill feed to its minimum to stretch the stockpile (`set-feed-rate`)
    loader-down     a second crew on the loader (`expedite-repair`); move a loader from a waste block to the
                    ore face of the broken one (`move-to-block`); the crew of a workshop bay to the loader's
                    face (`prioritise-repair`); reclaim ore from the ROM pad (`set-reclaim`); put off planned
                    truck service (`cancel-service`); lower the mill feed by 10 %
    road-closed     an emergency road crew (`repair-road`); reclaim from the ROM pad; put off planned truck
                    service; lower the mill feed by 10 %
    rain            a grader and a topping on the ramp the rain closed (`treat-surface`); put off planned
                    truck service; reclaim from the ROM pad; lower the mill feed by 10 %
    truck-cluster   put off planned truck service; a second workshop crew on overtime (`expedite-repair` to
                    the workshop); reclaim from the ROM pad; lower the mill feed by 10 %
    hard-ore        mill feed at 97 % or at 90 % of the power limit
    clay-face       move the loader off the clay face to a blasted block of sulphide ore (`move-to-block`); send the
                    face's ore to the clay-ore stockpile, into the blend of ОФ-1 (its feed then carries more clay than
                    the grade-control module allows), or to ОФ-2 while its feed stockpile has room (`set-destination`)
    clay-plant-stop a second repair crew on ОФ-2 (`expedite-repair` to the clay plant); send the clay faces to the
                    clay-ore stockpile, or into the blend of ОФ-1; move the clay shovel to a block of sulphide ore

Putting off a planned service keeps a truck at work now, and its failure rate
then grows with every hour past the service interval
(`reliability.overdue_hazard_slope`): the label says by how much.
For each option it asks the simulator for a forecast in one of the advisor's
slots (forecast-2 … forecast-5): branch A changes nothing, branch B applies the
option at the moment of the fork, each pair run `advisor.replicas` times on
random streams drawn anew for every replica and shared by A and B (paired
replicas, mine/sim/forecast.py). It ranks the options by the mean difference
B − A of the forecast's total: the copper revenue over the horizon less the
diesel, the electricity and the rehandling from the ROM pads, plus the value
of the ore and the waste the horizon leaves (mine/common/economics.py: every
ore inventory on one basis). Each option publishes that difference split into
copper revenue, the copper above the mine's average grade that the stocks gave
up, the fleet work held in ore and waste, rehandling, diesel and power (they
add up to it), where its copper difference comes from (`optionN_copper_text`),
its standard error (`optionN_gain_se_kzt`) and how sure the gain is, in words
(`optionN_confidence`); copper decides a tie. The cost of an option's response
resources is a part of its own («ресурсы смены», `optionN_cost_kzt`), and
`optionN_reason` says in one sentence why the option pays or does not: for a
repair that is sped up, whether the mill would run short of ore without it
(«без этого мельница встанет через 5 ч») or the stockpile covers the repair.

The operator applies an option with the live message `apply {option}` to the
module's twin; the module then sends the option's messages through Ditto and
records in the journal what was applied. An option never fights a module:
where it sets a lever that a module also sets (the mill setpoint, the reclaim
from a ROM pad, the destination of a block, the planned service of a truck),
it is an operator's order with `hold_min` until the incident's expected end
(at most `advisor.hold_h` hours; an order that outlasted the incident would
be held for the whole horizon in its forecast). The simulator keeps such an
order for that time and does not take the module's
own decisions for that lever meanwhile, and the module itself stands aside
while it reads the order in the lever's twin (`…_hold_until`). After the time
the lever returns to the module, or to the base rule if the module is off.
Moving a loader to another block is no module's lever, nor is a response
resource (a second crew, a road crew, a grader): the simulator carries it out
and it ends with the repair it speeds up. Switching a module on is the
module's own switch. When the incident an applied option was meant
for is over, the advisor ends its time-limited orders at once (the same
lever, for one minute, at its base value: the crusher for a block, no
reclaim, the design feed rate), so they do not outlast the incident; moved
loaders, switched modules and put-off services stay as they are.

The recommended option is the best-ranked one that
- gains at least `advisor.min_t` standard errors over «Ничего не менять»: the
  error measured over the paired replicas, but not below what the measured
  paired noise of one forecast (`forecast.noise_kzt`) gives for that many
  replicas; a smaller gain is within the scatter of the forecast;
- gains at least `advisor.min_gain_kzt`, the least that is worth an order;
- loses no more than `advisor.max_cu_loss_t` of copper over the horizon (the
  mill is the plant's bottleneck and does not make up later the copper it does
  not produce now): a hard bar, whatever the money;
- and does not owe its gain to inventory: the money of the horizon (the
  copper at the mine's average grade and the costs, everything but the fleet
  work) must carry at least `advisor.min_gain_kzt` when the copper differs by
  less than `advisor.min_cu_t` or when that money is less than half the gain.
If no option clears every bar, «Ничего не менять» is recommended, and the text
names the modules that are on and already answer the incident.

The journal and the twin name an applied option by its label, its number as
published (`option<N>_…`) and its place in the ranking, so that the page and
the journal cannot disagree about which one it was.

Without the module the incidents run their course under the base rules and
the other modules; nobody compares the possible answers.
"""
from __future__ import annotations

import math
from datetime import datetime

from mine.common.roads import RoadNetwork
from mine.model.build_twins import MARGIN_SPLIT
from mine.modules.base import Core, Message, num, short
from mine.sim.text import thousands

ORDER = ("crusher-stop", "clay-plant-stop", "sag-reline", "loader-down", "clay-face", "road-closed", "rain", "truck-cluster",
         "hard-ore")
OUT_STATES = ("down", "service", "to_service")
NOTHING = "Ничего не менять"
# A gain beyond the scatter that rests on inventory, not copper: the verdict starts with ON_STOCK,
# the reason text says STOCK_REASON when the copper differs by less than advisor.min_cu_t, else where it comes from.
ON_STOCK = "выигрыш набран не медью"
STOCK_REASON = "выигрыш держится на запасах руды, меди не прибавляет"
MODULE_TITLES = {"module-dispatch": "модуль диспетчеризации", "module-grade": "модуль управления качеством руды",
                 "module-plant": "модуль управления фабрикой", "module-maint": "модуль техобслуживания"}
# What a module that is on already does about an incident: said when «change nothing» is recommended.
DISPATCH_ROUTES = "ведёт самосвалы по открытым дорогам к экскаваторам, где они загрузятся быстрее"
PLANT_FEED = "держит уставку мельницы по притоку руды, чтобы склад дроблёной руды не опустел"
HANDLES = {
    "loader-down": {"module-dispatch": "переводит самосвалы неисправного экскаватора к тем, где они загрузятся быстрее",
                    "module-plant": PLANT_FEED,
                    "module-grade": "держит содержание меди в питании мельницы, пока руда идёт из других забоев"},
    "crusher-stop": {"module-dispatch": "везёт руду сразу на рудные склады и отправляет самосвалы на вскрышу вместо "
                                        "очереди у дробилки",
                     "module-plant": "снижает уставку мельницы, только если склада дроблёной руды не хватит до "
                                     "ожидаемого пуска дробилки"},
    "road-closed": {"module-dispatch": DISPATCH_ROUTES, "module-plant": PLANT_FEED},
    "rain": {"module-dispatch": DISPATCH_ROUTES, "module-plant": PLANT_FEED},
    "truck-cluster": {"module-maint": "ставит машины в ремонт и на обслуживание так, чтобы они не выбывали разом",
                      "module-dispatch": "распределяет оставшиеся самосвалы между экскаваторами",
                      "module-plant": PLANT_FEED},
    "hard-ore": {"module-plant": "держит уставку мельницы у предела мощности на крепкой руде"},
    "clay-face": {"module-grade": "переводит руду глинистого забоя на ОФ-2 или на склад глинистой руды, не пуская в "
                                  "питание ОФ-1 больше 8 % глины",
                  "module-plant": "снижает уставку ОФ-1, если глина в питании опускает предел мельницы"},
    "clay-plant-stop": {"module-grade": "везёт глинистую руду на склад глинистой руды, когда приёмный склад ОФ-2 полон"},
    "sag-reline": {"module-dispatch": "когда склад дроблёной руды полон, везёт руду на рудные склады и отправляет свободные "
                                      "самосвалы на вскрышу",
                   "module-plant": "после пуска мельницы поднимает подачу до пика, пока склад дроблёной руды полон"},
}
# When a planned reline of the SAG mill may begin instead of its plan: (start in `plan-reline`, label of the moment).
RELINE_STARTS = (("now", "сразу"), ("next-shift", "в начале следующей смены"), ("shift-after", "через смену"))
SPLIT_TITLES = {"revenue": "медь", "grade": "медь из запасов сверх средней руды", "work": "работа парка",
                "rehandle": "перевалка", "diesel": "дизель", "power": "электроэнергия", "resources": "ресурсы смены"}
# The parts of a gain that are money of the horizon: the copper at the mine's average grade (revenue less the
# copper the stocks gave up above it) and the costs. The fleet work held in the ore and the waste is not.
CASH_PARTS = ("revenue", "grade", "rehandle", "diesel", "power", "resources")
# The shift's response resources: (thing kind, subject) -> what it does, what it is called, what it shortens.
RESOURCES = {
    ("crusher", "expedite-repair"): ("Ускорить ремонт дробилки", "вторая бригада", "ремонта дробилки"),
    ("clay-plant", "expedite-repair"): ("Ускорить ремонт ОФ-2", "вторая бригада", "ремонта ОФ-2"),
    ("loader", "expedite-repair"): ("Ускорить ремонт экскаватора", "вторая бригада", "ремонта экскаватора"),
    ("loader", "prioritise-repair"): ("Бригада мастерской на ремонте экскаватора", "бригада мастерской",
                                      "ремонта экскаватора"),
    ("road-segment", "repair-road"): ("Аварийный ремонт дороги", "аварийная бригада", "закрытия дороги"),
    ("road-segment", "treat-surface"): ("Грейдер и подсыпка на съезде", "грейдер с подсыпкой", "дождя"),
    ("workshop", "expedite-repair"): ("Сверхурочная смена мастерской", "сверхурочная смена", "ремонтов самосвалов"),
}


def money(kzt: float) -> str:
    return num(kzt / 1e6, 1)


def hhmm(text: str) -> str:
    return str(text)[11:16]


def series(text) -> list[float]:
    try:
        return [float(v) for v in str(text or "").split(",") if v.strip()]
    except ValueError:
        return []


def signed(value: float, digits: int = 1) -> str:
    text = num(abs(value), digits)
    if text.strip("0,") == "":
        return text
    return ("+" if value > 0 else "−") + text


def split_of(twin: dict) -> dict:
    """The difference B − A of a forecast twin, split into its contributions in KZT: positive is in favour
    of branch B (mine/sim/forecast.py, `split`). revenue − diesel − power − rehandle + grade + work + pads +
    strip − resources of each branch is its total, so the parts add up exactly to `delta_total_kzt`."""
    d = lambda key: int(twin.get(f"b_{key}_kzt") or 0) - int(twin.get(f"a_{key}_kzt") or 0)
    return {"revenue": d("revenue"), "grade": d("grade"), "work": d("work") + d("strip"),
            "rehandle": d("pads") - d("rehandle"), "diesel": -d("diesel"), "power": -d("power"),
            "resources": -d("resources")}


class AdvisorCore(Core):
    name = "module-advisor"
    kpi_label = "Выигрыш рекомендованного варианта за горизонт прогноза"
    kpi_unit = "млн тг"

    def __init__(self, things: list[dict], params: dict, enabled: bool = True):
        super().__init__(things, params, enabled)
        w = self.world
        self.p = params["advisor"]
        # The horizon of an analysis: the forecast's default, longer for a planned reline (`_options`).
        self.default_horizon_h = self.horizon_h = int(params["forecast"]["horizon_h"])
        self.replicas = int(self.p["replicas"])
        self.noise_kzt = float(params["forecast"]["noise_kzt"])
        self.hold_min = int(round(self.p["hold_h"] * 60))
        self.slots = [name for name in w.by_kind.get("forecast", []) if w.spec(name, "role") == "advisor"]
        self.max_options = min(4, len(self.slots))
        self.loaders, self.trucks = w.by_kind["loader"], w.by_kind["truck"]
        self.segments, self.blocks = w.by_kind["road-segment"], w.by_kind["block"]
        self.pads = {w.get(pad, "role"): pad for pad in w.by_kind["rom-pad"]}
        self.crusher, self.mill, self.stockpile = w.by_kind["crusher"][0], w.by_kind["mill"][0], w.by_kind["stockpile"][0]
        self.sag = w.by_kind["sag-mill"][0]
        self.clay_plant, self.clay_stock = w.by_kind["clay-plant"][0], w.by_kind["clay-stockpile"][0]
        self.clay_min = float(params["clay"]["clay_ore_min_pct"])
        self.clay_ref = float(params["flotation"]["clay_ref_pct"])
        self.clay_limit = float(params["grade_control"]["clay_limit_pct"])
        self.roads = RoadNetwork.from_things(things, params["road"])
        self.loader_kmh = float(params["loader"]["travel_kmh"])
        self.setup_min = float(params["loader"]["relocate_setup_min"])
        self.r = params["response"]
        self.fx = float(params["economics"]["usd_kzt"])
        self.restart()

    def restart(self) -> None:
        super().restart()
        self.incident: dict | None = None
        self.options: list[dict] = []
        self.status = "idle"                    # idle / running / ready
        self.analysed_at: float | None = None
        self.asked_at = 0.0
        self.round = 0
        self.recommended = 0
        self.applied: dict = {}
        self.cluster_since: float | None = None

    # -- incidents --------------------------------------------------------------------------

    def _out_trucks(self) -> list[str]:
        return [t for t in self.trucks if self.world.get(t, "state") in OUT_STATES]

    def detect(self, now: float) -> dict | None:
        """The incident to analyse now, or None."""
        w, found = self.world, []
        current = self.incident["key"] if self.incident else ""
        if w.get(self.crusher, "state") == "stopped":
            level = float(w.get(self.stockpile, "level_pct", 0.0))
            found.append(("crusher-stop", self.crusher,
                          f"{w.title(self.crusher)} остановлена: самосвалы с рудой ждут у неё или везут руду на рудные склады; "
                          f"склад дроблёной руды заполнен на {level:.0f} %, это {num(float(w.get(self.stockpile, 'hours_of_feed', 0.0)))} ч "
                          f"питания мельницы."))
        if w.get(self.clay_plant, "state") == "stopped":
            found.append(("clay-plant-stop", self.clay_plant,
                          f"{w.title(self.clay_plant)} остановлена: глинистая руда копится на приёмном складе "
                          f"({float(w.get(self.clay_plant, 'bin_t', 0.0)):.0f} т, свободно "
                          f"{float(w.get(self.clay_plant, 'room_t', 0.0)):.0f} т), потом идёт на склад глинистой руды "
                          f"(свободно {float(w.get(self.clay_stock, 'room_t', 0.0)):.0f} т); ОФ-2 не даёт меди."))
        reline = self._reline()
        if reline is not None:
            found.append((f"sag-reline:{reline['phase']}", self.sag, reline["text"]))
        for loader in self.loaders:
            block = short(w.get(loader, "block", ""))
            if w.get(loader, "state") in ("loading", "waiting") and block in w.attrs \
                    and w.get(block, "material") in ("ore", "low-grade") \
                    and float(w.get(block, "clay_pct", 0.0)) >= self.clay_min - (1.0 if current == f"clay-face:{loader}" else 0.0):
                found.append((f"clay-face:{loader}", loader,
                              f"{w.title(loader)}: забой {w.title(block)} показал глинистую руду — глины "
                              f"{num(float(w.get(block, 'clay_pct', 0.0)))} % при пороге {num(self.clay_min, 0)} %, а по "
                              f"блочной модели руда сульфидная; {self._clay_destination_text(block)}. "
                              f"Глины на складе дроблёной руды ОФ-1 {num(float(w.get(self.stockpile, 'clay_pct', 0.0)))} %, "
                              f"предел мельницы {float(w.get(self.mill, 'max_feed_tph', 0.0)):.0f} т/ч."))
        for loader in self.loaders:
            if w.get(loader, "state") == "down" and str(w.get(loader, "service_kind", "")).startswith("breakdown"):
                block = short(w.get(loader, "block", ""))
                where = f" в забое {w.title(block)} ({'вскрыша' if w.get(block, 'material') == 'waste' else 'руда'})" if block else ""
                found.append((f"loader-down:{loader}", loader,
                              f"{w.title(loader)} неисправен{where}: его самосвалы уходят к другим экскаваторам, "
                              f"выемка горной массы падает."))
        for segment in self.segments:
            reasons = {r for r in str(w.get(segment, "closed_by", "")).split(",") if r}
            if w.get(segment, "status") == "closed" and reasons - {"blast", "scenario-heavy-rain"}:
                found.append((f"road-closed:{segment}", segment,
                              f"Закрыт участок «{w.title(segment)}»: самосвалы едут в объезд, рейс длиннее, "
                              f"руды к дробилке приходит меньше."))
        # Ordinary light rain or snow (the climate of the site) only slows the trucks a little; heavy precipitation is an incident.
        if w.get("weather", "precip_class") == "heavy":
            found.append(("rain", "weather",
                          f"Сильный дождь с мокрым снегом, {float(w.get('weather', 'rain_mm_h', 0.0)):.0f} мм/ч: дороги мокрые, "
                          f"скорость ограничена, крутой съезд закрыт; рейсы длиннее, расход топлива на тонну выше."))
        out = self._out_trucks()
        need = int(self.p["truck_cluster_out"]) - (1 if current == "truck-cluster" else 0)
        if len(out) >= need:
            self.cluster_since = now if self.cluster_since is None else self.cluster_since
            if current == "truck-cluster" or now - self.cluster_since >= self.p["truck_cluster_min"] * 60.0:
                found.append(("truck-cluster", "workshop",
                              f"Вне работы {len(out)} самосвалов из {len(self.trucks)}: "
                              f"{', '.join(w.title(t) for t in out)}; провозная способность парка ниже плановой."))
        else:
            self.cluster_since = None
        bwi = float(w.get(self.stockpile, "hardness_bwi", 0.0))
        if bwi >= float(self.p["hard_ore_bwi"]) - (0.5 if current == "hard-ore" else 0.0):
            found.append(("hard-ore", self.stockpile,
                          f"Крепкая руда на складе: индекс Бонда {num(bwi)} кВт·ч/т; предел мельницы по мощности при "
                          f"этой крепости {float(w.get(self.mill, 'max_feed_tph', 0.0)):.0f} т/ч, уставка "
                          f"{float(w.get(self.mill, 'feed_setpoint_tph', 0.0)):.0f} т/ч. Если руда станет ещё крепче, "
                          f"предел опустится, и уставка выше него перегрузит мельницу."))
        if not found:
            return None
        found.sort(key=lambda f: ORDER.index(f[0].split(":")[0]))
        key, thing, text = found[0]
        end, basis = self._expected(key.split(":")[0], thing)
        if end:
            left = max(0.0, (self.world.time_of(end) or now) - now) / 3600.0
            text += f" Ожидаемое окончание — {hhmm(end)}, через {num(left)} ч ({basis})."
        if len(found) > 1:
            text += f" Других инцидентов: {len(found) - 1}."
        return {"key": key, "kind": key.split(":")[0], "thing": thing, "text": text, "end": end, "basis": basis}

    def _grade_on(self) -> bool:
        return self.world.get("module-grade", "status") == "online"

    def _clay_destination_text(self, block: str) -> str:
        """Where the ore of a clay face goes now and who decided it, and what the grade-control module, if on,
        does with it from here. The incident text is refreshed when the destination changes (`decide`)."""
        w = self.world
        source = {"cutoff": "по блочной модели", "grade-control": "решение модуля управления качеством руды",
                  "operator": "указание оператора"}.get(str(w.get(block, "destination_source", "cutoff")), "")
        text = f"сейчас её везут на «{w.title(short(w.get(block, 'destination', '')))}»" + (f" ({source})" if source else "")
        if self._grade_on() and w.get(block, "destination_source") != "operator":
            text += (f"; модуль управления качеством руды распределяет её между ОФ-2, складом глинистой руды и шихтой "
                     f"ОФ-1 (до {num(self.clay_limit, 0)} % глины в питании)")
        return text

    def _clay_destination(self, incident: dict | None) -> str:
        """The destination and its source of the face of a clay-face incident; '' for any other incident."""
        if not incident or incident.get("kind") != "clay-face":
            return ""
        w = self.world
        block = short(w.get(incident["thing"], "block", ""))
        return f"{w.get(block, 'destination', '')}|{w.get(block, 'destination_source', '')}"

    def _scenario_end(self, scenario: str, target: str | None = None) -> tuple[str, str]:
        """The expected end of a running scenario (on `target`, if given) and its basis; ('', '') otherwise."""
        w = self.world
        if w.get(scenario, "status") != "active" or (target and short(w.get(scenario, "target", "")) != target):
            return "", ""
        return str(w.get(scenario, "expected_end_sim_time", "")), str(w.get(scenario, "duration_basis", ""))

    def _expected(self, kind: str, thing: str) -> tuple[str, str]:
        """When the incident is expected to end, and why: the scenario's estimate, the mechanics' estimate."""
        w = self.world
        if kind == "crusher-stop":
            end = str(w.get(self.crusher, "restart_expected_sim_time", ""))
            return end, self._scenario_end("scenario-crusher-stop")[1] if end else ""
        if kind == "loader-down":
            end = str(w.get(thing, "repair_end_sim_time", ""))
            basis = self._scenario_end("scenario-shovel-failure", thing)[1] or (
                f"оценка механиков после осмотра; среднее время ремонта экскаватора "
                f"{num(float(self.params['reliability']['loader_mttr_h']), 0)} ч")
            return end, basis if end else ""
        if kind == "road-closed":
            return self._scenario_end("scenario-road-closure", thing)
        if kind == "rain":
            return self._scenario_end("scenario-heavy-rain")
        if kind == "clay-plant-stop":
            end = str(w.get(self.clay_plant, "restart_expected_sim_time", ""))
            return end, self._scenario_end("scenario-clay-plant-stop")[1] if end else ""
        if kind == "clay-face":
            return self._scenario_end("scenario-clay-face", thing)
        if kind == "sag-reline":
            end = str(w.get(self.sag, "reline_end_sim_time", ""))
            return end, self._scenario_end("scenario-sag-reline")[1] if end else ""
        return "", ""

    # -- the planned reline of the SAG mill ---------------------------------------------------

    @staticmethod
    def _day(at: float) -> str:
        """A simulated moment as «DD.MM HH:MM»."""
        return datetime.fromtimestamp(at).strftime("%d.%m %H:%M")

    def _reline(self) -> dict | None:
        """The planned (or running) reline of the SAG mill from its twin: phase, start, end, duration, text."""
        w = self.world
        phase = str(w.get(self.sag, "reline_state", ""))
        start, end = w.time_of(w.get(self.sag, "reline_start_sim_time", "")), w.time_of(w.get(self.sag, "reline_end_sim_time", ""))
        if phase not in ("planned", "reline") or start is None or end is None:
            return None
        hours = (end - start) / 3600.0
        level = float(w.get(self.stockpile, "level_t", 0.0))
        capacity = float(w.spec(self.stockpile, "capacity_t", 1.0) or 1.0)
        room = max(0.0, capacity - level)
        crusher_tph = float(self.params["crusher"]["rated_tph"])
        if phase == "planned":
            text = (f"Запланирована замена футеровки МПСИ: начало {self._day(start)} (через {num(max(0.0, start - w.now) / 3600.0)} ч), "
                    f"{num(hours)} ч, пуск мельницы {self._day(end)}; на это время измельчение ОФ-1 стоит целиком, ОФ-2 "
                    f"работает. Склад дроблёной руды заполнен на {100 * level / capacity:.0f} %: во время остановки "
                    f"дробилка примет ещё {thousands(room)} т — около {num(room / crusher_tph)} ч, потом руда пойдёт на "
                    f"рудные склады.")
        else:
            text = (f"МПСИ на замене футеровки с {self._day(start)}, пуск мельницы ожидается {self._day(end)} (через "
                    f"{num(max(0.0, end - w.now) / 3600.0)} ч); измельчение ОФ-1 стоит, ОФ-2 работает. Склад дроблёной руды "
                    f"заполнен на {100 * level / capacity:.0f} %" + ("; руда из забоев идёт на рудные склады." if room < 2000 else "."))
        return {"phase": phase, "start": start, "end": end, "hours": hours, "text": text}

    def _next_shift(self) -> float:
        """The next shift change: shifts are 12 h, from the start of the current one (the mine twin)."""
        w = self.world
        began = w.time_of(w.get("mine", "shift_start_sim_time", "")) or w.now
        at = began + 12 * 3600.0
        while at <= w.now:
            at += 12 * 3600.0
        return at

    def _reline_start(self, choice: str) -> float:
        """When a reline set to begin `now`, at the `next-shift` change or `shift-after` it would begin."""
        if choice == "now":
            return self.world.now
        later = float(self.params["scenario"]["reline_shift_after_h"]) * 3600.0 if choice == "shift-after" else 0.0
        return self._next_shift() + later

    def _reline_options(self, reline: dict) -> tuple[list[dict], float]:
        """The options of a planned reline and the latest start among them and the plan (the horizon is set by it):
        to begin it at another moment (`plan-reline`), and to take the ore shovels off ore for the stop."""
        w, out = self.world, []
        latest = reline["start"]
        end = lambda start: start + reline["hours"] * 3600.0
        if reline["phase"] == "planned":
            for choice, title in RELINE_STARTS:
                start = self._reline_start(choice)
                if abs(start - reline["start"]) < 1800.0:
                    continue                    # that is the plan itself
                latest = max(latest, start)
                earlier = start < reline["start"]
                label = (f"Начать замену футеровки {title} ({self._day(start)}) вместо {self._day(reline['start'])}: "
                         f"пуск мельницы около {self._day(end(start))}")
                why = (f"мельница раньше встанет и раньше запустится со склада, заполненного за остановку"
                       if earlier else "мельница дольше работает до остановки и позже запускается")
                out.append({"label": label, "why": why, "start": choice,
                            "actions": [self._action(self.sag, "plan-reline", {"start": choice})]})
        prep = self._ore_shovels_to_waste(reline)
        if prep is not None:
            out.insert(min(len(out), 1), prep)
        return out, latest

    def _ore_shovels_to_waste(self, reline: dict) -> dict | None:
        """For the stop: the shovels of the sulphide ore faces to free blasted waste blocks, so that the ore stays in the
        pit instead of going onto the ROM pads (and being rehandled later) while the mill stands."""
        w = self.world
        taken = {short(w.get(l, "block", "")) for l in self.loaders}
        free = [b for b in self.blocks if w.get(b, "status") == "blasted" and w.get(b, "material") == "waste"
                and b not in taken and float(w.get(b, "tonnes_remaining", 0)) > 20000 and not self._in_blast_zone(b)]
        ore = [l for l in self.loaders if w.get(l, "state") in ("loading", "waiting", "relocating", "setup")
               and w.get(short(w.get(l, "block", "")), "material") == "ore"]
        moves = []
        for loader in sorted(ore):
            if not free:
                break
            block = min(free, key=lambda b: (self._move_minutes(loader, b), b))
            free.remove(block)
            moves.append((loader, block))
        if not moves:
            return None
        names = ", ".join(f"{w.title(l)} — на {w.title(b)}" for l, b in moves)
        when = "сейчас" if reline["phase"] == "reline" else "сейчас, до начала остановки"
        rehandle = float(self.params["rom"]["rehandle_kzt_t"])
        return {"label": f"Подготовить карьер: экскаваторы рудных забоев на вскрышу {when} ({names}); руда остаётся в "
                         f"забоях, а не уходит на рудные склады",
                "why": f"во время остановки руда из забоев ложится на рудные склады, её перевалка в дробилку потом стоит "
                       f"{rehandle:.0f} тг/т; вместо неё экскаваторы снимают вскрышу",
                "actions": [self._action(l, "move-to-block", {"block": w.tid(b)}) for l, b in moves]}

    def _reline_horizon_h(self, reline: dict, latest: float) -> int:
        """One horizon for every option: from now to the end of the stop of the latest of them, plus the restart."""
        end = latest + reline["hours"] * 3600.0
        hours = (end - self.world.now) / 3600.0 + float(self.params["scenario"]["reline_recovery_h"])
        return int(min(float(self.params["forecast"]["planned_max_horizon_h"]), math.ceil(max(1.0, hours))))

    # -- options ------------------------------------------------------------------------------

    def _module_off(self, module: str) -> bool:
        """The module's service runs but is switched off: switching it on is an option."""
        return self.world.get(module, "status") == "disabled"

    def _move_minutes(self, loader: str, block: str) -> float:
        w = self.world
        pos = (float(w.get(loader, "pos_x", 0.0)), float(w.get(loader, "pos_y", 0.0)))
        here = short(w.get(loader, "block", ""))
        start = w.link(here, "access_node") if here in w.attrs else min(
            self.roads.nodes, key=lambda n: math.dist(pos, self.roads.nodes[n][:2]))
        goal = w.link(block, "access_node")
        face = (float(w.spec(block, "face_x", 0.0)), float(w.spec(block, "face_y", 0.0)))
        metres = (math.dist(pos, self.roads.nodes[start][:2]) + self.roads.shortest_length(start, goal)
                  + math.dist(self.roads.nodes[goal][:2], face)) if start != goal else math.dist(pos, face)
        return metres / (self.loader_kmh / 3.6) / 60.0 + self.setup_min

    def _ore_block_for(self, loader: str, down: str | None) -> str | None:
        """An ore block the loader could dig: the face of the broken-down loader first, else the nearest free blasted ore block."""
        w = self.world
        if down:
            block = short(w.get(down, "block", ""))
            if block in w.attrs and w.get(block, "material") != "waste" and float(w.get(block, "tonnes_remaining", 0)) > 500 \
                    and w.get(block, "status") in ("blasted", "mining") and not self._in_blast_zone(block):
                return block
        taken = {short(w.get(l, "block", "")) for l in self.loaders}
        free = [b for b in self.blocks if w.get(b, "status") == "blasted" and w.get(b, "material") != "waste"
                and b not in taken and float(w.get(b, "tonnes_remaining", 0)) > 500 and not self._in_blast_zone(b)]
        return min(free, key=lambda b: (self._move_minutes(loader, b), b)) if free else None

    def _in_blast_zone(self, block: str) -> bool:
        """The simulator refuses to send a loader into the exclusion zone of a blast under way (pit twin)."""
        w = self.world
        fired = short(w.get("pit", "blast_block", ""))
        if not fired or fired not in w.attrs:
            return False
        centre = lambda b: (float(w.spec(b, "centre_x", 0.0)), float(w.spec(b, "centre_y", 0.0)))
        return math.dist(centre(block), centre(fired)) < float(w.get("pit", "blast_radius_m", 0.0))

    def _move_loader(self, down: str | None) -> dict | None:
        w = self.world
        # A loader on its way to a waste block can be sent to the ore face instead.
        working = [l for l in self.loaders if w.get(l, "state") in ("loading", "waiting", "relocating", "setup")
                   and w.get(l, "material") == "waste"]
        best = None
        for loader in working:
            block = self._ore_block_for(loader, down)
            if block is not None:
                minutes = self._move_minutes(loader, block)
                if best is None or minutes < best[0]:
                    best = (minutes, loader, block)
        if best is None:
            return None
        minutes, loader, block = best
        return {"label": f"Перевести {w.title(loader)} со вскрыши на {w.title(block)} "
                         f"(руда {num(float(w.get(block, 'grade_pct', 0.0)), 2)} % Cu, переезд около {minutes:.0f} мин)",
                "actions": [self._action(loader, "move-to-block", {"block": w.tid(block)})]}

    def _reclaim(self) -> dict | None:
        w = self.world
        if w.get(self.crusher, "state") == "stopped":
            return None
        pads = [pad for pad in self.pads.values() if float(w.get(pad, "level_t", 0.0)) >= float(self.p["pad_reserve_t"])]
        if not pads:
            return None
        pad = max(pads, key=lambda p: (float(w.get(p, "grade_pct", 0.0)), p))
        tph = float(w.spec(pad, "reclaim_rate_tph", 600.0))
        stock = f"{round(float(w.get(pad, 'level_t', 0.0)), -2):,.0f}".replace(",", " ")
        # The wheel loader serves the operator's order alone: the grade-control module does not feed from the pads
        # meanwhile (it would otherwise reclaim low-grade ore to thin a rich feed), and the forecast includes that.
        grade = "; модуль качества руды на это время не подаёт руду со складов" \
            if w.get("module-grade", "status") == "online" else ""
        return {"label": f"Подавать руду с «{w.title(pad)}» в дробилку {tph:.0f} т/ч ({self._hold_text()}, "
                         f"на складе {stock} т{grade})",
                "actions": [self._action(pad, "set-reclaim", {"tph": tph, "hold_min": self.hold_min})]}

    def _ore_to_pads(self) -> dict | None:
        """Crusher stopped: the ore faces send their trucks straight to the ROM pads instead of to the crusher queue."""
        w, actions, names = self.world, [], []
        for loader in self.loaders:
            block = short(w.get(loader, "block", ""))
            if block not in w.attrs or w.get(block, "material") == "waste" or w.get(loader, "state") == "down":
                continue
            pad = self.pads.get("low-grade" if w.get(block, "material") == "low-grade" else "high-grade")
            if pad is None or w.get(block, "destination") == pad:
                continue
            actions.append(self._action(block, "set-destination", {"destination": w.tid(pad), "hold_min": self.hold_min,
                                                                    "reason": "дробилка остановлена: сразу на рудный склад"}))
            names.append(w.title(block))
        if not actions:
            return None
        return {"label": f"Сразу везти руду из забоев ({', '.join(names)}) на рудные склады, не ожидая у дробилки "
                         f"({self._hold_text()}, до ожидаемого пуска)",
                "actions": actions}

    def _mill_setpoint(self, tph: float, why: str) -> dict | None:
        w = self.world
        tph = float(int(tph / 10.0) * 10)
        now = float(w.get(self.mill, "feed_setpoint_tph", 0.0))
        if abs(tph - now) < 50.0:
            return None
        return {"label": f"Уставка питания мельницы {tph:.0f} т/ч вместо {now:.0f} на {self._hold_text()}: {why}",
                "actions": [self._action(self.mill, "set-feed-rate", {"tph": tph, "hold_min": self.hold_min})]}

    # -- clay ore ----------------------------------------------------------------------------

    def _face_rate(self, loader: str) -> float:
        rate = float(self.world.get(loader, "dig_rate_tph", 0.0) or 0.0)
        return rate if rate > 300.0 else 1200.0

    def _clay_faces(self) -> list[tuple[str, str]]:
        """(loader, block) of every face digging clay ore now: clay-ore blocks and clay-rich faces of sulphide blocks."""
        w, out = self.world, []
        for loader in self.loaders:
            block = short(w.get(loader, "block", ""))
            if w.get(loader, "state") in ("loading", "waiting", "relocating", "setup") and block in w.attrs \
                    and w.get(block, "material") != "waste" and float(w.get(block, "clay_pct", 0.0)) >= self.clay_min:
                out.append((loader, block))
        return out

    def _clay_route(self, faces: list[tuple[str, str]], sink: str) -> dict | None:
        """Send the ore of these faces to the clay-ore stockpile, ОФ-2 or the crusher of ОФ-1, as an operator's order."""
        w = self.world
        # A face already sent there is left out; on ОФ-2, unless it is the grade-control module's choice: the module
        # may send the ore into the blend of ОФ-1 later, and the option holds it on ОФ-2 for the hold time.
        grade = self._grade_on()
        faces = [(l, b) for l, b in faces if short(w.get(b, "destination", "")) != sink
                 or (grade and sink == self.clay_plant and w.get(b, "destination_source") == "grade-control")]
        if not faces:
            return None
        tph = sum(self._face_rate(l) for l, _ in faces)
        names = ", ".join(w.title(b) for _, b in faces)
        if sink == self.clay_stock:
            room = float(w.get(self.clay_stock, "room_t", 0.0))
            if room < 6 * float(self.params["truck"]["payload_t"]):
                return None
            hours = room / max(tph, 1.0)
            label = f"Руду забоя ({names}) — на склад глинистой руды ({self._hold_text()})"
            why = (f"на складе глинистой руды свободно {thousands(room)} т — {num(hours)} ч работы забоя; перевалка "
                   f"потом {money(tph * self._hold_h() * float(self.params['rom']['rehandle_kzt_t']))} млн тг")
        elif sink == self.clay_plant:
            room = float(w.get(self.clay_plant, "room_t", 0.0))
            if w.get(self.clay_plant, "state") == "stopped" or room < 2000.0:
                return None
            hours = room / max(tph, 1.0)
            if grade:
                # The module sends clay ore to ОФ-2 too, but not only there: the option differs from «change nothing»
                # by holding all of this face's ore on ОФ-2, and the label says so.
                label = (f"Руду забоя ({names}) — закрепить за ОФ-2 на {self._hold_text()}: без указания модуль "
                         f"управления качеством руды переводит её и в шихту ОФ-1 (до {num(self.clay_limit, 0)} % глины) "
                         f"или на склад глинистой руды")
            else:
                label = f"Руду забоя ({names}) — сразу на ОФ-2 ({self._hold_text()})"
            why = (f"на приёмном складе ОФ-2 свободно {thousands(room)} т — {num(hours)} ч работы забоя, ОФ-2 перерабатывает "
                   f"{float(w.get(self.clay_plant, 'feed_setpoint_tph', 0.0)):.0f} т/ч")
        else:
            mill_tph = float(w.get(self.mill, "feed_setpoint_tph", 0.0)) or 3557.0
            pile = float(w.get(self.stockpile, "clay_pct", 0.0)) or self.clay_ref
            face_clay = sum(self._face_rate(l) * float(w.get(b, "clay_pct", 0.0)) for l, b in faces)
            clay = (pile * max(mill_tph - tph, 0.0) + face_clay) / max(mill_tph, tph)
            loss = float(self.params["flotation"]["clay_loss_pct"]) * max(0.0, clay - self.clay_ref)
            limit = (f"выше предела модуля {num(self.clay_limit, 0)} %" if clay > self.clay_limit
                     else f"в пределах {num(self.clay_limit, 0)} %")
            label = (f"Руду забоя ({names}) — в шихту ОФ-1: глины в питании около {num(clay, 0)} %, {limit} "
                     f"({self._hold_text()})")
            why = (f"глины в питании ОФ-1 станет около {num(clay)} %: минус {num(loss)} пункта извлечения ОФ-1 и предел "
                   f"мельницы ниже, зато склад глинистой руды и ОФ-2 не загружаются")
        reason = "глинистая руда: " + {self.clay_stock: "на склад глинистой руды", self.clay_plant: "на ОФ-2",
                                       self.crusher: "в шихту ОФ-1"}[sink] + " (указание советника)"
        actions = [self._action(b, "set-destination", {"destination": w.tid(sink), "hold_min": self.hold_min,
                                                       "reason": reason}) for _, b in faces]
        return {"label": label, "actions": actions, "why": why}

    def _hold_h(self) -> float:
        return self.hold_min / 60.0

    def _move_off_clay(self, loader: str) -> dict | None:
        """Move the loader of a clay face to the nearest free blasted block of sulphide ore."""
        w = self.world
        taken = {short(w.get(l, "block", "")) for l in self.loaders}
        free = [b for b in self.blocks if w.get(b, "status") == "blasted" and w.get(b, "material") == "ore"
                and float(w.get(b, "clay_pct", 0.0)) < self.clay_min and b not in taken
                and float(w.get(b, "tonnes_remaining", 0)) > 5000 and not self._in_blast_zone(b)]
        if not free or w.get(loader, "state") == "down":
            return None
        block = min(free, key=lambda b: (self._move_minutes(loader, b), b))
        minutes = self._move_minutes(loader, block)
        here = short(w.get(loader, "block", ""))
        return {"label": f"Перевести {w.title(loader)} с глинистого забоя {w.title(here)} на {w.title(block)} "
                         f"(сульфидная руда {num(float(w.get(block, 'grade_pct', 0.0)), 2)} % Cu, переезд около {minutes:.0f} мин)",
                "actions": [self._action(loader, "move-to-block", {"block": w.tid(block)})],
                "why": f"переезд около {minutes:.0f} мин; ОФ-1 получает сульфидную руду, глинистая остаётся в блоке "
                       f"{w.title(here)} до переработки на ОФ-2"}

    def _crew_clay_plant(self, incident: dict) -> dict | None:
        w = self.world
        end = incident.get("end") or ""
        if w.get(self.clay_plant, "state") != "stopped" or not end or int(w.get(self.clay_plant, "repair_crews", 0) or 0) > 1:
            return None
        left = self._left_h(end)
        if left < 0.5:
            return None
        new = left * float(self.r["second_crew_factor"])
        return {"label": f"Вторая бригада с подрядчиком на ремонт ОФ-2: пуск около {self._at(new)} вместо {hhmm(end)} "
                         f"(около {money(self._crew_cost(new))} млн тг)",
                "actions": [self._action(self.clay_plant, "expedite-repair", {})]}

    def _call_back(self) -> dict | None:
        """Trucks booked for planned service that have not reached the workshop yet stay at work for the hold time."""
        w = self.world
        booked = [t for t in self.trucks if w.get(t, "service_kind") == "planned"
                  and w.get(t, "state") not in ("service", "down")]
        if not booked:
            return None
        rel = self.params["reliability"]
        risk = 100.0 * float(rel["overdue_hazard_slope"]) / float(rel["pm_interval_h"])
        return {"label": f"Отложить плановое ТО и вернуть в работу: {', '.join(w.title(t) for t in booked)} "
                         f"({self._hold_text()}; риск отказа растёт на {num(risk)} % за каждый час сверх "
                         f"межсервисного интервала)",
                "actions": [self._action(t, "cancel-service", {"hold_min": self.hold_min}) for t in booked]}

    # -- the shift's response resources (mine/sim/response.py) ----------------------------

    @property
    def contractor_kzt_h(self) -> float:
        return float(self.r["contractor_people"]) * float(self.r["contractor_usd_h"]) * self.fx

    def _crew_cost(self, hours: float) -> float:
        """A second crew for `hours`: own fitters on overtime, the contractor paid at least its call-out."""
        return float(self.r["own_crew_kzt_h"]) * hours + self.contractor_kzt_h * max(hours, float(self.r["contractor_min_h"]))

    def _left_h(self, end: str) -> float:
        at = self.world.time_of(end)
        return max(0.0, at - self.world.now) / 3600.0 if at is not None else 0.0

    def _at(self, hours: float) -> str:
        """The simulated clock `hours` from now, as HH:MM."""
        return datetime.fromtimestamp(self.world.now + hours * 3600.0).isoformat(timespec="seconds")[11:16]

    def _crew_crusher(self, incident: dict) -> dict | None:
        w = self.world
        end = incident.get("end") or ""
        if w.get(self.crusher, "state") != "stopped" or not end or int(w.get(self.crusher, "repair_crews", 0) or 0) > 1:
            return None
        left = self._left_h(end)
        if left < 0.5:
            return None
        new = left * float(self.r["second_crew_factor"])
        return {"label": f"Вторая бригада с подрядчиком на ремонт дробилки: пуск около {self._at(new)} вместо {hhmm(end)} "
                         f"(около {money(self._crew_cost(new))} млн тг)",
                "actions": [self._action(self.crusher, "expedite-repair", {})]}

    def _crew_loader(self, down: str, incident: dict) -> dict | None:
        w = self.world
        end = incident.get("end") or ""
        if w.get(down, "state") != "down" or not end or int(w.get(down, "repair_crews", 0) or 0) > 1:
            return None
        left = self._left_h(end)
        if left < 0.5:
            return None
        new = left * float(self.r["second_crew_factor"])
        return {"label": f"Вторая бригада с подрядчиком на ремонт {w.title(down).replace('Экскаватор', 'экскаватора')}: "
                         f"до {self._at(new)} вместо {hhmm(end)} (около {money(self._crew_cost(new))} млн тг)",
                "actions": [self._action(down, "expedite-repair", {})]}

    def _lend_workshop(self, down: str, incident: dict) -> dict | None:
        w = self.world
        end = incident.get("end") or ""
        if w.get(down, "state") != "down" or not end or int(w.get(down, "repair_crews", 0) or 0) > 1 \
                or int(w.get("workshop", "bays_lent", 0) or 0) >= int(w.get("workshop", "bays_total", 4) or 4) - 1:
            return None
        left = self._left_h(end)
        if left < 0.5:
            return None
        new = left * float(self.r["workshop_lend_factor"])
        return {"label": f"Бригаду бокса мастерской — на ремонт {w.title(down).replace('Экскаватор', 'экскаватора')}: "
                         f"до {self._at(new)} вместо {hhmm(end)}; без затрат, но один бокс стоит и плановое ТО самосвалов ждёт",
                "actions": [self._action(down, "prioritise-repair", {})]}

    def _road_repair(self, segment: str, incident: dict) -> dict | None:
        end = incident.get("end") or ""
        hours = float(self.r["road_repair_h"])
        if not end or self._left_h(end) <= hours + 0.25:
            return None
        cost = float(self.r["road_crew_kzt_h"]) * hours
        return {"label": f"Аварийная дорожная бригада на «{self.world.title(segment)}»: одна полоса к {self._at(hours)} "
                         f"с ограничением {num(float(self.r['repaired_kmh']), 0)} км/ч вместо {hhmm(end)} "
                         f"(около {money(cost)} млн тг)",
                "actions": [self._action(segment, "repair-road", {})]}

    def _treat_ramp(self, incident: dict) -> dict | None:
        w = self.world
        closed = [s for s in self.segments if "scenario-heavy-rain" in str(w.get(s, "closed_by", "")).split(",")]
        end = incident.get("end") or ""
        hours = float(self.r["grading_h"])
        if not closed or not end or self._left_h(end) <= hours:
            return None
        segment = closed[0]
        tonnes = float(w.spec(segment, "length_m", 0.0)) * float(self.params["road"]["width_m"]) \
            * float(self.r["topping_m"]) * float(self.params["rock"]["loose_density_t_m3"])
        wet_h = self._left_h(end) + float(self.params["road"]["drying_min"]) / 60.0
        cost = tonnes * float(self.params["economics"]["stock_kzt_t"]) + float(self.r["grader_kzt_h"]) * wet_h
        amount = f"{tonnes:,.0f}".replace(",", " ")
        return {"label": f"Грейдер и подсыпка {amount} т на «{w.title(segment)}»: открыть к {self._at(hours)} с "
                         f"ограничением {num(float(self.r['treated_wet_kmh']), 0)} км/ч (около {money(cost)} млн тг)",
                "actions": [self._action(segment, "treat-surface", {})]}

    def _overtime(self) -> dict | None:
        w = self.world
        if str(w.get("workshop", "overtime_until", "")):
            return None
        down = [t for t in self.trucks if w.get(t, "state") == "down" and w.get(t, "service_kind") == "breakdown-major"]
        if not down:
            return None
        hours = float(self.r["workshop_overtime_h"])
        return {"label": f"Сверхурочная смена мастерской на {num(hours, 0)} ч: ремонт отказавших самосвалов "
                         f"({len(down)}) в {num(1 / float(self.r['second_crew_factor']), 1)} раза быстрее "
                         f"(до {money(float(self.r['own_crew_kzt_h']) * hours)} млн тг)",
                "actions": [self._action("workshop", "expedite-repair", {})]}

    def _switch_on(self, *modules: str) -> dict | None:
        """Switch on, in one option, those of the modules that answer this kind of incident and are off."""
        off = [m for m in modules if self._module_off(m)]
        if not off:
            return None
        titles = [MODULE_TITLES[m] for m in off]
        label = f"Включить {titles[0]}" if len(off) == 1 else \
            f"Включить модули: {', '.join(t.removeprefix('модуль ') for t in titles)}"
        return {"label": label, "actions": [self._action(m, "set-enabled", {"enabled": True}) for m in off]}

    def _hold_for(self, incident: dict) -> int:
        """Minutes an option's orders stand: until the incident's expected end (an order must not outlast the
        incident it answers: the forecast would hold it for the whole horizon), at most `advisor.hold_h`."""
        longest = int(round(self.p["hold_h"] * 60))
        end = incident.get("end") or ""
        if not end:
            return longest
        return max(30, min(longest, int(math.ceil(self._left_h(end) * 60.0 / 10.0) * 10)))

    def _hold_text(self) -> str:
        return f"{num(self.hold_min / 60.0, 0)} ч" if self.hold_min % 60 == 0 else f"{num(self.hold_min / 60.0)} ч"

    def _options(self, incident: dict) -> list[dict]:
        """Response options for the incident, the reference first."""
        w, kind = self.world, incident["kind"]
        self.hold_min = self._hold_for(incident)
        self.horizon_h = self.default_horizon_h
        min_feed = float(w.spec(self.mill, "min_feed_tph", 1440.0))
        max_feed = float(w.get(self.mill, "max_feed_tph", 0.0))
        setpoint = float(w.get(self.mill, "feed_setpoint_tph", 0.0))
        spare = self._mill_setpoint(max(min_feed, 0.9 * setpoint), "сберечь склад дроблёной руды, пока руды приходит меньше")
        # The modules that answer this kind of incident and are off: switched on together, as one option, first.
        switch = self._switch_on(*HANDLES.get(kind, {}))
        if kind == "loader-down":
            down = incident["thing"]
            ore_face = w.get(short(w.get(down, "block", "")), "material") not in ("waste", None)
            candidates = [switch, self._crew_loader(down, incident), self._move_loader(down) if ore_face else None,
                          self._lend_workshop(down, incident), self._reclaim(), self._call_back(), spare]
        elif kind == "crusher-stop":
            hours = float(w.get(self.stockpile, "level_t", 0.0)) / max(min_feed, 1.0)
            candidates = [switch, self._crew_crusher(incident), self._ore_to_pads(),
                          self._mill_setpoint(min_feed, f"растянуть склад дроблёной руды (около {num(hours)} ч питания)")]
        elif kind == "road-closed":
            candidates = [switch, self._road_repair(incident["thing"], incident), self._reclaim(), self._call_back(), spare]
        elif kind == "rain":
            candidates = [switch, self._treat_ramp(incident), self._call_back(), self._reclaim(), spare]
        elif kind == "truck-cluster":
            candidates = [switch, self._call_back(), self._overtime(), self._reclaim(), spare]
        elif kind == "clay-face":
            loader = incident["thing"]
            face = [(loader, short(w.get(loader, "block", "")))]
            candidates = [switch, self._move_off_clay(loader), self._clay_route(face, self.clay_stock),
                          self._clay_route(face, self.crusher), self._clay_route(face, self.clay_plant)]
        elif kind == "sag-reline":
            reline = self._reline()
            timing, latest = self._reline_options(reline) if reline else ([], w.now)
            candidates = [switch, *timing]
            if reline:
                # Every option over one horizon that holds the whole stop of the latest of them and the restart:
                # a later start must not look better only because its stop falls outside the forecast.
                self.horizon_h = self._reline_horizon_h(reline, latest)
                incident["text"] = incident["text"].split(" Варианты сравниваются")[0] + (
                    f" Варианты сравниваются на одном горизонте {self.horizon_h} ч: до конца остановки самого позднего "
                    f"из них ({self._day(latest + reline['hours'] * 3600.0)}) и ещё "
                    f"{num(float(self.params['scenario']['reline_recovery_h']), 0)} ч на пуск мельницы.")
        elif kind == "clay-plant-stop":
            faces = self._clay_faces()
            clay_loaders = [l for l, b in faces if w.get(b, "material") == "clay"]
            candidates = [switch, self._crew_clay_plant(incident), self._clay_route(faces, self.clay_stock),
                          self._move_off_clay(clay_loaders[0]) if clay_loaders else None,
                          self._clay_route(faces, self.crusher)]
        else:                                   # hard ore
            limit = f"предела по мощности {max_feed:.0f} т/ч при нынешней крепости"
            candidates = [switch, self._mill_setpoint(0.97 * max_feed, f"97 % {limit}; если руда станет крепче, "
                                                                       f"уставка окажется выше предела")
                          if max_feed > 0 else None,
                          self._mill_setpoint(0.90 * max_feed, f"90 % {limit}: запас на случай, если руда станет крепче")
                          if max_feed > 0 else None]
        options = [{"label": NOTHING, "actions": []}]
        for option in candidates:
            if option is not None and len(options) < self.max_options and option["label"] not in [o["label"] for o in options]:
                options.append(option)
        return options

    def _action(self, name: str, subject: str, body: dict) -> dict:
        return {"thing": self.world.tid(name), "subject": subject, "body": body}

    # -- one round --------------------------------------------------------------------------

    def step(self) -> list[Message]:
        out = super().step()
        if self.enabled and self.status == "running":
            self._collect()             # also while the simulation is paused: forecasts are computed meanwhile
        return out

    def decide(self, now: float) -> None:
        found = self.detect(now)
        key = found["key"] if found else ""
        if key != (self.incident["key"] if self.incident else ""):
            self._withdraw()
            if found:
                self._analyse(found, now)
            else:
                self._clear("Инцидент закончился.")
            return
        if found and found.get("end") != self.incident.get("end"):
            # The expected end moved (a crew was sent, the repair overran): the card says the new one.
            self.incident.update(end=found["end"], basis=found["basis"], text=found["text"])
        dest = self._clay_destination(found)
        if found and dest != self.incident.get("dest", dest):
            # The clay face's ore was sent elsewhere (by the grade-control module, an order): the card says where
            # it goes now, in the incident and in the decision that quotes it.
            old = self.incident["text"]
            self.incident.update(text=found["text"], dest=dest)
            self.last_decision = self.last_decision.replace(old, found["text"])
        if self.status == "running":
            self._collect()
            # A long horizon (a planned reline) takes longer to compute: the wait grows with it.
            wait_s = self.p["retry_min"] * 60.0 * max(1.0, self.horizon_h / self.default_horizon_h)
            if self.status == "running" and now - self.asked_at >= wait_s:
                self._ask(now)                  # no answer in time: ask again
        elif self.status == "ready" and found and not self.incident.get("applied") \
                and self._stock_h() <= self.incident["stock_h"] - float(self.p["reanalyse_stock_h"]):
            # The incident lasts and the stockpile has run down since the analysis: the options are worth
            # something else now, so they are built and forecast again.
            found["text"] += (f" Повторный разбор: запас склада дроблёной руды упал с {num(self.incident['stock_h'])} "
                              f"до {num(self._stock_h())} ч питания мельницы.")
            self._analyse(found, now, since=self.incident["since"], first=self.incident["sim_time"])

    def _stock_h(self) -> float:
        return float(self.world.get(self.stockpile, "hours_of_feed", 0.0))

    def _analyse(self, incident: dict, now: float, since: float | None = None, first: str = "") -> None:
        w = self.world
        incident["since"] = now if since is None else since
        incident["sim_time"] = first or str(w.get("mine", "sim_time", ""))
        incident["stock_h"] = self._stock_h()
        incident["dest"] = self._clay_destination(incident)
        self.incident = incident
        self.options = self._options(incident)
        self.recommended = 0
        self._ask(now)

    def _ask(self, now: float) -> None:
        w = self.world
        self.round += 1
        self.status, self.asked_at = "running", now
        for n, (option, slot) in enumerate(zip(self.options, self.slots), start=1):
            # The simulated moment makes the ID unique across restarts of the service.
            option.update(slot=slot, request_id=f"advisor-{int(now)}-{self.round}-{n}", result=None, error="", verdict="")
            self.send("mine", "forecast", {
                "slot": int(slot.split("-")[-1]), "horizon_h": self.horizon_h, "replicas": self.replicas,
                "request_id": option["request_id"],
                "requested_by": self.name, "subject": w.tid(self.incident["thing"]),
                "label_a": NOTHING, "label_b": option["label"], "actions_a": [], "actions_b": option["actions"]})
        self.last_decision = (f"Инцидент: {self.incident['text']} Запрошены прогнозы {len(self.options)} вариантов "
                              f"на {self.horizon_h} ч.")

    def _collect(self) -> None:
        w = self.world
        for option in self.options:
            if option.get("result") is not None or option.get("error"):
                continue
            twin = w.values.get(option["slot"], {})
            if twin.get("request_id") != option["request_id"]:
                continue
            if twin.get("status") == "ready":
                replicas = max(1, int(twin.get("replicas") or 1))
                option["result"] = {"delta": int(twin.get("delta_total_kzt") or 0),
                                    "cu": float(twin.get("delta_cu_t") or 0.0),
                                    "replicas": replicas,
                                    # The standard error of the mean gain: as measured over the paired replicas,
                                    # but never below what the measured paired noise of one replica allows.
                                    "se": max(float(twin.get("delta_total_se_kzt") or 0.0),
                                              self.noise_kzt / math.sqrt(replicas)),
                                    "cu_se": float(twin.get("delta_cu_se_t") or 0.0),
                                    "split": split_of(twin),
                                    "summary": str(twin.get("summary_text") or ""),
                                    "copper_text": str(twin.get("copper_text") or ""),
                                    "overloads": (int(twin.get("a_overloads") or 0), int(twin.get("b_overloads") or 0)),
                                    "refused": str(twin.get("refused_actions") or ""),
                                    # What the mill gets hour by hour in each branch: does it run short without the option?
                                    "a_mill": series(twin.get("a_series_mill_tph")),
                                    "b_mill": series(twin.get("b_series_mill_tph")),
                                    "a_stops": int(twin.get("a_mill_stops") or 0),
                                    "b_stops": int(twin.get("b_mill_stops") or 0),
                                    "clay_cu": float(twin.get("b_clay_cu_t") or 0.0) - float(twin.get("a_clay_cu_t") or 0.0),
                                    "clay_stock": int(twin.get("b_to_clay_stock_t") or 0) - int(twin.get("a_to_clay_stock_t") or 0),
                                    "ofs1_clay": (float(twin.get("a_ofs1_clay_pct") or 0.0), float(twin.get("b_ofs1_clay_pct") or 0.0))}
            elif twin.get("status") == "error":
                option["error"] = str(twin.get("error_text") or "прогноз не получен")
        if all(o.get("result") is not None or o.get("error") for o in self.options):
            self._rank()

    def _rank(self) -> None:
        w = self.world
        scored = [(o["result"]["delta"], o["result"]["cu"], -n) for n, o in enumerate(self.options, start=1)
                  if o.get("result") is not None]
        ranked = sorted(scored, reverse=True)
        for rank, (_, _, minus_n) in enumerate(ranked, start=1):
            self.options[-minus_n - 1]["rank"] = rank
        # The best-ranked option that clears every bar; «change nothing» if none does.
        order = [-minus_n for _, _, minus_n in ranked]             # option numbers, best first
        result = {n: self.options[n - 1]["result"] for n in order}
        for n in order:
            self.options[n - 1]["confidence"] = self._confidence(n, result[n])
            self.options[n - 1]["verdict"] = self._verdict(n, result[n])
        clears = lambda n: n == 1 or self.options[n - 1]["verdict"] == ""
        self.recommended = next((n for n in order if clears(n)), 0)
        for n in order:
            self.options[n - 1]["reason"] = self._reason(n, result[n], n == self.recommended)
        above = order[:order.index(self.recommended)] if self.recommended else order
        # Held back by a bar other than the scatter: the gain itself would be sure enough.
        skipped = [n for n in above if n != 1 and self._sure(result[n])]
        self.status = "ready"
        self.analysed_at = w.now
        best = self.options[self.recommended - 1] if self.recommended else None
        if best is None:
            text = f"Инцидент: {self.incident['text']} Прогнозы вариантов не получены."
        elif self.recommended == 1:
            text = (f"Инцидент: {self.incident['text']} Ни один из {len(self.options) - 1} вариантов не выигрывает у "
                    f"«{NOTHING}» за {self.horizon_h} ч достоверно (выигрыш не меньше {num(self.p['min_t'])} стандартных "
                    f"ошибок и {money(self.p['min_gain_kzt'])} млн тг, без потери меди): рекомендация — оставить как есть."
                    f"{self._why_nothing()}{self._resource_reasons()}")
        else:
            r = best["result"]
            text = (f"Инцидент: {self.incident['text']} Рекомендован вариант «{best['label']}» (№ {self.recommended}): "
                    f"{signed(r['delta'] / 1e6)} ± {money(r['se'])} млн тг и {signed(r['cu'])} т меди за {self.horizon_h} ч "
                    f"против «{NOTHING}», достоверность {best['confidence']}; {self._split_text(r)}."
                    + (f" {best['reason']}" if best.get("reason") and self._resource(best) else "")
                    + (f" {r['copper_text']}" if r.get("copper_text") else ""))
        if skipped:
            text += (" Не рекомендованы, хотя итог выше: " + "; ".join(
                f"«{self.options[n - 1]['label']}» — {self.options[n - 1]['verdict']}" for n in skipped) + ".")
        self.decisions += 1
        self.last_decision = text

    def _t(self, r: dict) -> float:
        """The gain in standard errors."""
        return r["delta"] / r["se"] if r["se"] > 0 else (math.inf if r["delta"] > 0 else 0.0)

    def _sure(self, r: dict) -> bool:
        """The gain is beyond the scatter of the forecast and large enough to be worth an order."""
        return self._t(r) >= float(self.p["min_t"]) and r["delta"] >= float(self.p["min_gain_kzt"])

    def _confidence(self, n: int, r: dict) -> str:
        """How sure the forecast is that option `n` beats «change nothing», in Russian words."""
        if n == 1:
            return ""
        t = self._t(r)
        if t >= 2.0 * float(self.p["min_t"]):
            return "высокая"
        if t >= float(self.p["min_t"]):
            return "средняя"
        if t >= 1.0:
            return "низкая"
        if t > -1.0:
            return "нет: в пределах разброса"
        return "нет: вариант хуже"

    def _verdict(self, n: int, r: dict) -> str:
        """Why option `n` is not recommended, in Russian; '' if it may be."""
        if n == 1:
            return ""
        min_gain, max_loss, min_cu = float(self.p["min_gain_kzt"]), float(self.p["max_cu_loss_t"]), float(self.p["min_cu_t"])
        cash = sum(r["split"][part] for part in CASH_PARTS)
        if r.get("refused"):
            return "действие варианта не выполнено"
        if r["delta"] <= 0:
            return f"не выгоднее, чем «{NOTHING}»"
        if self._t(r) < float(self.p["min_t"]):
            return (f"выигрыш {signed(r['delta'] / 1e6)} млн тг меньше {num(self.p['min_t'])} стандартных ошибок "
                    f"(±{money(r['se'])} млн тг): в пределах разброса прогноза")
        if r["delta"] < min_gain:
            return f"выигрыш меньше {money(min_gain)} млн тг за {self.horizon_h} ч, не стоит указания"
        if r["cu"] < -max_loss:
            return f"теряет {num(-r['cu'])} т меди за {self.horizon_h} ч, мельница позже её не наверстает"
        if cash < min_gain and (abs(r["cu"]) < min_cu or cash < 0.5 * r["delta"]):
            return (f"{ON_STOCK} и затратами за {self.horizon_h} ч ({signed(cash / 1e6)} млн тг), а оценкой "
                    f"работы парка в запасах руды и вскрыше ({signed(r['split']['work'] / 1e6)} млн тг)")
        return ""

    # -- why an option pays or does not ----------------------------------------------------

    def _resource(self, option: dict) -> tuple | None:
        """The shift's response resource an option spends, as RESOURCES names it; None for another lever."""
        for a in option["actions"]:
            kind = self.world.attrs.get(short(a["thing"]), {}).get("kind", "")
            if (kind, a["subject"]) in RESOURCES:
                return RESOURCES[(kind, a["subject"])]
        return None

    @staticmethod
    def _short_of_ore(a: list[float], b: list[float]) -> tuple[int, float] | None:
        """Without the option (branch A): the first hour (1-based) in which the mill gets less than 90 % of the
        most either branch feeds it in an hour, and the least it gets from then on, t/h; None when it never does."""
        if len(a) < 2:
            return None
        full = max(a + b)
        if full <= 0:
            return None
        for hour, value in enumerate(a, start=1):
            # Short of ore, and short because of what the option changes: with it the mill gets clearly more.
            if value < 0.9 * full and hour <= len(b) and b[hour - 1] - value >= 0.05 * full:
                return hour, min(a[hour - 1:])
        return None

    @staticmethod
    def _short_anyway(a: list[float], b: list[float]) -> bool:
        """The mill runs short of ore in both branches alike: the option does not change it."""
        full = max(a + b) if a or b else 0.0
        return full > 0 and any(x < 0.9 * full for x in a) and any(y < 0.9 * full for y in b)

    def _starve_text(self, r: dict) -> str:
        """What happens to the mill's feed without the option (branch A), in Russian; '' when it is not cut.
        A cut is not a lack of ore: with the plant-control module on it is the module stretching the stockpile,
        and for a stopped crusher the text says how long the stockpile lasts against the expected restart."""
        short_a = self._short_of_ore(r.get("a_mill", []), r.get("b_mill", []))
        if short_a is None:
            return ""
        hour, least = short_a
        at = lambda h: "уже в первый час" if h <= 1 else f"через {h - 1} ч"
        inc, stock = self.incident or {}, ""
        if inc.get("kind") == "crusher-stop" and inc.get("end") and inc.get("stock_h", 0.0) < self._left_h(inc["end"]):
            stock = (f" (склада дроблёной руды на {num(inc['stock_h'])} ч питания не хватит до пуска дробилки "
                     f"в {hhmm(inc['end'])})")
        cutter = ("модуль управления фабрикой начнёт снижать подачу мельницы"
                  if self.world.get("module-plant", "status") == "online" else "подача мельницы начнёт падать")
        a = r.get("a_mill", [])
        if least < 1.0 or r.get("a_stops", 0) > r.get("b_stops", 0):
            # The mill stops in the hour its feed is least; before that the feed may only be cut.
            stop = next((h for h in range(hour, len(a) + 1) if a[h - 1] <= least + 1.0), hour)
            if stop > hour:
                return f"{at(hour)} {cutter}{stock}, а {at(stop)} мельница встанет"
            return f"мельница встанет {at(stop)}{stock}"
        least_text = f"{least:,.0f}".replace(",", " ")
        if cutter.startswith("модуль"):
            return f"{at(hour)} {cutter}{stock} и снизит её до {least_text} т/ч"
        return f"{at(hour)} {cutter}{stock} и упадёт до {least_text} т/ч"

    def _stock_reason(self, r: dict) -> str:
        """Why a gain beyond the scatter rests on inventory, not copper, in Russian: «no copper» only when the
        copper differs by less than `advisor.min_cu_t`; else where the copper comes from."""
        cu, split = r["cu"], r["split"]
        if abs(cu) < float(self.p["min_cu_t"]):
            return STOCK_REASON
        work = signed(split["work"] / 1e6)
        if cu > 0 and split["grade"] < 0 and -split["grade"] >= 0.5 * max(split["revenue"], 1):
            return (f"медь {signed(cu)} т взята из запасов богатой руды, а выигрыш держится на оценке работы парка "
                    f"({work} млн тг)")
        return f"выигрыш держится на оценке работы парка в запасах руды и вскрыше ({work} млн тг), а не на меди"

    def _reason(self, n: int, r: dict, recommended: bool) -> str:
        """Why option `n` pays or does not, in one Russian sentence; '' for «change nothing»."""
        if n == 1:
            return ""
        option = self.options[n - 1]
        verdict = option.get("verdict", "")
        figures = f"{signed(r['delta'] / 1e6)} млн тг, медь {signed(r['cu'])} т за {self.horizon_h} ч"
        resource = self._resource(option)
        why = option.get("why", "")
        if resource is None:
            facts = f" ({why})" if why else ""
            if recommended:
                return f"Окупается{facts}: {figures}."
            return f"Не рекомендован{facts}: {verdict}." if verdict else f"Окупается{facts}: {figures}, но другой вариант даёт больше."
        what, who, of = resource
        if of == "ремонта ОФ-2":
            cost = -r["split"].get("resources", 0)
            gained = r.get("clay_cu", 0.0)
            pays = "окупается" if recommended or not verdict else (
                "не окупается достоверно (выигрыш в пределах разброса прогноза)" if "разброс" in verdict else "не окупается")
            left = self._left_h(self.incident.get("end") or "") if self.incident else 0.0
            if ON_STOCK in verdict and not recommended:
                # Beyond the scatter, but the gain is inventory, not copper: say so instead of «не окупается».
                return (f"{what}: {self._stock_reason(r)}; до пуска {num(left)} ч, со второй бригадой ОФ-2 запустится раньше "
                        f"({signed(gained)} т меди за {self.horizon_h} ч); {who} стоит {money(cost)} млн тг, "
                        f"итог {figures}.")
            head = (f"{what} {pays}: до пуска {num(left)} ч, со второй бригадой ОФ-2 запустится раньше и даст "
                    f"{signed(gained)} т меди за {self.horizon_h} ч; {who} стоит {money(cost)} млн тг")
            tail = f", но другой вариант даёт больше" if not recommended and not verdict else (
                f" ({verdict})" if verdict and "разброс" not in verdict and "не выгоднее" not in verdict else "")
            return f"{head}, итог {figures}{tail}."
        cost = -r["split"].get("resources", 0)
        price = f"{who} стоит {money(cost)} млн тг" if cost >= 50_000 else f"{who} денег не стоит"
        starve = self._starve_text(r)
        if recommended:
            head = f"{what} окупается: без этого {starve}" if starve else f"{what} окупается"
            return f"{head}; {price}, итог {figures}."
        if not verdict:
            # It clears every bar, but another option gains more.
            head = f"{what} окупается: без этого {starve}" if starve else f"{what} окупается"
            return f"{head}; {price}, итог {figures}, но другой вариант даёт больше."
        pays = "не окупится" if r["delta"] <= 0 else "не окупается достоверно (в пределах разброса прогноза)" \
            if self._t(r) < float(self.p["min_t"]) else "не окупается"
        if ON_STOCK in verdict:
            # Beyond the scatter, but the gain is inventory, not copper: say so instead of «не окупается».
            if starve:
                return f"{what}: без этого {starve}, но {who}: {self._stock_reason(r)}; итог {figures}."
            return f"{who[:1].upper()}{who[1:]}: {self._stock_reason(r)}; итог {figures}."
        # The verdict says more than the figures only when it is not about the scatter or a loss in money.
        why = verdict if verdict and "разброс" not in verdict and "не выгоднее" not in verdict else figures
        if starve:
            return f"{what}: без этого {starve}, но {who} {pays}: {why}."
        if self._short_anyway(r.get("a_mill", []), r.get("b_mill", [])):
            return f"Мельнице не хватает руды и с этим вариантом: {who} руды не прибавляет и {pays}: {why}."
        return f"Склада хватает до конца {of}: мельница и без этого получает полную подачу; {who} {pays}: {figures}."

    def _resource_reasons(self) -> str:
        """«Change nothing» recommended: why the shift's response resources among the options do not pay."""
        reasons = [o["reason"] for o in self.options[1:] if o.get("reason") and self._resource(o)]
        return (" " + " ".join(reasons)) if reasons else ""

    def _why_nothing(self) -> str:
        """Who already answers the incident when «change nothing» is recommended: the modules that are on."""
        kind = self.incident["kind"] if self.incident else ""
        on = [m for m in HANDLES.get(kind, ()) if self.world.get(m, "status") == "online"]
        text = ""
        if on:
            text = " Инцидент уже отрабатывают включённые модули: " + "; ".join(
                f"{MODULE_TITLES[m]} {HANDLES[kind][m]}" for m in on) + "."
        switched = [o for o in self.options if o["actions"] and all(a["subject"] == "set-enabled" for a in o["actions"])]
        if switched:
            text += f" Вариант «{switched[0]['label']}» по прогнозу достоверного выигрыша не даёт."
        if kind == "crusher-stop":
            text += (" Пока дробилка стоит, мельница получает руду только со склада дроблёной руды: меньшая уставка "
                     "растягивает склад, но меди за горизонт не прибавляет — извлечение при подаче ниже проектной не растёт.")
        return text

    def _split_text(self, r: dict) -> str:
        return "из них " + ", ".join(f"{SPLIT_TITLES[part]} {signed(r['split'][part] / 1e6)}" for part in MARGIN_SPLIT) \
            + " млн тг"

    def _clear(self, why: str) -> None:
        self.incident, self.options, self.status, self.recommended = None, [], "idle", 0
        self.horizon_h = self.default_horizon_h
        self.analysed_at = None
        self.last_decision = why

    # -- apply ------------------------------------------------------------------------------

    def command(self, subject: str, body: dict) -> list[Message]:
        if subject != "apply":
            self.last_decision = f"Команда «{subject}» не поддерживается."
            return []
        option = body.get("option")
        if not self.enabled:
            self.last_decision = "Вариант не применён: модуль выключен."
            return []
        if self.status != "ready" or isinstance(option, bool) or not isinstance(option, int) \
                or not 1 <= option <= len(self.options):
            self.last_decision = (f"Вариант {option!r} не применён: " +
                                  ("разбор инцидента не закончен." if self.status != "ready" else
                                   f"допустимо от 1 до {len(self.options)}."))
            return []
        chosen = self.options[option - 1]
        w = self.world
        # A truck that has broken down since the forecast cannot be kept at work: its call-back is left out.
        actions = [a for a in chosen["actions"]
                   if not (a["subject"] == "cancel-service" and w.get(short(a["thing"]), "state") == "down")]
        messages = [(short(a["thing"]), a["subject"], a["body"]) for a in actions]
        r = chosen.get("result") or {"delta": 0, "cu": 0.0}
        forecast = (f"прогноз {signed(r['delta'] / 1e6)} млн тг, {signed(r['cu'])} т меди за {self.horizon_h} ч"
                    if chosen.get("result") else "прогноза нет")
        rank = int(chosen.get("rank", 0))
        place = f", место {rank} из {len(self.options)} по прогнозу" if rank else ""
        text = (f"Советник: применён вариант «{chosen['label']}» (№ {option} в списке советника{place}; {forecast}); "
                f"сообщений отправлено: {len(messages)}." + (f" {self.describe_ru(actions)}." if messages else ""))
        if self.incident is not None:
            self.incident["applied"] = True          # no new analysis of this incident: an answer is under way
        self.applied = {"option": option, "rank": rank, "label": chosen["label"], "sim_time": str(w.get("mine", "sim_time", "")),
                        "text": text, "undo": self._undo(actions)}
        self.decisions += 1
        self.last_decision = text
        severity = "info"
        return [*messages, ("journal", "record", {"thing": w.tid(self.name), "severity": severity, "text": text})]

    def _undo(self, actions: list[dict]) -> list[Message]:
        """Messages that end, when the incident is over, the time-limited orders an option gave: the
        same lever for one minute more at its base value, after which it returns to its module or rule."""
        w, undo = self.world, []
        for a in actions:
            name, subject = short(a["thing"]), a["subject"]
            if subject == "set-destination":
                base = self.clay_plant if w.get(name, "material") == "clay" else self.crusher
                undo.append((name, subject, {"destination": w.tid(base), "hold_min": 1,
                                             "reason": "инцидент закончился"}))
            elif subject == "set-reclaim":
                undo.append((name, subject, {"tph": 0, "hold_min": 1}))
            elif subject == "set-feed-rate":
                undo.append((name, subject, {"tph": float(w.spec(self.mill, "design_tph", 2400)), "hold_min": 1}))
        return undo

    def _withdraw(self) -> None:
        """The incident an option was applied for is over: its orders end now instead of at their hold time."""
        undo = self.applied.pop("undo", None) if self.applied else None
        if not undo or self.incident is None:
            return
        text = (f"Советник: инцидент закончился, указания варианта «{self.applied['label']}» (№ {self.applied['option']}) "
                f"сняты, рычаги возвращаются модулям и базовым правилам: "
                f"{self.describe_ru([{'thing': n, 'subject': s, 'body': b} for n, s, b in undo])}.")
        for message in undo:
            self.send(*message)
        self.send("journal", "record", {"thing": self.world.tid(self.name), "severity": "info", "text": text}, text)

    def describe(self, actions: list[dict]) -> str:
        parts = []
        for a in actions:
            body = ", ".join(f"{k}={short(v) if isinstance(v, str) else v}" for k, v in a["body"].items() if k != "reason")
            parts.append(f"{short(a['thing'])}: {a['subject']}" + (f" ({body})" if body else ""))
        return "; ".join(parts)

    def describe_ru(self, actions: list[dict]) -> str:
        """The messages of an option in Russian, for the journal."""
        w, parts = self.world, []
        for a in actions:
            name, subject, body = short(a["thing"]), a["subject"], a["body"]
            title, hold = w.title(name), body.get("hold_min")
            until = f" на {hold / 60:.0f} ч" if isinstance(hold, (int, float)) and hold >= 60 else \
                (f" на {hold:.0f} мин" if isinstance(hold, (int, float)) else "")
            if subject == "move-to-block":
                parts.append(f"{title}: перевод на {w.title(short(body.get('block', '')))}")
            elif subject == "set-reclaim":
                parts.append(f"{title}: подача в дробилку {float(body.get('tph', 0)):.0f} т/ч{until}")
            elif subject == "set-feed-rate":
                parts.append(f"{title}: уставка питания {float(body.get('tph', 0)):.0f} т/ч{until}")
            elif subject == "set-destination":
                parts.append(f"{title}: руда на «{w.title(short(body.get('destination', '')))}»{until}")
            elif subject == "cancel-service":
                parts.append(f"{title}: плановое ТО отложено{until}")
            elif subject == "set-enabled":
                parts.append(f"{title}: {'включить' if body.get('enabled') else 'выключить'}")
            elif subject == "expedite-repair":
                parts.append(f"{title}: {'сверхурочная смена' if name == 'workshop' else 'вторая ремонтная бригада'}")
            elif subject == "prioritise-repair":
                parts.append(f"{title}: на ремонт — бригада бокса мастерской")
            elif subject == "repair-road":
                parts.append(f"{title}: аварийная дорожная бригада")
            elif subject == "treat-surface":
                parts.append(f"{title}: грейдер и подсыпка")
            elif subject == "plan-reline":
                when = dict(RELINE_STARTS).get(str(body.get("start", "")), f"через {body.get('start_in_h')} ч")
                parts.append(f"{title}: начать замену футеровки {when}")
            else:
                parts.append(f"{title}: {subject}")
        return "; ".join(parts)

    def revert(self) -> list[Message]:
        # Applied options are operator's orders and stay; the analysis on the page goes.
        self._clear("Модуль выключен: инциденты не разбираются.")
        return []

    # -- the twin ---------------------------------------------------------------------------

    def kpi_value(self) -> float:
        if self.status != "ready" or not self.recommended:
            return 0.0
        result = self.options[self.recommended - 1].get("result")
        return round(result["delta"] / 1e6, 1) if result else 0.0

    def own_features(self) -> dict:
        w, inc = self.world, self.incident
        out = {
            "incident": inc["key"] if inc else "",
            "incident_thing": w.tid(inc["thing"]) if inc else "",
            "incident_text": inc["text"] if inc else "",
            "incident_sim_time": inc["sim_time"] if inc else "",
            "incident_end_sim_time": (inc.get("end") or "") if inc else "",
            "incident_basis": (inc.get("basis") or "") if inc else "",
            "analysis_status": self.status,
            "analysis_sim_time": datetime.fromtimestamp(self.analysed_at).isoformat(timespec="seconds")
            if self.analysed_at else "",
            "analysis_horizon_h": float(self.horizon_h),
            "options_count": len(self.options),
        }
        for n in range(1, 5):
            o = self.options[n - 1] if n <= len(self.options) else None
            result = (o or {}).get("result")
            if o is None:
                summary = ""
            elif result is not None:
                summary = result["summary"]
            elif o.get("error"):
                summary = f"Прогноз не получен: {o['error']}"
            else:
                summary = "Прогноз считается."
            out.update({
                f"option{n}_label": o["label"] if o else "",
                f"option{n}_actions": self.describe(o["actions"]) if o else "",
                f"option{n}_actions_text": self.describe_ru(o["actions"]) if o else "",
                f"option{n}_delta_kzt": result["delta"] if result else 0,
                f"option{n}_cu_delta_t": round(result["cu"], 2) if result else 0.0,
                f"option{n}_gain_se_kzt": round(result["se"]) if result and n != 1 else 0,
                f"option{n}_confidence": o.get("confidence", "") if o and self.status == "ready" else "",
                **{f"option{n}_split_{part}_kzt": result["split"][part] if result else 0 for part in MARGIN_SPLIT},
                f"option{n}_recommendable": bool(o and result and self.status == "ready" and not o.get("verdict")
                                                 and n != 1),
                f"option{n}_verdict": o.get("verdict", "") if o and self.status == "ready" else "",
                f"option{n}_rank": o.get("rank", 0) if o and self.status == "ready" else 0,
                f"option{n}_forecast": w.tid(o["slot"]) if o and o.get("slot") else "",
                f"option{n}_summary": summary,
                f"option{n}_copper_text": result["copper_text"] if result else "",
                f"option{n}_cost_kzt": -result["split"].get("resources", 0) if result else 0,
                f"option{n}_reason": o.get("reason", "") if o and self.status == "ready" else "",
            })
        out.update({
            "recommended": self.recommended if self.status == "ready" else 0,
            "applied_option": self.applied.get("option", 0),
            "applied_rank": self.applied.get("rank", 0),
            "applied_label": self.applied.get("label", ""),
            "applied_sim_time": self.applied.get("sim_time", ""),
            "applied_text": self.applied.get("text", ""),
        })
        return out

    def effect_text(self) -> str:
        if not self.enabled:
            return "Модуль выключен: инциденты не разбираются, варианты ответа не прогнозируются."
        if self.incident is None:
            return ("Инцидентов нет. Советник следит за экскаваторами, дробилкой, ОФ-2, глинистостью забоев, дорогами, "
                    "погодой, самосвалами и крепостью руды и при инциденте сравнивает варианты ответа по прогнозу на "
                    f"{self.horizon_h} ч.")
        if self.status == "running":
            return f"{self.incident['text']} Считаются прогнозы {len(self.options)} вариантов ответа."
        text = self.last_decision
        if self.applied:
            text += f" Последний применённый вариант: «{self.applied['label']}» ({self.applied['sim_time'][11:16]})."
        return text
