"""Soak test: seven simulated days of the mine, once under its fixed plan (all modules off) and once with all
four modules on, checked against the ranges of the Aktogay basis. The measured values are printed, with the
annual figures they come to beside KAZ Minerals' published ones.

Ranges and why:
    cycle_min                20-35 min    hauls of 2.3-4.3 km from a pit 255 m deep at 8 % (design cycle 25 min)
    fuel_lph                 Turija band  0.35-0.50 of the 785C engine's full-power fuel, per engine hour
    head_grade_pct           0.36-0.46    Aktogay mill feed 0.41 % (2025), 0.43 (2024); a week's faces vary
    recovery_pct             84-89 %      Aktogay 84.9-87.9 % in 2020-2025
    mill_kwh_t               15.5-21      16.4 kWh/t at the reference hardness; harder blocks draw more
    strip_ratio              0.30-0.42    Aktogay 0.363 (2024); a week's blasts vary
    annual_ore_mt            30.4-31.4    Sulphide 1 processed 30.909 Mt in 2025 (no modules: the mill at its setpoint)
    annual_cu_kt             ±10 %        Sulphide 1's share of copper in concentrate 2025: 109.7 kt
    rock_vs_required         1.00-1.30    the fleet carries what the line needs, with its 10 % reserve and a little over
    use_of_available_pct     70-88 %      the design basis assumes 80 % (crew change, meal break, refuelling, queues)
    truck_availability_pct   82-92 %      without maintenance, against 85 % assumed by the design basis
"""
import unittest

from mine.model.build_twins import full_power_fuel_lph, load_params
from mine.tests.coupled import ALL, WEEK_CONFIGS, weeks

PARAMS = load_params()
FULL = full_power_fuel_lph(PARAMS)
HOURS_A_YEAR = 8760 * PARAMS["plan"]["plant_availability"]
CU_LINE_KT = 220.3 * 30.909 / 62.095            # Sulphide 1's share of Aktogay's copper in concentrate, 2025
COMMON = {
    "cycle_min": (20.0, 35.0),
    "fuel_lph": (PARAMS["truck"]["cycle_load_factor_min"] * FULL, PARAMS["truck"]["cycle_load_factor_max"] * FULL),
    "head_grade_pct": (0.36, 0.46),
    "recovery_pct": (84.0, 89.0),
    "mill_kwh_t": (15.5, 21.0),
    "lowest_stockpile_t": (0.0, PARAMS["stockpile"]["capacity_t"]),
    "longest_standby_min": (0.0, 75.0),
    "strip_ratio": (0.30, 0.42),
    "rock_vs_required": (1.0, 1.30),
    "use_of_available_pct": (70.0, 88.0),
}
RANGES = {
    (): {**COMMON, "mill_feed_pct_of_design": (99.0, 100.0 + 1e-6), "truck_availability_pct": (82.0, 92.0),
         "annual_ore_mt": (30.4, 31.4), "annual_cu_kt": (0.9 * CU_LINE_KT, 1.1 * CU_LINE_KT)},
    # Plant control may feed up to the setpoint limit; maintenance keeps more trucks available.
    ALL: {**COMMON, "mill_feed_pct_of_design": (100.0, 100.0 * PARAMS["mill"]["setpoint_max_factor"]),
          "truck_availability_pct": (90.0, 98.0), "annual_ore_mt": (30.4, 30.9 * PARAMS["mill"]["setpoint_max_factor"]),
          "annual_cu_kt": (0.9 * CU_LINE_KT, 1.2 * CU_LINE_KT)},
}
SHOWN = ("trips", "queue_min_per_trip", "tonnes_moved", "ore_mined_t", "waste_mined_t", "rom_pads_t", "ore_milled_t",
         "grade_in_band_pct", "cu_t", "mill_stops", "breakdowns", "workshop_jobs", "blasts", "blocks_dug_out", "messages")


def annual(r: dict) -> dict:
    """The week's figures as a year of the line: the mill's hours times the plant's share of the year."""
    tph = lambda total: total / r["hours"]
    rock_tph = tph(r["ore_mined_t"] + r["waste_mined_t"])
    return {
        **r,
        "annual_ore_mt": tph(r["ore_milled_t"]) * HOURS_A_YEAR / 1e6,
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
