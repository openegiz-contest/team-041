"""Sensitivity of the model to its two clay coefficients on ОФ-1, which no published source gives
(docs/mine/clay-literature.md): the recovery lost per point of clay in the feed (`flotation.clay_loss_pct`)
and the mill throughput lost per point (`mill.clay_handling_slope`), both scaled together by 0.5, 1 and 1.5.

For each factor it runs, in this process and its children (no stand needed):
- 7 simulated days of the mine without modules (the calibration run of test_soak) and with all four modules:
  recovery of the mine, of ОФ-1 and of ОФ-2, copper a year, the clay in the ОФ-1 feed;
- the advisor's two clay decisions as test_advisor sets them up (ClayDecides): a clay face with a free blasted
  sulphide block and without one, and ОФ-2 stopped for 6 h and for 1.5 h; for each, the option recommended
  and the gain of the options that answer it.

The 7-day runs go side by side; the advisor cases one after another, as on the stand: an advisor's twelve
forecast branches share the processors, and a branch slower than `forecast.timeout_s` would be dropped, so
cases run in parallel would not rank as the stand does. It is not part of `make mine-test` (it takes about
6 minutes on 24 cores). Run it with the test image:

    docker build -q --provenance=false -t bozshakol/mine:local mine
    docker run --rm bozshakol/mine:local python -m mine.tests.sensitivity_clay [--json /path/out.json]

(`--from-json /path/out.json` prints the tables of a saved run again without computing)

and it prints one Markdown table per part, in Russian, as SUBMISSION.md quotes them.
"""
from __future__ import annotations

import json
import sys
from concurrent.futures import ProcessPoolExecutor

FACTORS = (0.5, 1.0, 1.5)
HOURS_A_YEAR = 8760 * 0.92                      # as test_soak: `plan.plant_availability` of the year
SITE_RECOVERY_2024 = 78.1                       # KAZ Minerals, 2024: the mine, both plants
CASES = ("clay-face-free", "clay-face-none", "ofs2-6h", "ofs2-1.5h")


def scale(params: dict, factor: float) -> None:
    """Set the two clay coefficients to `factor` times their values in params.yaml, in place: every part of the
    model reads this one dict. Idempotent, so a worker process may take several jobs."""
    from mine.model.build_twins import load_params
    fresh = load_params()
    params["flotation"]["clay_loss_pct"] = fresh["flotation"]["clay_loss_pct"] * factor
    params["mill"]["clay_handling_slope"] = fresh["mill"]["clay_handling_slope"] * factor


def week_run(job: tuple[float, bool]) -> dict:
    factor, modules = job
    from mine.model.build_twins import load_params
    from mine.tests import coupled
    params = load_params()
    scale(params, factor)
    coupled.load_params = lambda: params        # week() builds its model from load_params()
    r = coupled.week(coupled.ALL if modules else ())
    rate = lambda total: total / r["hours"] * HOURS_A_YEAR
    return {"factor": factor, "modules": modules, "site_recovery_pct": r["site_recovery_pct"],
            "recovery_pct": r["recovery_pct"], "clay_recovery_pct": r["clay_recovery_pct"],
            "ofs1_clay_pct": r["ofs1_clay_pct"], "annual_cu_kt": rate(r["cu_t"]) / 1e3,
            "annual_ore_mt": rate(r["ore_milled_t"]) / 1e6, "mill_stops": r["mill_stops"]}


def advisor_run(job: tuple[float, str]) -> dict:
    factor, case = job
    from mine.tests import test_model
    scale(test_model.PARAMS, factor)            # the dict test_advisor, its stand and the forecasts read
    test_model._WARM.clear()
    from mine.tests.test_advisor import NS, Stand

    def clay_face(stand):
        stand.sim.handle_message("scenario-clay-face", "activate", {"target": f"{NS}:loader-03"})

    def clay_face_nothing_free(stand):
        for block in stand.sim.pit.blocks.values():
            if block.status == "blasted" and block.material == "ore" and not block.reserved_by:
                block.status = "charged"
        clay_face(stand)

    def stop(hours):
        return lambda stand: stand.sim.handle_message("scenario-clay-plant-stop", "activate", {"params": {"expected_h": hours}})

    setup = {"clay-face-free": clay_face, "clay-face-none": clay_face_nothing_free,
             "ofs2-6h": stop(6.0), "ofs2-1.5h": stop(1.5)}[case]
    import time
    stand = Stand(warm_min=90)
    setup(stand)
    stand.run(20)
    started = time.monotonic()
    twin = stand.run_until_ranked(1800)
    seconds = time.monotonic() - started
    options = []
    for n in range(1, twin["options_count"] + 1):
        options.append({"n": n, "label": twin[f"option{n}_label"], "actions": twin[f"option{n}_actions"],
                        "delta_mln": twin[f"option{n}_delta_kzt"] / 1e6, "se_mln": twin[f"option{n}_gain_se_kzt"] / 1e6,
                        "cu_t": twin[f"option{n}_cu_delta_t"]})
    rec = twin["recommended"]
    return {"factor": factor, "case": case, "incident": twin["incident"], "recommended": rec, "seconds": seconds,
            "recommended_actions": options[rec - 1]["actions"] if rec else "", "options": options}


def kind(actions: str) -> str:
    """What an option does, in a word: the lever its actions pull."""
    for key, word in (("move-to-block", "move"), ("expedite-repair", "crew"), ("clay-stockpile", "stockpile"),
                      ("destination=crusher", "blend"), ("destination=clay-plant", "ofs2"), ("set-enabled", "switch")):
        if key in actions:
            return word
    return "nothing" if not actions else "other"


WORDS = {"move": "перевести экскаватор на сульфидный блок", "crew": "вторая бригада на ремонт ОФ-2",
         "nothing": "«Ничего не менять»", "stockpile": "руду забоя на склад глинистой руды", "blend": "в шихту ОФ-1",
         "ofs2": "закрепить за ОФ-2", "switch": "включить модули", "other": "другой вариант"}
# The lever each decision is about: shown beside «change nothing» when that is recommended.
FOCUS = {"clay-face-free": "move", "clay-face-none": "stockpile", "ofs2-6h": "crew", "ofs2-1.5h": "crew"}
CASE_RU = {"clay-face-free": "Глинистый забой, есть свободный сульфидный блок",
           "clay-face-none": "Глинистый забой, свободного блока нет",
           "ofs2-6h": "Остановка ОФ-2 на 6 ч", "ofs2-1.5h": "Остановка ОФ-2 на 1,5 ч"}


def ru(x: float, digits: int = 1) -> str:
    return f"{x:.{digits}f}".replace(".", ",").replace("-", "−")


def signed(x: float, digits: int = 1) -> str:
    return ("+" if x >= 0 else "") + ru(x, digits)


def main(argv: list[str]) -> int:
    if "--from-json" in argv:                   # print the tables of an earlier run again
        with open(argv[argv.index("--from-json") + 1], encoding="utf-8") as f:
            saved = json.load(f)
        return print_tables(saved["weeks"], saved["advisor"])
    weeks = [(f, m) for f in FACTORS for m in (False, True)]
    cases = [(f, c) for f in FACTORS for c in CASES]
    with ProcessPoolExecutor(max_workers=6) as pool:
        week_results = list(pool.map(week_run, weeks))
    # One advisor case at a time, each in a fresh process (see the module's docstring).
    advisor_results = []
    for job in cases:
        with ProcessPoolExecutor(max_workers=1) as pool:
            advisor_results.append(pool.submit(advisor_run, job).result())
        r = advisor_results[-1]
        print(f"  advisor x{r['factor']} {r['case']}: recommended {r['recommended']} in {r['seconds']:.0f} s", flush=True)
    if "--json" in argv:
        with open(argv[argv.index("--json") + 1], "w", encoding="utf-8") as f:
            json.dump({"weeks": week_results, "advisor": advisor_results}, f, ensure_ascii=False, indent=1)
    return print_tables(week_results, advisor_results)


def print_tables(week_results: list[dict], advisor_results: list[dict]) -> int:
    print("\n## 7 суток модели, извлечение и медь\n")
    print("| Коэффициенты глины на ОФ-1 | Модули | Извлечение рудника (2024: 78,1 %) | ОФ-1 | ОФ-2 | Глина в питании ОФ-1 | "
          "Медь за год | Остановок мельницы |")
    print("|---|---|---|---|---|---|---|---|")
    for r in week_results:
        print(f"| ×{ru(r['factor'])} ({ru(0.6 * r['factor'], 2)} п./%, {ru(2.0 * r['factor'])} %/%) | "
              f"{'все четыре' if r['modules'] else 'без модулей'} | {ru(r['site_recovery_pct'])} % "
              f"({signed(r['site_recovery_pct'] - SITE_RECOVERY_2024)}) | {ru(r['recovery_pct'])} % | "
              f"{ru(r['clay_recovery_pct'])} % | {ru(r['ofs1_clay_pct'])} % | {ru(r['annual_cu_kt'])} тыс. т | {r['mill_stops']} |")

    print("\n## Решения советника\n")
    base = {r["case"]: kind(r["recommended_actions"]) for r in advisor_results if r["factor"] == 1.0}
    print("| Происшествие | ×0,5 | ×1 | ×1,5 | Рекомендация та же |")
    print("|---|---|---|---|---|")
    for case in CASES:
        cells, same = [], True
        for factor in FACTORS:
            r = next(x for x in advisor_results if x["case"] == case and x["factor"] == factor)
            k = kind(r["recommended_actions"])
            same &= k == base[case]
            # Beside «change nothing», the option the decision is about (the crew, the move), else the best one.
            lever = [o for o in r["options"][1:] if kind(o["actions"]) == FOCUS[case]]
            best = lever[0] if lever else max((o for o in r["options"][1:]), key=lambda o: o["delta_mln"], default=None)
            shown = r["options"][r["recommended"] - 1] if r["recommended"] > 1 else best
            gain = f"{signed(shown['delta_mln'])} ± {ru(shown['se_mln'])} млн ₸, {signed(shown['cu_t'])} т" if shown else ""
            if r["recommended"] > 1:
                cells.append(f"{WORDS[k]}: {gain}")
            else:
                cells.append(f"{WORDS['nothing']}" + (f" ({WORDS[kind(shown['actions'])]}: {gain})" if shown else ""))
        print(f"| {CASE_RU[case]} | " + " | ".join(cells) + f" | {'да' if same else 'нет'} |")

    print("\nOptions of every case (factor, case, n, gain ± se, copper, label):")
    for r in advisor_results:
        for o in r["options"]:
            mark = " *" if o["n"] == r["recommended"] else ""
            print(f"  x{r['factor']} {r['case']:15s} {o['n']}. {o['delta_mln']:+7.1f} ± {o['se_mln']:.1f} {o['cu_t']:+6.2f} t{mark} "
                  f"{o['label'][:110]}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
