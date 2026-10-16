"""The shift's response resources: what a shift supervisor can spend on an incident besides the levers
the control modules already pull (params.yaml, group `response`).

Each resource is a live message to the twin it acts on, applied like every other lever:

    crusher        expedite-repair {}      a second repair crew with the contractor's specialists: the rest of the
                                           crusher's repair takes `second_crew_factor` of the time
    loader-*       expedite-repair {}      the same on a broken-down loader
    loader-*       prioritise-repair {}    the crew of one workshop bay goes to the loader's face: its repair takes
                                           `workshop_lend_factor` of the time, the bay stands meanwhile
    road-segment-* repair-road {}          an emergency road crew opens a closed segment after `road_repair_h`,
                                           at `repaired_kmh` until the full repair is due
    road-segment-* treat-surface {}        a grader and a crushed-rock topping on a ramp the rain closed: it opens
                                           after `grading_h` at `treated_wet_kmh` while the road is wet
    workshop       expedite-repair {}      a second workshop crew on overtime for `workshop_overtime_h`: every
                                           repair of a broken-down truck in it takes `second_crew_factor` of the time

What a resource costs accrues while it works (`spent_kzt`, and per engagement); the forecast counts it in its
total as the part «ресурсы смены» (mine/sim/forecast.py). A resource that cannot be applied (the crusher runs,
the loader is not broken down, the road is open) is refused with a journal warning, like any other message.

Where an incident is a scenario (the crusher stop, the loader failure, the road closure), the repair is the
scenario's: a crew shortens its expected end (mine/sim/scenarios.py), which the forecasts take as the moment
the incident ends; the scenario on the stand still runs until it is cancelled. A natural breakdown's repair is
the simulator's own timer, and the crew shortens that.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from mine.sim.text import money, num

if TYPE_CHECKING:
    from mine.sim.model import Simulation

KINDS = {"crew": "вторая бригада", "lend": "бригада мастерской", "grader": "грейдер с подсыпкой",
         "road": "аварийная дорожная бригада", "overtime": "сверхурочная смена мастерской"}


@dataclass
class Engagement:
    kind: str                   # crew / lend / grader / road / overtime
    thing: str                  # the twin it acts on
    scenario: str               # the scenario of the incident it answers; '' for a natural breakdown
    started: float
    rate_kzt_h: float           # what it costs per hour while it works
    floor_h: float = 0.0        # hours of `floor_kzt_h` paid at least (the contractor's least call-out)
    floor_kzt_h: float = 0.0
    until: float = 0.0          # a fixed end (the road crew, the workshop overtime, the grading); 0: by state
    cost_kzt: float = 0.0
    active: bool = True


class Response:
    def __init__(self, sim: "Simulation"):
        self.sim = sim
        self.p = sim.params["response"]
        self.fx = float(sim.params["economics"]["usd_kzt"])
        self.jobs: list[Engagement] = []
        self.spent_kzt = 0.0
        # Speed caps to lift: (segment, until) with until a moment, or 0 for «when the roads are dry».
        self.caps: list[tuple[str, float]] = []

    # -- prices ---------------------------------------------------------------------

    @property
    def contractor_kzt_h(self) -> float:
        return float(self.p["contractor_people"]) * float(self.p["contractor_usd_h"]) * self.fx

    @property
    def crew_kzt_h(self) -> float:
        """A second crew: own fitters on overtime and the contractor's specialists."""
        return float(self.p["own_crew_kzt_h"]) + self.contractor_kzt_h

    def crew_cost_kzt(self, hours: float) -> float:
        """What a second crew costs for a repair of `hours`: the contractor is paid at least its call-out."""
        return float(self.p["own_crew_kzt_h"]) * hours + self.contractor_kzt_h * max(hours, float(self.p["contractor_min_h"]))

    def topping_t(self, segment) -> float:
        return segment.length * float(self.sim.params["road"]["width_m"]) * float(self.p["topping_m"]) \
            * float(self.sim.params["rock"]["loose_density_t_m3"])

    def topping_kzt(self, segment) -> float:
        return self.topping_t(segment) * float(self.sim.params["economics"]["stock_kzt_t"])

    # -- the messages -----------------------------------------------------------------

    def _engaged(self, kind: str, thing: str) -> bool:
        return any(j.active and j.kind == kind and j.thing == thing for j in self.jobs)

    def _add(self, job: Engagement, fixed_kzt: float = 0.0) -> Engagement:
        job.cost_kzt += fixed_kzt
        self.spent_kzt += fixed_kzt
        self.jobs.append(job)
        return job

    def expedite_crusher(self) -> None:
        sim, crusher = self.sim, self.sim.plant.crusher
        scenario = sim.scenarios.get(crusher.stopped_by)
        if not crusher.stopped_by or scenario is None:
            sim.refuse(crusher.name, f"{crusher.title}: вторая бригада не нужна, дробилка работает")
            return
        if self._engaged("crew", crusher.name):
            sim.refuse(crusher.name, f"{crusher.title}: вторая бригада уже на ремонте")
            return
        before = scenario.expected_end
        scenario.shorten(float(self.p["second_crew_factor"]), KINDS["crew"])
        self._crew(crusher.name, scenario.name)
        sim.log("info", crusher.name, f"{crusher.title}: на ремонт вышла вторая бригада со специалистами подрядчика "
                                      f"({rate(self.crew_kzt_h)}, вызов подрядчика {money(self.callout_kzt)} млн тг); "
                                      f"пуск ожидается в {_hhmm(sim, scenario.expected_end)} вместо {_hhmm(sim, before)}")

    @property
    def callout_kzt(self) -> float:
        return self.contractor_kzt_h * float(self.p["contractor_min_h"])

    def _crew(self, thing: str, scenario: str) -> None:
        """A second crew starts: the contractor's call-out is paid at once, then the crew by the hour."""
        self._add(Engagement("crew", thing, scenario, self.sim.now, float(self.p["own_crew_kzt_h"]),
                             float(self.p["contractor_min_h"]), self.contractor_kzt_h), self.callout_kzt)

    def _loader_scenario(self, loader):
        return self.sim.scenarios.get(loader.forced_by) if loader.forced_by else None

    def _broken(self, loader) -> bool:
        return loader.state == "down" and loader.job == "repair"

    def expedite_loader(self, loader) -> None:
        sim = self.sim
        if not self._broken(loader):
            sim.refuse(loader.name, f"{loader.title}: вторая бригада не нужна, экскаватор не в ремонте после отказа")
            return
        if self._engaged("crew", loader.name):
            sim.refuse(loader.name, f"{loader.title}: вторая бригада уже на ремонте")
            return
        end = self._shorten_loader(loader, float(self.p["second_crew_factor"]), KINDS["crew"])
        self._crew(loader.name, loader.forced_by)
        loader.crews.append("crew")
        sim.log("info", loader.name, f"{loader.title}: на ремонт вышла вторая бригада со специалистами подрядчика "
                                     f"({rate(self.crew_kzt_h)}, вызов подрядчика {money(self.callout_kzt)} млн тг); "
                                     f"ремонт закончится около {_hhmm(sim, end)}")

    def lend_workshop(self, loader) -> None:
        sim, rel = self.sim, self.sim.reliability
        if not self._broken(loader):
            sim.refuse(loader.name, f"{loader.title}: бригада мастерской не нужна, экскаватор не в ремонте после отказа")
            return
        if self._engaged("lend", loader.name):
            sim.refuse(loader.name, f"{loader.title}: бригада мастерской уже в забое")
            return
        if rel.lent >= len(rel.bays) - 1:
            sim.refuse(loader.name, f"{loader.title}: в мастерской не осталось свободной бригады")
            return
        end = self._shorten_loader(loader, float(self.p["workshop_lend_factor"]), KINDS["lend"])
        rel.lent += 1
        self._add(Engagement("lend", loader.name, loader.forced_by, sim.now, 0.0))
        loader.crews.append("lend")
        sim.log("info", loader.name, f"{loader.title}: бригада бокса мастерской ушла в забой на ремонт, бокс стоит; "
                                     f"ремонт закончится около {_hhmm(sim, end)}")

    def _shorten_loader(self, loader, factor: float, what: str) -> float:
        """Shorten the rest of a loader's repair; the moment it is now expected to end."""
        sim = self.sim
        scenario = self._loader_scenario(loader)
        if scenario is not None:
            scenario.shorten(factor, what)
            return scenario.expected_end
        loader.repair_s *= factor
        return sim.now + loader.repair_s

    def repair_road(self, segment) -> None:
        sim = self.sim
        scenario = sim.scenarios.get("scenario-road-closure")
        if scenario is None or scenario.status != "active" or scenario.target != segment.name:
            sim.refuse(segment.name, f"Участок «{segment.title}»: аварийный ремонт не нужен, участок не закрыт обрушением")
            return
        if self._engaged("road", segment.name):
            sim.refuse(segment.name, f"Участок «{segment.title}»: аварийная бригада уже работает")
            return
        hours = float(self.p["road_repair_h"])
        before = scenario.expected_end
        scenario.repair_now(sim.now + hours * 3600.0, KINDS["road"])
        self._add(Engagement("road", segment.name, scenario.name, sim.now, float(self.p["road_crew_kzt_h"]),
                             until=sim.now + hours * 3600.0))
        sim.log("info", segment.name, f"Участок «{segment.title}»: аварийная бригада ({rate(float(self.p['road_crew_kzt_h']))}) "
                                      f"откроет одну полосу к {_hhmm(sim, scenario.expected_end)} с ограничением "
                                      f"{num(float(self.p['repaired_kmh']))} км/ч; полный ремонт — к {_hhmm(sim, before)}")

    def treat_surface(self, segment) -> None:
        sim = self.sim
        rain = sim.scenarios.get("scenario-heavy-rain")
        if rain is None or rain.status != "active" or "scenario-heavy-rain" not in segment.closures:
            sim.refuse(segment.name, f"Участок «{segment.title}»: подсыпка не нужна, участок не закрыт дождём")
            return
        if self._engaged("grader", segment.name):
            sim.refuse(segment.name, f"Участок «{segment.title}»: грейдер уже работает")
            return
        hours = float(self.p["grading_h"])
        material = self.topping_kzt(segment)
        self._add(Engagement("grader", segment.name, rain.name, sim.now, float(self.p["grader_kzt_h"]),
                             until=sim.now + hours * 3600.0), material)
        rain.note_response(f"{KINDS['grader']} на «{segment.title}» с {_hhmm(sim, sim.now)}: "
                           f"{self.topping_t(segment):.0f} т подсыпки ({money(material)} млн тг)")
        sim.log("info", segment.name, f"Участок «{segment.title}»: грейдер и подсыпка {self.topping_t(segment):.0f} т "
                                      f"({money(material)} млн тг); съезд откроют в {_hhmm(sim, sim.now + hours * 3600)} "
                                      f"с ограничением {num(float(self.p['treated_wet_kmh']))} км/ч")

    def workshop_overtime(self) -> None:
        sim, rel = self.sim, self.sim.reliability
        if sim.now < rel.overtime_until:
            sim.refuse(rel.name, f"{rel.title}: сверхурочная смена уже работает")
            return
        if not rel.repairs():
            sim.refuse(rel.name, f"{rel.title}: сверхурочная смена не нужна, отказавших самосвалов в ремонте нет")
            return
        hours = float(self.p["workshop_overtime_h"])
        rel.start_overtime(sim.now + hours * 3600.0, float(self.p["second_crew_factor"]))
        self._add(Engagement("overtime", rel.name, "", sim.now, float(self.p["own_crew_kzt_h"]),
                             until=sim.now + hours * 3600.0))
        sim.log("info", rel.name, f"{rel.title}: сверхурочная смена до {_hhmm(sim, rel.overtime_until)} "
                                  f"({rate(float(self.p['own_crew_kzt_h']))}, пока в боксах есть ремонт); "
                                  f"ремонт отказавших самосвалов быстрее")

    # -- one step ---------------------------------------------------------------------

    def step(self, dt: float) -> None:
        if self.caps:
            self._lift_caps()
        if not self.jobs:
            return
        for job in self.jobs:
            if not job.active:
                continue
            if not self._working(job):
                self._end(job)
                continue
            hours = (self.sim.now - job.started) / 3600.0
            rate = job.rate_kzt_h
            if job.kind == "overtime" and not self.sim.reliability.repairs():
                rate = 0.0                      # the overtime crew is paid while there is a repair to do
            if job.floor_kzt_h and hours > job.floor_h:
                rate += job.floor_kzt_h             # past the call-out the contractor is paid by the hour
            amount = rate * dt / 3600.0
            job.cost_kzt += amount
            self.spent_kzt += amount
        if not any(j.active for j in self.jobs) and len(self.jobs) > 40:
            self.jobs = self.jobs[-40:]

    def _working(self, job: Engagement) -> bool:
        sim = self.sim
        if job.kind == "crew" and job.thing == sim.plant.crusher.name:
            return bool(sim.plant.crusher.stopped_by)
        if job.kind in ("crew", "lend"):
            return sim.fleet.loaders[job.thing].state == "down"
        if job.kind == "grader":
            rain = sim.scenarios["scenario-heavy-rain"]
            if job.until and sim.now >= job.until:
                job.until = 0.0
                rain.open_treated(sim.roads.segments[job.thing], float(self.p["treated_wet_kmh"]))
                self.caps.append((job.thing, 0.0))
            # The grader stays on the ramp while it is open wet; the job ends with the rain scenario.
            return rain.status != "inactive"
        return sim.now < job.until

    def _end(self, job: Engagement) -> None:
        job.active = False
        sim = self.sim
        if job.kind == "lend":
            sim.reliability.lent = max(0, sim.reliability.lent - 1)
        if job.kind in ("crew", "lend") and job.thing in sim.fleet.loaders:
            sim.fleet.loaders[job.thing].crews = []
        if job.kind == "grader" and job.until:
            job.until = 0.0                     # the rain ended before the grading did

    def _lift_caps(self) -> None:
        sim, kept = self.sim, []
        for name, until in self.caps:
            segment = sim.roads.segments[name]
            done = sim.now >= until if until else segment.surface == "dry"
            if done:
                sim.roads.set_cap(segment, 0.0)
                sim.fleet.roads_changed()
                sim.log("info", name, f"Участок «{segment.title}»: ограничение скорости после ремонта снято")
            else:
                kept.append((name, until))
        self.caps = kept

    def cap_until(self, segment, until: float, kmh: float) -> None:
        """A segment opened before its full repair: limited to `kmh` until `until`."""
        self.sim.roads.set_cap(segment, kmh)
        self.caps.append((segment.name, until))

    # -- published --------------------------------------------------------------------

    def cost_for(self, scenario: str = "", thing: str = "", since: float = 0.0) -> float:
        """What the resources sent to a scenario's incident or to a twin cost, those started since `since`."""
        return sum(j.cost_kzt for j in self.jobs if j.started >= since
                   and ((scenario and j.scenario == scenario) or (thing and j.thing == thing)))

    def working(self, thing: str) -> list[str]:
        """The resources working on a twin now, in Russian."""
        return [KINDS[j.kind] for j in self.jobs if j.active and j.thing == thing]


def rate(kzt_h: float) -> str:
    """An hourly rate in Russian: '196 тыс. тг/ч'."""
    return f"{kzt_h / 1000:.0f} тыс. тг/ч"


def _hhmm(sim: "Simulation", at: float | None) -> str:
    return sim.sim_time(at)[11:16] if at is not None else "—"
