"""Maintenance: finding a defect before it becomes a failure, and choosing
when the machine goes to the workshop.

A healthy machine loses condition only to wear: `health_pct` follows the
line 100 - wear x hours since service. The module watches how far each truck
and loader has fallen below that line and how fast it is falling. A gap that
is large enough and growing is a developing defect; its growth rate tells a
fast one (a minor fault, hours away) from a slow one (a major failure, a day
away) and gives the time left. The temperature that runs above the rest of
the fleet (engine or tyres) names the subsystem.

The booking is timed so that few machines are out at once: a defect with
time left waits until nobody else is in repair or service, one that is close
to failure goes at once; one of the two bays is kept for breakdowns. Planned
service is booked the same way as the 250-hour interval approaches. A loader
at an ore face is taken out while the coarse-ore stockpile can cover its
absence, if the defect leaves the time to wait for that.

Without the module machines run to failure and are repaired after they break.
"""
from __future__ import annotations

from collections import deque
from statistics import median

from mine.common.process import wear_pct
from mine.modules.base import Core, Message, num, short

OUT_STATES = ("down", "service", "to_service")
SAMPLE_S = 300.0                # condition is sampled every five simulated minutes
TREND_S = 5400.0                # and its trend is taken over the last hour and a half
LOADER_BUFFER_PCT = 60.0        # stockpile level at which an ore loader can be spared


class Watch:
    """What the module remembers about one machine."""

    def __init__(self):
        self.gaps: deque[tuple[float, float]] = deque()      # (time, points below the wear line)
        self.engine_c: float | None = None                   # running means while the machine works
        self.tyre_c: float | None = None
        self.found = False                                   # a defect is being tracked
        self.booked: str = ""                                # why the module booked it; '' = not booked
        self.last_sample = -1e9


class MaintCore(Core):
    name = "module-maint"
    kpi_label = "Готовность парка самосвалов"
    kpi_unit = "%"
    effect_kpi = ("avail_pct", "готовность самосвалов", "%", 1, "more")
    effect_note = ("Модуль ставит машины на обслуживание до отказа: отказов в работе меньше, готовность выше. Парк участка "
                   "больше, чем нужно фабрике, поэтому за 12 часов это мало сказывается на меди, а машины, стоящие на "
                   "плановом обслуживании, в эти часы не возят.")

    def __init__(self, things: list[dict], params: dict, enabled: bool = True):
        super().__init__(things, params, enabled)
        w = self.world
        self.p, self.rel = params["maintenance"], params["reliability"]
        self.trucks = w.by_kind["truck"]
        self.loaders = w.by_kind["loader"]
        self.units = [*self.trucks, *self.loaders]
        self.workshop = w.by_kind["workshop"][0]
        self.restart()

    def restart(self) -> None:
        super().restart()
        self.watch = {unit: Watch() for unit in self.units}
        self.defects_found = self.services_booked = self.breakdowns = 0
        self.next_round = 0.0

    # -- condition ----------------------------------------------------------------------

    def _gap(self, unit: str) -> float:
        """Points of health below what wear alone explains."""
        w = self.world
        expected = 100.0 - wear_pct(self.params, float(w.get(unit, "hours_since_service", 0.0)))
        return expected - float(w.get(unit, "health_pct", expected))

    def _sample(self, now: float, dt: float) -> None:
        w = self.world
        weight = min(1.0, dt / 1800.0)          # running means over about half an hour of work
        for unit, watch in self.watch.items():
            v = w.values[unit]
            if v.get("state") in OUT_STATES:
                # In repair or service the picture changes: start afresh when the machine is back.
                watch.gaps.clear()
                watch.found = False
                watch.engine_c = watch.tyre_c = None
                continue
            if unit in self.trucks and v.get("state") not in ("refuel", "standby"):
                for attr, feature in (("engine_c", "engine_temp_c"), ("tyre_c", "tyre_temp_c")):
                    reading, mean = float(v.get(feature) or 0.0), getattr(watch, attr)
                    setattr(watch, attr, reading if mean is None else mean + weight * (reading - mean))
            if now - watch.last_sample >= SAMPLE_S:
                watch.last_sample = now
                watch.gaps.append((now, self._gap(unit)))
                while watch.gaps and now - watch.gaps[0][0] > TREND_S:
                    watch.gaps.popleft()

    def _trend(self, watch: Watch) -> float:
        """Growth of the gap, points per hour (least squares over the samples kept)."""
        if len(watch.gaps) < 3 or watch.gaps[-1][0] - watch.gaps[0][0] < 900.0:
            return 0.0
        n = len(watch.gaps)
        mean_t = sum(t for t, _ in watch.gaps) / n
        mean_g = sum(g for _, g in watch.gaps) / n
        spread = sum((t - mean_t) ** 2 for t, _ in watch.gaps)
        return sum((t - mean_t) * (g - mean_g) for t, g in watch.gaps) / spread * 3600.0 if spread > 0 else 0.0

    def diagnose(self, unit: str) -> dict | None:
        """A developing defect of the unit: gap, trend, hours left and the subsystem; None if it is healthy."""
        watch = self.watch[unit]
        if not watch.gaps:
            return None
        gap, slope = watch.gaps[-1][1], self._trend(watch)
        if gap < self.p["deficit_alarm_pct"] or slope < self.p["slope_alarm_pct_h"]:
            return None
        fast = slope >= self.p["fast_slope_pct_h"]
        limit = self.rel["health_drop_minor_pct" if fast else "health_drop_major_pct"]
        indicator, value, where = "health_pct", float(self.world.get(unit, "health_pct", 0.0)), "трансмиссия или гидравлика"
        if unit in self.trucks:
            # Compared with the trucks of the same face: they drive the same haul, so their engines and tyres
            # work alike; the whole fleet is the yardstick only where a face has too few trucks.
            home = self.world.attrs[unit].get("links", {}).get("home_loader")
            peers = [t for t in self.trucks if t != unit and self.world.attrs[t].get("links", {}).get("home_loader") == home]
            if len(peers) < 3:
                peers = [t for t in self.trucks if t != unit]
            for attr, feature, threshold, title in (
                    ("engine_c", "engine_temp_c", self.p["engine_residual_c"], "система охлаждения двигателя"),
                    ("tyre_c", "tyre_temp_c", self.p["tyre_residual_c"], "ходовая часть и шины")):
                own = getattr(watch, attr)
                others = [getattr(self.watch[t], attr) for t in peers if getattr(self.watch[t], attr) is not None]
                if own is not None and len(others) >= 3 and own - median(others) >= threshold:
                    indicator, value, where = feature, round(own, 1), f"{title}, на {num(own - median(others))} °C выше машин того же забоя"
        return {"gap": gap, "slope": slope, "fast": fast, "hours_left": max(0.0, (limit - gap) / slope),
                "indicator": indicator, "value": value, "where": where}

    # -- booking ------------------------------------------------------------------------

    def _out(self, names: list[str]) -> int:
        """Machines that are out of production or about to be: in repair, in service, booked."""
        w = self.world
        return sum(1 for n in names if w.get(n, "state") in OUT_STATES
                   or w.get(n, "service_kind") == "planned")

    def _bay_free(self) -> bool:
        w = self.world
        return int(w.get(self.workshop, "bays_busy", 0)) + int(w.get(self.workshop, "jobs_queued", 0)) \
            < int(w.get(self.workshop, "bays_total", 2))

    def _book(self, unit: str, reason: str, indicator: str, value: float, text: str) -> None:
        self.watch[unit].booked = reason
        self.services_booked += 1
        self.send(unit, "schedule-service", {"reason": reason, "indicator": indicator, "value": value}, text)

    def _may_go(self, unit: str, hours_left: float | None) -> tuple[bool, str]:
        """Whether a machine may be taken out now, and what it is waiting for if not."""
        w = self.world
        urgent = hours_left is not None and hours_left <= self.p["urgent_h"]
        if unit in self.loaders:
            if self._out([l for l in self.loaders if l != unit]):
                return urgent, "другой экскаватор не работает"
            ore_face = w.get(unit, "material") not in ("waste", "none")
            buffer_ok = float(w.get("stockpile", "level_pct", 0.0)) >= LOADER_BUFFER_PCT
            if ore_face and not buffer_ok and not (hours_left is not None and hours_left <= 3 * self.p["urgent_h"]):
                return False, "склад дроблёной руды ниже 60 %"
            return True, ""
        out = self._out([t for t in self.trucks if t != unit])
        if urgent:
            return True, ""
        if out >= self.p["planned_out_max"]:
            return False, f"в ремонте и обслуживании уже {out} самосв."
        return self._bay_free(), "боксы заняты"

    def decide(self, now: float) -> None:
        if now < self.next_round:
            return
        self.next_round = now + 60.0
        w, interval = self.world, self.rel["pm_interval_h"]
        # Defects first, the one closest to failure before the others; then planned service, the most overdue first.
        cases = []
        for unit in self.units:
            v = w.values[unit]
            if v.get("state") in OUT_STATES or v.get("service_reason"):
                continue
            found = self.diagnose(unit)
            hours = float(v.get("hours_since_service") or 0.0)
            if found:
                if not self.watch[unit].found:
                    self.watch[unit].found = True
                    self.defects_found += 1
                cases.append((0, found["hours_left"], unit, found))
            elif hours >= interval - self.p["pm_lead_h"] and not w.held(unit, "service_hold_until"):
                # Planned service waits while an operator keeps the machine at work; a defect does not.
                cases.append((1, -hours, unit, None))
        for _, _, unit, found in sorted(cases, key=lambda c: c[:3]):
            title = w.title(unit)
            hours = float(w.get(unit, "hours_since_service", 0.0))
            if found:
                go, waits = self._may_go(unit, found["hours_left"])
                if not go:
                    continue
                kind = "быстро развивающийся дефект" if found["fast"] else "развивающийся дефект"
                health = float(w.get(unit, "health_pct", 0.0))
                self._book(unit, f"{kind}: {found['where']}", found["indicator"], found["value"],
                           f"{title}: показатель состояния {num(health)} %, на {num(found['gap'])} п. ниже нормы износа и падает на "
                           f"{num(found['slope'])} п./ч ({found['where']}). До отказа около {num(found['hours_left'])} ч: "
                           f"машина идёт в мастерскую {'после разгрузки' if unit in self.trucks else 'после текущей погрузки'}.")
            else:
                overdue = hours >= interval + self.p["pm_overdue_h"]
                go, waits = self._may_go(unit, 0.0 if overdue and unit in self.trucks and self._bay_free() else None)
                if not go:
                    continue
                self._book(unit, f"плановое ТО, наработка {hours:.0f} ч", "hours_since_service", round(hours, 1),
                           f"{title}: наработка {hours:.0f} ч при интервале {interval:.0f} ч, "
                           f"{'обслуживание просрочено' if overdue else 'других машин в ремонте нет'}: плановое ТО.")
            return                              # one booking per round: the next one sees it in the twins

    def revert(self) -> list[Message]:
        """Withdraw the bookings that have not started yet."""
        w, messages = self.world, []
        for unit, watch in self.watch.items():
            # Work already under way in a bay is finished; the simulator ignores the cancel for it.
            if watch.booked and w.get(unit, "state") not in ("service", "down"):
                messages.append((unit, "cancel-service", {}))
            watch.booked = ""
        return messages

    def switched_on(self) -> None:
        self.defects_found = self.services_booked = self.breakdowns = 0

    # -- indicators -----------------------------------------------------------------------

    def changed(self, name: str, old: dict, new: dict) -> None:
        if name in self.watch:
            if old.get("state") != "down" and new.get("state") == "down" \
                    and str(new.get("service_kind", "")).startswith("breakdown"):
                self.breakdowns += 1
            if old.get("state") in ("service", "down") and new.get("state") not in ("service", "down"):
                self.watch[name].booked = ""

    def measure(self, now: float, dt: float) -> None:
        w = self.world
        available = sum(1 for t in self.trucks if w.get(t, "state") not in OUT_STATES)
        self.tally.add(self.enabled, 100.0 * available * dt, len(self.trucks) * dt)
        self._sample(now, dt)

    def kpi_value(self) -> float:
        share = self.tally.ratio(self.enabled)
        return round(share, 1) if share is not None else 0.0

    def own_features(self) -> dict:
        at_risk = [unit for unit in self.units if self.watch[unit].found
                   and self.world.get(unit, "state") not in ("service", "down")]
        return {
            "fleet_avail_pct": self.kpi_value(),
            "defects_found": self.defects_found,
            "services_booked": self.services_booked,
            "breakdowns": self.breakdowns,
            "units_watched": len(self.units),
            "units_at_risk": ",".join(at_risk) if self.enabled else "",
        }

    def effect_text(self) -> str:
        text = self.effect_sentence() + self.since_text("готовность самосвалов", "%", 1)
        if self.enabled and self.tally.hours(True) > 0:
            text += (f" Отказов в работе за это время: {self.breakdowns}, найдено дефектов: {self.defects_found}, "
                     f"заявок на обслуживание: {self.services_booked}.")
        return text
