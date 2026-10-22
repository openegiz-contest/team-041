"""Generate docs/mine/parameters.md from params.yaml.

The document lists every numeric constant of the model with its unit, its
source and how far that source can be trusted, one table per subsystem, and
closes with the figures that follow from the constants by arithmetic.

    python -m mine.model.build_params_doc            # print the document
    python -m mine.model.build_params_doc --write    # write docs/mine/parameters.md
    python -m mine.model.build_params_doc --check    # exit 1 if the file is out of date
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

from mine.common.process import clay_plant_split

from mine.model.build_twins import (MODEL_DIR, bucket_load_t, full_power_fuel_lph, holes_per_block, load_params,
                                    load_params_raw, load_world, nominal_passes)

DOC_PATH = MODEL_DIR.parents[1] / "docs" / "mine" / "parameters.md"

STATUS_TITLES = {
    "sourced": "источник прочитан",
    "snippet": "по выдержке",
    "derived": "расчёт",
    "assumption": "допущение",
}
STATUS_NOTES = {
    "sourced": "значение прочитано в самом источнике: паспорт изготовителя, отчёт компании, нормативный документ, статья",
    "snippet": "значение взято из поисковой выдержки или пересказа; сам источник открыть не удалось",
    "derived": "значение вычислено из других строк; арифметика приведена в примечании",
    "assumption": "источник не найден, значение выбрано проектом; обоснование приведено в примечании",
}


def number(value) -> str:
    """A value as it is written in Russian text: decimal comma, thin grouping of thousands."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return str(value)
    if float(value).is_integer():
        whole = int(value)
        return f"{whole:,}".replace(",", " ") if abs(whole) >= 10000 else str(whole)
    return repr(float(value)).replace(".", ",")


def cell(text) -> str:
    return str(text).replace("|", "\\|").replace("\n", " ").strip()


def groups_meta() -> dict:
    with open(MODEL_DIR / "params.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)["_groups"]


def summary_rows(p: dict) -> list[tuple[str, str]]:
    """Figures that follow from the constants: (what, arithmetic and result)."""
    truck, loader, mill, flot, plan = p["truck"], p["loader"], p["mill"], p["flotation"], p["plan"]
    full = full_power_fuel_lph(p)
    bucket = bucket_load_t(p)
    gross = truck["empty_mass_t"] + truck["payload_t"]
    ramp = p["road"]["ramp_grade_max_pct"] + p["road"]["rr_ramp_pct"]
    climb_kmh = truck["engine_kw"] * truck["drivetrain_efficiency"] / (gross * 9.81 * ramp / 100.0) * 3.6
    cu_tph = mill["design_tph"] * p["geology"]["mean_head_grade_pct"] / 100.0 * flot["recovery_ref_pct"] / 100.0
    clay = p["clay_plant"]
    split = clay_plant_split(p, clay["mean_feed_grade_pct"], clay["ref_clay_pct"])
    cu2_tph = clay["design_tph"] * clay["mean_feed_grade_pct"] / 100.0 * split["recovery_pct"] / 100.0
    fed_tph = (mill["design_tph"] * p["geology"]["mean_head_grade_pct"] + clay["design_tph"] * clay["mean_feed_grade_pct"]) / 100.0
    drill, blast = p["drill"], p["blast"]
    energy = mill["specific_energy_kwh_t"] + flot["energy_kwh_t"] + p["plant"]["aux_energy_kwh_t"]
    width, depth = next(b for b in load_world(params=p)["benches"] if b["working"])["blocks"]["cell_m"]
    area = width * depth
    holes = holes_per_block(p, area)
    volume = area * drill["bench_height_m"]
    n = number
    return [
        ("Расход топлива на полной мощности",
         f"{n(truck['engine_kw'])} кВт × {n(truck['bsfc_g_kwh'])} г/кВт·ч / {n(truck['diesel_density_kg_l'])} кг/л = {full:.1f} л/ч".replace(".", ",")),
        ("Допустимая полоса среднего расхода за рейс",
         f"{full * truck['cycle_load_factor_min']:.0f}–{full * truck['cycle_load_factor_max']:.0f} л/ч "
         f"(загрузка двигателя {n(truck['cycle_load_factor_min'])}–{n(truck['cycle_load_factor_max'])}); "
         f"модель проверяется на попадание в эту полосу"),
        ("Расход на холостом ходу", f"{full:.1f} × {n(truck['idle_load_factor'])} = {full * truck['idle_load_factor']:.1f} л/ч".replace(".", ",")),
        ("Скорость гружёного самосвала на съезде с наибольшим уклоном",
         f"{n(truck['engine_kw'])} кВт × {n(truck['drivetrain_efficiency'])} / ({n(round(gross, 1))} т × 9,81 м/с2 × "
         f"({n(p['road']['ramp_grade_max_pct'])} + {n(p['road']['rr_ramp_pct'])}) %) = {climb_kmh:.1f} км/ч; "
         f"для сравнения, вторая передача 785C — до 16,4 км/ч".replace(".", ",")),
        ("Груз одного ковша", f"{n(loader['bucket_m3'])} м3 × {n(loader['fill_factor'])} × {n(p['rock']['loose_density_t_m3'])} т/м3 = {bucket:.1f} т".replace(".", ",")),
        ("Ковшей на самосвал", f"{n(truck['payload_t'])} т / {bucket:.1f} т = {nominal_passes(p)}".replace(".", ",")),
        ("Погрузка одного самосвала",
         f"{n(loader['spot_s'])} с + {nominal_passes(p)} × {n(loader['pass_s'])} с = {loader['spot_s'] + nominal_passes(p) * loader['pass_s']} с"),
        (f"Объём буровых работ на блок {n(width)} × {n(depth)} м",
         f"{n(area)} м2 / ({n(drill['burden_m'])} × {n(drill['spacing_m'])} м) = {holes} скважин × {n(drill['hole_depth_m'])} м = "
         f"{holes * drill['hole_depth_m']:.0f} м, около {holes * drill['hole_depth_m'] / drill['gross_rate_m_h']:.0f} часов одного станка"),
        (f"Масса блока {n(width)} × {n(depth)} × {n(drill['bench_height_m'])} м",
         f"{n(volume)} м3 × {n(p['rock']['insitu_density_t_m3'])} т/м3 = {n(round(volume * p['rock']['insitu_density_t_m3']))} т"),
        ("ВВ на блок", f"{holes} скважин × {n(blast['charge_per_hole_kg'])} кг = {holes * blast['charge_per_hole_kg'] / 1000:.1f} т".replace(".", ",")),
        ("Наибольшее питание мельницы при опорной крепости",
         f"{n(mill['installed_mw'])} МВт / {n(mill['specific_energy_kwh_t'])} кВт·ч/т = {mill['installed_mw'] * 1000 / mill['specific_energy_kwh_t']:.0f} т/ч"),
        ("Медь в концентрате на проектной производительности",
         f"{n(mill['design_tph'])} т/ч × {n(p['geology']['mean_head_grade_pct'])} % × {n(flot['recovery_ref_pct'])} % = {cu_tph:.2f} т/ч; "
         f"концентрата {cu_tph / flot['conc_grade_pct'] * 100:.1f} т/ч при {n(flot['conc_grade_pct'])} % Cu".replace(".", ",")),
        ("Электроэнергия фабрики на тонну руды",
         f"мельницы {n(mill['specific_energy_kwh_t'])} + флотация {n(flot['energy_kwh_t'])} + прочее {n(p['plant']['aux_energy_kwh_t'])} = "
         f"{energy:.1f} кВт·ч/т без дробления и конвейера; для сравнения, медные фабрики Чили: 22,9 кВт·ч/т".replace(".", ",")),
        ("Готовность парка самосвалов",
         f"1 − {n(p['reliability']['truck_mttr_h'])} / ({n(p['reliability']['truck_mtbf_h'])} + {n(p['reliability']['truck_mttr_h'])}) − "
         f"{n(p['reliability']['truck_minor_mttr_h'])} / ({n(p['reliability']['truck_minor_mtbf_h'])} + {n(p['reliability']['truck_minor_mttr_h'])}) = "
         f"{100 * (1 - p['reliability']['truck_mttr_h'] / (p['reliability']['truck_mtbf_h'] + p['reliability']['truck_mttr_h']) - p['reliability']['truck_minor_mttr_h'] / (p['reliability']['truck_minor_mtbf_h'] + p['reliability']['truck_minor_mttr_h'])):.0f} % "
         f"без учёта ожидания бокса; в расчёте парка принято {n(100 * plan['availability'])} %"),
        ("Производительность ОФ-1 и ОФ-2 за год",
         f"({n(mill['design_tph'])} + {n(clay['design_tph'])}) т/ч × 8760 ч × {n(plan['plant_availability'])} = "
         f"{(mill['design_tph'] + clay['design_tph']) * 8760 * plan['plant_availability'] / 1e6:.2f} млн т; "
         f"Бозшаколь за 2024 год — 34,76 млн т".replace(".", ",")),
        ("Извлечение ОФ-2 на опорной глинистой руде",
         f"шламы {split['slimes_share'] * 100:.0f} % массы и {split['slimes_cu_share'] * 100:.0f} % меди; "
         f"{100 - split['slimes_cu_share'] * 100:.0f} % × {split['sand_recovery_pct']:.1f} % + {split['slimes_cu_share'] * 100:.0f} % × "
         f"{split['slime_recovery_pct']:.0f} % = {split['recovery_pct']:.1f} %".replace(".", ",")),
        ("Медь в концентрате ОФ-1 и ОФ-2 за год",
         f"({cu_tph:.2f} + {cu2_tph:.2f}) т/ч × 8760 ч × {n(plan['plant_availability'])} = "
         f"{(cu_tph + cu2_tph) * 8760 * plan['plant_availability'] / 1000:.1f} тыс. т при извлечении рудника "
         f"{100 * (cu_tph + cu2_tph) / fed_tph:.1f} %; Бозшаколь за 2024 год — 109,6 тыс. т, 78,1 %".replace(".", ",").replace("тыс, т", "тыс. т")),
        *economics_rows(p, cu_tph, energy),
    ]


def economics_rows(p: dict, cu_tph: float, plant_kwh_t: float) -> list[tuple[str, str]]:
    """The money and CO2 figures that follow from the prices and factors."""
    e, mill = p["economics"], p["mill"]
    n = number
    cu_kzt_t = e["copper_usd_t"] * e["payable_cu_pct"] / 100.0 * e["usd_kzt"]
    revenue = cu_tph * cu_kzt_t
    power_mwh = mill["design_tph"] * plant_kwh_t / 1000.0

    def mln(value: float) -> str:
        return f"{value / 1e6:.2f}".replace(".", ",")
    return [
        ("Стоимость тонны меди в концентрате для рудника",
         f"{n(e['copper_usd_t'])} USD/т × {n(e['payable_cu_pct'])} % × {n(e['usd_kzt'])} тг/USD − TC {n(e['tc_usd_t'])} − RC "
         f"{n(e['rc_usc_lb'])} = {mln(cu_kzt_t)} млн тг"),
        ("Выручка в час на проектной производительности",
         f"{cu_tph:.2f} т/ч × {mln(cu_kzt_t)} млн тг/т = {mln(revenue)} млн тг/ч".replace(".", ",")),
        ("Электроэнергия фабрики в час на проектной производительности",
         f"{n(mill['design_tph'])} т/ч × {plant_kwh_t:.1f} кВт·ч/т = {power_mwh:.1f} МВт·ч/ч: "
         f"{mln(power_mwh * 1000 * e['power_kzt_kwh'])} млн тг/ч и {power_mwh * e['grid_co2_t_mwh']:.1f} т CO₂/ч; "
         f"экскаваторы, дробилка и конвейер сверх этого".replace(".", ",")),
        ("Дизельное топливо: цена и выбросы на 1000 л",
         f"1000 л × {n(e['diesel_kzt_l'])} тг/л = {mln(1000 * e['diesel_kzt_l'])} млн тг; "
         f"1000 л × {n(e['diesel_co2_kg_l'])} кг/л = {e['diesel_co2_kg_l']:.2f} т CO₂".replace(".", ",")),
    ]


def render() -> str:
    raw, meta, values = load_params_raw(), groups_meta(), load_params()
    counts = {status: 0 for status in STATUS_TITLES}
    for entries in raw.values():
        for entry in entries.values():
            counts[entry["status"]] += 1
    total = sum(counts.values())
    lines = [
        "# Параметры модели рудника Бозшаколь: карьер, сульфидная фабрика ОФ-1 и глинистая фабрика ОФ-2 (упрощённо)",
        "",
        "Модель рудника Бозшаколь (KAZ Minerals, Павлодарская область): карьер, сульфидная фабрика ОФ-1 и глинистая "
        "фабрика ОФ-2 (упрощённо). Числа взяты из открытых источников, у каждого указаны источник и статус; модель не "
        "связана с компанией и не использует её внутренних данных.",
        "",
        "Документ создан скриптом `mine/model/build_params_doc.py` из файла `mine/model/params.yaml` и вручную не "
        "редактируется: чтобы изменить число, меняют его в `params.yaml` и запускают `python -m mine.model.build_params_doc --write`. "
        "Тест `make mine-test` проверяет, что документ соответствует файлу.",
        "",
        f"В модели {total} числовых параметров. В коде симулятора констант с физическим смыслом нет: "
        "каждое число модель берёт из `params.yaml`, геометрию и исходное состояние из `mine/model/world.yaml`.",
        "",
        "## Как читать статус",
        "",
        "| Статус | Что означает | Параметров |",
        "|---|---|---|",
    ]
    for status, title in STATUS_TITLES.items():
        lines.append(f"| {title} | {STATUS_NOTES[status]} | {counts[status]} |")
    lines += [
        "",
        "Столбец «Обозначение» содержит имя параметра в `params.yaml`: по нему число находится в коде.",
        "",
    ]
    for group, entries in raw.items():
        info = meta[group]
        lines += [f"## {info['title']}", ""]
        if info.get("intro"):
            lines += [info["intro"], ""]
        lines += ["| Параметр | Обозначение | Значение | Ед. | Статус | Источник | Примечание |", "|---|---|---|---|---|---|---|"]
        for name, entry in entries.items():
            source = f"[{cell(entry['source'])}]({entry['url']})" if entry["url"] else cell(entry["source"])
            unit = "" if entry["unit"] == "-" else cell(entry["unit"])
            lines.append(f"| {cell(entry['title'])} | `{group}.{name}` | {number(entry['value'])} | {unit} | "
                         f"{STATUS_TITLES[entry['status']]} | {source} | {cell(entry.get('note', ''))} |")
        lines.append("")
    lines += [
        "## Что следует из параметров",
        "",
        "Величины ниже в `params.yaml` не записаны: скрипт вычисляет их из строк таблиц при создании документа.",
        "",
        "| Величина | Расчёт |",
        "|---|---|",
    ]
    lines += [f"| {cell(what)} | {cell(how)} |" for what, how in summary_rows(values)]
    lines += [
        "",
        "## Чего найти не удалось",
        "",
        "- Текст казахстанских правил промышленной безопасности при взрывных работах (приказ № 343 от 30.12.2014) "
        "получить не удалось; радиус опасной зоны и время допуска взяты из российских правил (приказ Ростехнадзора № 605).",
        "- KAZ Minerals не публикует углы бортов, трассы съездов, высоту уступа, расстояния откатки и положение "
        "фабрик карьера Бозшаколь; известны размеры карьера по верху (2023), отметки этапов углубки и горный отвод. "
        "Геометрия модели — допущение, построенное по параметрам группы «Карьер».",
        "- Текущий парк Бозшаколя не опубликован; модели и число техники 2014–2015 годов — проектные (18 самосвалов "
        "Caterpillar 785C, 4 экскаватора Hitachi 21 м3, Sandvik D55), число машин модели рассчитано по фабрикам "
        "(группа «Расчёт парка по фабрикам»).",
        "- Раздельная переработка, содержание и извлечение ОФ-1 и ОФ-2 не публикуются, только рудник в целом; "
        "доля меди ОФ-2 известна за III и IV кварталы 2024 года. Пороги глинистости руды, доля глины в руде, "
        "показатели обесшламливания и шламовой флотации не найдены — это допущения группы «Глинистая руда» и «ОФ-2». "
        "Опубликованных коэффициентов влияния глины на извлечение и производительность сульфидной фабрики тоже нет: "
        "литература подтверждает направление эффекта, но не его величину (подборка с номерами [Л…] и [Б…], на которые "
        "ссылаются примечания, — `docs/mine/clay-literature.md`; чувствительность — `mine/tests/sensitivity_clay.py`).",
        "- Для Caterpillar 785C не найдены удельный расход топлива двигателя 3512B, кривые тяги и замедлителя, "
        "показатель TKPH шин 33.00-R51; для Hitachi EX3600E-6 — длительность цикла черпания.",
        "- Для Бозшаколя не найдены плотность руды, индекс Бонда, содержание меди в концентрате, вместимость склада "
        "дроблёной руды ОФ-1, мощности мельниц ОФ-2, текущие запасы (протокол ГКЗ 2021 года) и время работы фабрик в году.",
        "- Климатические нормы станции Экибастуз взяты из таблицы Википедии со ссылкой на Pogoda.ru.net и NOAA; "
        "отметка площадки рудника не найдена. Данных о ветре нет.",
        "- Не найдены измеренные значения для мокрых карьерных дорог, время установки и разгрузки самосвала, "
        "наработка на отказ из рецензируемых источников.",
        "- Не найдены средний промышленный тариф на электроэнергию по Казахстану (взят предельный тариф одного региона), "
        "официальная цена дизельного топлива Бюро национальной статистики (взят трекер цен) и официальный "
        "коэффициент выбросов CO₂ энергосистемы Казахстана (взят агрегатор; оценки расходятся до 35 %).",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--write", action="store_true", help="write docs/mine/parameters.md")
    parser.add_argument("--check", action="store_true", help="exit 1 if docs/mine/parameters.md is out of date")
    args = parser.parse_args()
    text = render()
    if args.write:
        DOC_PATH.write_text(text, encoding="utf-8", newline="\n")
        print(f"wrote {DOC_PATH}")
    elif args.check:
        if not DOC_PATH.exists() or DOC_PATH.read_text(encoding="utf-8") != text:
            print(f"{DOC_PATH} is out of date: run python -m mine.model.build_params_doc --write", file=sys.stderr)
            return 1
        print(f"{DOC_PATH} is current")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
