"""Plant control: the feed rate of the mill.

Every control period the module sets the setpoint to

    min( ceiling,  max( lowest stable feed,  ore arriving + (stockpile - reserve) / horizon ) )

- the ceiling has two levels. The sustained one is what ОФ-1 processed on
  average in 2024 (`plant_control.sustained_tph`: 28.67 Mt over 8,059 operating
  hours, 3,557 t/h). Above it the module goes only in short peaks, up to the
  2024 trial rate (`plant_control.max_feed_tph`, 3,902 t/h): a peak starts when
  the stockpile is nearly full (`peak_from_pct`) and the crusher runs, lasts
  while the stockpile holds above `peak_hold_pct`, and the shift has
  `peak_h_shift` hours of it. The mill counts the minutes of the shift its
  setpoint stood above the working rate (`mill.peak_min_shift`), so the shift
  average stays within `shift_margin_pct` of the sustained ceiling. Both levels
  are capped by the feed the installed mill power grinds at the hardness and
  clay of the ore on the stockpile, less a small margin: feeding more would
  overload the mill;
- the second term lets the stockpile glide towards its reserve over the
  horizon: with a full stockpile it exceeds the ceiling, so the mill runs at
  the ceiling; when the stockpile falls it shrinks to what arrives, so the feed
  is cut early and smoothly instead of the mill running until it is empty;
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

Without the module the setpoint is the working rate (the same 3,557 t/h),
whatever the stockpile and the ore: on hard or clay-rich ore the mill then
overloads and stops.
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
        # The two levels of the ceiling: the sustained one, ОФ-1's 2024 average (`plant_control.sustained_tph`), and
        # the peak one, the 2024 trial rate (`plant_control.max_feed_tph`), never above what the mill accepts.
        self.sustained_tph = float(self.p["sustained_tph"])
        self.max_setpoint_tph = min(float(w.spec(self.mill, "max_setpoint_tph")), float(self.p["max_feed_tph"]))
        self.peak_min = float(self.p["peak_h_shift"]) * 60.0
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
        self.power_limit_tph = 0.0                  # the ceiling of this round: sustained, peak or the power limit
        self.peaking = False
        self.peak_left_min = 0.0
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
        clay = float(w.get(self.stockpile, "clay_pct", 0.0))
        level_pct = 100.0 * level / self.capacity_t
        power_tph = max_feed * self.p["power_margin"]
        # A peak above the sustained ceiling: only with the stockpile nearly full and the crusher running, while the
        # shift has peak time left (the mill counts the minutes of the shift above its working rate).
        self.peak_left_min = max(0.0, self.peak_min - float(w.get(self.mill, "peak_min_shift", 0.0)))
        room = self.peak_left_min >= float(self.p["control_min"]) and w.get(self.crusher, "state") != "stopped"
        threshold = self.p["peak_hold_pct"] if self.peaking else self.p["peak_from_pct"]
        self.peaking = room and level_pct >= threshold and min(power_tph, self.max_setpoint_tph) > self.sustained_tph + 10.0
        if power_tph < self.sustained_tph:
            ceiling, kind = float(int(power_tph / 10.0) * 10), "power"
        elif self.peaking:
            ceiling, kind = float(int(min(power_tph, self.max_setpoint_tph) / 10.0) * 10), "peak"
        else:
            ceiling, kind = self.sustained_tph, "sustained"
        self.power_limit_tph = ceiling
        # While the crusher is stopped and expected back sooner than the horizon, the stockpile only has to
        # last until then: the feed is not cut for a stop the stockpile covers.
        horizon_h, restart_h = float(self.p["horizon_h"]), self._restart_in_h(now)
        if restart_h is not None and restart_h < horizon_h:
            horizon_h = max(restart_h, 0.25)
        glide = self.inflow_tph + (level - self.reserve_t) / horizon_h
        floor = math.ceil(self.min_tph / 10.0) * 10.0
        # At the ceiling the setpoint is the ceiling itself (the sustained one is 3557, not a step of 10); below it
        # the setpoint goes in steps of 10 t/h, never below the lowest stable feed (rounded up to a step).
        setpoint = ceiling if glide >= ceiling else max(float(int(max(glide, self.min_tph) / 10.0) * 10), floor)
        setpoint = max(min(setpoint, ceiling), floor)
        recovery = recovery_pct(self.params, grade, bwi, setpoint) if grade > 0 else 0.0
        self.predicted = (recovery, setpoint * grade / 100.0 * recovery / 100.0)
        pile = f"склад заполнен на {level_pct:.0f} %"
        if setpoint >= ceiling - 10.0 and kind == "peak":
            state = "peak"
            reason = (f"{pile} — пик выше устойчивого потолка, пока склад не ниже {self.p['peak_hold_pct']:.0f} %; "
                      f"пикового времени в этой смене осталось {self.peak_left_min:.0f} мин из {self.peak_min:.0f}")
        elif setpoint >= ceiling - 10.0 and kind == "power":
            state = "power"
            reason = f"{pile}, уставка на пределе мельницы по мощности для этой руды"
        elif setpoint >= ceiling - 10.0:
            state = "sustained"
            why = ("пиковое время смены израсходовано" if self.peak_left_min < float(self.p["control_min"])
                   else "дробилка стоит" if w.get(self.crusher, "state") == "stopped"
                   else f"пик начинается при складе от {self.p['peak_from_pct']:.0f} %")
            reason = f"{pile}, уставка на устойчивом потолке модуля ({why})"
        elif setpoint <= floor:
            state = "min"
            reason = (f"склад {level_pct:.0f} %, приход руды {self.inflow_tph:.0f} т/ч: "
                      f"наименьшее устойчивое питание")
        elif restart_h is not None and restart_h < float(self.p["horizon_h"]):
            state = "restart"
            reason = (f"склад {level_pct:.0f} %, дробилка стоит: питание снижено, чтобы склад не "
                      f"опустился ниже {self.p['reserve_pct']:.0f} % до её ожидаемого пуска через {num(restart_h)} ч")
        else:
            state = "glide"
            reason = (f"склад {level_pct:.0f} %, приход руды {self.inflow_tph:.0f} т/ч: питание снижено заранее, "
                      f"чтобы склад не опустился ниже {self.p['reserve_pct']:.0f} % за {self.p['horizon_h']:.0f} ч")
        text = None
        if abs(setpoint - self.setpoint) >= 30.0 or state != self.last_reason:
            if kind == "power":
                ceiling_text = (f"Потолок модуля {ceiling:.0f} т/ч — {100 * self.p['power_margin']:.0f} % предела мельницы "
                                f"по мощности {max_feed:.0f} т/ч при крепости {num(bwi)} кВт·ч/т и глинистости {num(clay)} %, "
                                f"ниже устойчивого потолка {self.sustained_tph:.0f} т/ч")
            else:
                ceiling_text = (f"Устойчивый потолок {self.sustained_tph:.0f} т/ч — средняя производительность ОФ-1 за "
                                f"2024 год; пик до {int(min(power_tph, self.max_setpoint_tph) / 10.0) * 10:.0f} т/ч "
                                f"(испытания 2024 года — {num(float(self.p['max_feed_tph']), 0)} т/ч) только при складе от "
                                f"{self.p['peak_from_pct']:.0f} % и не дольше {num(self.peak_min / 60.0, 0)} ч за смену; "
                                f"предел мельницы по мощности {max_feed:.0f} т/ч при крепости {num(bwi)} кВт·ч/т")
            text = (f"Уставка питания {setpoint:.0f} т/ч: {reason}. {ceiling_text}; прогноз: извлечение {num(recovery)} %, "
                    f"медь {num(self.predicted[1], 2)} т/ч.")
            self.last_reason = state
        self.setpoint = setpoint
        self.send(self.mill, "set-feed-rate", {"tph": setpoint}, text)

    def revert(self) -> list[Message]:
        self.next_control, self.setpoint, self.predicted, self.last_reason = 0.0, 0.0, (0.0, 0.0), ""
        self.peaking = False
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
            "sustained_tph": round(self.sustained_tph),
            "peak_tph": int(self.max_setpoint_tph / 10.0) * 10,          # the peak setpoint, in steps of 10 t/h
            "peak_left_min": round(max(0.0, self.peak_min - float(w.get(self.mill, "peak_min_shift", 0.0)))),
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
            text += (f" Сверх устойчивого потолка {self.design_tph:.0f} т/ч (средняя производительность ОФ-1 за 2024 год) "
                     f"за это время переработано "
                     f"{self.extra_t:+.0f} т руды (около {num(self.extra_cu_t, 1)} т меди); остановок мельницы: {max(stops, 0)}.")
        return text
