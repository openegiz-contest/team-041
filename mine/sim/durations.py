"""How long each incident scenario is expected to last, and why (params.yaml).

A scenario runs on the stand until it is cancelled; its expected end is what the shift supervisor is told
(«ремонт до 14:20») and what every forecast assumes: a forecast branch ends the scenario at that moment,
unless an option changes it (a second crew shortens the repair, mine/sim/response.py). The table is shared
by the scenario objects and by the twin definitions, which publish the causes a scenario can be started with.
"""
from __future__ import annotations


def causes(params: dict) -> dict[str, list[dict]]:
    """{scenario: [{id, title, hours, basis}]}, the default cause first. A scenario without an entry (the
    blast) runs by itself and has no expected end."""
    crusher, road, rel, weather, scenario = (params["crusher"], params["road"], params["reliability"],
                                             params["weather"], params["scenario"])
    h = lambda value: f"{value:g}".replace(".", ",")
    return {
        "scenario-crusher-stop": [
            {"id": "mechanical", "title": "механическая неисправность", "hours": float(crusher["repair_mechanical_h"]),
             "basis": f"механическая неисправность (гидросистема, смазка, привод): замена узла со склада запчастей одной "
                      f"сменой ремонтников, около {h(crusher['repair_mechanical_h'])} ч — допущение модели"},
            {"id": "jam", "title": "заклинивание негабаритом", "hours": float(crusher["repair_jam_h"]),
             "basis": f"заклинивание камеры негабаритом: глыбу дробят гидромолотом или вынимают краном, затем осматривают "
                      f"броню, около {h(crusher['repair_jam_h'])} ч — допущение модели"},
        ],
        "scenario-shovel-failure": [
            {"id": "breakdown", "title": "отказ в забое", "hours": float(rel["loader_mttr_h"]),
             "basis": f"отказ экскаватора в забое: среднее время ремонта {h(rel['loader_mttr_h'])} ч — допущение модели"},
        ],
        "scenario-road-closure": [
            {"id": "slide", "title": "осыпь на дорогу", "hours": float(road["closure_expected_h"]),
             "basis": f"осыпь с откоса уступа на дорогу: дорожная бригада убирает горную массу и восстанавливает "
                      f"предохранительный вал, около {h(road['closure_expected_h'])} ч — допущение модели"},
        ],
        "scenario-heavy-rain": [
            {"id": "heavy", "title": "сильные осадки", "hours": float(weather["heavy_event_h"]),
             "basis": f"сильные осадки 10–15 мм при {h(weather['rain_heavy_mm_h'])} мм/ч идут около "
                      f"{h(weather['heavy_event_h'])} ч (климатические нормы станции Актогай), затем дороги сохнут "
                      f"{h(road['drying_min'])} мин"},
        ],
        "scenario-hard-ore": [
            {"id": "zone", "title": "зона крепкой руды", "hours": float(scenario["hard_ore_expected_h"]),
             "basis": f"крепкую руду даёт забой, вскрывший зону; руды зоны хватит не меньше чем на смену, "
                      f"{h(scenario['hard_ore_expected_h'])} ч — допущение модели"},
        ],
    }
