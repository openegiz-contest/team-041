"""Money and CO2 of the mine, from the prices and factors in params.yaml
(group `economics`).

The margin is a variable margin: the payable copper in concentrate at the
copper price, less the charges of the smelter (TC per tonne of concentrate,
RC per pound of payable copper), less the diesel the trucks burn and the
electricity the mine and the plant draw. Labour, repairs, explosives,
reagents, depreciation and every other fixed cost are left out.

A forecast over a horizon compares its branches by that margin plus an
adjustment for what the horizon leaves behind (mine/sim/forecast.py). Every
tonne of ore, wherever it lies between the face and the flotation (broken in
the pit, in a truck, in the crusher's pocket, on the belt, on the coarse-ore
stockpile, on a ROM pad, in the mill), is valued on one basis: the copper
it will yield at the reference recovery and the copper price, net of what
it still costs to turn it into concentrate. The mill is the plant's
bottleneck, so the largest of those costs is the mill time the tonne will
take: in that time the mill would grind a tonne of the mine's average ore
(`geology.mean_head_grade_pct`) instead. A tonne of ore is therefore worth

    copper value x (its copper - average grade x its tonnes)      everywhere
  + `economics.stock_kzt_t`                                        once it is dug: the fleet work it saves
  - `rom.rehandle_kzt_t`                                           while it lies on a ROM pad

The value is linear in tonnes and copper, so mixing two heaps changes
nothing, and the same tonne is worth the same in a truck, in the pocket, on
the belt and on the stockpile. Moving ore between heaps changes the total
only by what the move really costs: diesel and power within the horizon, and
the rehandling a pad still needs (charged when the ore is tipped on the pad,
paid by the loader when it is fed back to the crusher). Milling a tonne
turns its value into revenue: the copper above the average grade it brings
is copper the stocks give up, so drawing down a rich stock or digging a rich
face earns nothing by itself; what earns is the mill time (the tonnes
milled), the recovery and the costs. Ore broken in the pit is not counted
block by block: the change of its value over a horizon is minus the value of
the ore loaded from it, so pushbacks and blasts do not create value.
Waste stripped is valued at the same fleet work per tonne as ore dug.
"""
from __future__ import annotations


class Economics:
    def __init__(self, params: dict):
        e = params["economics"]
        self.params = params
        self.fx = float(e["usd_kzt"])
        self.cu_usd_t = float(e["copper_usd_t"])
        self.payable = float(e["payable_cu_pct"]) / 100.0
        self.tc_usd_t = float(e["tc_usd_t"])
        self.rc_usd_lb = float(e["rc_usc_lb"]) / 100.0
        self.lb_per_t = float(e["lb_per_t"])
        self.conc_grade = float(params["flotation"]["conc_grade_pct"]) / 100.0
        self.diesel_kzt_l = float(e["diesel_kzt_l"])
        self.power_kzt_kwh = float(e["power_kzt_kwh"])
        self.diesel_co2_t_l = float(e["diesel_co2_kg_l"]) / 1000.0
        self.grid_co2_t_kwh = float(e["grid_co2_t_mwh"]) / 1000.0
        self.stock_kzt_t = float(e["stock_kzt_t"])
        self.recovery = float(params["flotation"]["recovery_ref_pct"]) / 100.0
        self.ref_grade = float(params["geology"]["mean_head_grade_pct"]) / 100.0
        rom = params["rom"]
        self.rehandle_l_t = float(rom["rehandle_diesel_l_t"])
        self.rehandle_kzt_t = self.rehandle_l_t * self.diesel_kzt_l + float(rom["rehandle_other_kzt_t"])

    # -- prices ---------------------------------------------------------------------

    def revenue_kzt(self, cu_t: float) -> float:
        """Payable value of `cu_t` tonnes of copper in concentrate, less TC and RC."""
        payable_t = cu_t * self.payable
        usd = payable_t * self.cu_usd_t - cu_t / self.conc_grade * self.tc_usd_t - payable_t * self.lb_per_t * self.rc_usd_lb
        return usd * self.fx

    def diesel_kzt(self, litres: float) -> float:
        return litres * self.diesel_kzt_l

    def power_kzt(self, kwh: float) -> float:
        return kwh * self.power_kzt_kwh

    def co2_t(self, litres: float, kwh: float) -> float:
        return litres * self.diesel_co2_t_l + kwh * self.grid_co2_t_kwh

    def margin_kzt(self, cu_t: float, litres: float, kwh: float) -> float:
        return self.revenue_kzt(cu_t) - self.diesel_kzt(litres) - self.power_kzt(kwh)

    def rehandle_kzt(self, tonnes: float) -> float:
        """Rehandling `tonnes` from a ROM pad to the crusher: loader diesel and the other costs."""
        return tonnes * self.rehandle_kzt_t

    def rehandle_l(self, tonnes: float) -> float:
        return tonnes * self.rehandle_l_t

    # -- the value of ore ------------------------------------------------------------------

    @property
    def cu_value_kzt_t(self) -> float:
        """What a tonne of copper contained in ore is worth: payable at the reference recovery."""
        return self.revenue_kzt(self.recovery)

    def grade_value_kzt(self, tonnes: float, cu_t: float) -> float:
        """The copper of `tonnes` of ore above (or below) the same tonnes of the mine's average ore, valued."""
        return self.cu_value_kzt_t * (cu_t - self.ref_grade * tonnes)

    def ore_held(self, sim) -> dict:
        """Ore dug and not yet milled, in tonnes and copper: `mined` between the faces and the mill
        (truck bodies, the crusher's pocket, the belt, the coarse-ore stockpile, the ore in the mill above its
        normal load) and `pads` on the ROM pads."""
        plant = sim.plant
        trucks = [t for t in sim.fleet.trucks if t.payload_t > 1e-6 and t.material not in ("waste", "none")]
        heaps = [(plant.crusher.hopper.t, plant.crusher.hopper.cu), (plant.conveyor.load_t, plant.conveyor.load_cu),
                 (plant.stockpile.t, plant.stockpile.cu), (plant.mill.charge.t, plant.mill.charge.cu)]
        mined_t = sum(t.payload_t for t in trucks) + sum(h[0] for h in heaps)
        mined_cu = sum(t.payload_cu_t for t in trucks) + sum(h[1] for h in heaps)
        pads_t = sum(pad.stock.t for pad in plant.rom_pads.values())
        pads_cu = sum(pad.stock.cu for pad in plant.rom_pads.values())
        return {"mined_t": mined_t, "mined_cu": mined_cu, "pads_t": pads_t, "pads_cu": pads_cu}

    def stock_parts_kzt(self, sim) -> dict:
        """The value of the ore between the faces and the flotation, in its three parts (the module
        docstring): `grade` the copper above the average grade, of the ore dug and of the ore still in
        the pit, which counts by what was loaded from it (minus the copper loaded since the start);
        `work` the fleet work held in the ore dug; `pads` the rehandling the ore on the ROM pads still needs
        (negative). Only differences between two moments mean something."""
        held, kpis = self.ore_held(sim), sim.kpis
        dug = self.grade_value_kzt(held["mined_t"] + held["pads_t"], held["mined_cu"] + held["pads_cu"])
        in_pit = -self.grade_value_kzt(kpis.ore_mined_t, kpis.ore_cu_mined_t)
        return {"grade": dug + in_pit, "work": self.stock_kzt_t * (held["mined_t"] + held["pads_t"]),
                "pads": -self.rehandle_kzt_t * held["pads_t"]}

    def stock_value_kzt(self, sim) -> float:
        return sum(self.stock_parts_kzt(sim).values())

    def strip_value_kzt(self, waste_t: float) -> float:
        """Waste stripped: the fleet work it saves later, at the same price per tonne as ore dug."""
        return waste_t * self.stock_kzt_t

    def basis(self) -> dict:
        """The prices and factors, for the `spec` of the mine twin."""
        e = self.params["economics"]
        return {"copper_usd_t": e["copper_usd_t"], "usd_kzt": e["usd_kzt"], "payable_cu_pct": e["payable_cu_pct"],
                "tc_usd_t": e["tc_usd_t"], "rc_usc_lb": e["rc_usc_lb"], "diesel_kzt_l": e["diesel_kzt_l"],
                "power_kzt_kwh": e["power_kzt_kwh"], "diesel_co2_kg_l": e["diesel_co2_kg_l"],
                "grid_co2_t_mwh": e["grid_co2_t_mwh"], "stock_kzt_t": e["stock_kzt_t"],
                "rehandle_kzt_t": round(self.rehandle_kzt_t),
                "ref_grade_pct": self.params["geology"]["mean_head_grade_pct"],
                "cu_value_kzt_t": round(self.cu_value_kzt_t),
                "copper_price_source": "World Bank, Pink Sheet, среднее за сентябрь 2026 года (LME)",
                "margin_note": "Переменная маржа: выручка за оплачиваемую медь минус дизельное топливо и электроэнергия; "
                               "труд и постоянные затраты не учитываются. Перевалка руды с рудных складов погрузчиком "
                               "показана отдельно (rehandle_cost_kzt_h).",
                "stock_note": "Прогноз оценивает руду везде одинаково: медь сверх средней руды месторождения "
                              "(0,41 %) по цене меди при опорном извлечении, плюс работа парка, уже вложенная в "
                              "добытую руду, минус перевалка, которая ещё нужна руде на рудных складах. Мельница — "
                              "узкое место: богатая руда, взятая со склада, даёт медь, но склад теряет столько же, "
                              "так что перекладывание руды из кучи в кучу выигрыша не даёт."}
