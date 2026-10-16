"""Process relations of the two plants that more than one service needs:
the simulator applies them, the plant-control and grade-control modules
predict with them, the forecast values ore with them. Every constant comes
from params.yaml."""
from __future__ import annotations

import math


def recovery_pct(params: dict, grade_pct: float, bwi: float, feed_tph: float | None = None,
                 clay_pct: float | None = None) -> float:
    """Copper recovery of the sulphide plant ОФ-1, %: rises with the head grade,
    falls on harder ore, on clay in the feed (slimes coat the sulphides) and when
    the flow exceeds the design rate (less time in the cells)."""
    p, mill = params["flotation"], params["mill"]
    value = (p["recovery_ref_pct"]
             + p["grade_gain_pct"] * math.log(max(grade_pct, 1e-3) / params["geology"]["mean_head_grade_pct"])
             - p["hardness_loss_pct"] * (bwi / mill["bwi_ref_kwh_t"] - 1.0))
    if clay_pct is not None:
        value -= p["clay_loss_pct"] * (clay_pct - p["clay_ref_pct"])
    if feed_tph is not None and feed_tph > mill["design_tph"]:
        value -= p["overfeed_loss_pct"] * (feed_tph / mill["design_tph"] - 1.0)
    return min(p["recovery_max_pct"], max(p["recovery_min_pct"], value))


def clay_handling(params: dict, clay_pct: float) -> float:
    """Share of the power-limited feed ОФ-1 passes with this much clay in its feed: 1 up to the reference clay,
    then `mill.clay_handling_slope` % less per point of clay, not below `mill.clay_handling_min`."""
    mill, ref = params["mill"], params["flotation"]["clay_ref_pct"]
    return max(float(mill["clay_handling_min"]), 1.0 - float(mill["clay_handling_slope"]) / 100.0 * max(0.0, clay_pct - ref))


def clay_plant_split(params: dict, grade_pct: float, clay_pct: float) -> dict:
    """The clay-ore plant ОФ-2: the desliming cyclones send the clay and the fines to the slimes, the sands go to
    their own flotation, the slimes to slime flotation. Returns the slimes' share of the solids and of the copper,
    the recovery of each flotation and of the plant, %."""
    c = params["clay_plant"]
    slimes = min(0.7, float(c["slimes_base_share"]) + max(clay_pct, 0.0) / 100.0)
    slimes_cu = min(1.0, slimes * float(c["slimes_cu_ratio"]))
    gain = float(params["flotation"]["grade_gain_pct"])
    sand = float(c["sand_recovery_ref_pct"]) + gain * math.log(max(grade_pct, 1e-3) / float(c["mean_feed_grade_pct"]))
    sand = min(92.0, max(40.0, sand))
    slime = float(c["slime_recovery_pct"])
    return {"slimes_share": slimes, "slimes_cu_share": slimes_cu, "sand_recovery_pct": sand,
            "slime_recovery_pct": slime, "recovery_pct": (1.0 - slimes_cu) * sand + slimes_cu * slime}


def clay_plant_recovery_pct(params: dict, grade_pct: float, clay_pct: float) -> float:
    return clay_plant_split(params, grade_pct, clay_pct)["recovery_pct"]


def wear_pct(params: dict, hours_since_service: float) -> float:
    """Condition lost to wear since the last service, in points of health."""
    rel = params["reliability"]
    return rel["wear_pct_per_pm_interval"] * hours_since_service / rel["pm_interval_h"]
