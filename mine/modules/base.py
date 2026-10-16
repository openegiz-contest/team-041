"""What the module cores share: the picture of the mine built from twin
reports, the on/off switch, the count of decisions, and the measurement of
what the module brings.

A module's effect is measured against the same mine at the same moment
without it: every `forecast.module_period_h` simulated hours the module asks
the forecast engine, in a forecast slot of its own, for 12 hours ahead in two
branches from one copy of the live model: A with the module as it is, B with
the module switched off at the fork. The difference A − B in copper, in
variable margin and in the module's own indicator is its effect; the mean of
the last few such forecasts is published with the method (`effect_basis`).
Nothing is compared with another period or with a fixed reference run.
"""
from __future__ import annotations

from collections import deque
from datetime import datetime

Message = tuple[str, str, dict]         # (twin name, subject, body) of a Ditto live message


def short(thing_id: str) -> str:
    return thing_id.split(":", 1)[1] if isinstance(thing_id, str) and ":" in thing_id else thing_id


def num(value: float, digits: int = 1) -> str:
    """A number the way Russian text writes it: with a decimal comma."""
    return f"{value:.{digits}f}".replace(".", ",")


class World:
    """The mine as the platform reports it: static attributes of every twin
    and the latest feature values. A core reads it and never writes to it."""

    def __init__(self, things: list[dict]):
        self.ns = things[0]["thingId"].split(":", 1)[0]
        self.attrs: dict[str, dict] = {}
        self.values: dict[str, dict] = {}
        self.by_kind: dict[str, list[str]] = {}
        for thing in things:
            name = short(thing["thingId"])
            self.attrs[name] = thing.get("attributes", {})
            self.values[name] = {feature: body.get("properties", {}).get("value")
                                 for feature, body in thing.get("features", {}).items()}
            self.by_kind.setdefault(self.attrs[name].get("kind", ""), []).append(name)
        for names in self.by_kind.values():
            names.sort()
        self.now = self._clock()

    def tid(self, name: str) -> str:
        return f"{self.ns}:{name}"

    def title(self, name: str) -> str:
        return self.attrs.get(name, {}).get("name", name)

    def get(self, name: str, feature: str, default=None):
        value = self.values.get(name, {}).get(feature)
        return default if value is None else value

    def link(self, name: str, relation: str) -> str:
        return short(self.attrs[name]["links"][relation])

    def spec(self, name: str, key: str, default=None):
        return self.attrs[name].get("spec", {}).get(key, default)

    def _clock(self) -> float:
        """Simulated time in seconds, read from the `mine` twin."""
        return self.time_of(self.get("mine", "sim_time", "")) or 0.0

    @staticmethod
    def time_of(text) -> float | None:
        """A simulated moment as the twins write it ('2026-10-26T10:00:00'), in the units of `now`."""
        try:
            return datetime.fromisoformat(text).timestamp()
        except (TypeError, ValueError):
            return None

    def held(self, name: str, feature: str) -> bool:
        """True while an operator's order on this twin stands: its `…_hold_until` feature lies ahead."""
        until = self.time_of(self.get(name, feature, ""))
        return until is not None and until > self.now

    def update(self, name: str, features: dict) -> dict:
        """Take a twin's new feature values; return the previous ones."""
        old = self.values.get(name, {})
        self.values[name] = {**old, **features}
        if name == "mine":
            self.now = self._clock()
        return old


class Tally:
    """A ratio (for example queueing seconds per trip) accumulated separately
    for the time the module was on and the time it was off."""

    def __init__(self):
        self.sums = {True: [0.0, 0.0], False: [0.0, 0.0]}
        self.seconds = {True: 0.0, False: 0.0}

    def add(self, enabled: bool, amount: float, base: float) -> None:
        self.sums[enabled][0] += amount
        self.sums[enabled][1] += base

    def tick(self, enabled: bool, seconds: float) -> None:
        self.seconds[enabled] += seconds

    def reset(self, enabled: bool) -> None:
        self.sums[enabled], self.seconds[enabled] = [0.0, 0.0], 0.0

    def ratio(self, enabled: bool) -> float | None:
        amount, base = self.sums[enabled]
        return amount / base if base > 0 else None

    def hours(self, enabled: bool) -> float:
        return self.seconds[enabled] / 3600.0


def signed(value: float, digits: int = 1) -> str:
    text = num(abs(value), digits)
    if text.strip("0,") == "":
        return text
    return ("+" if value > 0 else "−") + text


class Core:
    """Base of a module's decision logic.

    The shell (or a test) calls `observe` for every twin report, then `step`,
    and sends the messages `step` returns. `set_enabled` switches the module;
    switching it off returns the messages that withdraw its decisions.
    """

    name = ""                           # the module's own twin
    kpi_label = ""
    kpi_unit = ""
    # The module's own indicator in a forecast branch: (feature of the forecast twin without `a_`/`b_`, or
    # several joined by "+" to be added up; Russian label, unit, digits, which way is better: "more" or "less").
    effect_kpi: tuple[str, str, str, int, str] | None = None
    # A second indicator shown beside the first one, in the same form.
    effect_kpi_extra: tuple[str, str, str, int, str] | None = None
    # A sentence that says what the module works for and what it may cost, appended to the effect text.
    effect_note = ""

    def __init__(self, things: list[dict], params: dict, enabled: bool = True):
        self.world = World(things)
        self.params = params
        self.enabled = enabled
        self.decisions = 0
        self.last_decision = ""
        self.enabled_at = self.world.now if enabled else None
        self.tally = Tally()
        self.out: list[Message] = []
        self.last_now = self.world.now
        # The forecast slot of this module (`spec.module` of a forecast twin), and whether it may ask for
        # forecasts: only the service on the stand does; a core inside a forecast or a test does not.
        w = self.world
        self.effect_slot = next((n for n in w.by_kind.get("forecast", []) if short(w.spec(n, "module", "")) == self.name), None)
        self.effect_live = False
        fp = params.get("forecast", {})
        self.effect_period_s = float(fp.get("module_period_h", 3)) * 3600.0
        self.effect_first_s = float(fp.get("module_first_min", 10)) * 60.0
        self.effect_horizon_h = int(fp.get("horizon_h", 12))
        first = int(fp.get("module_slots_first", 6))
        self.effect_offset_s = 1800.0 * (int(self.effect_slot.split("-")[-1]) - first) if self.effect_slot else 0.0
        self.effects: deque[dict] = deque(maxlen=int(fp.get("module_results_kept", 4)))
        self.effect_pending: dict | None = None
        self.next_effect_at = self.world.now + self.effect_first_s + self.effect_offset_s

    # -- the interface the shell uses ---------------------------------------------

    def observe(self, name: str, features: dict) -> None:
        old = self.world.update(name, features)
        self.changed(name, old, self.world.values[name])

    def step(self) -> list[Message]:
        """Measure, decide, and hand over the messages to send."""
        now = self.world.now
        if now < self.last_now - 60.0:
            self.restart()              # the simulator was started anew: forget what belonged to the old run
        dt = min(max(now - self.last_now, 0.0), 600.0)
        self.last_now = now
        if dt > 0:
            self.tally.tick(self.enabled, dt)
            self.measure(now, dt)
            if self.enabled:
                self.decide(now)
        if self.enabled and self.effect_live and self.effect_slot:
            self._effect_round(now)
        out, self.out = self.out, []
        return out

    # -- the effect: forecasts with and without the module --------------------------------

    def _effect_round(self, now: float) -> None:
        w, pending = self.world, self.effect_pending
        if pending is not None:
            twin = w.values.get(self.effect_slot, {})
            if twin.get("request_id") == pending["id"] and twin.get("status") in ("ready", "error"):
                if twin.get("status") == "ready":
                    self.effects.append(self._effect_of(twin))
                self.effect_pending = None
            elif now - pending["at"] > 3600.0:
                self.effect_pending = None              # no answer within an hour of the model: ask again later
            return
        if now < self.next_effect_at:
            return
        self.next_effect_at = now + self.effect_period_s
        request_id = f"{self.name}-{int(now)}"
        self.effect_pending = {"id": request_id, "at": now}
        self.send("mine", "forecast", {
            "slot": int(self.effect_slot.split("-")[-1]), "horizon_h": self.effect_horizon_h, "request_id": request_id,
            "requested_by": self.name, "subject": w.tid(self.name), "label_a": "С модулем", "label_b": "Без модуля",
            "actions_a": [], "actions_b": [{"thing": w.tid(self.name), "subject": "set-enabled", "body": {"enabled": False}}]})

    def _effect_of(self, twin: dict) -> dict:
        value = lambda key: float(twin.get(key) or 0.0)
        effect = {"sim_time": str(twin.get("fork_sim_time") or ""), "cu": value("a_cu_t") - value("b_cu_t"),
                  "margin": value("a_margin_kzt") - value("b_margin_kzt"), "total": value("a_total_kzt") - value("b_total_kzt"),
                  # Where the copper difference comes from, as the forecast says it (B is the mine without the module).
                  "copper_text": str(twin.get("copper_text") or "")}
        for prefix, kpi in (("kpi", self.effect_kpi), ("extra", self.effect_kpi_extra)):
            if kpi:
                keys = kpi[0].split("+")                 # a sum of branch figures, e.g. "ore_t+waste_t"
                effect.update({f"{prefix}_with": sum(value(f"a_{k}") for k in keys),
                               f"{prefix}_without": sum(value(f"b_{k}") for k in keys)})
        return effect

    def effect_features(self) -> dict:
        if not self.effect_slot:
            return {}
        effects = list(self.effects) if self.enabled else []
        n = len(effects)
        return {
            "effect_cu_t": round(sum(e["cu"] for e in effects) / n, 2) if n else 0.0,
            "effect_margin_kzt": round(sum(e["margin"] for e in effects) / n) if n else 0,
            "effect_total_kzt": round(sum(e["total"] for e in effects) / n) if n else 0,
            "effect_basis": self.effect_basis(effects),
            "effect_forecast": self.world.tid(self.effect_slot),
            "effect_count": n,
            "effect_sim_time": effects[-1]["sim_time"] if n else "",
        }

    def effect_basis(self, effects: list[dict]) -> str:
        h = self.effect_horizon_h
        how = (f"Прогноз на {h} ч от одного и того же состояния рудника в двух ветвях: «с модулем» и «без модуля» "
               f"(модуль выключается в момент разветвления); эффект — разница меди, маржи и итога с поправкой ветвей")
        if not effects:
            return how + "; прогнозов ещё нет."
        return how + f"; среднее по {len(effects)} прогнозам, последний от {effects[-1]['sim_time'][11:16]}."

    def effect_sentence(self) -> str:
        """The A/B effect in Russian, for `effect_text`."""
        if not self.enabled:
            return "Модуль выключен, рудник работает по базовым правилам."
        effects = list(self.effects)
        h = self.effect_horizon_h
        if not effects:
            if self.effect_pending:
                when = "первый прогноз считается"
            elif self.effect_live:
                when = f"первый прогноз — в {datetime.fromtimestamp(self.next_effect_at).strftime('%H:%M')} модели"
            else:
                when = "прогнозы заказывает сервис модуля на стенде"
            return (f"Эффект модуля измеряется прогнозом на {h} ч «с модулем / без модуля» от одного состояния рудника; "
                    f"{when}.")
        last = effects[-1]
        noise_kzt = float(self.params["forecast"]["noise_kzt"])
        noise_cu = float(self.params["forecast"]["noise_cu_t"])
        text = f"Прогноз на {h} ч от {last['sim_time'][11:16]}, с модулем и без него"
        shown = [(prefix, kpi) for prefix, kpi in (("kpi", self.effect_kpi), ("extra", self.effect_kpi_extra))
                 if kpi and f"{prefix}_with" in last]
        if shown:
            text += ": " + "; ".join(f"{kpi[1]} {num(last[f'{prefix}_with'], kpi[3])} против "
                                     f"{num(last[f'{prefix}_without'], kpi[3])} {kpi[2]}" for prefix, kpi in shown)
        text += (f"; с модулем медь {signed(last['cu'])} т, итог с поправкой {signed(last['total'] / 1e6)} млн тг "
                 f"(маржа {signed(last['margin'] / 1e6)}) за {h} ч")
        n = len(effects)
        cu = sum(e["cu"] for e in effects) / n
        total = sum(e["total"] for e in effects) / n
        # The scatter of the mean of n forecasts: two standard errors, from the paired noise of one forecast.
        bound_cu, bound_kzt = 2.0 * noise_cu / n ** 0.5, 2.0 * noise_kzt / n ** 0.5
        within_cu, within_kzt = abs(cu) <= bound_cu, abs(total) <= bound_kzt
        if n > 1:
            text += f". Среднее по {n} прогнозам: медь {signed(cu)} т, итог с поправкой {signed(total / 1e6)} млн тг за {h} ч"
        scatter = f"±{num(bound_cu)} т, ±{num(bound_kzt / 1e6)} млн тг"
        if within_cu and within_kzt:
            text += f" — в пределах разброса прогноза ({scatter})."
        elif within_kzt:
            text += (f"; по итогу с поправкой — в пределах разброса прогноза ({scatter}), по меди — больше: "
                     f"медь {'выше' if cu > 0 else 'ниже'}, но по деньгам с учётом запасов это равноценно.")
        else:
            text += f", больше разброса прогноза ({scatter})."
        # Why the copper differs, from the last forecast (its B is the mine without the module: signs reversed).
        if last.get("copper_text"):
            text += f" Без модуля, по последнему прогнозу: {last['copper_text']}"
        return text + (f" {self.effect_note}" if self.effect_note else "")

    def set_enabled(self, enabled: bool) -> list[Message]:
        if enabled == self.enabled:
            return []
        self.enabled = enabled
        if enabled:
            self.enabled_at = self.world.now
            self.tally.reset(True)
            self.next_effect_at = self.world.now + self.effect_first_s + self.effect_offset_s
            self.switched_on()
            self.last_decision = "Модуль включён."
            return []
        self.enabled_at = None
        self.out.clear()
        self.effects.clear()
        self.effect_pending = None
        messages = self.revert()
        self.last_decision = "Модуль выключен: его решения сняты, рудник работает по базовым правилам."
        return messages

    def twin(self, status: str | None = None) -> dict:
        """All features of the module's twin."""
        w = self.world
        return {
            "enabled": self.enabled,
            "status": status or ("online" if self.enabled else "disabled"),
            "enabled_sim_time": datetime.fromtimestamp(self.enabled_at).isoformat(timespec="seconds") if self.enabled_at else "",
            "decisions_total": self.decisions,
            "last_decision": self.last_decision,
            "kpi_label": self.kpi_label,
            "kpi_unit": self.kpi_unit,
            "kpi_value": self.kpi_value(),
            "effect_text": self.effect_text(),
            **self.effect_features(),
            **self.own_features(),
        }

    # -- for the subclasses ---------------------------------------------------------

    def changed(self, name: str, old: dict, new: dict) -> None:
        """A twin reported new values."""

    def measure(self, now: float, dt: float) -> None:
        """Track the module's indicator; runs whether the module is on or off."""

    def decide(self, now: float) -> None:
        """Queue messages with `send`; runs only while the module is on."""

    def revert(self) -> list[Message]:
        """Messages that withdraw every standing decision of the module."""
        return []

    def command(self, subject: str, body: dict) -> list[Message]:
        """A live message to the module's own twin other than `set-enabled`;
        returns the messages it leads to. Modules without commands ignore it."""
        return []

    def switched_on(self) -> None:
        """The module was enabled again: forget decisions made before."""

    def restart(self) -> None:
        self.tally = Tally()
        self.enabled_at = self.world.now if self.enabled else None
        if hasattr(self, "effects"):
            self.effects.clear()
            self.effect_pending = None
            self.next_effect_at = self.world.now + self.effect_first_s + self.effect_offset_s

    def own_features(self) -> dict:
        return {}

    def kpi_value(self) -> float:
        return 0.0

    def effect_text(self) -> str:
        return ""

    # -- helpers --------------------------------------------------------------------

    def send(self, name: str, subject: str, body: dict, decision: str | None = None) -> None:
        """Queue a live message. With `decision` it counts as a decision and
        its text becomes `last_decision`; without, it only repeats one."""
        self.out.append((name, subject, body))
        if decision:
            self.decisions += 1
            self.last_decision = decision

    def since_text(self, what: str, unit: str, digits: int) -> str:
        """The module's own indicator since it was switched on (no comparison: that is the forecast's job)."""
        on = self.tally.ratio(True)
        if not self.enabled or on is None:
            return ""
        return f" С включения ({num(self.tally.hours(True))} ч модели): {what} {num(on, digits)} {unit}."
