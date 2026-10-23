"""Two futures: the forecast engine of the simulator (docs/mine/ARCHITECTURE.md, section 6).

A `forecast` live message to the mine twin asks what the next hours bring in
two branches:

    {slot, horizon_h?, replicas?, label_a?, label_b?, actions_a?, actions_b?, requested_by?, subject?, request_id?}
    action = {thing, subject, body}

At the next tick the simulator takes a copy of its live model (a deep copy,
no I/O) and gives each branch to a worker process of its own, forked from the
simulator, so the live mine never waits for a forecast. A worker applies the
branch's actions to its copy as if they had arrived as live messages at that
moment, then runs the copy to the horizon together with the cores of the
modules, each in the state the stand reports for its module (on, off, or
switched by an action of the branch). The result goes to the twin
`forecast-<slot>`: totals of both branches, hourly series, the difference in
money and a sentence that says it in Russian. A newer request for a slot
replaces the one before it; branches that two requests of the same tick share
(the advisor's "change nothing" in every slot) are computed once.

The money of a branch is published in two parts (mine/common/economics.py):

- `margin`: the variable margin over the horizon, exactly as the mine twin
  defines it: payable copper less the smelter's charges, less the diesel of
  the trucks and the electricity;
- `adjust`: what the horizon leaves behind and the rehandling it needed. Every
  ore inventory is valued on one basis (mine/common/economics.py): its copper
  above the mine's average ore (`grade_kzt`: milling rich ore now gives copper
  the stocks give up), the fleet work held in the ore dug (`work_kzt`) and in
  the waste stripped (`strip_kzt`; a branch that stops stripping and sends
  every truck to the ore faces must not look like a gain), the rehandling the
  ore on the ROM pads still needs (`pads_kzt`), less the rehandling from the
  ROM pads done within the horizon.

`resources`: what the shift's response resources the branch used cost (a second repair crew, a grader, an
emergency road crew; mine/sim/response.py).

`total` = margin + adjust - resources is what options are compared by. The texts lead with
it (`verdict_text`), then say where the copper difference comes from: tonnes
milled, head grade and recovery, the sources of the mill's ore, what of it the
total counts (`copper_text`).

The same model state with the same actions gives the same forecast: the
model and the cores are deterministic, and each worker starts from an exact
copy of the model, its random streams included. Every random process of the
model draws from a stream of its own, keyed by its entity (Simulation.rng),
so branches A and B share their random numbers draw for draw, and B differs
from A only by what its actions change (common random numbers).

`replicas` (default 1, at most `forecast.max_replicas`) asks for paired
replicas: replica 0 runs both branches on the live streams, replica k on
streams drawn anew from the fork moment and k (Simulation.reseed), the same
for A and B. The twin then shows the mean of each branch over the replicas,
and the differences B − A with their standard error over them
(`delta_total_se_kzt`, `delta_cu_se_t`), which the advisor ranks by.
"""
from __future__ import annotations

import copy
import gc
import json
import logging
import os
import pickle
import signal
import time
from dataclasses import dataclass, field
from pathlib import Path

from mine.common.economics import Economics
from mine.sim.coupling import ALL, Coupled
from mine.sim.model import Simulation, short
from mine.sim.text import money, num, signed, thousands

log = logging.getLogger("mine.sim.forecast")

# Subjects that mean nothing inside a forecast: the copy has no speed and no forecasts of its own.
IGNORED = {("mine", "forecast"), ("mine", "set-speed"), ("mine", "pause"), ("mine", "resume")}

# Order in which waiting branches get a worker, by the role of the slot that waits for them (spec.role).
# The stand check's slot is served like the page's: someone waits for it.
RANKS = {"page": 0, "check": 0, "advisor": 1, "module": 2}


# -- one branch ---------------------------------------------------------------------

def apply_action(sim: Simulation, rig: Coupled, name: str, subject: str, body: dict) -> None:
    """One action of a branch, as if it had arrived as a live message now."""
    module = name.removeprefix("module-")
    if subject == "set-enabled" and module in rig.cores:
        if isinstance(body.get("enabled"), bool):
            rig.set_enabled(module, body["enabled"])
        return
    if (name, subject) in IGNORED or name == "module-advisor":
        return
    sim.handle_message(name, subject, body)


def totals(sim: Simulation) -> dict:
    kpis, plant = sim.kpis, sim.plant
    clay = plant.clay_plant
    return {"cu": plant.flotation.cu_conc_t + clay.cu_conc_t, "ore": kpis.ore_mined_t, "waste": kpis.waste_mined_t,
            "clay_milled": clay.total_t, "clay_milled_cu": clay.total_cu, "clay_cu": clay.cu_conc_t,
            "clay_pile": plant.clay_stock.stock.t, "mill_clay": kpis.mill_clay_total,
            "milled": plant.mill.total_t, "milled_cu": plant.mill.total_cu, "diesel": kpis.diesel_l,
            "kwh": kpis.electric_kwh_total, "reclaim": plant.reclaimed_t, "reclaim_cu": plant.reclaimed_cu,
            "crushed": plant.crusher.total_t, "crushed_cu": plant.crusher.total_cu,
            "pile": plant.stockpile.t, "pile_cu": plant.stockpile.cu, "queue_s": kpis.queue_s,
            "cycles": kpis.cycle_count, "truck_s": kpis.truck_s, "unavailable_s": kpis.unavailable_s,
            "breakdowns": sim.reliability.breakdowns, "mill_stops": plant.mill.stops, "overloads": plant.mill.overloads,
            "resources": sim.response.spent_kzt}


def run_branch(sim: Simulation, actions: list[tuple[str, str, dict]], enabled: dict[str, bool],
               horizon_h: int, period_s: int = 20, salt: str = "") -> dict:
    """Run `sim` (which is changed: pass a copy) `horizon_h` hours ahead with
    the module cores and return what the branch produced and what it is worth.
    `salt` (a replica other than the first) draws the randomness of the future
    anew from that key (Simulation.reseed); both branches of a pair get the same."""
    if salt:
        sim.reseed(salt)
    # An incident ends at its expected end in a forecast (mine/sim/durations.py), unless an action moves it.
    sim.auto_end = True
    econ = Economics(sim.params)
    rig = Coupled(sim, ALL, period_s=period_s, enabled=enabled)
    # Taken before the actions: what an action pays at once (a contractor's call-out) belongs to its branch.
    start, stock0 = totals(sim), econ.stock_parts_kzt(sim)
    refused = []
    for name, subject, body in actions:
        sim.refusals = []
        apply_action(sim, rig, name, subject, body)
        # An action the simulator refuses leaves the branch as it was: say so instead of a silent zero.
        refused += sim.refusals
    ends = ends_text(sim)
    band = sim.params["grade_control"]
    plant = sim.plant
    mill_tph, pile_pct = [], []
    milled = start["milled"]
    fed = in_band = 0
    for _ in range(horizon_h):
        for second in range(3600):
            rig.step()
            if second % 60 == 0 and plant.mill.ground_tph > 0:
                fed += 1
                # As the grade-control module measures it: the grade of the stockpile the mill draws from.
                in_band += abs(plant.stockpile.grade_pct - band["target_pct"]) <= band["band_pct"] + 1e-9
        mill_tph.append(round(plant.mill.total_t - milled))
        milled = plant.mill.total_t
        pile_pct.append(round(100.0 * plant.stockpile.t / plant.capacity_t, 1))
    end = totals(sim)
    d = {key: end[key] - start[key] for key in start}
    revenue, diesel_kzt, power_kzt = (round(econ.revenue_kzt(d["cu"])), round(econ.diesel_kzt(d["diesel"])),
                                      round(econ.power_kzt(d["kwh"])))
    rehandle = round(econ.rehandle_kzt(d["reclaim"]))
    stock1 = econ.stock_parts_kzt(sim)
    grade, work, pads = (round(stock1[k] - stock0[k]) for k in ("grade", "work", "pads"))
    stock = grade + work + pads
    strip = round(econ.strip_value_kzt(d["waste"]))
    margin = revenue - diesel_kzt - power_kzt
    adjust = stock + strip - rehandle
    resources = round(d["resources"])
    mill = plant.mill
    return {
        "cu_t": round(d["cu"], 2), "ore_t": round(d["ore"]), "waste_t": round(d["waste"]), "milled_t": round(d["milled"]),
        "milled_cu_t": round(d["milled_cu"], 2),
        # Where the mill's ore came from: the coarse-ore stockpile run down (negative: built up), the ROM pads
        # through the crusher, the pit through the crusher; tonnes and the copper in them.
        "from_stock_t": round(-d["pile"]), "from_stock_cu_t": round(-d["pile_cu"], 2),
        "from_pads_t": round(d["reclaim"]), "from_pads_cu_t": round(d["reclaim_cu"], 2),
        "from_pit_t": round(d["crushed"] - d["reclaim"]), "from_pit_cu_t": round(d["crushed_cu"] - d["reclaim_cu"], 2),
        "clay_milled_t": round(d["clay_milled"]), "clay_milled_cu_t": round(d["clay_milled_cu"], 2),
        "clay_cu_t": round(d["clay_cu"], 2), "to_clay_stock_t": round(d["clay_pile"]),
        "ofs1_clay_pct": round(d["mill_clay"] / d["milled"], 1) if d["milled"] > 1.0 else 0.0,
        # Hours of feed left on the stockpile at the end, at the mill's setpoint then (as the stockpile twin counts).
        "stock_end_h": round(plant.stockpile.t / mill.setpoint_tph, 1) if mill.setpoint_tph > 0 else 0.0,
        "diesel_l": round(d["diesel"]), "energy_mwh": round(d["kwh"] / 1000.0, 1),
        "revenue_kzt": revenue, "diesel_kzt": diesel_kzt, "power_kzt": power_kzt, "margin_kzt": margin,
        "rehandle_t": round(d["reclaim"], 1), "rehandle_kzt": rehandle, "stock_kzt": stock, "strip_kzt": strip,
        "grade_kzt": grade, "work_kzt": work, "pads_kzt": pads,
        "adjust_kzt": adjust, "resources_kzt": resources, "total_kzt": margin + adjust - resources,
        "co2_t": round(econ.co2_t(d["diesel"] + econ.rehandle_l(d["reclaim"]), d["kwh"]), 1),
        "queue_min": round(d["queue_s"] / d["cycles"] / 60.0, 2) if d["cycles"] else 0.0,
        "in_band_pct": round(100.0 * in_band / fed, 1) if fed else 0.0,
        "avail_pct": round(100.0 * (1.0 - d["unavailable_s"] / d["truck_s"]), 1) if d["truck_s"] else 0.0,
        "breakdowns": int(d["breakdowns"]), "mill_stops": int(d["mill_stops"]), "overloads": int(d["overloads"]),
        "series_mill_tph": ",".join(str(v) for v in mill_tph),
        "series_stockpile_pct": ",".join(f"{v:.1f}" for v in pile_pct),
        "refused": "; ".join(refused)[:300],
        "ends": ends,
        "end_sim_time": sim.sim_time(),
    }


def ends_text(sim: Simulation) -> str:
    """When the branch takes each running incident to end, in Russian: the scenario's expected end."""
    parts = []
    for scenario in sim.scenarios.values():
        if scenario.status == "active" and scenario.expected_end is not None:
            hours = max(0.0, scenario.expected_end - sim.now) / 3600.0
            parts.append(f"«{scenario.title}» — до {sim.sim_time(scenario.expected_end)[11:16]} "
                         f"(ещё {num(hours)} ч: {scenario.basis})")
    return "; ".join(parts)


# -- requests -------------------------------------------------------------------------

class RequestError(ValueError):
    pass


def _actions(value, things: dict, limit: int, which: str) -> list[tuple[str, str, dict]]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise RequestError(f"{which}: ожидается список действий")
    if len(value) > limit:
        raise RequestError(f"{which}: действий больше {limit}")
    out = []
    for i, action in enumerate(value, start=1):
        if not isinstance(action, dict):
            raise RequestError(f"{which}, действие {i}: ожидается объект {{thing, subject, body}}")
        thing, subject, body = action.get("thing"), action.get("subject"), action.get("body", {})
        name = short(thing) if isinstance(thing, str) else ""
        if name not in things:
            raise RequestError(f"{which}, действие {i}: двойника {thing!r} нет")
        if not isinstance(subject, str) or not subject or len(subject) > 40:
            raise RequestError(f"{which}, действие {i}: нет темы сообщения")
        if not isinstance(body, dict):
            raise RequestError(f"{which}, действие {i}: тело сообщения должно быть объектом")
        out.append((name, subject, body))
    return out


def _text(value, default: str, limit: int) -> str:
    return value.strip()[:limit] if isinstance(value, str) and value.strip() else default


def describe(actions: list[tuple[str, str, dict]]) -> str:
    """The actions of a branch as one line, for the forecast twin."""
    parts = []
    for name, subject, body in actions:
        detail = ", ".join(f"{k}={short(v) if isinstance(v, str) else v}" for k, v in body.items())
        parts.append(f"{name} {subject}" + (f" ({detail})" if detail else ""))
    return "; ".join(parts)[:400]


@dataclass
class Request:
    slot: int
    request_id: str
    requested_by: str
    subject: str
    horizon_h: int
    label_a: str
    label_b: str
    actions_a: list
    actions_b: list
    replicas: int = 1               # paired replicas: branch A and B of replica k share their random streams
    enabled: dict = field(default_factory=dict)
    keys: tuple = ()                # ((key A, key B) of each replica)
    fork_sim_time: str = ""
    asked_at: float = 0.0           # monotonic time of the request

    def job_keys(self) -> list[str]:
        return [key for pair in self.keys for key in pair]


def longest_horizon_h(sim: Simulation) -> int:
    """The longest horizon a request may ask for: a day, or, while a reline of the SAG mill is planned or under
    way, as long as the advisor needs to see the whole stop of its latest option (`forecast.planned_max_horizon_h`)."""
    p = sim.params["forecast"]
    reline = sim.scenarios.get("scenario-sag-reline")
    if reline is not None and reline.status == "active":
        return int(p["planned_max_horizon_h"])
    return int(p["max_horizon_h"])


def timeout_s(params: dict, horizon_h: int) -> float:
    """How long one branch may compute: `forecast.timeout_s`, longer in proportion for a horizon beyond a day."""
    p = params["forecast"]
    return float(p["timeout_s"]) * max(1.0, horizon_h / float(p["max_horizon_h"]))


def parse(body: dict, sim: Simulation, slots: int, counter: int) -> Request:
    p = sim.params["forecast"]
    slot = body.get("slot")
    if isinstance(slot, bool) or not isinstance(slot, int) or not 1 <= slot <= slots:
        raise RequestError(f"номер прогноза {slot!r}: допустимо от 1 до {slots}")
    horizon = body.get("horizon_h", p["horizon_h"])
    longest = longest_horizon_h(sim)
    if isinstance(horizon, bool) or not isinstance(horizon, (int, float)) or float(horizon) != int(horizon) \
            or not 1 <= horizon <= longest:
        raise RequestError(f"горизонт {horizon!r} ч: допустимо целое число от 1 до {longest}")
    limit = int(p["max_actions"])
    actions_a = _actions(body.get("actions_a"), sim.things, limit, "ветвь A")
    actions_b = _actions(body.get("actions_b"), sim.things, limit, "ветвь B")
    replicas = body.get("replicas", 1)
    if isinstance(replicas, bool) or not isinstance(replicas, int) or not 1 <= replicas <= int(p["max_replicas"]):
        raise RequestError(f"число повторов {replicas!r}: допустимо целое число от 1 до {p['max_replicas']}")
    first = actions_b[0][0] if actions_b else ""
    return Request(
        replicas=replicas,
        slot=slot, request_id=_text(body.get("request_id"), f"forecast-{slot}-{counter}", 60),
        requested_by=_text(body.get("requested_by"), "page" if slot == 1 else "module-advisor", 40),
        subject=_text(body.get("subject"), sim.tid(first) if first else "", 80), horizon_h=int(horizon),
        label_a=_text(body.get("label_a"), "Как есть", 160), label_b=_text(body.get("label_b"), "Изменение", 160),
        actions_a=actions_a, actions_b=actions_b)


# -- the workers ------------------------------------------------------------------------

@dataclass
class Job:
    key: str
    snapshot: Simulation | None
    actions: list
    enabled: dict
    horizon_h: int
    salt: str = ""                                # the replica's key for its random streams; '' = the live ones
    users: set = field(default_factory=set)       # slots waiting for this branch
    pid: int = 0
    fd: int = -1
    data: bytes = b""
    started: float = 0.0
    seconds: float = 0.0
    result: dict | None = None
    error: str = ""

    @property
    def done(self) -> bool:
        return self.result is not None or bool(self.error)


def _child(job: Job, period_s: int, write_fd: int) -> None:
    """Body of a worker process: compute the branch, write the result, exit."""
    try:
        signal.signal(signal.SIGTERM, signal.SIG_DFL)
        signal.signal(signal.SIGINT, signal.SIG_DFL)
        # Everything inherited from the simulator (its twins, caches, the snapshot) stays alive until the worker
        # exits: keep the collector off it, so that it walks only what the branch itself creates.
        gc.freeze()
        try:
            os.nice(10)                         # the live simulator and the services keep their priority
        except OSError:
            pass
        started = time.monotonic()
        result = run_branch(job.snapshot, job.actions, job.enabled, job.horizon_h, period_s, job.salt)
        data = json.dumps({"result": result, "seconds": time.monotonic() - started})
    except BaseException as exc:                    # noqa: BLE001 - everything goes back to the parent
        data = json.dumps({"error": f"{type(exc).__name__}: {exc}"})
    try:
        view = memoryview(data.encode())
        while view:
            view = view[os.write(write_fd, view):]
    finally:
        os._exit(0)


class ForecastDesk:
    """The forecast slots and the worker processes behind them. The runner
    hands it the `forecast` messages and the module twins' reports, calls
    `tick` once per tick and publishes `values()`."""

    def __init__(self, sim: Simulation, things: list[dict], fork: bool = True, dump_dir: str | None = None):
        """`dump_dir` (the runner passes FORECAST_DUMP_DIR): keep every copy of the
        model a forecast starts from, with its requests, so that the forecast
        can be run again outside the stand."""
        self.p = sim.params["forecast"]
        self.params = sim.params
        self.dump_dir = Path(dump_dir) if dump_dir else None
        self.fork = fork and hasattr(os, "fork")
        self.names = [short(t["thingId"]) for t in things if t["attributes"]["kind"] == "forecast"]
        self.roles = {int(t["attributes"]["spec"]["slot"]): t["attributes"]["spec"]["role"]
                      for t in things if t["attributes"]["kind"] == "forecast"}
        self.defaults = {short(t["thingId"]): {f: b["properties"]["value"] for f, b in t["features"].items()}
                         for t in things if t["attributes"]["kind"] == "forecast"}
        self.twins = {name: dict(values) for name, values in self.defaults.items()}
        self.requests: dict[int, Request] = {}
        self.new: list[Request] = []
        self.jobs: dict[str, Job] = {}
        self.counter = 0
        self.snapshots = 0
        # What the stand reports about each module that steers the mine; on until a report says otherwise.
        self.modules = {name: {"enabled": True, "status": "online"} for name in ALL}
        self.compute_s: list[float] = []            # seconds per branch, for the log
        self.compute_starts = 0                     # worker processes started
        self.requeued = 0                           # module branches stopped for the advisor's and started again

    # -- inputs -------------------------------------------------------------------------

    def observe_module(self, name: str, features: dict) -> None:
        module = name.removeprefix("module-")
        if module in self.modules:
            self.modules[module].update({k: v for k, v in features.items() if k in ("enabled", "status")})

    def enabled(self) -> dict[str, bool]:
        """Which module cores decide in a forecast: those whose service is online and switched on."""
        return {name: s.get("enabled") is not False and s.get("status") == "online" for name, s in self.modules.items()}

    def request(self, sim: Simulation, body: dict) -> None:
        self.counter += 1
        try:
            req = parse(body, sim, len(self.names), self.counter)
        except RequestError as exc:
            slot = body.get("slot")
            sim.log("warning", "mine", f"Запрос прогноза отклонён: {exc}")
            if isinstance(slot, int) and not isinstance(slot, bool) and 1 <= slot <= len(self.names):
                twin = self.twins[f"forecast-{slot}"]
                twin.update(status="error", error_text=str(exc)[:300])
            return
        self.new = [r for r in self.new if r.slot != req.slot] + [req]

    # -- the cycle ----------------------------------------------------------------------

    def tick(self, sim: Simulation) -> None:
        if self.new:
            self._take(sim)
        self._start()
        self._collect()
        self._finish(sim)

    def _take(self, sim: Simulation) -> None:
        """Requests of this tick: one copy of the model for all of them."""
        snapshot = copy.deepcopy(sim)
        self.snapshots += 1
        enabled = self.enabled()
        if self.dump_dir is not None:
            self._dump(snapshot, enabled)
        for req in self.new:
            self._drop(req.slot)
            req.enabled, req.fork_sim_time, req.asked_at = enabled, sim.sim_time(), time.monotonic()
            keys = []
            for replica in range(req.replicas):
                # Replica 0 runs on the live streams; replica k on streams keyed by the fork moment and k.
                salt = f"{req.fork_sim_time}/{replica}" if replica else ""
                pair = []
                for actions in (req.actions_a, req.actions_b):
                    key = json.dumps([self.snapshots, actions, enabled, req.horizon_h, salt], sort_keys=True,
                                     ensure_ascii=False)
                    job = self.jobs.get(key)
                    if job is None:
                        job = self.jobs[key] = Job(key, snapshot, actions, enabled, req.horizon_h, salt)
                    job.users.add(req.slot)
                    pair.append(key)
                keys.append(tuple(pair))
            req.keys = tuple(keys)
            self.requests[req.slot] = req
            twin = self.twins[f"forecast-{req.slot}"]
            twin.update(self.defaults[f"forecast-{req.slot}"])
            twin.update(status="running", request_id=req.request_id, requested_by=req.requested_by, subject=req.subject,
                        horizon_h=req.horizon_h, label_a=req.label_a, label_b=req.label_b,
                        actions_a=describe(req.actions_a), actions_b=describe(req.actions_b),
                        fork_sim_time=req.fork_sim_time)
            if req.slot == 1:
                sim.log("info", f"forecast-{req.slot}", f"Прогноз на {req.horizon_h} ч от {req.fork_sim_time[11:16]}: "
                                                        f"«{req.label_a}» против «{req.label_b}»")
        self.new = []

    def _dump(self, snapshot: Simulation, enabled: dict) -> None:
        try:
            self.dump_dir.mkdir(parents=True, exist_ok=True)
            requests = [{"slot": r.slot, "request_id": r.request_id, "horizon_h": r.horizon_h,
                         "actions_a": r.actions_a, "actions_b": r.actions_b} for r in self.new]
            with open(self.dump_dir / f"snapshot-{self.snapshots}.pickle", "wb") as f:
                pickle.dump({"sim": snapshot, "enabled": enabled, "requests": requests}, f)
        except OSError as exc:
            log.warning("could not keep the forecast snapshot: %s", exc)

    def _drop(self, slot: int) -> None:
        """A newer request replaces the slot's request: its branches go unless another slot waits for them."""
        old = self.requests.pop(slot, None)
        if old is None:
            return
        for key in old.job_keys():
            job = self.jobs.get(key)
            if job is None:
                continue
            job.users.discard(slot)
            if not job.users:
                self._kill(job)
                del self.jobs[key]

    def _kill(self, job: Job) -> None:
        if job.pid and not job.done:
            try:
                os.kill(job.pid, signal.SIGKILL)
                os.waitpid(job.pid, 0)
            except (ProcessLookupError, ChildProcessError):
                pass
        if job.fd >= 0:
            os.close(job.fd)
            job.fd = -1

    def running(self) -> int:
        return sum(1 for job in self.jobs.values() if job.pid and not job.done)

    def _rank(self, job: Job) -> int:
        """Who waits for a branch: 0 the page (an operator is looking at it) or the stand check, 1 the advisor,
        2 a module."""
        return min((RANKS.get(self.roles.get(slot), 2) for slot in job.users), default=2)

    def _start(self) -> None:
        """Start waiting branches, the page's first. The page's two branches start at once, beside whatever
        runs; the advisor's and the modules' share `max_parallel` workers, the advisor's first: a module's
        branch that runs while the advisor's wait is stopped and started again once a worker is free, so that
        an incident's options never wait for a background measurement."""
        # No more workers than the processors this container may use, less two kept for the live simulator
        # and the module services (the affinity mask follows a cpuset limit; `os.cpu_count` reads the host).
        limit = max(1, min(int(self.p["max_parallel"]), len(os.sched_getaffinity(0)) - 2))
        running = [job for job in self.jobs.values() if job.pid and not job.done and self._rank(job) > 0]
        waiting = sum(1 for job in self.jobs.values() if not job.pid and not job.done and self._rank(job) == 1)
        modules = [job for job in running if self._rank(job) == 2 and job.snapshot is not None]
        for job in modules[:max(0, len(running) + waiting - limit)]:
            self._requeue(job)
        free = limit - sum(
            1 for job in self.jobs.values() if job.pid and not job.done and self._rank(job) > 0)
        for job in sorted(self.jobs.values(), key=self._rank):
            if job.pid or job.done:
                continue
            if self._rank(job) > 0:
                if free <= 0:
                    break
                free -= 1
            job.started = time.monotonic()
            if not self.fork:
                # Without fork (and in tests that ask for it): compute here, on a copy.
                t0 = time.monotonic()
                try:
                    job.result = run_branch(copy.deepcopy(job.snapshot), job.actions, job.enabled, job.horizon_h,
                                            int(self.p["period_s"]), job.salt)
                except Exception as exc:            # noqa: BLE001
                    job.error = f"{type(exc).__name__}: {exc}"
                job.seconds, job.snapshot, job.pid = time.monotonic() - t0, None, -1
                continue
            read_fd, write_fd = os.pipe()
            pid = os.fork()
            if pid == 0:
                os.close(read_fd)
                _child(job, int(self.p["period_s"]), write_fd)
            os.close(write_fd)
            os.set_blocking(read_fd, False)
            job.pid, job.fd = pid, read_fd
            if self._rank(job) < 2:
                job.snapshot = None             # a module's branch keeps its copy: it may be started again
            self.compute_starts += 1

    def _requeue(self, job: Job) -> None:
        """Stop a running branch and put it back in the queue, from the same copy of the model."""
        self._kill(job)
        job.pid, job.data, job.started = 0, b"", 0.0
        self.requeued += 1

    def _collect(self) -> None:
        now = time.monotonic()
        for job in self.jobs.values():
            if job.fd < 0 or job.done:
                continue
            try:
                while True:
                    chunk = os.read(job.fd, 65536)
                    if not chunk:
                        self._close(job)
                        break
                    job.data += chunk
            except BlockingIOError:
                limit = timeout_s(self.params, job.horizon_h)
                if now - job.started > limit:
                    self._kill(job)
                    job.error = f"ветвь не досчитана за {limit:.0f} с"

    def _close(self, job: Job) -> None:
        os.close(job.fd)
        job.fd = -1
        try:
            os.waitpid(job.pid, 0)
        except ChildProcessError:
            pass
        try:
            message = json.loads(job.data.decode())
        except ValueError:
            message = {"error": "рабочий процесс прогноза завершился без результата"}
        job.data = b""
        if "result" in message:
            job.result, job.seconds = message["result"], float(message.get("seconds", 0.0))
            self.compute_s.append(job.seconds)
        else:
            job.error = str(message.get("error", "ошибка"))[:300]

    def _finish(self, sim: Simulation) -> None:
        for slot, req in list(self.requests.items()):
            jobs = [self.jobs.get(key) for key in req.job_keys()]
            if any(job is None or not job.done for job in jobs):
                continue
            name = f"forecast-{slot}"
            twin = self.twins[name]
            error = next((job.error for job in jobs if job.error), "")
            if error:
                twin.update(status="error", error_text=error[:300])
                sim.log("warning", name, f"Прогноз {slot} не получен: {error}")
            else:
                pairs = [(self.jobs[a].result, self.jobs[b].result) for a, b in req.keys]
                twin.update(self.features(req, pairs, sim))
                if slot == 1:
                    sim.log("info", name, twin["summary_text"][:400])
            del self.requests[slot]
            for key in req.job_keys():
                job = self.jobs.get(key)
                if job is not None:
                    job.users.discard(slot)
                    if not job.users:
                        del self.jobs[key]

    # -- the twins ----------------------------------------------------------------------

    def features(self, req: Request, pairs: list[tuple[dict, dict]], sim: Simulation) -> dict:
        """The twin of a finished request. With several replicas each branch shows the mean over them, and the
        differences B − A come with their standard error over the replicas (pairs share their random streams,
        so the error is that of the difference itself, not of two independent forecasts)."""
        a, b = combine([pair[0] for pair in pairs]), combine([pair[1] for pair in pairs])
        out = {"status": "ready", "ready_sim_time": sim.sim_time(), "error_text": "",
               "compute_s": round(time.monotonic() - req.asked_at, 1)}
        spread = paired(pairs)
        out.update(replicas=len(pairs), delta_total_se_kzt=round(spread["total"][1]),
                   delta_margin_se_kzt=round(spread["margin"][1]), delta_cu_se_t=round(spread["cu"][1], 2),
                   delta_total_replicas_kzt=",".join(str(round(d)) for d in spread["total"][2]),
                   delta_cu_replicas_t=",".join(f"{d:.2f}" for d in spread["cu"][2]))
        for branch, r in (("a", a), ("b", b)):
            for key in BRANCH_KEYS:
                out[f"{branch}_{key}"] = r[key]
        out["refused_actions"] = "; ".join(f"{branch.upper()}: {r['refused']}" for branch, r in (("a", a), ("b", b))
                                           if r["refused"])[:400]
        out["delta_margin_kzt"] = b["margin_kzt"] - a["margin_kzt"]
        out["delta_adjust_kzt"] = b["adjust_kzt"] - a["adjust_kzt"]
        out["delta_total_kzt"] = b["total_kzt"] - a["total_kzt"]
        out["delta_cu_t"] = round(b["cu_t"] - a["cu_t"], 2)
        out["delta_co2_t"] = round(b["co2_t"] - a["co2_t"], 1)
        econ = Economics(sim.params)
        out["verdict_text"] = verdict(req, a, b, econ, len(pairs), spread["total"][1])
        out["copper_text"] = copper_text(req.label_b, a, b, econ)
        out["summary_text"] = summary(req, a, b, econ, len(pairs), spread["total"][1])
        out["assumption_text"] = assumption(req, a, b)
        return out

    def values(self) -> dict[str, dict]:
        """Copies: the publisher compares them with what it sent last."""
        return {name: dict(values) for name, values in self.twins.items()}

    def close(self) -> None:
        for job in self.jobs.values():
            self._kill(job)
        self.jobs.clear()


BRANCH_KEYS = ("cu_t", "ore_t", "waste_t", "milled_t", "milled_cu_t", "clay_milled_t", "clay_milled_cu_t", "clay_cu_t",
               "to_clay_stock_t", "ofs1_clay_pct", "from_stock_t", "from_stock_cu_t", "from_pads_t",
               "from_pads_cu_t", "from_pit_t", "from_pit_cu_t", "stock_end_h", "diesel_l", "energy_mwh", "revenue_kzt",
               "diesel_kzt", "power_kzt", "margin_kzt", "rehandle_t", "rehandle_kzt", "grade_kzt", "work_kzt", "pads_kzt",
               "stock_kzt", "strip_kzt", "adjust_kzt", "resources_kzt", "total_kzt", "co2_t", "queue_min", "in_band_pct", "avail_pct",
               "breakdowns", "mill_stops", "overloads", "series_mill_tph", "series_stockpile_pct")
SAME_KZT = 50_000           # below this two branches are called equal in money
SAME_CU_T = 0.05
# Rounding of a branch's figures, as run_branch rounds them; the mean over replicas is rounded the same way.
DIGITS = {"cu_t": 2, "ore_t": 0, "waste_t": 0, "milled_t": 0, "milled_cu_t": 2, "clay_milled_t": 0, "clay_milled_cu_t": 2,
          "clay_cu_t": 2, "to_clay_stock_t": 0, "ofs1_clay_pct": 1, "from_stock_t": 0, "from_stock_cu_t": 2,
          "from_pads_t": 0, "from_pads_cu_t": 2, "from_pit_t": 0, "from_pit_cu_t": 2, "stock_end_h": 1, "diesel_l": 0,
          "energy_mwh": 1, "revenue_kzt": 0, "diesel_kzt": 0, "power_kzt": 0, "rehandle_t": 1, "rehandle_kzt": 0,
          "grade_kzt": 0, "work_kzt": 0, "pads_kzt": 0, "strip_kzt": 0, "resources_kzt": 0, "co2_t": 1, "queue_min": 2, "in_band_pct": 1,
          "avail_pct": 1, "breakdowns": 0, "mill_stops": 0, "overloads": 0}


def combine(results: list[dict]) -> dict:
    """One branch over its replicas: the mean of each figure; the hourly series and the refusals of the first
    replica (the one on the live streams). Margin, adjustment and total are summed again from the rounded
    parts, so that they add up exactly as in one branch."""
    if len(results) == 1:
        return results[0]
    out = dict(results[0])
    n = len(results)
    for key, digits in DIGITS.items():
        mean = sum(r[key] for r in results) / n
        out[key] = round(mean, digits) if digits else round(mean)
    out["margin_kzt"] = out["revenue_kzt"] - out["diesel_kzt"] - out["power_kzt"]
    out["stock_kzt"] = out["grade_kzt"] + out["work_kzt"] + out["pads_kzt"]
    out["adjust_kzt"] = out["stock_kzt"] + out["strip_kzt"] - out["rehandle_kzt"]
    out["total_kzt"] = out["margin_kzt"] + out["adjust_kzt"] - out["resources_kzt"]
    out["refused"] = next((r["refused"] for r in results if r["refused"]), "")
    return out


def paired(pairs: list[tuple[dict, dict]]) -> dict[str, tuple[float, float, list[float]]]:
    """Mean, standard error and the values of the differences B − A over the replicas (error 0 for one)."""
    out = {}
    for name, key in (("total", "total_kzt"), ("margin", "margin_kzt"), ("cu", "cu_t")):
        values = [b[key] - a[key] for a, b in pairs]
        mean = sum(values) / len(values)
        se = (sum((v - mean) ** 2 for v in values) / (len(values) - 1) / len(values)) ** 0.5 if len(values) > 1 else 0.0
        out[name] = (mean, se, values)
    return out


def split(a: dict, b: dict) -> dict[str, int]:
    """The difference of the totals B − A in its parts, KZT (positive is in favour of B); they add up to it
    exactly: the copper sold, the copper above the mine's average grade that the stocks and the pit gave up
    (or kept), the fleet work held in the ore dug and the waste stripped, the rehandling (done, and still owed
    by the ore on the ROM pads), diesel and power, and the shift's response resources."""
    d = lambda key: b[key] - a[key]
    return {"revenue": d("revenue_kzt"), "grade": d("grade_kzt"), "work": d("work_kzt") + d("strip_kzt"),
            "rehandle": d("pads_kzt") - d("rehandle_kzt"), "diesel": -d("diesel_kzt"), "power": -d("power_kzt"),
            "resources": -d("resources_kzt")}


def explain(label_b: str, a: dict, b: dict) -> str:
    """Why copper and margin of branch B move apart from A; '' when they do not."""
    d = lambda key: b[key] - a[key]
    cu, margin = d("cu_t"), d("margin_kzt")
    costs = d("diesel_kzt") + d("power_kzt")
    if (cu > SAME_CU_T and margin < -SAME_KZT) or (cu < -SAME_CU_T and margin > SAME_KZT):
        return (f" Меди в «{label_b}» {'больше' if cu > 0 else 'меньше'} на {num(abs(cu))} т "
                f"({signed(d('revenue_kzt') / 1e6)} млн тг выручки), но маржа {'ниже' if margin < 0 else 'выше'}: "
                f"дизель {signed(d('diesel_kzt') / 1e6)} и электроэнергия {signed(d('power_kzt') / 1e6)} млн тг "
                f"{'дороже' if costs > 0 else 'дешевле'} на {money(abs(costs))} млн тг.")
    return ""


def assumption(req: Request, a: dict, b: dict) -> str:
    """When the forecast takes the running incidents to end, in each branch; '' when none runs."""
    ea, eb = a.get("ends", ""), b.get("ends", "")
    if not ea and not eb:
        return ""
    if ea == eb:
        return f"Прогноз считает, что инцидент заканчивается в ожидаемое время: {ea}."
    return (f"Прогноз считает окончание инцидента: в «{req.label_a}» {ea or 'инцидентов нет'}; "
            f"в «{req.label_b}» {eb or 'инцидентов нет'}.")[:600]


def scatter_kzt(params: dict, replicas: int, se_kzt: float) -> float:
    """How far apart B and A may be by chance: twice the standard error of their difference, the larger of
    the one measured over the replicas and the one the measured paired noise gives (`forecast.noise_kzt`)."""
    return 2.0 * max(se_kzt, float(params["forecast"]["noise_kzt"]) / replicas ** 0.5)


def verdict(req: Request, a: dict, b: dict, econ: Economics, replicas: int = 1, se_kzt: float = 0.0) -> str:
    """The decision figure in one sentence: which branch is better by the total with the adjustment."""
    delta = b["total_kzt"] - a["total_kzt"]
    if abs(delta) < SAME_KZT:
        return f"По итогу с поправкой «{req.label_b}» и «{req.label_a}» равны"
    text = (f"Итог с поправкой: «{req.label_b}» {'выгоднее' if delta > 0 else 'хуже'} на {money(abs(delta))} млн тг "
            f"за {req.horizon_h} ч")
    noise = scatter_kzt(econ.params, replicas, se_kzt)
    if abs(delta) <= noise:
        text += f"; это в пределах разброса прогноза (±{num(noise / 1e6)} млн тг), по деньгам варианты равны"
    return text


def _grade(t: float, cu: float) -> str:
    return f"{num(100.0 * cu / t, 2)} %" if t > 1.0 else ""


def copper_text(label_b: str, a: dict, b: dict, econ: Economics) -> str:
    """Where the copper difference B − A comes from, in Russian and in short sentences: the tonnes milled, the
    head grade and the recovery; where the mill's ore came from (the stockpile, the ROM pads, the pit); what of
    it the total counts; the work of the fleet; the overload trips; the stockpile left. '' when the branches do
    not differ."""
    d = lambda key: b[key] - a[key]
    dcu, dmilled = d("cu_t"), d("milled_t")
    if abs(dcu) < SAME_CU_T and abs(dmilled) < 50 and abs(d("ore_t")) < 500 and a["overloads"] == b["overloads"] \
            and abs(d("resources_kzt")) < 50_000:
        return ""
    pm = lambda value: f"{'+' if value >= 0 else '−'}{thousands(abs(value))}"
    sentences = []
    # ОФ-1: its copper is the total less that of ОФ-2.
    cu1a, cu1b = a["cu_t"] - a.get("clay_cu_t", 0.0), b["cu_t"] - b.get("clay_cu_t", 0.0)
    ta, tb = a["milled_t"], b["milled_t"]
    dcu2 = b.get("clay_cu_t", 0.0) - a.get("clay_cu_t", 0.0)
    if ta > 1.0 and tb > 1.0 and a["milled_cu_t"] > 0 and b["milled_cu_t"] > 0:
        ga, gb = a["milled_cu_t"] / ta, b["milled_cu_t"] / tb
        ra, rb = cu1a / a["milled_cu_t"], cu1b / b["milled_cu_t"]
        tonnage, grade, recovery = (tb - ta) * ga * ra, tb * (gb - ga) * ra, tb * gb * (rb - ra)
        parts = []
        if abs(dmilled) >= 50 or abs(tonnage) >= SAME_CU_T:
            parts.append(f"переработано руды на ОФ-1 {pm(dmilled)} т ({signed(tonnage)} т)")
        if abs(grade) >= SAME_CU_T:
            parts.append(f"содержание меди в питании ОФ-1 {num(100 * ga, 3)} → {num(100 * gb, 3)} % ({signed(grade)} т)")
        if abs(recovery) >= SAME_CU_T:
            clay = (f", глины в питании {num(a.get('ofs1_clay_pct', 0.0))} → {num(b.get('ofs1_clay_pct', 0.0))} %"
                    if abs(b.get("ofs1_clay_pct", 0.0) - a.get("ofs1_clay_pct", 0.0)) >= 0.1 else "")
            parts.append(f"извлечение ОФ-1 {num(100 * ra)} → {num(100 * rb)} %{clay} ({signed(recovery)} т)")
        if abs(dcu2) >= SAME_CU_T:
            dt2 = b.get("clay_milled_t", 0) - a.get("clay_milled_t", 0)
            parts.append(f"ОФ-2 {signed(dcu2)} т (" + (f"переработано {pm(dt2)} т)" if abs(dt2) >= 50 else
                                                     "та же переработка, другое содержание и глинистость питания)"))
        if parts:
            sentences.append(f"Медь {signed(dcu)} т: " + ", ".join(parts))
    elif abs(dcu) >= SAME_CU_T:
        sentences.append(f"Медь {signed(dcu)} т")
    if abs(d("to_clay_stock_t") if "to_clay_stock_t" in a else 0) >= 500:
        sentences.append(f"На склад глинистой руды: {pm(a['to_clay_stock_t'])} т в А, {pm(b['to_clay_stock_t'])} т в Б")
    if a["overloads"] != b["overloads"]:
        sentences.append(f"Отключений мельницы по перегрузу: {a['overloads']} → {b['overloads']} (ветвь Б против А)")
    sources = []
    for key, title in (("stock", "со склада дроблёной руды"), ("pads", "с рудных складов"), ("pit", "из забоев")):
        diff = d(f"from_{key}_t")
        if abs(diff) >= 500:
            grade = _grade(b[f"from_{key}_t"], b[f"from_{key}_cu_t"]) if key != "stock" else ""
            sources.append(f"{title} {pm(diff)} т" + (f" ({grade})" if grade else ""))
    if sources:
        sentences.append("Руда на мельницу, Б против А: " + ", ".join(sources))
    grade_kzt = d("grade_kzt")
    if abs(grade_kzt) >= 1e6:
        ref = num(100.0 * econ.ref_grade, 2)
        sentences.append(f"Запасы руды (медь сверх средней руды фабрики, {ref} % на ОФ-1, и глина) отдали её, итог это "
                         f"вычитает: {signed(grade_kzt / 1e6)} млн тг"
                         if grade_kzt < 0 else
                         f"Запасы руды (медь сверх средней руды фабрики, {ref} % на ОФ-1, и глина) стали ценнее, итог это "
                         f"учитывает: {signed(grade_kzt / 1e6)} млн тг")
    if abs(d("ore_t")) >= 500 or abs(d("waste_t")) >= 500:
        sentences.append(f"Добыто руды {pm(d('ore_t'))} т, вскрыши {pm(d('waste_t'))} т: работа парка "
                         f"{signed((d('work_kzt') + d('strip_kzt')) / 1e6)} млн тг")
    if abs(d("stock_end_h")) >= 0.5:
        sentences.append(f"Склад дроблёной руды к концу: {num(a['stock_end_h'])} ч в А, {num(b['stock_end_h'])} ч в Б")
    if abs(d("resources_kzt")) >= 50_000:
        sentences.append(f"Ресурсы смены (бригады, техника, материалы): {money(a['resources_kzt'])} млн тг в А, "
                         f"{money(b['resources_kzt'])} млн тг в Б")
    return "".join(f"{s}. " for s in sentences).strip()


def summary(req: Request, a: dict, b: dict, econ: Economics, replicas: int = 1, se_kzt: float = 0.0) -> str:
    """The Russian summary of both branches: the decision figure first (the total with the adjustment), then
    where the copper difference comes from, the branches' figures and the basis of the money."""
    def branch(label: str, r: dict) -> str:
        return (f"«{label}»: медь {num(r['cu_t'])} т, добыто руды {thousands(r['ore_t'])} т и вскрыши "
                f"{thousands(r['waste_t'])} т, переработано {thousands(r['milled_t'])} т, маржа {money(r['margin_kzt'])} млн тг, "
                f"поправка {signed(r['adjust_kzt'] / 1e6)} млн тг")
    refused = "".join(f" Действие ветви «{label}» не выполнено: {r['refused']}." for label, r in
                      ((req.label_a, a), (req.label_b, b)) if r["refused"])
    times = "раза" if replicas % 10 in (2, 3, 4) and replicas % 100 not in (12, 13, 14) else "раз"
    over = (f" Обе ветви посчитаны {replicas} {times} с разной случайностью отказов, погрузки и рейсов, одинаковой для "
            f"A и B; цифры — средние, стандартная ошибка разницы {num(se_kzt / 1e6)} млн тг." if replicas > 1 else "")
    margin, cu = b["margin_kzt"] - a["margin_kzt"], b["cu_t"] - a["cu_t"]
    lead = (f"{verdict(req, a, b, econ, replicas, se_kzt)} (маржа {signed(margin / 1e6)} млн тг, медь {signed(cu)} т, "
            f"CO₂ {signed(b['co2_t'] - a['co2_t'])} т).")
    copper = copper_text(req.label_b, a, b, econ)
    return (f"Прогноз на {req.horizon_h} ч от {req.fork_sim_time[11:16]}. {lead}{(' ' + copper) if copper else ''}{over}"
            f"{refused} {branch(req.label_a, a)}; {branch(req.label_b, b)}.{explain(req.label_b, a, b)} Маржа — "
            f"переменная: выручка за медь минус дизель и электроэнергия, постоянные затраты не учтены. Поправка "
            f"оценивает руду везде одинаково: медь сверх средней руды месторождения ({num(100 * econ.ref_grade, 2)} % Cu) "
            f"по цене меди, работу парка — {econ.stock_kzt_t:.0f} тг на тонну добытой руды и снятой вскрыши, минус "
            f"перевалка с рудных складов ({econ.rehandle_kzt_t:.0f} тг/т); перекладывание руды из кучи в кучу итог "
            f"не меняет. Ресурсы смены (вторая бригада, грейдер, аварийная дорожная бригада) вычитаются из итога по "
            f"их цене.{(' ' + assumption(req, a, b)) if assumption(req, a, b) else ''}")
