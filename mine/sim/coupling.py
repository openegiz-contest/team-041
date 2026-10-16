"""The simulator and the module cores in one process, without a broker.

On the stand a module sees the mine through twin events and answers with
live messages; both travel through the platform and take about one tick of
the simulator. `Coupled` reproduces that: every `period_s` simulated seconds
it shows each core the twins whose features changed, lets it decide, and
hands its messages to the simulator's message handler. With the default
period this is the stand at its default speed-up of 20.

The forecast engine (mine/sim/forecast.py) runs a copy of the live model this
way, with each module in the state the stand reports; the tests use it to run
the mine with its modules for days.
"""
from __future__ import annotations

import copy

from mine.modules.dispatch.core import DispatchCore
from mine.modules.grade.core import GradeCore
from mine.modules.maint.core import MaintCore
from mine.modules.plant.core import PlantCore
from mine.sim.model import Simulation, short

# The modules that steer the mine. The advisor is not among them: it changes
# nothing by itself, only when an operator applies one of its options.
CORES = {"dispatch": DispatchCore, "grade": GradeCore, "plant": PlantCore, "maint": MaintCore}
ALL = tuple(CORES)


def things_now(sim: Simulation) -> list[dict]:
    """The twins as the platform would return them at this moment."""
    values = sim.values()
    things = copy.deepcopy(list(sim.things.values()))
    for thing in things:
        for feature, value in values.get(short(thing["thingId"]), {}).items():
            thing["features"].setdefault(feature, {"properties": {}})["properties"]["value"] = value
    return things


class Coupled:
    def __init__(self, sim: Simulation, modules: tuple[str, ...] = ALL, period_s: int = 20,
                 enabled: dict[str, bool] | None = None):
        """`enabled` gives the switch of each module (default: on). A module
        that is off still has its core, so that switching it on later works
        as on the stand."""
        self.sim, self.period_s = sim, period_s
        things = things_now(sim)
        enabled = enabled or {}
        self.cores = {name: CORES[name](things, sim.params, enabled=enabled.get(name, True)) for name in modules}
        self.seen: dict[str, dict] = {}
        self.messages = 0
        self.log: list[tuple[float, str, str, dict]] = []       # kept only while `keep_log` is set
        self.keep_log = False

    def exchange(self) -> None:
        """One round trip: twin reports to the cores, their messages to the simulator."""
        sim = self.sim
        values = sim.values(scenarios=False)            # the cores read no scenario twin
        changed = {name: v for name, v in values.items() if self.seen.get(name) != v}
        self.seen = values
        for core in self.cores.values():
            for name, features in changed.items():
                core.observe(name, features)
            self.deliver(core.step())

    def deliver(self, messages) -> None:
        for name, subject, body in messages:
            self.messages += 1
            if self.keep_log:
                self.log.append((self.sim.now, name, subject, body))
            self.sim.handle_message(name, subject, body)

    def set_enabled(self, module: str, enabled: bool) -> None:
        self.deliver(self.cores[module].set_enabled(enabled))

    def step(self) -> None:
        """One simulated second, and the round trip when its period comes."""
        self.sim.step()
        if int(self.sim.now) % self.period_s == 0:
            self.exchange()

    def run(self, minutes: float) -> None:
        for _ in range(int(minutes * 60)):
            self.step()

    def run_until(self, condition, limit_min: float = 600) -> None:
        for _ in range(int(limit_min * 60)):
            if condition():
                return
            self.step()
        raise AssertionError(f"condition not reached within {limit_min} simulated minutes")
