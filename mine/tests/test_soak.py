"""Soak test: seven simulated days of the mine, once under its fixed plan (all modules off) and once with all
four modules on, checked against the ranges of the Bozshakol basis. The measured values are printed, with the
annual figures they come to beside KAZ Minerals' published ones.

Ranges and why:
    cycle_min                15-30 min    hauls of 1.7-4.1 km from a pit 225 m deep at 8-10 % (design cycle 20.9 min)
    fuel_lph                 Turija band  0.35-0.50 of the 785C engine's full-power fuel, per engine hour
    head_grade_pct           0.33-0.46    ОФ-1 feed: sulphide ore 0.39 % (derived for 2024); a week's faces vary
    recovery_pct             76-85 %      ОФ-1: 81.1 % derived for 2024 at the reference grade and clay
    clay_recovery_pct        55-76 %      ОФ-2: 66.5 % derived for 2024; the week's clay faces vary in clay and grade
    site_recovery_pct        73-81.5 %    the mine (both plants): 73.4-81.1 % in 2019-2025, 78.1 % in 2024
    site_head_grade_pct      0.35-0.48    the mine: 0.39-0.51 % in 2019-2025, 0.40 % in 2024
    clay_share_pct           8-26 %       clay ore in the ore mined: 14.2-30.5 % in 2019-2025, 17.1 % in 2024
    mill_kwh_t               16.5-22      17.7 kWh/t at the reference hardness; harder blocks draw more
    strip_ratio              0.35-0.75    waste per tonne of ore and low-grade ore, 0.545 by design (0.7 with the low-grade)
    annual_ore_mt            28.0-29.3    ОФ-1: 28.67 Mt derived for 2024 (no modules: the mill at its setpoint);
                                          with plant control at most 1.6 % more (peaks of 2 h a shift)
    annual_processed_mt      33.8-35.6    both plants: 34.755 Mt processed in 2024; with plant control as above
    annual_cu_kt             ±10 %        copper in concentrate 2024: 109.6 kt
    rock_vs_required         1.00-1.30    the fleet carries what the plants need, with its 10 % reserve and a little over
    use_of_available_pct     70-88 %      the design basis assumes 80 % (crew change, meal break, refuelling, queues)
    truck_availability_pct   82-92 %      without maintenance, against 85 % assumed by the design basis
"""
import unittest

from mine.model.build_twins import full_power_fuel_lph, load_params
from mine.tests.coupled import ALL, WEEK_CONFIGS, shift_ceiling_tph, weeks

PARAMS = load_params()
FULL = full_power_fuel_lph(PARAMS)
HOURS_A_YEAR = 8760 * PARAMS["plan"]["plant_availability"]
CU_KT = 109.6                                   # Bozshakol, copper in concentrate 2024 (both plants)
# Plant control feeds at most this much above the working rate over a shift: its peaks, 2 h a shift at the
# 2024 trial rate (`plant_control.shift_margin_pct`); a week is a run of such shifts.
PEAK = shift_ceiling_tph(PARAMS) / PARAMS["mill"]["design_tph"]
COMMON = {
    "cycle_min": (15.0, 30.0),
    "fuel_lph": (PARAMS["truck"]["cycle_load_factor_min"] * FULL, PARAMS["truck"]["cycle_load_factor_max"] * FULL),
    "head_grade_pct": (0.33, 0.46),
    "recovery_pct": (76.0, 85.0),
    "clay_recovery_pct": (55.0, 76.0),
    "site_recovery_pct": (73.0, 81.5),
    "site_head_grade_pct": (0.35, 0.48),
    "clay_share_pct": (8.0, 26.0),
    "mill_kwh_t": (16.5, 22.0),
    "lowest_stockpile_t": (0.0, PARAMS["stockpile"]["capacity_t"]),
    "longest_standby_min": (0.0, 75.0),
    "strip_ratio": (0.35, 0.75),
    "rock_vs_required": (1.0, 1.30),
    "use_of_available_pct": (70.0, 88.0),
}
RANGES = {
    (): {**COMMON, "mill_feed_pct_of_design": (99.0, 100.0 + 1e-6), "truck_availability_pct": (82.0, 92.0),
         "annual_ore_mt": (28.0, 29.3), "annual_processed_mt": (33.8, 35.6), "annual_cu_kt": (0.9 * CU_KT, 1.1 * CU_KT)},
    # Plant control feeds at the sustained ceiling (the 2024 average) with short peaks: at most PEAK times the
    # working rate over the week; maintenance keeps more trucks available.
    ALL: {**COMMON, "mill_feed_pct_of_design": (100.0, 100.0 * PEAK + 0.05),
          "truck_availability_pct": (90.0, 98.0), "annual_ore_mt": (28.0, 28.67 * PEAK + 0.01),
          "annual_processed_mt": (33.8, 34.755 * PEAK + 0.01),
          "annual_cu_kt": (0.9 * CU_KT, 1.1 * CU_KT)},
}
SHOWN = ("trips", "queue_min_per_trip", "tonnes_moved", "ore_mined_t", "waste_mined_t", "rom_pads_t", "ore_milled_t",
         "clay_milled_t", "clay_head_grade_pct", "ofs1_clay_pct", "clay_stock_t", "cu_ofs1_t",
         "grade_in_band_pct", "cu_t", "mill_stops", "breakdowns", "workshop_jobs", "blasts", "blocks_dug_out", "messages")


def annual(r: dict) -> dict:
    """The week's figures as a year of the line: the mill's hours times the plant's share of the year."""
    tph = lambda total: total / r["hours"]
    rock_tph = tph(r["ore_mined_t"] + r["waste_mined_t"])
    return {
        **r,
        "annual_ore_mt": tph(r["ore_milled_t"]) * HOURS_A_YEAR / 1e6,
        "annual_processed_mt": tph(r["ore_milled_t"] + r["clay_milled_t"]) * HOURS_A_YEAR / 1e6,
        "annual_cu_kt": tph(r["cu_t"]) * HOURS_A_YEAR / 1e3,
        "rock_vs_required": rock_tph / PARAMS["plan"]["rock_tph"],
    }


class Soak(unittest.TestCase):
    def check(self, config):
        r = annual(weeks(WEEK_CONFIGS)[config])
        print(f"\nSoak: 7 simulated days, seed {r['seed']}, modules: {r['modules']}")
        for name, (low, high) in RANGES[config].items():
            print(f"  {name:28s} {r[name]:12.2f}   allowed {low:.2f} .. {high:.2f}")
        for name in SHOWN:
            print(f"  {name:28s} {r[name]:12.2f}")
        print(f"  {'journal events':28s} {r['events']}")
        print(f"  {'balance rock / copper, t':28s} {r['rock_balance_t']:.4f} / {r['copper_balance_t']:.6f}")
        for text in sorted(set(r["warning_texts"]))[:8]:
            print(f"    warning: {text[:160]}")
        for name, (low, high) in RANGES[config].items():
            self.assertTrue(low <= r[name] <= high, f"{name}: {r[name]:.3f} is outside {low:.2f} .. {high:.2f}")
        self.assertAlmostEqual(r["rock_balance_t"], 0.0, delta=0.1)
        self.assertAlmostEqual(r["copper_balance_t"], 0.0, delta=1e-3)
        self.assertAlmostEqual(r["mill_copper_balance_t"], 0.0, delta=1e-6)
        self.assertEqual(r["latin_texts"], [], "Russian texts with identifiers in them")
        return r

    def test_seven_days_without_modules(self):
        r = self.check(())
        self.assertEqual(r["messages"], 0)

    def test_seven_days_with_all_modules(self):
        r = self.check(ALL)
        self.assertGreater(min(r["decisions"].values()), 10)
        # The modules' messages are accepted: the warnings are the failures no monitoring can foresee
        # (`reliability.sudden_share_pct`, a minor one is a warning) and a few more.
        self.assertLessEqual(r["events"].get("warning", 0) - r["breakdowns"], 20)


if __name__ == "__main__":
    unittest.main()
