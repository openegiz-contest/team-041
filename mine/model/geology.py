"""Block model of the modelled Aktogay pit slice: copper grade and ore hardness.

Both are smooth random fields over the mine grid, so neighbouring blocks are
alike and a loader moving along a bench sees the grade drift, not jump. The
grade field is turned into grades by rank, in three classes whose shares
come from KAZ Minerals' figures (params.yaml, group `geology`):

    waste      below the cut-off grade; its share follows the strip ratio
    low-grade  from the cut-off to `low_grade_below_pct`; goes to the low-grade
               stockpile, as the sulphide ore mined beyond what the
               concentrators process does at Aktogay; its share is that excess
    ore        above it, log-normal around the mean head grade of the mill feed

The same ranking prices the blocks of every later pushback, so the mine
keeps its strip ratio, its low-grade share and its head grade for as long as
it runs.

Every number comes from params.yaml (group `geology`) or world.yaml.
"""
from __future__ import annotations

import bisect
import math
import random
from statistics import NormalDist

NORMAL = NormalDist()


class Field:
    """Stationary field with unit variance: a sum of plane waves of random
    direction and phase with wavelengths between `shortest_m` and `longest_m`."""

    def __init__(self, seed: str, shortest_m: float, longest_m: float, waves: int = 24):
        rng = random.Random(seed)
        self.waves = []
        for _ in range(waves):
            direction = rng.uniform(0.0, math.pi)
            k = 2.0 * math.pi / rng.uniform(shortest_m, longest_m)
            self.waves.append((k * math.cos(direction), k * math.sin(direction), rng.uniform(0.0, 2.0 * math.pi)))
        self.scale = math.sqrt(2.0 / waves)

    def at(self, x: float, y: float) -> float:
        return self.scale * sum(math.cos(kx * x + ky * y + phase) for kx, ky, phase in self.waves)


class BlockModel:
    """Grade, hardness and material class of a block from where it lies."""

    def __init__(self, world: dict, params: dict, cells: list[dict]):
        """`cells` are the blocks of the first cut: {bench, x, y}."""
        self.p = params["geology"]
        geology = world["geology"]
        self.shift_m = geology["pushback_shift_m"]
        self.trend = {b["id"]: b["grade_trend"] for b in world["benches"]}
        self.waste_dump = "waste-dump"
        self.low_grade_pad = next(pad["id"] for pad in world["rom_pads"] if pad["role"] == "low-grade")
        shortest = self.p["correlation_length_m"] * self.p["wavelength_min_factor"]
        longest = self.p["correlation_length_m"] * self.p["wavelength_max_factor"]
        self.grade_field = Field(f"{geology['seed']}/grade", shortest, longest)
        self.hardness_field = Field(f"{geology['seed']}/hardness", shortest, longest)
        # The scores of the first cut are the yardstick for every later one.
        self.reference = sorted(self._score(c["bench"], c["x"], c["y"], 0) for c in cells)

    def _sample(self, field: Field, x: float, y: float, generation: int) -> float:
        offset = generation * self.shift_m
        return field.at(x + offset, y - offset)

    def _score(self, bench: str, x: float, y: float, generation: int) -> float:
        return self._sample(self.grade_field, x, y, generation) + self.trend[bench]

    def _quantile(self, score: float) -> float:
        """Share of the first cut's blocks that score lower, in (0, 1)."""
        below = bisect.bisect_left(self.reference, score - 1e-12)
        return (below + 0.5) / len(self.reference)

    def shares(self) -> tuple[float, float]:
        """Shares of waste and of low-grade ore in the rock mined."""
        p = self.p
        waste = p["strip_ratio"] / (1.0 + p["strip_ratio"])
        low = (1.0 - waste) * p["low_grade_share"] / (1.0 + p["low_grade_share"])
        return waste, low

    def grade_pct(self, quantile: float) -> float:
        p = self.p
        cutoff, upper = p["cutoff_grade_pct"], p["low_grade_below_pct"]
        waste_share, low_share = self.shares()
        if quantile < waste_share:
            low, high = p["waste_grade_min_share"], p["waste_grade_max_share"]
            return cutoff * (low + (high - low) * quantile / waste_share)
        if quantile < waste_share + low_share:
            # Low-grade ore spread evenly over its band, a little inside both edges.
            f = (quantile - waste_share) / low_share
            return cutoff + (upper - cutoff) * (0.02 + 0.96 * f)
        q = min(max((quantile - waste_share - low_share) / (1.0 - waste_share - low_share), 0.02), 0.98)
        sigma = p["ore_grade_log_sigma"]
        mu = math.log(p["mean_head_grade_pct"] - upper) - sigma ** 2 / 2.0
        return upper + math.exp(mu + sigma * NORMAL.inv_cdf(q))

    def material(self, grade_pct: float) -> str:
        if grade_pct < self.p["cutoff_grade_pct"]:
            return "waste"
        return "low-grade" if grade_pct < self.p["low_grade_below_pct"] else "ore"

    def block(self, bench: str, x: float, y: float, generation: int = 0) -> dict:
        """Properties of the block centred on (x, y) in the given pushback."""
        p = self.p
        grade = round(self.grade_pct(self._quantile(self._score(bench, x, y, generation))), 3)
        hardness = p["bwi_mean_kwh_t"] + p["bwi_sd_kwh_t"] * self._sample(self.hardness_field, x, y, generation)
        hardness = round(min(max(hardness, p["bwi_min_kwh_t"]), p["bwi_max_kwh_t"]), 1)
        material = self.material(grade)
        return {"grade_pct": grade, "hardness_bwi": hardness, "material": material,
                "destination": self.destination(material)}

    def destination(self, material: str) -> str:
        """The base rule of the mine: ore to the crusher, low-grade ore to the low-grade stockpile, waste to the dump."""
        if material == "waste":
            return self.waste_dump
        return self.low_grade_pad if material == "low-grade" else "crusher"
