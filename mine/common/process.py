"""Process relations of the concentrator that more than one service needs:
the simulator applies them, the plant-control module predicts with them.
Every constant comes from params.yaml."""
from __future__ import annotations

import math


def recovery_pct(params: dict, grade_pct: float, bwi: float, feed_tph: float | None = None) -> float:
    """Copper recovery of the flotation plant, %: rises with the head grade,
    falls on harder ore and when the flow exceeds the design rate (less time
    in the cells)."""
    p, mill = params["flotation"], params["mill"]
    value = (p["recovery_ref_pct"]
             + p["grade_gain_pct"] * math.log(max(grade_pct, 1e-3) / params["geology"]["mean_head_grade_pct"])
             - p["hardness_loss_pct"] * (bwi / mill["bwi_ref_kwh_t"] - 1.0))
    if feed_tph is not None and feed_tph > mill["design_tph"]:
        value -= p["overfeed_loss_pct"] * (feed_tph / mill["design_tph"] - 1.0)
    return min(p["recovery_max_pct"], max(p["recovery_min_pct"], value))


def wear_pct(params: dict, hours_since_service: float) -> float:
    """Condition lost to wear since the last service, in points of health."""
    rel = params["reliability"]
    return rel["wear_pct_per_pm_interval"] * hours_since_service / rel["pm_interval_h"]
