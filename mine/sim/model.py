"""Discrete-time model of the Bozshakol mine (the pit, the sulphide concentrator ОФ-1 and the clay-ore plant ОФ-2).

The model owns the state of every twin the simulator writes. It knows
nothing about MQTT or Ditto: the runner feeds it messages, advances it with
`step`, and publishes what `values` returns.

Time is simulated seconds since the shift start. One `step` is one simulated
second, so the result does not depend on the speed-up the stand runs at.

The subsystems live in their own modules and meet here:

    roads.py        road graph, closures, wet and dry surface, routing
    pit.py          blocks, drilling, charging, blasting, pushbacks, the short-term plan
    fleet.py        trucks, loaders, fuel station, the fixed dispatch plan
    plant.py        dump points, crusher, conveyor, stockpile, mill, flotation, ОФ-2, clay-ore stockpile, substation
    reliability.py  wear, failures, the workshop
    weather.py      air temperature and rain
    scenarios.py    what-if scenarios
    kpis.py         mine indicators and the material balance

The decision modules are services of their own (mine/modules). They act on
the model only through `handle_message`, and each of them writes its own
twin: the model publishes none of the module twins. The forecast engine
(forecast.py) runs copies of this model ahead; the runner publishes its
twins.
"""
from __future__ import annotations

import random
from datetime import datetime, timedelta

from mine.sim import scenarios
from mine.sim.fleet import Fleet
from mine.sim.kpis import Kpis
from mine.sim.pit import Pit
from mine.sim.plant import Plant
from mine.sim.reliability import Reliability
from mine.sim.response import Response
from mine.common.roads import RoadNetwork
from mine.sim.weather import Weather

ALLOWED_SPEEDUPS = (1, 10, 20, 60)       # `set-speed`, docs/mine/ARCHITECTURE.md, section 5.2


def short(thing_id: str) -> str:
    return thing_id.split(":", 1)[1] if ":" in thing_id else thing_id


class Simulation:
    def __init__(self, things: list[dict], world: dict, params: dict, seed: int | None = None):
        self.ns = world["namespace"]
        self.world, self.params = world, params
        self.seed = params["sim"]["seed"] if seed is None else seed
        self.salt = ""                          # set by `reseed` in a forecast replica
        self.clock = world["clock"]
        self.epoch = datetime.fromisoformat(self.clock["shift_start"])
        self.now = 0.0
        self.speedup = int(params["sim"]["default_speedup"])
        self.paused = False
        self.events: list[dict] = []
        self.seq = 0
        self.refusals: list[str] = []           # what the last message was refused for
        # In a forecast branch an incident ends at its expected end (mine/sim/forecast.py); on the stand the
        # operator ends it, and a scenario that outlasts its estimate gets a new one.
        self.auto_end = False

        self.things = {short(t["thingId"]): t for t in things}
        self.by_kind: dict[str, list[dict]] = {}
        for t in things:
            self.by_kind.setdefault(t["attributes"]["kind"], []).append(t)

        # Shifts change at the two hours named in world.yaml.
        self.shift_index = 0
        self.next_shift_at = self._next_shift_change(0.0)
        self.shift_started_at = 0.0             # the model starts at the beginning of a shift

        self.roads = RoadNetwork.from_things(things, params["road"])
        self.kpis = Kpis(self)
        self.weather = Weather(self)
        self.pit = Pit(self)
        self.plant = Plant(self)
        self.reliability = Reliability(self)
        self.fleet = Fleet(self)
        self.response = Response(self)
        self.scenarios = scenarios.build(self)
        self.fleet.start()

    # -- helpers ---------------------------------------------------------------

    def rng(self, stream: str) -> random.Random:
        """A random stream of its own, fixed by the seed, the salt of a forecast replica and the stream's key.

        Every random process has its own stream, keyed by the entity it belongs to: the driving and the
        tipping of each truck, the bucket fill and the pass time of each loader, the failures and the repairs
        of each machine, the charge of each block, the precipitation of each day. What one entity draws then
        never shifts what another one draws: two forecast branches with the same actions are identical, and
        a small action changes only the draws of what it actually changes (common random numbers)."""
        key = f"{self.seed}/{self.salt}/{stream}" if self.salt else f"{self.seed}/{stream}"
        return random.Random(key)

    def reseed(self, salt: str) -> None:
        """Draw the future anew: every entity's stream restarts from `salt` (a forecast replica: the fork
        moment and the replica's number), and the operating hours until the next defect of each machine are
        drawn again, which the memoryless onset of defects allows. The state of the mine stays as it is:
        defects already developing, repairs under way, the precipitation planned for today."""
        self.salt = salt
        self.fleet.reseed()
        self.reliability.reseed()

    def tid(self, name: str) -> str:
        return f"{self.ns}:{name}"

    def sim_time(self, at: float | None = None) -> str:
        moment = self.epoch + timedelta(seconds=self.now if at is None else at)
        return moment.isoformat(timespec="seconds")

    def _next_shift_change(self, after_s: float) -> float:
        """Simulated second of the first shift change later than `after_s`."""
        moment = self.epoch + timedelta(seconds=after_s)
        day = moment.replace(hour=0, minute=0, second=0, microsecond=0)
        changes = [day + timedelta(days=d, hours=self.clock[key])
                   for d in (0, 1) for key in ("day_shift_from_h", "day_shift_to_h")]
        return (min(c for c in changes if c > moment) - self.epoch).total_seconds()

    def is_day_shift(self, at: float | None = None) -> bool:
        hour = (self.epoch + timedelta(seconds=self.now if at is None else at)).hour
        return self.clock["day_shift_from_h"] <= hour < self.clock["day_shift_to_h"]

    def log(self, severity: str, thing: str, text: str) -> None:
        """Queue one journal event; the runner publishes it to the journal twin."""
        self.seq += 1
        self.events.append({"seq": self.seq, "severity": severity, "thing": self.tid(thing),
                            "text": text, "sim_time": self.sim_time()})

    def record(self, body: dict) -> None:
        """Live message `record {text, severity?, thing?}` to the journal: a
        module that has no twin of the mine to change (the advisor) writes into
        the journal through the simulator, its only writer."""
        text = body.get("text")
        thing = short(body["thing"]) if isinstance(body.get("thing"), str) else "journal"
        if not isinstance(text, str) or not text.strip():
            self.refuse("journal", "Запись в журнал без текста отклонена")
            return
        severity = body.get("severity") if body.get("severity") in ("info", "warning", "alarm") else "info"
        self.log(severity, thing if thing in self.things else "journal", text.strip()[:400])

    def refuse(self, thing: str, text: str) -> None:
        """A message that cannot be applied: journalled as a warning, and kept
        until the next message so that a forecast can tell a refused action
        from one that changed nothing."""
        self.refusals.append(text)
        self.log("warning", thing, text)

    def hold_s(self, body: dict, name: str, title: str):
        """Seconds an operator's order is to stand, from `hold_min` in its body:
        None for an order without one (a module's decision), False if the value
        is not allowed (the message is then ignored and journalled)."""
        if "hold_min" not in body:
            return None
        hold = body.get("hold_min")
        if isinstance(hold, bool) or not isinstance(hold, (int, float)) or not 1 <= hold <= 1440:
            self.refuse(name, f"{title}: срок указания {hold!r} мин отклонён, допустимо от 1 до 1440 мин")
            return False
        return float(hold) * 60.0

    def pop_events(self) -> list[dict]:
        events, self.events = self.events, []
        return events

    def warm_start(self) -> None:
        """Run the model silently so that the first published state is mid-shift:
        trucks spread over the loop, trip counters and averages already filled."""
        for _ in range(int(self.clock["warm_start_min"] * 60)):
            self.step()
        self.events.clear()
        self.seq = 0
        self.log("info", "mine", f"Симуляция запущена, смена идёт с {self.sim_time(0)[11:16]}")

    # -- one step --------------------------------------------------------------

    def step(self, dt: float = 1.0) -> None:
        """Advance the model by `dt` simulated seconds."""
        self.now += dt
        if self.now >= self.next_shift_at:
            self.shift_index += 1
            self.next_shift_at = self._next_shift_change(self.now)
            self.shift_started_at = self.now
            self.plant.new_shift()
            self.kpis.new_shift()
            name = "дневная" if self.is_day_shift() else "ночная"
            self.log("info", "mine", f"Началась {name} смена: экипажи самосвалов меняются после разгрузки")
        self.weather.step(dt)
        self.pit.step(dt)
        self.fleet.step(dt)
        self.plant.step(dt)
        self.reliability.step(dt)
        self.response.step(dt)
        for scenario in self.scenarios.values():
            if scenario.status != "inactive":
                scenario.step()
                if scenario.status == "active" and scenario.expected_end is not None and self.now >= scenario.expected_end:
                    if self.auto_end:
                        scenario.end_expected()
                    else:
                        scenario.overrun()
        if int(self.now) % 60 == 0:
            self.kpis.history.sample()          # what the scenario twins compare

    # -- messages --------------------------------------------------------------

    def handle_message(self, name: str, subject: str, body: dict) -> None:
        """Apply one Ditto live message. The effect shows in the features of the
        addressed twin; anything that cannot be applied goes to the journal."""
        self.refusals = []
        kind = self.things[name]["attributes"]["kind"] if name in self.things else ""
        if kind == "scenario" and subject == "activate":
            self.scenarios[name].activate(body)
        elif kind == "scenario" and subject == "cancel":
            self.scenarios[name].cancel()
        elif kind == "mine" and subject == "set-speed":
            speedup = body.get("speedup")
            if isinstance(speedup, bool) or speedup not in ALLOWED_SPEEDUPS:
                self.refuse("mine", f"Скорость симуляции {speedup!r} не поддерживается, "
                                            f"допустимо: {', '.join(map(str, ALLOWED_SPEEDUPS))}")
                return
            self.speedup = int(speedup)
            self.log("info", "mine", f"Скорость симуляции: ×{self.speedup}")
        elif kind == "mine" and subject == "pause":
            if not self.paused:
                self.paused = True
                self.log("info", "mine", "Симуляция приостановлена")
        elif kind == "mine" and subject == "resume":
            if self.paused:
                self.paused = False
                self.log("info", "mine", "Симуляция продолжена")
        elif kind == "module":
            # Messages to a module twin are for that module's service, which carries them out and reports in its
            # own twin. The model only journals the switch; every other subject (`apply` to the advisor, …) is
            # not its business and is passed over without a word.
            if subject == "set-enabled" and isinstance(body.get("enabled"), bool):
                title = self.things[name]["attributes"]["name"]
                self.log("info", name, f"{title}: команда {'включить' if body['enabled'] else 'выключить'}")
        elif kind == "truck" and subject == "assign":
            self.fleet.assign(self.fleet.by_name[name], body)
        elif kind == "truck" and subject == "release":
            self.fleet.release(self.fleet.by_name[name])
        elif kind == "block" and subject == "set-destination":
            self.pit.set_destination(name, body)
        elif kind == "block" and subject == "release":
            self.pit.release_destination(name)
        elif kind == "rom-pad" and subject == "set-reclaim":
            self.plant.set_reclaim(name, body)
        elif kind == "rom-pad" and subject == "release":
            self.plant.release_reclaim(name)
        elif kind == "mill" and subject == "set-feed-rate":
            self.plant.set_feed_rate(body)
        elif kind == "mill" and subject == "release":
            self.plant.release_feed_rate()
        elif kind in ("truck", "loader") and subject in ("schedule-service", "cancel-service"):
            unit = self.fleet.by_name[name] if kind == "truck" else self.fleet.loaders[name]
            if subject == "schedule-service":
                self.reliability.schedule(unit, body, loader=kind == "loader")
            else:
                self.reliability.cancel(unit, loader=kind == "loader", body=body)
        elif kind == "sag-mill" and subject == "plan-reline":
            # The planned reline of the SAG mill moves (scenario «Замена футеровки МПСИ», an advisor's option).
            self.scenarios["scenario-sag-reline"].reschedule(body)
        elif kind == "loader" and subject == "move-to-block":
            self.fleet.move_to_block(self.fleet.loaders[name], body)
        # The shift's response resources (mine/sim/response.py).
        elif kind == "crusher" and subject == "expedite-repair":
            self.response.expedite_crusher()
        elif kind == "clay-plant" and subject == "expedite-repair":
            self.response.expedite_clay_plant()
        elif kind == "loader" and subject == "expedite-repair":
            self.response.expedite_loader(self.fleet.loaders[name])
        elif kind == "loader" and subject == "prioritise-repair":
            self.response.lend_workshop(self.fleet.loaders[name])
        elif kind == "road-segment" and subject == "repair-road":
            self.response.repair_road(self.roads.segments[name])
        elif kind == "road-segment" and subject == "treat-surface":
            self.response.treat_surface(self.roads.segments[name])
        elif kind == "workshop" and subject == "expedite-repair":
            self.response.workshop_overtime()
        elif kind == "journal" and subject == "record":
            self.record(body)
        else:
            target = name if name in self.things else "journal"
            self.refuse(target, f"Сообщение «{subject}» для «{name}» не поддерживается")

    # -- published state -------------------------------------------------------

    def values(self, scenarios: bool = True) -> dict[str, dict]:
        """Current feature values of every twin the simulator writes: {name: {feature: value}}.
        `scenarios=False` leaves out the scenario twins, which no module core reads (a forecast's exchanges)."""
        out: dict[str, dict] = {"mine": {
            "sim_time": self.sim_time(),
            "sim_state": "paused" if self.paused else "running",
            "speedup": self.speedup,
            "shift": "day" if self.is_day_shift() else "night",
            **self.kpis.values(),
            # What the shift's response resources have cost since the model started (mine/sim/response.py).
            "response_cost_kzt": round(self.response.spent_kzt),
        }}
        out.update(self.pit.values())
        out.update(self.fleet.values())
        out.update(self.plant.values())
        out.update(self.reliability.values())
        out.update(self.weather.values())
        if scenarios:
            for name, scenario in self.scenarios.items():
                out[name] = scenario.values()
        return out
