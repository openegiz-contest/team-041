"""Grade control: what each block is and where its rock goes.

When a block is blasted the module classifies it against the cut-off grade
(waste / low-grade ore / ore) and gives it a destination with the reason.
While ore faces are being dug it then steers the blend: every control period
it looks at all combinations of

    each ore face        -> crusher, or the ROM pad of its class
    the wheel loader     -> idle, or feeding the crusher from one of the pads

and predicts for each the grade of the coarse-ore stockpile (the mill feed) a
few hours ahead. While the prediction stays well inside the target band it
leaves the ore on its direct way to the crusher; when the stockpile would
drift out, it picks the combination that keeps it in, without starving the
mill and with as little rehandling as that takes.

Without the module everything above the cut-off goes straight to the crusher
and the pads are used only as overflow.
"""
from __future__ import annotations

from itertools import product

from mine.modules.base import Core, Message, num, short

CLASS_TITLES = {"ore": "руда", "low-grade": "бедная руда", "waste": "вскрыша"}
RECLAIM_STEPS = (0.5, 1.0)          # shares of the wheel loader's rate that are tried


class GradeCore(Core):
    name = "module-grade"
    kpi_label = "Время, когда содержание в питании мельницы в полосе"
    kpi_unit = "%"
    effect_kpi = ("in_band_pct", "содержание в полосе", "% времени", 0, "more")
    effect_note = ("Модуль держит содержание в питании мельницы в полосе, под которую настроен реагентный режим флотации: "
                   "руда богаче полосы уходит на склад богатой руды, бедная добавляется в шихту. Медь, отложенная на склад, "
                   "будет переработана позже, поэтому за 12 часов меди с модулем может быть меньше.")

    def __init__(self, things: list[dict], params: dict, enabled: bool = True):
        super().__init__(things, params, enabled)
        w = self.world
        self.p = params["grade_control"]
        geology = params["geology"]
        self.cutoff, self.low_below = geology["cutoff_grade_pct"], geology["low_grade_below_pct"]
        self.target, self.band = self.p["target_pct"], self.p["band_pct"]
        self.blocks = w.by_kind["block"]
        self.loaders = w.by_kind["loader"]
        self.crusher = w.by_kind["crusher"][0]
        self.waste_dump = w.by_kind["waste-dump"][0]
        self.pads = {w.get(pad, "role"): pad for pad in w.by_kind["rom-pad"]}
        self.reclaim_tph = float(params["rom"]["reclaim_tph"])
        self.payload_t = float(params["truck"]["payload_t"])
        self.design_tph = float(params["mill"]["design_tph"])
        self.restart()

    def restart(self) -> None:
        super().restart()
        self.classified: dict[str, str] = {}        # block -> class given at the blast
        self.routing: dict[str, str] = {}           # block -> destination ordered
        self.reclaim: dict[str, float] = {pad: 0.0 for pad in self.pads.values()}
        self.next_control = 0.0
        self.blend_grade = 0.0
        self.classified_total = 0

    # -- classification -------------------------------------------------------------------

    def classify(self, grade: float) -> tuple[str, str]:
        """Class of a block and the reason, from its grade."""
        if grade < self.cutoff:
            return "waste", f"{num(grade, 3)} % Cu ниже бортового {num(self.cutoff, 2)} %"
        if grade < self.low_below:
            return "low-grade", f"{num(grade, 3)} % Cu между бортовым {num(self.cutoff, 2)} % и {num(self.low_below, 2)} %"
        return "ore", f"{num(grade, 3)} % Cu не ниже {num(self.low_below, 2)} %"

    def _pad_of(self, cls: str) -> str:
        return self.pads["low-grade" if cls == "low-grade" else "high-grade"]

    def _class_destination(self, cls: str) -> str:
        """Where a block of this class goes until the blend says otherwise."""
        if cls == "waste":
            return self.waste_dump
        return self._pad_of(cls) if cls == "low-grade" else self.crusher

    def _classify_new(self) -> None:
        w = self.world
        for block in self.blocks:
            status = w.get(block, "status")
            if status not in ("blasted", "mining"):
                self.classified.pop(block, None)
                self.routing.pop(block, None)
                continue
            if block in self.classified:
                continue
            cls, reason = self.classify(float(w.get(block, "grade_pct", 0.0)))
            destination = self._class_destination(cls)
            if cls != "waste" and not self._pad_has_room(destination):
                destination = self.crusher
            self.classified[block] = cls
            self.classified_total += 1
            if self._operator_block(block):
                continue                    # the operator's destination stands; the blend works around it
            self.routing[block] = destination
            text = f"{CLASS_TITLES[cls]}: {reason}"
            self.send(block, "set-destination", {"destination": destination, "reason": text},
                      f"{w.title(block)} взорван, {text}; назначение: {w.title(destination)}.")

    def _operator_block(self, block: str) -> bool:
        w = self.world
        return w.get(block, "destination_source") == "operator" and w.held(block, "destination_hold_until")

    def _operator_pad(self, pad: str) -> bool:
        w = self.world
        return w.get(pad, "reclaim_source") == "operator" and w.held(pad, "reclaim_hold_until")

    # -- the blend --------------------------------------------------------------------------

    def _pad_has_room(self, sink: str) -> bool:
        w = self.world
        if sink not in self.pads.values():
            return True
        return float(w.spec(sink, "capacity_t", 0.0)) - float(w.get(sink, "level_t", 0.0)) > 6 * self.payload_t

    def _faces(self) -> list[dict]:
        """Ore faces being dug: block, grade, class and the rate the loader delivers."""
        w, faces = self.world, []
        for loader in self.loaders:
            v = w.values[loader]
            block = short(v.get("block") or "")
            if v.get("state") not in ("loading", "waiting") or block not in self.classified:
                continue
            cls = self.classified[block]
            if cls == "waste":
                continue
            rate = float(v.get("dig_rate_tph") or 0.0)
            faces.append({"block": block, "grade": float(w.get(block, "grade_pct", 0.0)), "cls": cls,
                          "tph": rate if rate > 300.0 else 1200.0})
        return faces

    def _control(self) -> None:
        w = self.world
        faces = self._faces()
        level = float(w.get("stockpile", "level_pct", 0.0))
        pile_grade = float(w.get("stockpile", "grade_pct", 0.0)) or self.target
        mill_tph = float(w.get("mill", "feed_tph", 0.0)) or float(w.get("mill", "feed_setpoint_tph", self.design_tph))
        pile_t = max(float(w.get("stockpile", "level_t", 0.0)), 1.0)
        horizon_h = self.p["horizon_h"]
        hold = self.p["hold_share"] * self.band         # the part of the band the prediction should stay in
        # How much of the mill's draw the stream must cover: all of it at a low stockpile, none at a full one.
        low, full = self.p["full_feed_below_pct"], 90.0
        need_tph = mill_tph * min(1.0, max(0.0, (full - level) / (full - low)))
        crusher_runs = w.get(self.crusher, "state") != "stopped"

        reclaims: list[tuple[str | None, float]] = [(None, 0.0)]
        # While an operator orders reclaim from a pad, the module leaves the wheel loader to that order.
        operator_pads = [pad for pad in self.pads.values() if self._operator_pad(pad)]
        if crusher_runs and not operator_pads:
            for pad in self.pads.values():
                if float(w.get(pad, "level_t", 0.0)) > 20 * self.payload_t:
                    reclaims += [(pad, share * self.reclaim_tph) for share in RECLAIM_STEPS]
        # Low-grade ore goes to the mill only to bring an over-rich feed back into the band, or while the stockpile
        # is short of ore: the mill is the plant's bottleneck, and a tonne of it milled now displaces a tonne of ore.
        rich = pile_grade > self.target + hold
        short_of_ore = level < self.p["full_feed_below_pct"] / 2
        choices = []
        for face in faces:
            pad = self._pad_of(face["cls"])
            if self._operator_block(face["block"]):
                choices.append([short(str(w.get(face["block"], "destination", self.crusher)))])
            elif face["cls"] == "low-grade" and not (rich or short_of_ore) and self._pad_has_room(pad):
                choices.append([pad])
            else:
                choices.append([self.crusher, pad] if self._pad_has_room(pad) else [self.crusher])

        best = None
        for sinks in product(*choices):
            for pad, rate in reclaims:
                tph = sum(f["tph"] for f, s in zip(faces, sinks) if s == self.crusher) + rate
                cu = sum(f["tph"] * f["grade"] for f, s in zip(faces, sinks) if s == self.crusher)
                cu += rate * float(w.get(pad, "grade_pct", 0.0)) if pad else 0.0
                grade = cu / tph if tph > 0 else pile_grade
                # The stockpile after the horizon: the share of it replaced by this stream takes the stream's grade.
                predicted = pile_grade + (grade - pile_grade) * min(1.0, tph * horizon_h / pile_t)
                rehandled = sum(f["tph"] for f, s in zip(faces, sinks) if s != self.crusher) + rate
                changes = sum(1 for f, s in zip(faces, sinks) if self.routing.get(f["block"]) != s)
                changes += sum(1 for name, was in self.reclaim.items() if (rate if name == pad else 0.0) != was)
                cost = (max(0.0, abs(predicted - self.target) - hold) / self.band
                        + 4.0 * max(0.0, need_tph - tph) / max(mill_tph, 1.0)
                        + self.p["rehandle_cost"] * rehandled / max(mill_tph, 1.0)
                        + 0.03 * changes)
                if best is None or cost < best[0] - 1e-9:
                    best = (cost, sinks, pad, rate, grade, tph, predicted)
        if best is None:
            return
        _, sinks, pad, rate, grade, tph, predicted = best
        self.blend_grade = grade if tph > 0 else 0.0

        changed, parts = False, []
        for face, sink in zip(faces, sinks):
            block = face["block"]
            if self._operator_block(block):
                self.routing.pop(block, None)
                parts.append(f"{w.title(block)} → {w.title(sink)} (указание оператора)")
                continue
            if self.routing.get(block) != sink:
                changed = True
            self.routing[block] = sink
            parts.append(f"{w.title(block)} ({num(face['grade'], 2)} %) → {w.title(sink)}")
        for name in self.pads.values():
            new = rate if name == pad else 0.0
            if new != self.reclaim[name]:
                changed = True
            self.reclaim[name] = new
        if pad:
            parts.append(f"со склада «{w.title(pad)}» {rate:.0f} т/ч ({num(float(w.get(pad, 'grade_pct', 0.0)), 2)} %)")
        text = None
        if changed and parts:
            text = (f"Шихта: {'; '.join(parts)}. Содержание в потоке на склад {num(grade, 3)} %; на складе сейчас "
                    f"{num(pile_grade, 3)} %, через {horizon_h:.0f} ч ожидается {num(predicted, 3)} % "
                    f"(полоса {num(self.target - self.band, 2)}–{num(self.target + self.band, 2)} %).")
        # Every standing order is repeated each period: the simulator drops what is not confirmed.
        first = True
        for block, sink in self.routing.items():
            if self._operator_block(block):
                continue
            cls = self.classified.get(block, "ore")
            reason = f"{CLASS_TITLES[cls]}; шихта: на складе {num(pile_grade, 3)} % Cu, прогноз {num(predicted, 3)} %"
            self.send(block, "set-destination", {"destination": sink, "reason": reason}, text if first else None)
            first = False
        for name, new in self.reclaim.items():
            if name in operator_pads:
                continue
            self.send(name, "set-reclaim", {"tph": new}, text if first else None)
            first = False

    # -- one round ----------------------------------------------------------------------------

    def decide(self, now: float) -> None:
        self._classify_new()
        if now >= self.next_control:
            self.next_control = now + self.p["control_min"] * 60.0
            self._control()

    def changed(self, name: str, old: dict, new: dict) -> None:
        # A loader starting a new block is a reason to look at the blend at once.
        if name in self.loaders and old.get("block") != new.get("block"):
            self.next_control = 0.0

    def revert(self) -> list[Message]:
        messages = [(block, "release", {}) for block in self.blocks
                    if self.world.get(block, "destination_source") != "cutoff" or block in self.routing]
        messages += [(pad, "release", {}) for pad in self.pads.values()]
        self.classified.clear()
        self.routing.clear()
        self.reclaim = {pad: 0.0 for pad in self.pads.values()}
        self.next_control = 0.0
        self.blend_grade = 0.0
        return messages

    def switched_on(self) -> None:
        self.classified_total = 0

    # -- indicators -----------------------------------------------------------------------------

    def _feed_grade(self) -> float:
        return float(self.world.get("stockpile", "grade_pct", 0.0))

    def measure(self, now: float, dt: float) -> None:
        # Only the time the mill is fed counts.
        if float(self.world.get("mill", "feed_tph", 0.0)) > 0:
            in_band = abs(self._feed_grade() - self.target) <= self.band + 1e-9
            self.tally.add(self.enabled, 100.0 * dt if in_band else 0.0, dt)

    def kpi_value(self) -> float:
        share = self.tally.ratio(self.enabled)
        return round(share, 1) if share is not None else 0.0

    def own_features(self) -> dict:
        return {
            "feed_grade_pct": round(self._feed_grade(), 3),
            "target_grade_pct": float(self.target),
            "band_low_pct": round(self.target - self.band, 3),
            "band_high_pct": round(self.target + self.band, 3),
            "in_band_pct": self.kpi_value(),
            "blend_grade_pct": round(self.blend_grade, 3),
            "blocks_classified": self.classified_total,
            "reclaim_tph": round(sum(self.reclaim.values())) if self.enabled else 0,
        }

    def effect_text(self) -> str:
        band = f"{num(self.target - self.band, 2)}–{num(self.target + self.band, 2)} % Cu"
        return (self.effect_sentence().replace("содержание в полосе", f"содержание в полосе {band}", 1)
                + self.since_text(f"содержание в полосе {band}", "% времени", 0))
