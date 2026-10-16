"""Haul-truck motion and fuel from nameplate values.

The engine works against grade and rolling resistance and accelerates the
truck with whatever power is left; on a steep descent the retarder holds the
speed; everywhere the road's speed limit caps it. Fuel follows the power the
engine actually delivers: power x specific fuel consumption / diesel density.
"""
from __future__ import annotations

G = 9.81  # m/s2


def resistance_kn(mass_t: float, grade_pct: float, rr_pct: float) -> float:
    """Force along the road that the wheels must overcome; negative when gravity pulls ahead."""
    return mass_t * G * (grade_pct + rr_pct) / 100.0          # t x m/s2 = kN


def speed_cap_kmh(truck: dict, loaded: bool, grade_pct: float, limit_kmh: float) -> float:
    """Highest speed allowed here: the road limit, the truck's top speed, the retarder on a descent."""
    cap = min(limit_kmh, truck["max_speed_kmh"])
    if grade_pct < -truck["downhill_threshold_pct"]:
        cap = min(cap, truck["downhill_loaded_kmh"] if loaded else truck["downhill_empty_kmh"])
    return cap


def steady_speed_kmh(truck: dict, mass_t: float, loaded: bool, grade_pct: float, rr_pct: float,
                     limit_kmh: float) -> float:
    """Speed a truck of `mass_t` settles at on a long stretch of this grade."""
    cap = speed_cap_kmh(truck, loaded, grade_pct, limit_kmh)
    force = resistance_kn(mass_t, grade_pct, rr_pct)
    if force > 0:
        cap = min(cap, truck["engine_kw"] * truck["drivetrain_efficiency"] / force * 3.6)   # kW / kN = m/s
    return cap


def advance(truck: dict, mass_t: float, grade_pct: float, rr_pct: float,
            speed_ms: float, cap_ms: float, dt: float) -> tuple[float, float]:
    """One time step of driving: return the new speed (m/s) and the engine power used (kW)."""
    # Called for every moving truck every simulated second: plain comparisons instead of min/max, same results.
    if speed_ms > cap_ms:
        slowed = speed_ms - truck["brake_ms2"] * dt
        return (slowed if slowed > cap_ms else cap_ms), 0.0
    force = mass_t * G * (grade_pct + rr_pct) / 100.0                     # resistance_kn
    efficiency, engine_kw = truck["drivetrain_efficiency"], truck["engine_kw"]
    pull = engine_kw * efficiency / (speed_ms if speed_ms >= 1.0 else 1.0)   # kN available at the wheels
    accel, accel_max = (pull - force) / mass_t, truck["accel_max_ms2"]
    if not accel < accel_max:
        accel = accel_max
    new_speed = speed_ms + accel * dt
    if new_speed < 0.3:
        new_speed = 0.3
    if not new_speed < cap_ms:
        new_speed = cap_ms
    wheel_kw = (force + mass_t * (new_speed - speed_ms) / dt) * (speed_ms + new_speed) / 2.0
    used_kw = (0.0 if wheel_kw < 0.0 else wheel_kw) / efficiency
    return new_speed, (used_kw if used_kw < engine_kw else engine_kw)


def load_factor(truck: dict, engine_kw: float) -> float:
    """Share of rated power the engine delivers; never below the idle load."""
    return max(truck["idle_load_factor"], engine_kw / truck["engine_kw"])


def fuel_rate_lph(truck: dict, engine_kw: float) -> float:
    return (load_factor(truck, engine_kw) * truck["engine_kw"]
            * truck["bsfc_g_kwh"] / 1000.0 / truck["diesel_density_kg_l"])
