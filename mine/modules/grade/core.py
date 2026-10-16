"""Grade control: what each block is and where its rock goes.

When a block is blasted the module classifies it against the cut-off grade
and by the clay the face shows (waste / low-grade ore / sulphide ore / clay
ore, `clay.clay_ore_min_pct`) and gives it a destination with the reason; a
face whose clay changes (a zone of kaolinisation the block model did not
show) is classified again. While ore faces are being dug it then steers the
blend: every control period it looks at all combinations of

    each sulphide face   -> crusher of ОФ-1, or the ROM pad of its class
    each clay-ore face   -> ОФ-2 (while it runs and its feed stockpile has room),
                            the clay-ore stockpile (while it has room), or the
                            blend of ОФ-1 (while the clay of its feed stays within
                            `grade_control.clay_limit_pct`)
    the wheel loader     -> idle, or feeding the crusher from one of the pads

and predicts for each the grade and the clay of the coarse-ore stockpile (the
ОФ-1 feed) a few hours ahead. While the prediction stays well inside the
target band it leaves the ore on its direct way; when the stockpile would
drift out, it picks the combination that keeps it in, keeps the clay of the
ОФ-1 feed within its limit, does not starve the mill and rehandles as little
as that takes.

Without the module every block goes where the block model put it: sulphide
ore straight to the crusher, clay ore to ОФ-2 (the clay-ore stockpile when
ОФ-2 cannot take it), and the pads are used only as overflow; a clay-rich
face the block model took for sulphide ore goes on to ОФ-1.
"""
from __future__ import annotations

from itertools import product

from mine.modules.base import Core, Message, num, short

CLASS_TITLES = {"ore": "сульфидная руда", "low-grade": "забалансовая руда", "waste": "вскрыша", "clay": "глинистая руда"}
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
        self.clay_min = float(params["clay"]["clay_ore_min_pct"])
        self.clay_limit = float(self.p["clay_limit_pct"])
        self.clay_ref = float(params["flotation"]["clay_ref_pct"])
        self.clay_plant = w.by_kind["clay-plant"][0]
        self.clay_stock = w.by_kind["clay-stockpile"][0]
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
        self.clay_seen: dict[str, float] = {}       # block -> the clay it was classified with
        self.blend_clay = 0.0
        self.routing: dict[str, str] = {}           # block -> destination ordered
        self.reclaim: dict[str, float] = {pad: 0.0 for pad in self.pads.values()}
        self.next_control = 0.0
        self.blend_grade = 0.0
        self.classified_total = 0

    # -- classification -------------------------------------------------------------------

    def classify(self, grade: float, clay: float = 0.0) -> tuple[str, str]:
        """Class of a block and the reason, from its grade and the clay at the face."""
        if grade < self.cutoff:
            return "waste", f"{num(grade, 3)} % Cu ниже бортового {num(self.cutoff, 2)} %"
        if grade < self.low_below:
            return "low-grade", f"{num(grade, 3)} % Cu между бортовым {num(self.cutoff, 2)} % и {num(self.low_below, 2)} %"
        if clay >= self.clay_min:
            return "clay", f"{num(grade, 3)} % Cu, глины {num(clay)} % — не меньше {num(self.clay_min, 0)} %"
        return "ore", f"{num(grade, 3)} % Cu не ниже {num(self.low_below, 2)} %, глины {num(clay)} %"

    def _pad_of(self, cls: str) -> str:
        return self.pads["low-grade" if cls == "low-grade" else "high-grade"]

    def _ofs2_open(self) -> bool:
        """ОФ-2 takes clay ore: its feed stockpile has room (while the plant stands, too: it feeds the plant after
        the restart without rehandling)."""
        return float(self.world.get(self.clay_plant, "room_t", 0.0)) > float(self.p["ofs2_room_t"])

    def _clay_stock_open(self) -> bool:
        return float(self.world.get(self.clay_stock, "room_t", 0.0)) > 6 * self.payload_t

    def _clay_sinks(self) -> list[str]:
        """Where clay ore may go now, the plant first."""
        sinks = [self.clay_plant] if self._ofs2_open() else []
        if self._clay_stock_open():
            sinks.append(self.clay_stock)
        return sinks

    def _class_destination(self, cls: str) -> str:
        """Where a block of this class goes until the blend says otherwise."""
        if cls == "waste":
            return self.waste_dump
        if cls == "clay":
            return (self._clay_sinks() or [self.crusher])[0]
        return self._pad_of(cls) if cls == "low-grade" else self.crusher

    def _classify_new(self) -> None:
        w = self.world
        for block in self.blocks:
            status = w.get(block, "status")
            if status not in ("blasted", "mining"):
                self.classified.pop(block, None)
                self.routing.pop(block, None)
                continue
            clay = float(w.get(block, "clay_pct", 0.0))
            if block in self.classified and abs(self.clay_seen.get(block, clay) - clay) < 0.5:
                continue
            again = block in self.classified
            cls, reason = self.classify(float(w.get(block, "grade_pct", 0.0)), clay)
            self.clay_seen[block] = clay
            if again and cls == self.classified[block]:
                continue
            if again:
                self.next_control = 0.0         # the face changed: look at the blend at once
            destination = self._class_destination(cls)
            if cls != "waste" and not self._pad_has_room(destination):
                destination = self.crusher
            self.classified[block] = cls
            self.classified_total += 1
            if self._operator_block(block):
                continue                    # the operator's destination stands; the blend works around it
            self.routing[block] = destination
            text = f"{CLASS_TITLES[cls]}: {reason}"
            what = "забой показал другую глинистость" if again else "взорван"
            self.send(block, "set-destination", {"destination": destination, "reason": text},
                      f"{w.title(block)} {what}, {text}; назначение: {w.title(destination)}.")

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
                          "clay": float(w.get(block, "clay_pct", 0.0)), "tph": rate if rate > 300.0 else 1200.0})
        return faces

    def _control(self) -> None:
        w = self.world
        faces = self._faces()
        level = float(w.get("stockpile", "level_pct", 0.0))
        pile_grade = float(w.get("stockpile", "grade_pct", 0.0)) or self.target
        pile_clay = float(w.get("stockpile", "clay_pct", 0.0)) or self.clay_ref
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
            elif face["cls"] == "clay":
                choices.append([*self._clay_sinks(), *([self.crusher] if crusher_runs else [])] or [self.crusher])
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
                clay_t = sum(f["tph"] * f["clay"] for f, s in zip(faces, sinks) if s == self.crusher)
                clay_t += rate * float(w.get(pad, "clay_pct", self.clay_ref)) if pad else 0.0
                clay = clay_t / tph if tph > 0 else pile_clay
                # The stockpile after the horizon: the share of it replaced by this stream takes the stream's grade.
                share = min(1.0, tph * horizon_h / pile_t)
                predicted = pile_grade + (grade - pile_grade) * share
                predicted_clay = pile_clay + (clay - pile_clay) * share
                # Rehandling: ore tipped on a ROM pad or the clay-ore stockpile is to be fed back later.
                rehandled = sum(f["tph"] for f, s in zip(faces, sinks)
                                if s != self.crusher and s != self.clay_plant) + rate
                changes = sum(1 for f, s in zip(faces, sinks) if self.routing.get(f["block"]) != s)
                changes += sum(1 for name, was in self.reclaim.items() if (rate if name == pad else 0.0) != was)
                # Clay in the ОФ-1 feed costs recovery; above the limit it is not allowed.
                clay_cost = (0.5 * max(0.0, predicted_clay - self.clay_ref) / max(self.clay_limit - self.clay_ref, 1.0)
                             + 20.0 * max(0.0, predicted_clay - self.clay_limit) / self.clay_limit)
                cost = (max(0.0, abs(predicted - self.target) - hold) / self.band
                        + 4.0 * max(0.0, need_tph - tph) / max(mill_tph, 1.0)
                        + self.p["rehandle_cost"] * rehandled / max(mill_tph, 1.0)
                        + clay_cost + 0.03 * changes)
                if best is None or cost < best[0] - 1e-9:
                    best = (cost, sinks, pad, rate, grade, tph, predicted, predicted_clay)
        if best is None:
            return
        _, sinks, pad, rate, grade, tph, predicted, predicted_clay = best
        self.blend_grade = grade if tph > 0 else 0.0
        self.blend_clay = predicted_clay

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
            text = (f"Шихта: {'; '.join(parts)}. Содержание в потоке на склад ОФ-1 {num(grade, 3)} %; на складе сейчас "
                    f"{num(pile_grade, 3)} %, через {horizon_h:.0f} ч ожидается {num(predicted, 3)} % "
                    f"(полоса {num(self.target - self.band, 2)}–{num(self.target + self.band, 2)} %); глины в питании "
                    f"ОФ-1 ожидается {num(predicted_clay)} % (предел {num(self.clay_limit, 0)} %).")
        # Every standing order is repeated each period: the simulator drops what is not confirmed.
        first = True
        for block, sink in self.routing.items():
            if self._operator_block(block):
                continue
            cls = self.classified.get(block, "ore")
            if cls == "clay":
                where = {self.clay_plant: "ОФ-2 принимает", self.clay_stock: "ОФ-2 не принимает, склад глинистой руды",
                         self.crusher: f"в шихту ОФ-1, глины в питании ожидается {num(predicted_clay)} %"}.get(sink, "")
                reason = f"{CLASS_TITLES[cls]}: {where}"
            else:
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
        self.clay_seen.clear()
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
            "blend_clay_pct": round(self.blend_clay, 1),
            "clay_limit_pct": float(self.clay_limit),
            "clay_blocks": sum(1 for c in self.classified.values() if c == "clay"),
            "blocks_classified": self.classified_total,
            "reclaim_tph": round(sum(self.reclaim.values())) if self.enabled else 0,
        }

    def effect_text(self) -> str:
        band = f"{num(self.target - self.band, 2)}–{num(self.target + self.band, 2)} % Cu"
        return (self.effect_sentence().replace("содержание в полосе", f"содержание в полосе {band}", 1)
                + self.since_text(f"содержание в полосе {band}", "% времени", 0))
