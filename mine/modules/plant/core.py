"""Plant control: the feed rate of the mill.

Every control period the module sets the setpoint to

    min( power limit,  max( lowest stable feed,  ore arriving + (stockpile - reserve) / horizon ) )

- the power limit is the feed the installed mill power can grind at the
  hardness of the ore on the stockpile, less a small margin; feeding more
  would overload the mill;
- the second term lets the stockpile glide towards its reserve over the
  horizon: with a full stockpile it exceeds the power limit, so the mill runs
  as fast as its power allows; when the stockpile falls it shrinks to what
  arrives, so the feed is cut early and smoothly instead of the mill running
  flat out until it is empty;
- while the crusher is stopped and its twin says when it is expected back
  (`restart_expected_sim_time`) sooner than the horizon, the stockpile only has
  to last until then: the horizon is that time, so a stop the stockpile covers
  costs no feed;
- after the crusher restarts, the first ore trucks are still on their way back
  from the faces and the pads; for one trend window the ore is taken to arrive
  at least as fast as the mill takes it, so the empty hour behind does not cut
  the feed.

With the setpoint the module publishes what it predicts for it: recovery
(the flotation model of params.yaml) and the copper rate.

Without the module the setpoint is the design rate, whatever the stockpile
and the ore.
"""
from __future__ import annotations

import math
from collections import deque

from mine.modules.base import Core, Message, num
from mine.common.process import recovery_pct


class PlantCore(Core):
    name = "module-plant"
    kpi_label = "Выпуск меди в концентрате"
    kpi_unit = "т/ч"
    effect_kpi = ("milled_t", "переработано руды", "т", 0, "more")

    def __init__(self, things: list[dict], params: dict, enabled: bool = True):
        super().__init__(things, params, enabled)
        w = self.world
        self.p = params["plant_control"]
        self.mill = w.by_kind["mill"][0]
        self.stockpile = w.by_kind["stockpile"][0]
        self.crusher = w.by_kind["crusher"][0]
        self.capacity_t = float(w.spec(self.stockpile, "capacity_t"))
        self.design_tph = float(w.spec(self.mill, "design_tph"))
        self.min_tph = float(w.spec(self.mill, "min_feed_tph"))
        self.max_setpoint_tph = float(w.spec(self.mill, "max_setpoint_tph"))
        self.reserve_t = self.p["reserve_pct"] / 100.0 * self.capacity_t
        self.restart()

    def restart(self) -> None:
        super().restart()
        self.history: deque[tuple[float, float, float]] = deque()    # (time, stockpile t, tonnes milled in total)
        self.crusher_stopped = False
        self.restarted_at: float | None = None
        self.next_control = 0.0
        self.setpoint = 0.0
        self.inflow_tph = 0.0
        self.power_limit_tph = 0.0
        self.predicted = (0.0, 0.0)                 # recovery %, copper t/h
        self.last_cu: float | None = None
        self.stops_at_start: int | None = None
        self.last_reason = ""
        self.extra_t = self.extra_cu_t = 0.0        # milled above the constant design setpoint since enabled

    # -- inputs -----------------------------------------------------------------------

    def _inflow(self, now: float) -> float:
        """Ore arriving on the stockpile, t/h: its change plus what the mill took, over the trend window."""
        w = self.world
        level, milled = float(w.get(self.stockpile, "level_t", 0.0)), float(w.get(self.mill, "tonnes_total", 0.0))
        stopped = w.get(self.crusher, "state") == "stopped"
        if self.crusher_stopped and not stopped:
            # The crusher is back: the hour behind, without ore, says nothing about what arrives now, and the
            # first ore trucks reach it only after their trips from the faces and the pads they were sent to.
            # For one trend window the module assumes the ore arrives as fast as the mill takes it.
            self.history.clear()
            self.restarted_at = now
        self.crusher_stopped = stopped
        if not self.history or now - self.history[-1][0] >= 60.0:
            self.history.append((now, level, milled))
        while self.history and now - self.history[0][0] > self.p["trend_window_min"] * 60.0:
            self.history.popleft()
        t0, level0, milled0 = self.history[0]
        balance = float(w.get(self.mill, "feed_tph", 0.0)) or self.design_tph
        if now - t0 < 600.0 or milled < milled0:
            return balance                          # too little history: assume balance
        trend = max(0.0, (level - level0 + milled - milled0) / (now - t0) * 3600.0)
        if self.restarted_at is not None and now - self.restarted_at < self.p["trend_window_min"] * 60.0:
            return max(trend, balance)
        return trend

    def _restart_in_h(self, now: float) -> float | None:
        """Hours until the stopped crusher is expected back (its twin's `restart_expected_sim_time`), or None."""
        w = self.world
        if w.get(self.crusher, "state") != "stopped":
            return None
        at = w.time_of(w.get(self.crusher, "restart_expected_sim_time", ""))
        return max(0.0, at - now) / 3600.0 if at is not None else None

    # -- one round ----------------------------------------------------------------------

    def decide(self, now: float) -> None:
        w = self.world
        self.inflow_tph = self._inflow(now)
        if w.get(self.mill, "setpoint_source") == "operator" and w.held(self.mill, "setpoint_hold_until"):
            # An operator's setpoint stands for its stated time: the module does not override it.
            if self.last_reason != "operator":
                self.last_reason = "operator"
                self.last_decision = (f"Уставку {float(w.get(self.mill, 'feed_setpoint_tph', 0.0)):.0f} т/ч задал оператор "
                                      f"до {str(w.get(self.mill, 'setpoint_hold_until'))[11:16]}: модуль не вмешивается до "
                                      f"окончания указания.")
            self.next_control = 0.0
            return
        if now < self.next_control:
            return
        self.next_control = now + self.p["control_min"] * 60.0
        level = float(w.get(self.stockpile, "level_t", 0.0))
        bwi = float(w.get(self.stockpile, "hardness_bwi", 0.0)) or float(w.get(self.mill, "feed_bwi", 14.0))
        grade = float(w.get(self.stockpile, "grade_pct", 0.0))
        max_feed = float(w.get(self.mill, "max_feed_tph", self.design_tph))
        self.power_limit_tph = min(max_feed * self.p["power_margin"], self.max_setpoint_tph)
        # While the crusher is stopped and expected back sooner than the horizon, the stockpile only has to
        # last until then: the feed is not cut for a stop the stockpile covers.
        horizon_h, restart_h = float(self.p["horizon_h"]), self._restart_in_h(now)
        if restart_h is not None and restart_h < horizon_h:
            horizon_h = max(restart_h, 0.25)
        glide = self.inflow_tph + (level - self.reserve_t) / horizon_h
        setpoint = min(self.power_limit_tph, max(self.min_tph, glide))
        # In steps of 10 t/h, never below the lowest stable feed (rounded up to a step).
        setpoint = max(float(int(setpoint / 10.0) * 10), math.ceil(self.min_tph / 10.0) * 10.0)
        recovery = recovery_pct(self.params, grade, bwi, setpoint) if grade > 0 else 0.0
        self.predicted = (recovery, setpoint * grade / 100.0 * recovery / 100.0)
        if setpoint >= self.power_limit_tph - 10.0:
            reason = (f"склад заполнен на {100 * level / self.capacity_t:.0f} %, уставка на потолке модуля "
                      f"{self.power_limit_tph:.0f} т/ч")
        elif setpoint <= self.min_tph:
            reason = (f"склад {100 * level / self.capacity_t:.0f} %, приход руды {self.inflow_tph:.0f} т/ч: "
                      f"наименьшее устойчивое питание")
        elif restart_h is not None and restart_h < float(self.p["horizon_h"]):
            reason = (f"склад {100 * level / self.capacity_t:.0f} %, дробилка стоит: питание снижено, чтобы склад не "
                      f"опустился ниже {self.p['reserve_pct']:.0f} % до её ожидаемого пуска через {num(restart_h)} ч")
        else:
            reason = (f"склад {100 * level / self.capacity_t:.0f} %, приход руды {self.inflow_tph:.0f} т/ч: питание снижено заранее, "
                      f"чтобы склад не опустился ниже {self.p['reserve_pct']:.0f} % за {self.p['horizon_h']:.0f} ч")
        text = None
        if abs(setpoint - self.setpoint) >= 30.0 or reason.split(":")[-1] != self.last_reason:
            text = (f"Уставка питания {setpoint:.0f} т/ч: {reason}. Потолок уставки модуля {self.power_limit_tph:.0f} т/ч — "
                    f"{100 * self.p['power_margin']:.0f} % предела мельницы по мощности {max_feed:.0f} т/ч при крепости "
                    f"{num(bwi)} кВт·ч/т; прогноз: извлечение {num(recovery)} %, медь {num(self.predicted[1], 2)} т/ч.")
            self.last_reason = reason.split(":")[-1]
        self.setpoint = setpoint
        self.send(self.mill, "set-feed-rate", {"tph": setpoint}, text)

    def revert(self) -> list[Message]:
        self.next_control, self.setpoint, self.predicted, self.last_reason = 0.0, 0.0, (0.0, 0.0), ""
        return [(self.mill, "release", {})]

    def switched_on(self) -> None:
        self.stops_at_start = None
        self.extra_t = self.extra_cu_t = 0.0

    # -- indicators -----------------------------------------------------------------------

    def measure(self, now: float, dt: float) -> None:
        w = self.world
        cu = float(w.get("flotation", "cu_total_t", 0.0))
        if self.last_cu is not None and cu >= self.last_cu:
            self.tally.add(self.enabled, cu - self.last_cu, dt / 3600.0)        # tonnes of copper per hour
        self.last_cu = cu
        if self.stops_at_start is None:
            self.stops_at_start = int(w.get(self.mill, "stops_total", 0))
        if self.enabled:
            # The direct effect of the setpoint: what the mill ground above the constant design rate.
            above_t = (float(w.get(self.mill, "throughput_tph", 0.0)) - self.design_tph) * dt / 3600.0
            grade = float(w.get("flotation", "feed_grade_pct", 0.0)) / 100.0
            recovery = float(w.get("flotation", "recovery_pct", 0.0)) / 100.0
            self.extra_t += above_t
            self.extra_cu_t += above_t * grade * recovery

    def kpi_value(self) -> float:
        rate = self.tally.ratio(self.enabled)
        return round(rate, 2) if rate is not None else 0.0

    def own_features(self) -> dict:
        w = self.world
        level = float(w.get(self.stockpile, "level_t", 0.0))
        rate = self.setpoint if self.enabled and self.setpoint > 0 else float(w.get(self.mill, "feed_setpoint_tph", 0.0))
        stops = int(w.get(self.mill, "stops_total", 0)) - (self.stops_at_start or 0)
        return {
            "feed_setpoint_tph": round(self.setpoint) if self.enabled else 0,
            "power_limit_tph": round(self.power_limit_tph) if self.enabled else 0,
            "ore_inflow_tph": round(self.inflow_tph) if self.enabled else 0,
            "hours_of_feed": round(level / rate, 1) if rate > 0 else 0.0,
            "predicted_recovery_pct": round(self.predicted[0], 1),
            "predicted_cu_tph": round(self.predicted[1], 2),
            "mill_stops": max(stops, 0),
        }

    def effect_text(self) -> str:
        text = self.effect_sentence() + self.since_text("медь в концентрате", "т/ч", 2)
        if self.enabled and self.tally.hours(True) > 0:
            stops = int(self.world.get(self.mill, "stops_total", 0)) - (self.stops_at_start or 0)
            text += (f" Сверх проектной производительности {self.design_tph:.0f} т/ч за это время переработано "
                     f"{self.extra_t:+.0f} т руды (около {num(self.extra_cu_t, 1)} т меди); остановок мельницы: {max(stops, 0)}.")
        return text
