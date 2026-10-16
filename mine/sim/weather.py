"""Weather over the mine, from the climate normals of the Ekibastuz station
(1991-2020, the nearest station, 73 km east of the mine) for the simulated date.

Temperature: the normal daily mean, maximum and minimum of a month belong
to its middle; between two middles they are interpolated by the day. Through
the day the air follows a cosine between the day's minimum and maximum,
warmest at `weather.warmest_hour`.

Precipitation: every day, with the chance the normals give (days with at
least 1 mm / days of the month), the precipitation of an average wet day
(monthly amount / wet days) falls within `weather.wet_event_h` hours, as
rain or, below `weather.snow_below_c`, as wet snow. It wets every road
(lower speed limits, more rolling resistance); after it the roads dry for
`road.drying_min`.

The heavy-precipitation scenario overrides this with heavy rain and wet
snow at a rate it names (mine/sim/scenarios.py, HeavyRain); the scenario
also closes the steep ramp, the ordinary precipitation does not.
"""
from __future__ import annotations

import calendar
import math
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from mine.sim.text import num

if TYPE_CHECKING:
    from mine.sim.model import Simulation

# Months whose normals params.yaml carries (group `weather`), with the prefix of their entries.
MONTHS = ((10, "oct"), (11, "nov"))
BASIS = ("Нормы 1991–2020, станция Экибастуз (Pogoda.ru.net, NOAA), 73 км к востоку от рудника: средние температуры, "
         "максимум, минимум и осадки октября и ноября")


def normals(p: dict, moment: datetime) -> dict:
    """The climate normals for the day of `moment`: mean, max, min (°C), mm on a wet day, chance of a wet day.
    The values of a month belong to its 15th; before the first and after the last month the nearest one holds."""
    anchors = [(datetime(moment.year, m, 15), key, calendar.monthrange(moment.year, m)[1]) for m, key in MONTHS]
    if moment <= anchors[0][0]:
        f, a, b = 0.0, anchors[0], anchors[0]
    elif moment >= anchors[-1][0]:
        f, a, b = 0.0, anchors[-1], anchors[-1]
    else:
        a, b = anchors[0], anchors[1]
        f = (moment - a[0]) / (b[0] - a[0])
    mix = lambda field: p[f"{a[1]}_{field}"] * (1 - f) + p[f"{b[1]}_{field}"] * f
    wet_mm = mix("precip_mm") / max(mix("wet_days"), 0.1)
    chance = (p[f"{a[1]}_wet_days"] / a[2]) * (1 - f) + (p[f"{b[1]}_wet_days"] / b[2]) * f
    return {"mean_c": mix("mean_c"), "max_c": mix("max_c"), "min_c": mix("min_c"), "wet_mm": wet_mm, "wet_chance": chance}


def air_c(p: dict, moment: datetime) -> float:
    """Air temperature at `moment`: a cosine between the normal minimum and maximum of the day."""
    n = normals(p, moment)
    hour = moment.hour + moment.minute / 60.0
    return (n["max_c"] + n["min_c"]) / 2 + (n["max_c"] - n["min_c"]) / 2 * math.cos(
        2.0 * math.pi * (hour - p["warmest_hour"]) / 24.0)


class Weather:
    def __init__(self, sim: "Simulation"):
        self.sim = sim
        self.p = sim.params["weather"]
        self.condition = "dry"                  # dry / rain / snow
        self.rain_mm_h = 0.0
        self.heavy = False                      # the heavy-precipitation scenario is on
        self.wet = False                        # state of the road surface
        self.dry_at: float | None = None        # when wet roads will have dried after the precipitation
        self.event: tuple[float, float, float] | None = None    # ordinary precipitation today: (start s, end s, mm/h)
        self.planned_day = -1
        self.temperature_c = air_c(self.p, sim.epoch)
        self.today = normals(self.p, sim.epoch)
        self._plan_day((sim.epoch - datetime(sim.epoch.year, 1, 1)).days)

    @property
    def drying(self) -> bool:
        return self.wet and self.condition == "dry"

    def _moment(self, at: float | None = None) -> datetime:
        return self.sim.epoch + timedelta(seconds=self.sim.now if at is None else at)

    # -- the heavy-precipitation scenario ----------------------------------------------

    def set_heavy(self, on: bool, rain_mm_h: float | None = None) -> None:
        """Start or end heavy rain with wet snow. It wets the roads at once; after it they dry for `road.drying_min`."""
        sim = self.sim
        if on == self.heavy:
            return
        self.heavy = on
        if on:
            self.rain_mm_h = float(rain_mm_h or self.p["rain_heavy_mm_h"])
            self.condition = "rain"
            self._wet()
            sim.log("warning", "weather", f"Сильный дождь с мокрым снегом, {self.rain_mm_h:.0f} мм/ч: дороги мокрые, "
                                          f"ограничения скорости снижены, сопротивление качению выросло")
        else:
            self._precipitation_ends("Сильные осадки прекратились")

    def _wet(self) -> None:
        sim = self.sim
        self.dry_at = None
        if not self.wet:
            self.wet = True
            sim.roads.set_surface(wet=True)
            sim.fleet.roads_changed()

    def _precipitation_ends(self, text: str) -> None:
        sim = self.sim
        self.condition, self.rain_mm_h = "dry", 0.0
        self.dry_at = sim.now + sim.params["road"]["drying_min"] * 60.0
        sim.log("info", "weather", f"{text}: дороги просохнут через {sim.params['road']['drying_min']:.0f} мин")

    # -- ordinary precipitation ----------------------------------------------------------

    def _plan_day(self, day: int) -> None:
        """Decide at midnight whether, when and how much it rains or snows on this day."""
        self.planned_day = day
        midnight = self._moment().replace(hour=0, minute=0, second=0, microsecond=0)
        n = self.today = normals(self.p, midnight + timedelta(hours=12))
        rng = self.sim.rng(f"weather/{day}")            # each day draws on a stream of its own
        if rng.random() >= n["wet_chance"]:
            self.event = None
            return
        hours = float(self.p["wet_event_h"])
        start = (midnight - self.sim.epoch).total_seconds() + rng.uniform(0.0, 24.0 - hours) * 3600.0
        self.event = (start, start + hours * 3600.0, n["wet_mm"] / hours)

    def step(self, dt: float) -> None:
        sim = self.sim
        if self.dry_at is not None and sim.now >= self.dry_at and self.condition == "dry":
            self.wet, self.dry_at = False, None
            sim.roads.set_surface(wet=False)
            sim.fleet.roads_changed()
            sim.log("info", "weather", "Дороги просохли: ограничения скорости восстановлены")
        if int(sim.now) % 60:
            return
        moment = self._moment()
        day = (moment - datetime(moment.year, 1, 1)).days
        if day != self.planned_day:
            self._plan_day(day)
        self.temperature_c = air_c(self.p, moment)
        if self.heavy:
            return
        falling = self.event is not None and self.event[0] <= sim.now < self.event[1]
        if falling and self.condition == "dry":
            self.rain_mm_h = round(self.event[2], 1)
            self.condition = "snow" if self.temperature_c < self.p["snow_below_c"] else "rain"
            self._wet()
            what = "мокрый снег" if self.condition == "snow" else "дождь"
            sim.log("info", "weather", f"Слабые осадки ({what}, {num(self.rain_mm_h)} мм/ч, около "
                                       f"{self.p['wet_event_h']:.0f} ч): дороги мокрые, ограничения скорости снижены")
        elif not falling and self.condition != "dry":
            self._precipitation_ends("Осадки прекратились")

    # -- published --------------------------------------------------------------------------

    def precip_class(self) -> str:
        if self.heavy:
            return "heavy"
        return "light" if self.condition != "dry" else "none"

    def values(self) -> dict[str, dict]:
        visibility = {"heavy": "visibility_rain_m", "light": "visibility_light_m", "none": "visibility_dry_m"}
        n = self.today
        return {"weather": {
            "condition": self.condition,
            "rain_mm_h": self.rain_mm_h,
            "precip_class": self.precip_class(),
            "road_surface": "wet" if self.wet else "dry",
            "visibility_m": float(self.p[visibility[self.precip_class()]]),
            "temperature_c": round(self.temperature_c, 1),
            "normal_mean_c": round(n["mean_c"], 1),
            "normal_max_c": round(n["max_c"], 1),
            "normal_min_c": round(n["min_c"], 1),
            "wet_day_chance_pct": round(100.0 * n["wet_chance"], 1),
        }}
