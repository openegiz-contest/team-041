"""Wear, failures and the workshop.

A machine's condition (`health_pct`) falls with the hours worked since its
last service. A defect appears at random, at a rate that grows once a service
is overdue, and then develops for a while before the machine fails: over that
time one of its condition indicators drifts (engine temperature, tyre
temperature or health alone), so the failure can be seen coming. Without
intervention the machine runs to failure: a minor fault is fixed where the
truck stands, a major one needs a workshop bay.

The maintenance module books a machine for the workshop with the
`schedule-service` message: a truck drives to a bay after its current trip, a
loader stops at its face after the truck under it. What is done there depends
on what the mechanics find: a full service when one is nearly due or a major
defect is developing, a short repair of a minor defect, or only an inspection
when nothing is wrong. `cancel-service` withdraws a booking that has not
started.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING

from mine.common.process import wear_pct
from mine.sim.text import num

if TYPE_CHECKING:
    from mine.sim.model import Simulation

INDICATORS = ("engine", "tyre", "drive")
SUDDEN_H = 1.0 / 3600.0         # a defect without warning: it ends in a failure within a second of operation
INDICATOR_TITLES = {"engine": "система охлаждения двигателя", "tyre": "ходовая часть и шины", "drive": "трансмиссия",
                    "scenario": "сценарий"}
JOB_TITLES = {"service": "плановое обслуживание", "fix": "устранение дефекта", "inspect": "осмотр, дефект не подтвердился"}


def service_view(reason: str, due: str) -> tuple[str, str]:
    """What a machine's twin says about its service: the reason in Russian and
    its kind (planned / breakdown-major / breakdown-minor / breakdown-scenario, or '').
    `reason` is the simulator's own code ('breakdown: major, engine', 'planned: <text>');
    `due` the reason of a booking not yet started."""
    if reason.startswith("breakdown: scenario"):
        return "Отказ (сценарий «Отказ экскаватора»), ремонт до ожидаемого окончания сценария", "breakdown-scenario"
    if reason.startswith("breakdown: "):
        kind, _, indicator = reason.removeprefix("breakdown: ").partition(", ")
        what = INDICATOR_TITLES.get(indicator, indicator)
        if kind == "major":
            return f"Отказ: {what}, ремонт", "breakdown-major"
        return f"Неисправность: {what}, ремонт на месте", "breakdown-minor"
    if reason.startswith("planned: "):
        return f"Обслуживание по заявке: {reason.removeprefix('planned: ')}", "planned"
    if due:
        return f"Обслуживание назначено: {due}", "planned"
    return "", ""


def short(thing_id: str) -> str:
    return thing_id.split(":", 1)[1] if ":" in thing_id else thing_id


@dataclass
class Fault:
    kind: str                   # minor / major
    indicator: str              # which reading shows it: engine / tyre / drive
    develops_h: float           # operating hours from the first sign to the failure
    progress: float = 0.0       # 0 = just appeared, 1 = failure


@dataclass
class Job:
    truck: object
    kind: str                   # repair / service / fix / inspect
    duration_s: float
    remaining_s: float = 0.0
    fast: bool = False          # shortened by the workshop's overtime crew


class Reliability:
    def __init__(self, sim: "Simulation"):
        self.sim = sim
        self.p = sim.params["reliability"]
        thing = sim.by_kind["workshop"][0]
        geo = thing["attributes"]["geo"]
        self.name = short(thing["thingId"])
        self.title = thing["attributes"]["name"]
        self.node = short(thing["attributes"]["links"]["access_node"])
        self.pos = (geo["x"], geo["y"], geo["z"])
        self.bays: list[Job | None] = [None] * int(sim.params["workshop"]["bays"])
        self.queue: list[Job] = []
        self.jobs_done = 0
        self.breakdowns = 0             # failures in service: trucks and loaders, minor and major
        self.lent = 0                   # bays whose crew is repairing a loader at its face (mine/sim/response.py)
        self.overtime_until = 0.0       # a second crew on overtime repairs broken-down trucks faster until then
        self.overtime_factor = 1.0

    # -- failure clocks -------------------------------------------------------------

    def _onset_h(self, unit, kind: str, loader: bool = False) -> float:
        """Operating hours until the next defect of this kind appears, from the machine's own stream."""
        p = self.p
        mtbf = p["loader_mtbf_h"] if loader else p["truck_mtbf_h" if kind == "major" else "truck_minor_mtbf_h"]
        return unit.rng_fail.expovariate(1.0 / (mtbf - p[f"pf_interval_{kind}_h"]))

    def _streams(self, unit, loader: bool) -> None:
        what = "loader" if loader else "truck"
        unit.rng_fail = self.sim.rng(f"{what}/{unit.name}/fail")
        unit.rng_repair = self.sim.rng(f"{what}/{unit.name}/repair")

    def start(self, unit, loader: bool = False) -> None:
        """Give a machine its random streams and its first failure clocks."""
        self._streams(unit, loader)
        unit.hazard = {"major": self._onset_h(unit, "major", loader)}
        if not loader:
            unit.hazard["minor"] = self._onset_h(unit, "minor")

    def reseed(self) -> None:
        """New streams for every machine (Simulation.reseed) and new clocks until its next defects: the onset
        is memoryless, so the hours left can be drawn again. A defect already developing stays."""
        fleet = self.sim.fleet
        for unit, loader in [(t, False) for t in fleet.trucks] + [(l, True) for l in fleet.loaders.values()]:
            self._streams(unit, loader)
            unit.hazard = {kind: self._onset_h(unit, kind, loader) for kind in unit.hazard}

    def _repair_s(self, unit, mttr_h: float) -> float:
        """Repair time: log-normal with the mean `mttr_h`, from the machine's own stream."""
        sigma = self.p["repair_log_sigma"]
        return 3600.0 * unit.rng_repair.lognormvariate(math.log(mttr_h) - sigma ** 2 / 2.0, sigma)

    def _age(self, unit, hours: float, loader: bool = False) -> bool:
        """Advance wear and the defect of one working machine; True when it fails now."""
        p = self.p
        unit.service_h += hours
        overdue = max(0.0, unit.service_h - p["pm_interval_h"]) / p["pm_interval_h"]
        stress = hours * (1.0 + p["overdue_hazard_slope"] * overdue)
        for kind in unit.hazard:
            unit.hazard[kind] -= stress
            if unit.hazard[kind] <= 0:
                unit.hazard[kind] = self._onset_h(unit, kind, loader)
                if unit.fault is None:
                    indicator = unit.rng_fail.choice(INDICATORS)
                    # A share of the defects gives no warning (a burst hose, a cut tyre, an electrical fault): it
                    # fails at once, and no monitoring can book the machine before it does.
                    sudden = unit.rng_fail.random() < p["sudden_share_pct"] / 100.0
                    unit.fault = Fault(kind, indicator, SUDDEN_H if sudden else p[f"pf_interval_{kind}_h"])
        if unit.fault is None:
            return False
        unit.fault.progress += hours / unit.fault.develops_h
        return unit.fault.progress >= 1.0

    def health_pct(self, unit) -> float:
        health = 100.0 - wear_pct(self.sim.params, unit.service_h)
        if unit.fault is not None:
            health -= self.p[f"health_drop_{unit.fault.kind}_pct"] * min(unit.fault.progress, 1.0)
        return max(0.0, health)

    def indicator_rise(self, unit, indicator: str) -> float:
        """Extra degrees on a temperature reading caused by a developing defect."""
        fault = unit.fault
        if fault is None or fault.indicator != indicator:
            return 0.0
        return self.p[f"{indicator}_fault_rise_c"] * min(fault.progress, 1.0)

    # -- what the workshop does with a booked machine ---------------------------------

    def _booked_job(self, unit) -> tuple[str, float]:
        """Kind and duration (s) of the work a booked machine gets."""
        p, fault = self.p, unit.fault
        due = unit.service_h >= p["pm_min_share"] * p["pm_interval_h"]
        if due or (fault is not None and fault.kind == "major"):
            return "service", p["pm_duration_h"] * 3600.0
        if fault is not None:
            return "fix", p["fix_minor_h"] * 3600.0
        return "inspect", p["inspect_h"] * 3600.0

    def _done(self, unit, kind: str) -> None:
        """Apply the result of the work: a service or a repair renews the
        machine, a fix removes the defect only, an inspection changes nothing."""
        if kind in ("service", "repair"):
            unit.fault, unit.service_h = None, 0.0
        elif kind == "fix":
            unit.fault = None
        unit.service_reason = unit.service_due = ""

    # -- trucks ---------------------------------------------------------------------

    def _fail_truck(self, truck) -> None:
        sim, fault = self.sim, truck.fault
        what = INDICATOR_TITLES[fault.indicator]
        self.breakdowns += 1
        sim.fleet.break_down(truck)
        if fault.kind == "major":
            truck.service_reason = f"breakdown: major, {fault.indicator}"
            job = Job(truck, "repair", self._repair_s(truck, self.p["truck_mttr_h"]))
            self.queue.append(job)
            self._hasten(job)
            sim.log("alarm", truck.name, f"{truck.title}: отказ ({what}), нужен ремонт в мастерской, "
                                         f"наработка после обслуживания {truck.service_h:.0f} ч")
        else:
            truck.service_reason = f"breakdown: minor, {fault.indicator}"
            truck.repair_s = self._repair_s(truck, self.p["truck_minor_mttr_h"])
            sim.log("warning", truck.name, f"{truck.title}: неисправность ({what}), ремонт на месте "
                                           f"около {num(truck.repair_s / 3600)} ч")

    def arrive(self, truck) -> None:
        """A truck sent for service reached the workshop: it waits for a bay."""
        truck.state, truck.place = "service", self.pos
        truck.service_reason = f"planned: {truck.service_due}"
        kind, duration_s = self._booked_job(truck)
        self.queue.append(Job(truck, kind, duration_s))

    def schedule(self, unit, body: dict, loader: bool = False) -> None:
        """Live message `schedule-service` from the maintenance module."""
        sim = self.sim
        reason = body.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            sim.refuse(unit.name, f"{unit.title}: заявка на обслуживание без причины отклонена")
            return
        if unit.state == "down" or unit.service_due or unit.state == "service":
            sim.refuse(unit.name, f"{unit.title}: заявка на обслуживание отклонена, "
                                          f"машина уже {'в ремонте' if unit.state == 'down' else 'ждёт обслуживания'}")
            return
        if body.get("indicator") == "hours_since_service" and sim.now < getattr(unit, "service_hold_until", 0.0):
            # Planned service waits while an operator keeps the machine at work; a developing defect does not.
            sim.log("info", unit.name, f"{unit.title}: плановое ТО не назначено, машина оставлена в работе по указанию "
                                       f"оператора до {sim.sim_time(unit.service_hold_until)[11:16]}")
            return
        unit.service_due = reason.strip()[:80]
        readings = {"engine_temp_c": ("температура двигателя", "°C"), "tyre_temp_c": ("температура шин", "°C"),
                    "health_pct": ("показатель состояния", "%"), "hours_since_service": ("наработка", "ч")}
        reading, measure = readings.get(body.get("indicator"), ("показатель", ""))
        value = body.get("value")
        shown = num(float(value)) if isinstance(value, (int, float)) and not isinstance(value, bool) else str(value)
        detail = f" ({reading} {shown} {measure})".replace(" )", ")") if body.get("indicator") else ""
        where = "после текущей погрузки" if loader else "после разгрузки"
        sim.log("info", unit.name, f"{unit.title}: обслуживание назначено {where}: {unit.service_due}{detail}")

    def cancel(self, unit, loader: bool = False, body: dict | None = None) -> None:
        """Live message `cancel-service`. From the maintenance module: a booking
        that has not started is withdrawn; work under way is finished. From an
        operator, with `hold_min`: a truck booked for planned service, or
        already waiting at the workshop for a bay, goes back to work, and no
        planned service is booked for it for that time."""
        sim = self.sim
        body = body or {}
        hold_s = sim.hold_s(body, unit.name, unit.title) if not loader else None
        if hold_s is False:
            return
        if hold_s:
            self._call_back(unit, hold_s)
            return
        if not unit.service_due or unit.state in ("service", "down"):
            return
        unit.service_due = ""
        sim.log("info", unit.name, f"{unit.title}: заявка на обслуживание снята (модуль техобслуживания выключен)")
        if not loader and unit.goal is not None and unit.goal[0] == "service":
            sim.fleet.assign_next(unit)             # it was on its way to the workshop

    def _call_back(self, truck, hold_s: float) -> None:
        """An operator keeps the truck at work: no planned service is booked for it until the hold ends.
        A booking not yet started is withdrawn; planned work in a bay (service, a fix, an inspection) is
        broken off and the truck leaves. A truck that is broken down cannot be called back."""
        sim = self.sim
        until = sim.sim_time(sim.now + hold_s)[11:16]
        waiting = next((job for job in self.queue if job.truck is truck), None)
        in_bay = next((i for i, job in enumerate(self.bays) if job is not None and job.truck is truck), None)
        if truck.state == "down" or (waiting is not None and waiting.kind == "repair") \
                or (in_bay is not None and self.bays[in_bay].kind == "repair"):
            sim.refuse(truck.name, f"{truck.title}: вернуть в работу нельзя, машина неисправна и в ремонте")
            return
        truck.service_hold_until = sim.now + hold_s
        truck.service_due = ""
        if in_bay is not None:
            job = self.bays[in_bay]
            self.bays[in_bay] = None
            truck.in_bay, truck.service_reason = False, ""
            sim.log("info", truck.name, f"{truck.title}: {JOB_TITLES[job.kind]} прервано по указанию оператора, машина "
                                        f"возвращается в работу; плановое ТО отложено до {until}")
            sim.fleet.leave_workshop(truck, resume=False)
            return
        if waiting is None and truck.goal is not None and truck.goal[0] != "service":
            sim.log("info", truck.name, f"{truck.title}: машина остаётся в работе по указанию оператора, "
                                        f"плановое ТО не назначается до {until}")
            return
        if waiting is not None:
            self.queue.remove(waiting)
            truck.service_reason = ""
            sim.log("info", truck.name, f"{truck.title}: возвращён из очереди мастерской в работу по указанию оператора, "
                                        f"плановое ТО отложено до {until}")
            sim.fleet.leave_workshop(truck, resume=False)
            return
        sim.log("info", truck.name, f"{truck.title}: заявка на плановое ТО снята по указанию оператора, машина в работе до {until}")
        if truck.goal is not None and truck.goal[0] == "service":
            sim.fleet.assign_next(truck)

    def _finish(self, job: Job) -> None:
        sim, truck = self.sim, job.truck
        self._done(truck, job.kind)
        self.jobs_done += 1
        hours = num(job.duration_s / 3600)
        if job.kind == "repair":
            sim.log("info", truck.name, f"{truck.title}: ремонт в мастерской окончен, заняло {hours} ч")
        else:
            sim.log("info", truck.name, f"{truck.title}: {JOB_TITLES[job.kind]}, заняло {hours} ч")
        sim.fleet.leave_workshop(truck, resume=job.kind == "repair")

    # -- the shift's response resources ----------------------------------------------

    def repairs(self) -> list[Job]:
        """Repairs of broken-down trucks in the bays or waiting for one."""
        return [job for job in [*self.bays, *self.queue] if job is not None and job.kind == "repair"]

    def start_overtime(self, until: float, factor: float) -> None:
        """A second crew on overtime: every repair of a broken-down truck takes `factor` of its time until `until`."""
        self.overtime_until, self.overtime_factor = until, factor
        for job in self.repairs():
            self._hasten(job)

    def _hasten(self, job: Job) -> None:
        if job.kind != "repair" or job.fast or self.sim.now >= self.overtime_until:
            return
        job.fast = True
        job.duration_s *= self.overtime_factor
        job.remaining_s *= self.overtime_factor

    def _step_bays(self, dt: float) -> None:
        free = len(self.bays) - self.lent
        for i, job in enumerate(self.bays):
            # A bay whose crew is out at a loader takes no new job; the one under way is finished.
            if job is None and self.queue and i < free:
                job = self.bays[i] = self.queue.pop(0)
                job.remaining_s = job.duration_s
                self._hasten(job)
                if job.kind == "repair":
                    self.sim.fleet.tow(job.truck, self.node, self.pos)
                    self.sim.log("info", job.truck.name, f"{job.truck.title} доставлен в мастерскую, бокс №{i + 1}")
                job.truck.in_bay = True
            if job is None:
                continue
            job.remaining_s -= dt
            if job.remaining_s <= 0:
                self.bays[i] = None
                job.truck.in_bay = False
                self._finish(job)

    # -- loaders --------------------------------------------------------------------

    def force_down(self, loader, scenario: str) -> None:
        """A scenario breaks the loader down; it stays down until `force_up`."""
        loader.forced_by = scenario
        loader.fault = loader.fault or Fault("major", "drive", self.p["pf_interval_major_h"], progress=1.0)
        loader.service_reason, loader.service_due = "breakdown: scenario", ""
        loader.job = "repair"
        if loader.state != "down":
            self.breakdowns += 1
            self.sim.fleet.loader_down(loader)

    def force_up(self, loader) -> None:
        """The scenario is cancelled: the loader is repaired at once."""
        loader.forced_by = ""
        self._done(loader, "repair")
        self.sim.fleet.loader_up(loader)

    def _step_loader(self, loader, dt: float) -> None:
        sim, hours = self.sim, dt / 3600.0
        if loader.state == "down":
            if loader.forced_by:
                return
            loader.repair_s -= dt
            if loader.repair_s <= 0:
                kind = loader.job
                self._done(loader, kind)
                sim.log("info", loader.name, f"{loader.title}: {'ремонт окончен' if kind == 'repair' else JOB_TITLES[kind]}")
                sim.fleet.loader_up(loader)
            return
        if loader.service_due and loader.truck is None:
            loader.job, loader.repair_s = self._booked_job(loader)
            loader.service_reason, loader.service_due = f"planned: {loader.service_due}", ""
            sim.log("info", loader.name, f"{loader.title} остановлен: {JOB_TITLES[loader.job]}, "
                                         f"{num(loader.repair_s / 3600)} ч")
            sim.fleet.loader_down(loader)
            return
        if self._age(loader, hours, loader=True) and loader.state in ("loading", "waiting"):
            what = INDICATOR_TITLES[loader.fault.indicator]
            self.breakdowns += 1
            loader.job = "repair"
            loader.service_reason = f"breakdown: major, {loader.fault.indicator}"
            loader.repair_s = self._repair_s(loader, self.p["loader_mttr_h"])
            sim.log("alarm", loader.name, f"{loader.title}: отказ ({what}), ремонт в забое "
                                          f"около {num(loader.repair_s / 3600)} ч")
            sim.fleet.loader_down(loader)

    # -- one step -------------------------------------------------------------------

    def step(self, dt: float) -> None:
        hours = dt / 3600.0
        for truck in self.sim.fleet.trucks:
            if truck.state == "down":
                if not truck.service_reason.startswith("breakdown: major"):
                    truck.repair_s -= dt
                    if truck.repair_s <= 0:
                        truck.fault, truck.service_reason = None, ""
                        self.sim.log("info", truck.name, f"{truck.title}: неисправность устранена, продолжает рейс")
                        self.sim.fleet.resume(truck)
            elif truck.engine_on and self._age(truck, hours) and truck.seg is not None:
                self._fail_truck(truck)         # a defect ends in a failure only on the move
        for loader in self.sim.fleet.loaders.values():
            self._step_loader(loader, dt)
        self._step_bays(dt)

    def values(self) -> dict[str, dict]:
        return {self.name: {
            "bays_busy": sum(1 for job in self.bays if job is not None),
            "bays_total": len(self.bays),
            "bays_lent": self.lent,
            "overtime_until": self.sim.sim_time(self.overtime_until) if self.overtime_until > self.sim.now else "",
            "jobs_queued": len(self.queue),
            "jobs_done": self.jobs_done,
            "breakdowns_total": self.breakdowns,
        }}
