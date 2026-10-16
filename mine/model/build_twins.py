"""Generate the Ditto things of the Bozshakol mine model from world.yaml and params.yaml.

This module is the one place where a twin is defined: its ID, attributes,
links, feature names, units and initial values. The setup job creates the
things it returns, the simulator takes its topology, its starting state and
the units of its telemetry from them, and the stand check compares Ditto
against the same list.

    python -m mine.model.build_twins            # all things as a JSON array
    python -m mine.model.build_twins --ids      # thing IDs, one per line
    python -m mine.model.build_twins --kinds    # number of twins of each kind
    python -m mine.model.build_twins --roads    # length and grade of every road segment
"""
from __future__ import annotations

import argparse
import json
import math
import os
from collections import Counter
from datetime import datetime
from pathlib import Path

import yaml

from mine.common.economics import Economics
from mine.common.process import clay_plant_split, wear_pct
from mine.model.geology import BlockModel
from mine.model.layout import Rim, expand
from mine.sim.durations import causes as scenario_causes
from mine.sim.weather import BASIS as CLIMATE_BASIS, air_c, normals

MODEL_DIR = Path(__file__).resolve().parent
ADVISOR_OPTIONS = 4                 # response options the advisor publishes at most: one per advisor forecast slot
SCENARIO_KPIS = 4                   # indicators along the causal chain of a scenario
HOME_LABEL = "Место на начало работы модели; текущее положение — признаки pos_x, pos_y"
PIT_DEPTH_BASIS = ("Глубина рабочего дна 225 м (отметка +5 м) — допущение модели по плану горных работ: второй этап "
                   "углубки (2018–2024) — до отметки 0 м, третий (2021–2033) — до −40 м; горный отвод — 8,19 км², "
                   "глубина 430 м; карьер по верху 4070 × 880 м на 2023 год (заявление о намечаемой деятельности, "
                   "ecoportal 29174)")
# Contributions to the advisor's difference against «change nothing», in the order they are published.
# "resources": what the shift's response resources of the option cost (mine/sim/response.py).
MARGIN_SPLIT = ("revenue", "grade", "work", "rehandle", "diesel", "power", "resources")
STATUSES = ("sourced", "snippet", "derived", "assumption")

# Units reused across kinds.
T, TPH, PCT, CU, MIN, M, KMH, KW, MW, BWI, MM, DEG_C, H, KZT, KZT_H = (
    "t", "t/h", "%", "% Cu", "min", "m", "km/h", "kW", "MW", "kWh/t", "mm", "°C", "h", "KZT", "KZT/h")


def load_world(path: Path | None = None, params: dict | None = None) -> dict:
    """world.yaml with its generated geometry (mine/model/layout.py). The namespace is
    MINE_NAMESPACE from the environment when that is set, else the one in the file."""
    with open(path or MODEL_DIR / "world.yaml", encoding="utf-8") as f:
        world = yaml.safe_load(f)
    namespace = os.environ.get("MINE_NAMESPACE", "").strip()
    if namespace:
        world["namespace"] = namespace
    return expand(world, params or load_params())


def load_params_raw(path: Path | None = None) -> dict:
    """params.yaml as written: {group: {name: {value, unit, source, url, status, title, note}}}.
    Keys that start with an underscore hold shared source records, not parameters."""
    with open(path or MODEL_DIR / "params.yaml", encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    groups = {group: entries for group, entries in raw.items() if not group.startswith("_")}
    for group, entries in groups.items():
        for name, entry in entries.items():
            where = f"params.yaml: {group}.{name}"
            missing = {"value", "unit", "source", "url", "status", "title"} - set(entry)
            if missing:
                raise ValueError(f"{where} lacks {sorted(missing)}")
            if not str(entry["source"]).strip():
                raise ValueError(f"{where} has an empty source")
            if entry["status"] not in STATUSES:
                raise ValueError(f"{where}: status {entry['status']!r} is not one of {STATUSES}")
            if entry["status"] in ("derived", "assumption") and not str(entry.get("note", "")).strip():
                raise ValueError(f"{where}: a {entry['status']} value needs a note with its reasoning")
            if entry["status"] in ("sourced", "snippet") and not str(entry["url"]).startswith("http"):
                raise ValueError(f"{where}: a {entry['status']} value needs the URL of its source")
    return groups


def load_params(path: Path | None = None) -> dict:
    """Return {group: {name: value}}; every entry is checked for its unit, source and status."""
    return {group: {name: entry["value"] for name, entry in entries.items()}
            for group, entries in load_params_raw(path).items()}


# -- geometry ---------------------------------------------------------------------

def polygon_area(points: list) -> float:
    """Shoelace area of a simple polygon given as [[x, y], ...], in m2."""
    total = 0.0
    for (x1, y1), (x2, y2) in zip(points, points[1:] + points[:1]):
        total += x1 * y2 - x2 * y1
    return abs(total) / 2


def path_length(path: list) -> float:
    """Length of a 3D polyline [[x, y, z], ...], in m."""
    return sum(math.dist(a, b) for a, b in zip(path, path[1:]))


def outline(shape, centre: list, points: int = 36) -> list:
    """A polygon as written, or the polygon of an `ellipse` or a `stadium` around `centre`."""
    if isinstance(shape, list):
        return shape
    return Rim(shape).polygon(centre, points)


# -- derived quantities --------------------------------------------------------------

def bucket_load_t(params: dict) -> float:
    """Mean tonnes in one bucket pass."""
    loader, rock = params["loader"], params["rock"]
    return loader["bucket_m3"] * loader["fill_factor"] * rock["loose_density_t_m3"]


def nominal_passes(params: dict) -> int:
    """Bucket passes that bring a truck closest to its rated payload."""
    return round(params["truck"]["payload_t"] / bucket_load_t(params))


def holes_per_block(params: dict, area_m2: float) -> int:
    drill = params["drill"]
    return round(area_m2 / (drill["burden_m"] * drill["spacing_m"]))


def full_power_fuel_lph(params: dict) -> float:
    """Fuel burned at rated engine power: power x BSFC / diesel density."""
    truck = params["truck"]
    return truck["engine_kw"] * truck["bsfc_g_kwh"] / 1000.0 / truck["diesel_density_kg_l"]


class TwinBuilder:
    def __init__(self, world: dict, params: dict):
        self.world = world
        self.params = params
        self.ns = world["namespace"]
        self.things: list[dict] = []
        self.centre = world["pit"]["centre"]
        # Blocks are generated here once and shared by the methods below.
        self.block_defs = self._block_defs()
        elevation = {b["id"]: b["elevation_m"] for b in world["benches"]}
        self.block_ref = {f"{elevation[b['bench']]:.0f}/{b['number']}": b["id"] for b in self.block_defs}

    def tid(self, name: str) -> str:
        return f"{self.ns}:{name}"

    def tids(self, names) -> list[str]:
        return [self.tid(n) for n in names]

    def names(self, key: str) -> list[str]:
        return [item["id"] for item in self.world[key]]

    def add(self, name, kind, title, parent, features, links=None, geo=None, spec=None, home=None, extra=None):
        """Append one thing. `features` maps a feature name to (initial value, unit).
        A machine that moves has no `geo`: its live position is in its features
        (pos_x, pos_y), and `home` gives where it stood when the model started."""
        attributes = {"name": title, "kind": kind, "_isType": False}
        if parent:
            attributes["_parents"] = self.tid(parent)
        attributes["links"] = links or {}
        if home is not None:
            attributes["home"] = {**home, "label": HOME_LABEL}
        else:
            attributes["geo"] = geo or {}
        attributes["spec"] = spec or {}
        attributes.update(extra or {})
        props = {}
        for feature, (value, unit) in features.items():
            if isinstance(value, (list, dict)):
                raise TypeError(f"{name}.{feature}: feature values must be scalar")
            props[feature] = {"properties": {"value": value, "unit": unit} if unit else {"value": value}}
        self.things.append({
            "thingId": self.tid(name),
            "policyId": self.world["policy"],
            "attributes": attributes,
            "features": props,
        })

    def _block_defs(self) -> list[dict]:
        defs = []
        for bench in self.world["benches"]:
            if not bench["working"]:
                continue
            cells = bench["cells"]
            initial = bench["blocks"]["initial"]
            if len(initial) != len(cells):
                raise ValueError(f"{bench['id']}: {len(cells)} blocks but {len(initial)} initial states")
            for number, (cell, state) in enumerate(zip(cells, initial), start=1):
                state = {"status": state} if isinstance(state, str) else state
                defs.append({**cell, "bench": bench["id"], "number": number,
                             "id": f"block-{len(defs) + 1:02d}",
                             "name": f"Блок {bench['elevation_m']:.0f}-{number:02d}",
                             "status": state["status"], "remaining_pct": state.get("remaining_pct", 100)})
        model = BlockModel(self.world, self.params, defs)
        for d in defs:
            d.update(model.block(d["bench"], d["x"], d["y"]))
        return defs

    # -- one method per kind, parents before children --------------------------

    def build(self) -> list[dict]:
        self.mine()
        self.pit()
        self.benches()
        self.blocks()
        self.road_nodes()
        self.road_segments()
        self.substation()
        self.drill_rigs()
        self.loaders()
        self.trucks()
        self.plant()
        self.dumps()
        self.services()
        self.weather()
        self.modules()
        self.scenarios()
        self.forecasts()
        self.journal()
        return self.things

    def mine(self):
        w = self.world["mine"]
        self.add("mine", "mine", w["name"], None, {
            "sim_time": (self.world["clock"]["shift_start"], ""),
            "sim_state": ("running", ""),
            "speedup": (self.params["sim"]["default_speedup"], "x"),
            "shift": ("day", ""),
            # Since the current shift began (they start again from zero at 08:00 and 20:00).
            "cu_shift_t": (0.0, T),
            "ore_shift_t": (0, T),
            "waste_shift_t": (0, T),
            "milled_shift_t": (0, T),
            "shift_start_sim_time": (self.world["clock"]["shift_start"], ""),
            # Since the model started.
            "ore_mined_t": (0.0, T),
            "waste_mined_t": (0.0, T),
            "strip_ratio": (0.0, "t/t"),
            "mill_feed_tph": (0.0, TPH),
            # Both plants together, as KAZ Minerals reports the mine.
            "processed_tph": (0.0, TPH),
            "head_grade_pct": (0.0, CU),
            "recovery_pct": (0.0, PCT),
            # Each plant on its own, and the clay in the feed of ОФ-1.
            "ofs1_head_grade_pct": (0.0, CU),
            "ofs1_recovery_pct": (0.0, PCT),
            "ofs1_clay_pct": (0.0, PCT),
            "ofs2_feed_tph": (0.0, TPH),
            "ofs2_head_grade_pct": (0.0, CU),
            "ofs2_recovery_pct": (0.0, PCT),
            "ofs2_cu_tph": (0.0, TPH),
            # Clay ore dug since the model started, its share of the ore, and what lies on the clay-ore stockpile.
            "clay_mined_t": (0, T),
            "clay_share_pct": (0.0, PCT),
            "clay_stock_t": (0, T),
            "cu_produced_t": (0.0, T),
            "avg_cycle_min": (0.0, MIN),
            "avg_queue_min": (0.0, MIN),
            "fleet_util_pct": (0.0, PCT),
            "fleet_avail_pct": (0.0, PCT),
            "trucks_active": (len(self.world["trucks"]), ""),
            "diesel_l_per_t": (0.0, "l/t"),
            "diesel_l_per_engine_h": (0.0, "l/h"),
            "energy_kwh_per_t": (0.0, "kWh/t"),
            # Money and CO2 over the KPI window (mine/common/economics.py).
            "cu_tph": (0.0, TPH),
            "diesel_l_h": (0.0, "l/h"),
            "energy_mwh_h": (0.0, "MWh/h"),
            "revenue_kzt_h": (0, KZT_H),
            "diesel_cost_kzt_h": (0, KZT_H),
            "power_cost_kzt_h": (0, KZT_H),
            "margin_kzt_h": (0, KZT_H),
            # Ore the wheel loader feeds from the ROM pads to the crusher, and what that rehandling costs.
            "rehandle_t_h": (0.0, TPH),
            "rehandle_cost_kzt_h": (0, KZT_H),
            "co2_t_h": (0.0, "t/h"),
            # What the shift's response resources (second crews, a grader, a road crew) cost since the model started.
            "response_cost_kzt": (0, KZT),
        }, links={"pit": self.tid("pit"), "crusher": self.tid("crusher"), "mill": self.tid("mill"),
                  "clay_plant": self.tid("clay-plant"), "clay_stockpile": self.tid("clay-stockpile"),
                  "concentrate": self.tid("concentrate"), "tailings": self.tid("tailings"),
                  "substation": self.tid("substation"), "weather": self.tid("weather"),
                  "journal": self.tid("journal"), "modules": self.tids(self.names("modules")),
                  "scenarios": self.tids(self.names("scenarios")), "forecasts": self.tids(self.forecast_names())},
            geo={"polygon": w["boundary"], "z": w["surface_z"]}, extra={"about": w["about"]},
            spec={"mill_design_tph": self.params["mill"]["design_tph"],
                  "clay_plant_design_tph": self.params["clay_plant"]["design_tph"],
                  "annual_ore_mt": round((self.params["mill"]["design_tph"] + self.params["clay_plant"]["design_tph"])
                                         * 8760 * self.params["plan"]["plant_availability"] / 1e6, 1),
                  "trucks": len(self.world["trucks"]), "loaders": len(self.world["loaders"]),
                  **Economics(self.params).basis()})

    def pit(self):
        w = self.world["pit"]
        benches = self.world["benches"]
        floor = min(b["elevation_m"] for b in benches)
        blast, info = self.params["blast"], self.world["wall_info"]
        self.add("pit", "pit", w["name"], "mine", {
            "active_faces": (len(self.world["loaders"]), ""),
            "depth_m": (float(w["rim_z"] - floor), M),
            "broken_stock_t": (0.0, T),
            "blast_state": ("idle", ""),
            "blast_block": ("", ""),
            "blast_radius_m": (0.0, M),
            "blast_source": ("", ""),
            "next_blast_sim_time": ("", ""),
        }, links={"mine": self.tid("mine"), "benches": self.tids(b["id"] for b in benches),
                  "loaders": self.tids(self.names("loaders")), "drill_rigs": self.tids(self.names("drill_rigs"))},
            geo={"polygon": outline(w["rim"], self.centre, 72), "z": w["rim_z"]},
            spec={"rim_elevation_m": w["rim_z"], "floor_elevation_m": floor, "depth_m": info["depth_m"],
                  "rim_length_m": Rim(w["rim"]).length_m, "rim_width_m": Rim(w["rim"]).width_m,
                  "benches": info["benches"], "bench_height_m": self.params["drill"]["bench_height_m"],
                  "face_angle_deg": self.params["pit"]["face_angle_deg"], "berm_m": info["berm_m"],
                  "final_wall_angle_deg": info["overall_angle_deg"],
                  "depth_basis": PIT_DEPTH_BASIS,
                  "ramps": "; ".join(f"{r['name']}: {r['length_m']} м, уклон {r['grade_pct']:.0f} %, разворотов {r['switchbacks']}"
                                     for r in self.world["ramp_info"]),
                  "blast_exclusion_radius_m": blast["exclusion_radius_m"],
                  "blast_reentry_min": blast["reentry_min"],
                  "blast_times": ", ".join(self.world["clock"]["blast_times"]),
                  "extra_blast_times": ", ".join(self.world["clock"].get("extra_blast_times", [])),
                  "blast_rule": "Массовые взрывы только днём, не больше одного в сутки; второе дневное окно — "
                                "только если взорванной горной массы в забоях меньше чем на "
                                f"{self.params['blast']['extra_window_below_h']:.0f} ч выемки"})

    def benches(self):
        height = self.params["drill"]["bench_height_m"]
        for b in self.world["benches"]:
            blocks = [k for k in self.block_defs if k["bench"] == b["id"]]
            links = {"pit": self.tid("pit"), "blocks": self.tids(k["id"] for k in blocks)}
            if b.get("access_node"):
                links["access_node"] = self.tid(b["access_node"])
            geo = {"polygon": b["outline"], "z": b["elevation_m"]}
            if b.get("crest"):
                geo["crest"] = b["crest"]           # inner edge of the bench floor: the crest of the face below
            last = b["number"] == len(self.world["benches"])
            self.add(b["id"], "bench", b["name"], "pit", {
                "elevation_m": (float(b["elevation_m"]), M),
                "blocks_remaining": (sum(1 for k in blocks if k["status"] != "depleted"), ""),
                "tonnes_remaining": (0.0, T),
                "pushback": (1, ""),
            }, links=links, geo=geo,
                spec={"height_m": height, "number_from_top": b["number"], "working": b["working"],
                      "role": "дно карьера" if last else ("рабочий уступ" if b["working"] else "уступ конечного борта")})

    def blocks(self):
        benches = {b["id"]: b for b in self.world["benches"]}
        drill, blast, rock = self.params["drill"], self.params["blast"], self.params["rock"]
        being_drilled = {self.block_ref[r["block"]] for r in self.world["drill_rigs"]}
        for k in self.block_defs:
            bench = benches[k["bench"]]
            area = polygon_area(k["outline"])
            tonnes = round(area * drill["bench_height_m"] * rock["insitu_density_t_m3"])
            broken = k["status"] in ("blasted", "mining", "depleted")
            # The powder factor is known once the block is charged; the fragment size once it is blasted.
            charged = broken or k["status"] == "charged"
            status = "drilling" if k["status"] == "insitu" and k["id"] in being_drilled else k["status"]
            remaining = 0.0 if k["status"] == "depleted" else round(tonnes * k["remaining_pct"] / 100.0, 1)
            self.add(k["id"], "block", k["name"], k["bench"], {
                "status": (status, ""),
                "grade_pct": (float(k["grade_pct"]), CU),
                "hardness_bwi": (float(k["hardness_bwi"]), BWI),
                # Clay minerals in the rock, %: the block model's estimate until the face shows otherwise.
                "clay_pct": (float(k["clay_pct"]), PCT),
                "tonnes_remaining": (remaining, T),
                "material": (k["material"], ""),
                "frag_p80_mm": (float(blast["p80_ref_mm"]) if broken else 0.0, MM),
                "powder_factor_kg_m3": ((blast["powder_factor_min_kg_m3"] + blast["powder_factor_max_kg_m3"]) / 2
                                        if charged else 0.0, "kg/m3"),
                "destination": (k["destination"], ""),
                "destination_source": ("cutoff", ""),
                "destination_reason": ("", ""),
                "destination_hold_until": ("", ""),
            }, links={"bench": self.tid(k["bench"]), "access_node": self.tid(bench["access_node"])},
                geo={"polygon": k["outline"], "z": bench["elevation_m"]},
                spec={"tonnes_initial": tonnes, "height_m": drill["bench_height_m"], "area_m2": round(area),
                      "holes_planned": holes_per_block(self.params, area),
                      "hole_depth_m": drill["hole_depth_m"], "charge_per_hole_kg": blast["charge_per_hole_kg"],
                      "centre_x": k["x"], "centre_y": k["y"],
                      "face_x": k["face"][0], "face_y": k["face"][1], "mining_order": k["number"]})

    def road_nodes(self):
        segments = self.world["road_segments"]
        w = self.world
        serves: dict[str, list[str]] = {}
        for bench in w["benches"]:
            if bench.get("access_node"):
                serves.setdefault(bench["access_node"], []).append(bench["id"])
        for key in ("crusher", "waste_dump", "fuel_station", "workshop", "clay_plant", "clay_stockpile"):
            serves.setdefault(w[key]["access_node"], []).append(key.replace("_", "-"))
        for pad in w["rom_pads"]:
            serves.setdefault(pad["access_node"], []).append(pad["id"])
        for n in w["road_nodes"]:
            touching = [s["id"] for s in segments if n["id"] in (s["from"], s["to"])]
            x, y, z = n["xyz"]
            links = {"segments": self.tids(touching)}
            if n["id"] in serves:
                links["serves"] = self.tids(serves[n["id"]])
            self.add(n["id"], "road-node", n["name"], "mine", {
                "trucks_waiting": (0, ""),
            }, links=links, geo={"x": x, "y": y, "z": z})

    def road_segments(self):
        nodes = {n["id"]: n["xyz"] for n in self.world["road_nodes"]}
        road = self.params["road"]
        for s in self.world["road_segments"]:
            path = s.get("path") or [nodes[s["from"]], *s.get("via", []), nodes[s["to"]]]
            length = path_length(path)
            rise = path[-1][2] - path[0][2]
            run = math.sqrt(max(length ** 2 - rise ** 2, 1.0))
            steepest = max(abs(100 * (b[2] - a[2]) / max(math.dist(a[:2], b[:2]), 1.0)) for a, b in zip(path, path[1:]))
            self.add(s["id"], "road-segment", s["name"], "mine", {
                "status": ("open", ""),
                "closed_by": ("", ""),
                "speed_limit_kmh": (float(road[f"speed_limit_{s['class']}_kmh"]), KMH),
                # Signed in the from -> to direction: positive means uphill.
                "grade_pct": (round(100 * rise / run, 1), PCT),
                "rolling_resistance_pct": (float(road[f"rr_{s['class']}_pct"]), PCT),
                "trucks_on": (0, ""),
                "surface": ("dry", ""),
                # A limit on top of the surface's (opened after an emergency repair, a treated wet ramp); 0: none.
                "speed_cap_kmh": (0.0, KMH),
            }, links={"from": self.tid(s["from"]), "to": self.tid(s["to"])},
                geo={"path": path},
                spec={"length_m": round(length), "rise_m": round(rise, 1), "max_grade_pct": round(steepest, 1),
                      "width_m": road["width_m"], "road_class": s["class"], **({"ramp": s["ramp"]} if "ramp" in s else {})})

    def substation(self):
        w = self.world["substation"]
        x, y, z = w["xyz"]
        consumers = [*self.names("loaders"), "crusher", "conveyor", "mill-sag", "mill-ball", "flotation", "clay-plant"]
        self.add("substation", "substation", w["name"], "mine", {
            "load_mw": (0.0, MW),
            "plant_mw": (0.0, MW),
            "pit_mw": (0.0, MW),
            "energy_mwh_shift": (0.0, "MWh"),
        }, links={"supplies": self.tids(consumers)}, geo={"x": x, "y": y, "z": z},
            spec={"mill_installed_mw": self.params["mill"]["installed_mw"]})

    def drill_rigs(self):
        p = self.params["drill"]
        blocks = {k["id"]: k for k in self.block_defs}
        for r in self.world["drill_rigs"]:
            block = blocks[self.block_ref[r["block"]]]
            planned = holes_per_block(self.params, polygon_area(block["outline"]))
            self.add(r["id"], "drill-rig", r["name"], "pit", {
                "state": ("drilling", ""),
                "block": (self.tid(block["id"]), ""),
                "holes_done": (int(r["holes_done"]), ""),
                "holes_planned": (planned, ""),
                "penetration_m_h": (0.0, "m/h"),
                # Metres in the block being drilled: the holes done times the hole depth, plus the hole under way.
                "metres_drilled": (round(int(r["holes_done"]) * p["hole_depth_m"], 1), M),
                # Metres since the model started.
                "metres_total": (0.0, M),
                "pos_x": (float(block["x"]), M),
                "pos_y": (float(block["y"]), M),
            }, links={"pit": self.tid("pit")}, home={"x": block["x"], "y": block["y"], "z": self._bench_z(block)},
                spec={"model": self.world["equipment"]["drill"], "hole_diameter_mm": p["hole_diameter_mm"],
                      "hole_depth_m": p["hole_depth_m"],
                      "burden_m": p["burden_m"], "spacing_m": p["spacing_m"],
                      "penetration_ref_m_h": p["penetration_m_h"]})

    def _bench_z(self, block: dict) -> float:
        return next(b["elevation_m"] for b in self.world["benches"] if b["id"] == block["bench"])

    def loaders(self):
        p = self.params["loader"]
        blocks = {k["id"]: k for k in self.block_defs}
        for l in self.world["loaders"]:
            block = blocks[self.block_ref[l["block"]]]
            if block["status"] != "mining":
                raise ValueError(f"{l['id']} starts on {block['id']}, which is not in status 'mining'")
            x, y = block["face"]
            self.add(l["id"], "loader", l["name"], "pit", {
                "state": ("waiting", ""),
                "block": (self.tid(block["id"]), ""),
                "material": (block["material"], ""),
                "truck": ("", ""),
                "passes": (0, ""),
                "dig_rate_tph": (0.0, TPH),
                "ready_in_min": (0.0, MIN),
                "queue_len": (0, ""),
                "power_kw": (round(p["power_rated_kw"] * p["load_factor_idle"]), KW),
                "health_pct": (round(100.0 - wear_pct(self.params, l["hours_since_service"]), 1), PCT),
                "hours_since_service": (float(l["hours_since_service"]), H),
                "service_reason": ("", ""),
                "service_kind": ("", ""),
                # While it is down for a repair: when the repair is expected to end, and how many crews work on it.
                "repair_end_sim_time": ("", ""),
                "repair_crews": (0, ""),
                "pos_x": (float(x), M),
                "pos_y": (float(y), M),
            }, links={"pit": self.tid("pit"), "power": self.tid("substation")},
                home={"x": x, "y": y, "z": self._bench_z(block)},
                spec={"model": self.world["equipment"]["loader"], "bucket_m3": p["bucket_m3"],
                      "bucket_t": round(bucket_load_t(self.params), 1),
                      "passes_per_truck": nominal_passes(self.params), "pass_s": p["pass_s"],
                      "power_rated_kw": p["power_rated_kw"], "travel_kmh": p["travel_kmh"]})

    def trucks(self):
        p = self.params["truck"]
        nodes = {n["id"]: n["xyz"] for n in self.world["road_nodes"]}
        idle_lph = full_power_fuel_lph(self.params) * p["idle_load_factor"]
        for t in self.world["trucks"]:
            x, y, z = nodes[t["start_node"]]
            self.add(t["id"], "truck", t["name"], "mine", {
                "state": ("standby", ""),
                "payload_t": (0.0, T),
                "material": ("none", ""),
                "grade_pct": (0.0, CU),
                "speed_kmh": (0.0, KMH),
                "pos_x": (float(x), M),
                "pos_y": (float(y), M),
                "pos_z": (float(z), M),
                "heading_deg": (0.0, "deg"),
                "segment": ("", ""),
                "assigned_loader": (self.tid(t["loader"]), ""),
                "assigned_dump": ("", ""),
                "route": ("", ""),
                "assignment_source": ("static", ""),
                "fuel_pct": (float(t["fuel_pct"]), PCT),
                "fuel_rate_lph": (round(idle_lph), "l/h"),
                "engine_load_pct": (round(100 * p["idle_load_factor"]), PCT),
                "engine_temp_c": (float(p["engine_temp_thermostat_c"]), DEG_C),
                "tyre_temp_c": (round(air_c(self.params["weather"], datetime.fromisoformat(self.world["clock"]["shift_start"])), 1), DEG_C),
                "health_pct": (round(100.0 - wear_pct(self.params, t["hours_since_service"]), 1), PCT),
                "hours_since_service": (float(t["hours_since_service"]), H),
                "service_reason": ("", ""),
                "service_kind": ("", ""),
                "service_hold_until": ("", ""),
                "standby_reason": ("", ""),
                "trips": (0, ""),
                "cycle_min": (0.0, MIN),
            }, links={"home_node": self.tid(t["start_node"]), "home_loader": self.tid(t["loader"]),
                      "fuel_station": self.tid("fuel-station"), "workshop": self.tid("workshop")},
                home={"x": x, "y": y, "z": z},
                spec={"model": self.world["equipment"]["truck"], "payload_t": p["payload_t"], "empty_mass_t": p["empty_mass_t"],
                      "engine_kw": p["engine_kw"], "max_speed_kmh": p["max_speed_kmh"], "tank_l": p["tank_l"],
                      "full_power_fuel_lph": round(full_power_fuel_lph(self.params), 1),
                      "tyre_temp_limit_c": p["tyre_temp_limit_c"],
                      "pm_interval_h": self.params["reliability"]["pm_interval_h"]})

    def plant(self):
        w, p = self.world, self.params
        x, y, z = w["crusher"]["xyz"]
        crusher = p["crusher"]
        self.add("crusher", "crusher", w["crusher"]["name"], "mine", {
            "state": ("idle", ""),
            # Mean over the rate window (sim.rate_window_min); the instantaneous rate is what the belt carries now.
            "throughput_tph": (0.0, TPH),
            "throughput_now_tph": (0.0, TPH),
            "capacity_tph": (float(crusher["rated_tph"]), TPH),
            "feed_grade_pct": (0.0, CU),
            "feed_p80_mm": (0.0, MM),
            "hopper_t": (0.0, T),
            "queue_len": (0, ""),
            "power_kw": (round(crusher["motor_kw"] * crusher["idle_load_factor"]), KW),
            "tonnes_total": (0.0, T),
            # While stopped: when it is expected back, and how many crews repair it.
            "restart_expected_sim_time": ("", ""),
            "repair_crews": (0, ""),
        }, links={"access_node": self.tid(w["crusher"]["access_node"]), "feeds": self.tid("conveyor"),
                  "power": self.tid("substation")},
            geo={"x": x, "y": y, "z": z},
            spec={"model": w["equipment"]["crusher"], "rated_tph": crusher["rated_tph"],
                  "design_tph": crusher["design_tph"], "hopper_capacity_t": crusher["hopper_t"],
                  "motor_kw": crusher["motor_kw"], "product_p80_mm": crusher["product_p80_mm"]})

        path = w["conveyor"]["path"]
        length = path_length(path)
        self.add("conveyor", "conveyor", w["conveyor"]["name"], "mine", {
            "state": ("idle", ""),
            "load_tph": (0.0, TPH),
            "load_t": (0.0, T),
            "power_kw": (float(p["conveyor"]["empty_kw"]), KW),
        }, links={"from": self.tid("crusher"), "to": self.tid("stockpile"), "power": self.tid("substation")},
            geo={"path": path},
            spec={"length_m": round(length), "lift_m": path[-1][2] - path[0][2],
                  "belt_speed_ms": p["conveyor"]["belt_speed_ms"], "design_tph": p["conveyor"]["design_tph"],
                  "installed_kw": p["conveyor"]["installed_kw"],
                  "transport_s": round(length / p["conveyor"]["belt_speed_ms"])})

        x, y, z = w["stockpile"]["xyz"]
        capacity = p["stockpile"]["capacity_t"]
        level = capacity * w["stockpile"]["initial_level_pct"] / 100.0
        self.add("stockpile", "stockpile", w["stockpile"]["name"], "mine", {
            "level_t": (round(level, 1), T),
            "level_pct": (float(w["stockpile"]["initial_level_pct"]), PCT),
            "grade_pct": (float(p["geology"]["mean_head_grade_pct"]), CU),
            "hardness_bwi": (float(p["mill"]["bwi_ref_kwh_t"]), BWI),
            "clay_pct": (float(p["flotation"]["clay_ref_pct"]), PCT),
            "hours_of_feed": (round(level / p["mill"]["design_tph"], 1), H),
        }, links={"fed_by": self.tid("conveyor"), "feeds": self.tid("mill-sag"), "circuit": self.tid("mill")},
            geo={"x": x, "y": y, "z": z},
            spec={"capacity_t": capacity, "live_hours": p["stockpile"]["live_hours"],
                  "radius_m": w["stockpile"]["radius_m"]})

        # The grinding circuit: feeder, setpoint, overload and totals. Its two
        # units, the SAG mill and the ball mills, are twins of their own on the chain
        # stockpile -> mill-sag -> mill-ball -> flotation.
        x, y, z = w["mill"]["xyz"]
        mill = p["mill"]
        self.add("mill", "mill", w["mill"]["name"], "mine", {
            "state": ("running", ""),
            "feed_tph": (0.0, TPH),
            "feed_setpoint_tph": (float(mill["design_tph"]), TPH),
            "setpoint_source": ("constant", ""),
            "setpoint_hold_until": ("", ""),
            # What the circuit can grind: the power limit at the hardness of the feed, less what its clay takes.
            "max_feed_tph": (float(round(mill["installed_mw"] * 1000.0 / mill["specific_energy_kwh_t"])), TPH),
            "power_limit_tph": (float(round(mill["installed_mw"] * 1000.0 / mill["specific_energy_kwh_t"])), TPH),
            "clay_factor_pct": (100.0, PCT),
            "feed_clay_pct": (float(p["flotation"]["clay_ref_pct"]), PCT),
            "throughput_tph": (0.0, TPH),
            "power_mw": (0.0, MW),
            "specific_energy_kwh_t": (0.0, "kWh/t"),
            "feed_bwi": (float(mill["bwi_ref_kwh_t"]), BWI),
            # Ore fed beyond what the circuit grinds, held in it; feed is cut off at overload_t (100 %).
            "overload_t": (0.0, T),
            "overload_pct": (0.0, PCT),
            "stops_total": (0, ""),
            "tonnes_total": (0.0, T),
        }, links={"fed_by": self.tid("stockpile"), "feeds": self.tid("flotation"), "power": self.tid("substation"),
                  "units": self.tids(["mill-sag", "mill-ball"])},
            geo={"x": x, "y": y, "z": z},
            spec={"design_tph": mill["design_tph"], "sag_design_tph_2014": mill["sag_design_tph"],
                  "annual_mt": round(mill["design_tph"] * 8760 * p["plan"]["plant_availability"] / 1e6, 2),
                  "installed_mw": mill["installed_mw"],
                  "specific_energy_ref_kwh_t": mill["specific_energy_kwh_t"], "bwi_ref_kwh_t": mill["bwi_ref_kwh_t"],
                  "min_feed_tph": round(mill["design_tph"] * mill["min_feed_factor"]),
                  "max_setpoint_tph": round(mill["design_tph"] * mill["setpoint_max_factor"]),
                  "overload_t": mill["overload_t"]})

        for name, kind, key, units, feeds, fed_by in (
                ("mill-sag", "sag-mill", "sag", 1, "mill-ball", "stockpile"),
                ("mill-ball", "ball-mill", "ball", int(mill["ball_mills"]), "flotation", "mill-sag")):
            x, y, z = w[name.replace("-", "_")]["xyz"]
            features = {
                "state": ("running", ""),
                "throughput_tph": (0.0, TPH),
                "power_mw": (0.0, MW),
                "installed_mw": (float(mill[f"{key}_installed_mw"]), MW),
                "drive_load_pct": (0.0, PCT),
                "specific_energy_kwh_t": (0.0, "kWh/t"),
                "feed_bwi": (float(mill["bwi_ref_kwh_t"]), BWI),
            }
            if key == "sag":
                features["charge_pct"] = (float(mill["charge_full_pct"]), PCT)
            self.add(name, kind, w[name.replace("-", "_")]["name"], "mill", features,
                     links={"circuit": self.tid("mill"), "fed_by": self.tid(fed_by), "feeds": self.tid(feeds),
                            "power": self.tid("substation")},
                     geo={"x": x, "y": y, "z": z},
                     spec={"model": w["equipment"][f"{key}_mill"], "units": units, "installed_mw": mill[f"{key}_installed_mw"],
                           "unit_installed_mw": round(mill[f"{key}_installed_mw"] / units, 1),
                           "specific_energy_ref_kwh_t": mill[f"{key}_kwh_t"],
                           **({"charge_range_pct": f"{mill['charge_idle_pct']:.0f}–{mill['charge_trip_pct']:.0f}"}
                              if key == "sag" else {})})

        x, y, z = w["flotation"]["xyz"]
        flotation = p["flotation"]
        self.add("flotation", "flotation", w["flotation"]["name"], "mine", {
            "state": ("running", ""),
            "feed_grade_pct": (0.0, CU),
            "recovery_pct": (0.0, PCT),
            # Clay in the feed and the points of recovery it takes (mine/common/process.py).
            "feed_clay_pct": (float(flotation["clay_ref_pct"]), PCT),
            "clay_loss_pct": (0.0, PCT),
            "conc_grade_pct": (float(flotation["conc_grade_pct"]), CU),
            "conc_tph": (0.0, TPH),
            "cu_tph": (0.0, TPH),
            "tails_grade_pct": (0.0, CU),
            "tails_tph": (0.0, TPH),
            "cu_total_t": (0.0, T),
        }, links={"fed_by": self.tid("mill-ball"), "circuit": self.tid("mill"), "power": self.tid("substation"),
                  "feeds": self.tids(["concentrate", "tailings"])},
            geo={"x": x, "y": y, "z": z},
            spec={"recovery_ref_pct": flotation["recovery_ref_pct"], "conc_grade_pct": flotation["conc_grade_pct"],
                  "clay_ref_pct": flotation["clay_ref_pct"], "clay_loss_pct_per_pct": flotation["clay_loss_pct"]})

        x, y, z = w["concentrate"]["xyz"]
        conc = p["concentrate"]
        self.add("concentrate", "concentrate", w["concentrate"]["name"], "mine", {
            "level_t": (0.0, T),
            "level_pct": (0.0, PCT),
            "cu_t": (0.0, T),
            "grade_pct": (float(flotation["conc_grade_pct"]), CU),
            "inflow_tph": (0.0, TPH),
            "produced_t": (0.0, T),
            "shipped_t": (0.0, T),
            "shipments_total": (0, ""),
            "last_shipment_sim_time": ("", ""),
        }, links={"fed_by": self.tids(["flotation", "clay-flotation"])}, geo={"x": x, "y": y, "z": z},
            spec={"capacity_t": conc["store_capacity_t"], "shipment_t": conc["shipment_t"],
                  "grade_pct": flotation["conc_grade_pct"]})

        w_t = w["tailings"]
        x, y, z = w_t["discharge_xyz"]
        self.add("tailings", "tailings", w_t["name"], "mine", {
            "inflow_tph": (0.0, TPH),
            "grade_pct": (0.0, CU),
            "stored_t": (0.0, T),
            "stored_m3": (0.0, "m3"),
            "cu_lost_t": (0.0, T),
        }, links={"fed_by": self.tids(["flotation", "clay-flotation"])}, geo={"polygon": w_t["outline"], "z": z},
            spec={"discharge_x": x, "discharge_y": y, "density_t_m3": conc["tails_density_t_m3"],
                  "area_m2": round(polygon_area(w_t["outline"]))})
        self.clay_plant()

    def clay_plant(self):
        """The clay-ore plant ОФ-2: its feed stockpile (where trucks tip) and plant, the desliming cyclones, the
        sands and slime flotation; and the clay-ore stockpile beside it."""
        w, p = self.world, self.params
        c, rock = p["clay_plant"], p["rock"]
        wc = w["clay_plant"]
        x, y, z = wc["xyz"]
        tip = wc["tip_xyz"]
        level = c["feed_bin_t"] * wc["initial_level_pct"] / 100.0
        grade = float(c["mean_feed_grade_pct"])
        clay = float(c["ref_clay_pct"])
        split = clay_plant_split(p, grade, clay)
        self.add("clay-plant", "clay-plant", wc["name"], "mine", {
            "state": ("running", ""),
            "feed_tph": (0.0, TPH),
            "feed_setpoint_tph": (float(c["design_tph"]), TPH),
            "capacity_tph": (float(c["licence_tph"]), TPH),
            # The feed stockpile trucks tip on.
            "bin_t": (round(level), T),
            "bin_pct": (float(wc["initial_level_pct"]), PCT),
            "bin_hours": (round(level / c["design_tph"], 1), H),
            "room_t": (round(c["feed_bin_t"] - level), T),
            "feed_grade_pct": (grade, CU),
            "feed_clay_pct": (clay, PCT),
            "recovery_pct": (0.0, PCT),
            "cu_tph": (0.0, TPH),
            "conc_tph": (0.0, TPH),
            "power_mw": (0.0, MW),
            "queue_len": (0, ""),
            # The wheel loader feeding the feed stockpile from the clay-ore stockpile.
            "reclaim_tph": (0.0, TPH),
            "tonnes_total": (0.0, T),
            "cu_total_t": (0.0, T),
            "stops_total": (0, ""),
            # While stopped: when it is expected back, and how many crews repair it.
            "restart_expected_sim_time": ("", ""),
            "repair_crews": (0, ""),
        }, links={"access_node": self.tid(wc["access_node"]), "feeds": self.tid("desliming"),
                  "fed_by": self.tid("clay-stockpile"), "power": self.tid("substation")},
            geo={"x": x, "y": y, "z": z, "polygon": wc["outline"]},
            spec={"tip_x": tip[0], "tip_y": tip[1], "design_tph": c["design_tph"], "licence_tph": c["licence_tph"],
                  "feed_bin_t": c["feed_bin_t"], "annual_mt": round(c["design_tph"] * 8760 * p["plan"]["plant_availability"] / 1e6, 2),
                  "flowsheet": "приёмный склад → дробление → МПСИ (частичное измельчение) → гидроциклоны обесшламливания → "
                               "шаровая мельница → песковая и шламовая флотация → общий склад концентрата; хвосты — "
                               "в общее хвостохранилище",
                  "recovery_basis": "извлечение ОФ-2 не публикуется; модель обесшламливания откалибрована на 66,5 % за "
                                    "2024 год (доля меди ОФ-2 в IV квартале 2024 года, отчёт KAZ Minerals)"})
        x, y, z = w["desliming"]["xyz"]
        self.add("desliming", "desliming", w["desliming"]["name"], "clay-plant", {
            "state": ("running", ""),
            "feed_tph": (0.0, TPH),
            "feed_clay_pct": (clay, PCT),
            "slimes_pct": (round(100 * split["slimes_share"], 1), PCT),
            "slimes_tph": (0.0, TPH),
            "sands_tph": (0.0, TPH),
            "slimes_cu_pct": (round(100 * split["slimes_cu_share"], 1), PCT),
        }, links={"fed_by": self.tid("clay-plant"), "feeds": self.tid("clay-flotation"), "circuit": self.tid("clay-plant")},
            geo={"x": x, "y": y, "z": z},
            spec={"slimes_base_share": c["slimes_base_share"], "slimes_cu_ratio": c["slimes_cu_ratio"]})
        x, y, z = w["clay_flotation"]["xyz"]
        self.add("clay-flotation", "clay-flotation", w["clay_flotation"]["name"], "clay-plant", {
            "state": ("running", ""),
            "feed_grade_pct": (grade, CU),
            "recovery_pct": (0.0, PCT),
            "sand_recovery_pct": (round(split["sand_recovery_pct"], 1), PCT),
            "slime_recovery_pct": (round(split["slime_recovery_pct"], 1), PCT),
            "conc_grade_pct": (float(p["flotation"]["conc_grade_pct"]), CU),
            "conc_tph": (0.0, TPH),
            "cu_tph": (0.0, TPH),
            "tails_tph": (0.0, TPH),
            "tails_grade_pct": (0.0, CU),
            "cu_total_t": (0.0, T),
        }, links={"fed_by": self.tid("desliming"), "circuit": self.tid("clay-plant"),
                  "feeds": self.tids(["concentrate", "tailings"])},
            geo={"x": x, "y": y, "z": z},
            spec={"recovery_check_pct": c["recovery_check_pct"], "slime_recovery_pct": c["slime_recovery_pct"],
                  "sand_recovery_ref_pct": c["sand_recovery_ref_pct"]})
        s = w["clay_stockpile"]
        x, y, z = s["tip_xyz"]
        capacity = round(polygon_area(s["outline"]) * s["stack_height_m"] * rock["loose_density_t_m3"])
        self.add("clay-stockpile", "clay-stockpile", s["name"], "mine", {
            "level_t": (float(s["initial_level_t"]), T),
            "level_pct": (round(100.0 * s["initial_level_t"] / capacity, 1), PCT),
            "room_t": (round(capacity - s["initial_level_t"]), T),
            "grade_pct": (float(s["initial_grade_pct"]), CU),
            "clay_pct": (float(s["initial_clay_pct"]), PCT),
            "queue_len": (0, ""),
            "reclaim_tph": (0.0, TPH),
        }, links={"access_node": self.tid(s["access_node"]), "feeds": self.tid("clay-plant")},
            geo={"polygon": s["outline"], "z": z},
            spec={"tip_x": x, "tip_y": y, "capacity_t": capacity, "stack_height_m": s["stack_height_m"],
                  "reclaim_rate_tph": c["reclaim_tph"]})

    def dumps(self):
        rom, rock = self.params["rom"], self.params["rock"]
        for pad in self.world["rom_pads"]:
            x, y, z = pad["tip_xyz"]
            # What the pad holds: its area stacked to its height.
            capacity = round(polygon_area(pad["outline"]) * pad["stack_height_m"] * rock["loose_density_t_m3"])
            self.add(pad["id"], "rom-pad", pad["name"], "mine", {
                "level_t": (float(pad["initial_level_t"]), T),
                "level_pct": (round(100.0 * pad["initial_level_t"] / capacity, 1), PCT),
                "grade_pct": (float(pad["initial_grade_pct"]), CU),
                "hardness_bwi": (float(self.params["mill"]["bwi_ref_kwh_t"]), BWI),
                "clay_pct": (float(self.params["flotation"]["clay_ref_pct"]), PCT),
                "role": (pad["role"], ""),
                "queue_len": (0, ""),
                "reclaim_tph": (0.0, TPH),
                "reclaim_source": ("rule", ""),
                "reclaim_hold_until": ("", ""),
            }, links={"access_node": self.tid(pad["access_node"]), "feeds": self.tid("crusher")},
                geo={"polygon": pad["outline"], "z": z},
                spec={"tip_x": x, "tip_y": y, "capacity_t": capacity, "stack_height_m": pad["stack_height_m"],
                      "reclaim_rate_tph": rom["reclaim_tph"]})
        w = self.world["waste_dump"]
        x, y, z = w["tip_xyz"]
        self.add("waste-dump", "waste-dump", w["name"], "mine", {
            "tonnes_total": (0.0, T),
            "queue_len": (0, ""),
        }, links={"access_node": self.tid(w["access_node"])},
            geo={"polygon": w["outline"], "z": z}, spec={"tip_x": x, "tip_y": y})

    def services(self):
        w = self.world["fuel_station"]
        x, y, z = w["xyz"]
        fuel = self.params["fuel_station"]
        self.add("fuel-station", "fuel-station", w["name"], "mine", {
            "queue_len": (0, ""),
            "trucks_fuelling": (0, ""),
            "litres_issued": (0.0, "l"),
        }, links={"access_node": self.tid(w["access_node"])}, geo={"x": x, "y": y, "z": z},
            spec={"pumps": fuel["pumps"], "flow_l_min": fuel["flow_l_min"]})
        w = self.world["workshop"]
        x, y, z = w["xyz"]
        self.add("workshop", "workshop", w["name"], "mine", {
            "bays_busy": (0, ""),
            "bays_total": (int(self.params["workshop"]["bays"]), ""),
            "jobs_queued": (0, ""),
            "jobs_done": (0, ""),
            "breakdowns_total": (0, ""),
            # Bays whose crew repairs a loader at its face, and the end of a second crew's overtime.
            "bays_lent": (0, ""),
            "overtime_until": ("", ""),
        }, links={"access_node": self.tid(w["access_node"])}, geo={"x": x, "y": y, "z": z},
            spec={"bays": self.params["workshop"]["bays"]})

    def weather(self):
        p = self.params["weather"]
        start = datetime.fromisoformat(self.world["clock"]["shift_start"])
        n = normals(p, start)
        self.add("weather", "weather", self.world["weather"]["name"], "mine", {
            "condition": ("dry", ""),
            "rain_mm_h": (0.0, "mm/h"),
            "precip_class": ("none", ""),
            "road_surface": ("dry", ""),
            "visibility_m": (float(p["visibility_dry_m"]), M),
            "temperature_c": (round(air_c(p, start), 1), DEG_C),
            "normal_mean_c": (round(n["mean_c"], 1), DEG_C),
            "normal_max_c": (round(n["max_c"], 1), DEG_C),
            "normal_min_c": (round(n["min_c"], 1), DEG_C),
            "wet_day_chance_pct": (round(100.0 * n["wet_chance"], 1), PCT),
        }, links={"affects": self.tids(self.names("road_segments"))},
            spec={"climate_basis": CLIMATE_BASIS, "station": "Экибастуз (около 51°43′ с. ш., 75°19′ в. д.), 73 км к востоку от рудника",
                  "heavy_rain_mm_h": p["rain_heavy_mm_h"]})

    def modules(self):
        """The twin of each decision module. Its service is the only writer of
        these features; until the service has started the twin says `offline`."""
        slots = self.world["forecasts"]
        controls = {
            "module-dispatch": [*self.names("trucks"), *self.names("loaders"), "crusher"],
            "module-grade": [*(b["id"] for b in self.block_defs), "crusher", *self.names("rom_pads"), "waste-dump", "stockpile",
                             "clay-plant", "clay-stockpile"],
            "module-plant": ["mill", "stockpile", "flotation"],
            "module-maint": ["workshop", *self.names("trucks"), *self.names("loaders")],
            # The advisor asks for forecasts and, on `apply`, sends the levers of its options.
            "module-advisor": ["mine", *(f"forecast-{n}" for n in slots["advisor_slots"]), *self.names("loaders"), "mill",
                               *self.names("rom_pads"), *self.names("trucks"), "crusher", "workshop", "clay-plant",
                               *(b["id"] for b in self.block_defs),
                               *(s["id"] for s in self.world["road_segments"]),
                               *(m["id"] for m in self.world["modules"] if m["id"] != "module-advisor"), "journal"],
        }
        # Each control module also asks the forecast engine, in a slot of its own, what the mine does without it.
        effect_slot = {module: f"forecast-{n}" for module, n in slots["module_slots"].items()}
        effect = {
            "effect_cu_t": (0.0, T),
            "effect_margin_kzt": (0, KZT),
            "effect_total_kzt": (0, KZT),
            "effect_basis": ("", ""),
            "effect_forecast": ("", ""),
            "effect_count": (0, ""),
            "effect_sim_time": ("", ""),
        }
        advisor = {
            "incident": ("", ""),
            "incident_thing": ("", ""),
            "incident_text": ("", ""),
            "incident_sim_time": ("", ""),
            # When the incident is expected to end and why (the scenario's estimate, the mechanics' estimate).
            "incident_end_sim_time": ("", ""),
            "incident_basis": ("", ""),
            "analysis_status": ("idle", ""),
            "analysis_sim_time": ("", ""),
            "options_count": (0, ""),
        }
        for n in range(1, ADVISOR_OPTIONS + 1):
            advisor.update({
                f"option{n}_label": ("", ""),
                f"option{n}_actions": ("", ""),
                f"option{n}_actions_text": ("", ""),
                f"option{n}_delta_kzt": (0, KZT),
                f"option{n}_cu_delta_t": (0.0, T),
                # The standard error of the gain over the paired replicas, and how sure the gain is, in Russian words.
                f"option{n}_gain_se_kzt": (0, KZT),
                f"option{n}_confidence": ("", ""),
                # The difference against «change nothing» split by where it comes from; the parts add up to delta_kzt.
                **{f"option{n}_split_{part}_kzt": (0, KZT) for part in MARGIN_SPLIT},
                f"option{n}_rank": (0, ""),
                f"option{n}_recommendable": (False, ""),
                f"option{n}_verdict": ("", ""),
                f"option{n}_forecast": ("", ""),
                f"option{n}_summary": ("", ""),
                # Where the option's copper difference comes from (the forecast's copper_text).
                f"option{n}_copper_text": ("", ""),
                # What the option's response resources cost over the horizon, and why it pays or does not.
                f"option{n}_cost_kzt": (0, KZT),
                f"option{n}_reason": ("", ""),
            })
        advisor.update({
            "recommended": (0, ""),
            "applied_option": (0, ""),
            "applied_rank": (0, ""),
            "applied_label": ("", ""),
            "applied_sim_time": ("", ""),
            "applied_text": ("", ""),
        })
        own = {
            "module-advisor": advisor,
            "module-dispatch": {
                "avg_queue_min": (0.0, MIN),
                "tonnes_per_h": (0.0, TPH),
                "ore_share_pct": (0.0, PCT),
                "plan_ore_share_pct": (round(100.0 * self.params["plan"]["ore_share"], 1), PCT),
                "reassigned_total": (0, ""),
                "diverted_total": (0, ""),
            },
            "module-grade": {
                "feed_grade_pct": (0.0, CU),
                "target_grade_pct": (float(self.params["grade_control"]["target_pct"]), CU),
                "band_low_pct": (round(self.params["grade_control"]["target_pct"] - self.params["grade_control"]["band_pct"], 3), CU),
                "band_high_pct": (round(self.params["grade_control"]["target_pct"] + self.params["grade_control"]["band_pct"], 3), CU),
                "in_band_pct": (0.0, PCT),
                "blend_grade_pct": (0.0, CU),
                # The clay expected in the ОФ-1 feed, the limit the module keeps it within, the faces it found clay.
                "blend_clay_pct": (0.0, PCT),
                "clay_limit_pct": (float(self.params["grade_control"]["clay_limit_pct"]), PCT),
                "clay_blocks": (0, ""),
                "blocks_classified": (0, ""),
                "reclaim_tph": (0.0, TPH),
            },
            "module-plant": {
                "feed_setpoint_tph": (0.0, TPH),
                "power_limit_tph": (0.0, TPH),
                "ore_inflow_tph": (0.0, TPH),
                "hours_of_feed": (0.0, H),
                "predicted_recovery_pct": (0.0, PCT),
                "predicted_cu_tph": (0.0, TPH),
                "mill_stops": (0, ""),
            },
            "module-maint": {
                "fleet_avail_pct": (0.0, PCT),
                "defects_found": (0, ""),
                "services_booked": (0, ""),
                "breakdowns": (0, ""),
                "units_watched": (0, ""),
                "units_at_risk": ("", ""),
            },
        }
        for m in self.world["modules"]:
            self.add(m["id"], "module", m["name"], "mine", {
                "enabled": (True, ""),
                "status": ("offline", ""),
                "enabled_sim_time": ("", ""),
                "decisions_total": (0, ""),
                "last_decision": ("", ""),
                "kpi_label": ("", ""),
                "kpi_unit": ("", ""),
                "kpi_value": (0.0, ""),
                "effect_text": ("Сервис модуля не запущен: рудник работает по базовым правилам.", ""),
                **(effect if m["id"] in effect_slot else {}),
                **own[m["id"]],
            }, links={"controls": self.tids(controls[m["id"]]),
                      **({"effect_forecast": self.tid(effect_slot[m["id"]])} if m["id"] in effect_slot else {})},
                spec={"service": m["id"].replace("module-", "mine-")})

    def scenarios(self):
        by_kind: dict[str, list[str]] = {}
        for t in self.things:
            by_kind.setdefault(t["attributes"]["kind"], []).append(t["thingId"])
        table = scenario_causes(self.params)
        for s in self.world["scenarios"]:
            causes = table.get(s["id"], [])
            self.add(s["id"], "scenario", s["name"], "mine", {
                "status": ("inactive", ""),
                "target": ("", ""),
                "started_sim_time": ("", ""),
                "elapsed_min": (0.0, MIN),
                # While a cancel takes time (status `cancelling`): model minutes until the mine is back to normal.
                "cancel_remaining_min": (0.0, MIN),
                "baseline_feed_tph": (0.0, TPH),
                "current_feed_tph": (0.0, TPH),
                "baseline_cycle_min": (0.0, MIN),
                "current_cycle_min": (0.0, MIN),
                "baseline_margin_kzt_h": (0, KZT_H),
                "current_margin_kzt_h": (0, KZT_H),
                "kpi_label": ("", ""),
                "kpi_unit": ("", ""),
                "kpi_baseline": (0.0, ""),
                "kpi_current": (0.0, ""),
                # Up to four indicators along the scenario's causal chain, first link first:
                # «before» and «now» over equal windows, and whether the change exceeds the indicator's usual swing.
                **{f"kpi{n}_{field}": value for n in range(1, SCENARIO_KPIS + 1) for field, value in (
                    ("label", ("", "")), ("unit", ("", "")), ("baseline", (0.0, "")), ("baseline_known", (False, "")),
                    ("current", (0.0, "")), ("significant", (False, "")),
                    # «now» is a forecast from the routes, not yet measured trips (a road closure, its first trips)
                    ("forecast", (False, "")))},
                "kpi_window_min": (0.0, MIN),
                # Where the «before» window lies when it cannot end at the start, or why there is none («база недоступна»).
                "kpi_baseline_note": ("", ""),
                "effect_delay_text": ("", ""),
                # When the incident is expected to end and why; forecasts assume it ends then (mine/sim/durations.py).
                "expected_end_sim_time": ("", ""),
                "expected_duration_h": (causes[0]["hours"] if causes else 0.0, H),
                "expected_remaining_h": (0.0, H),
                "duration_basis": (causes[0]["basis"] if causes else "", ""),
                # What was done to end it sooner (mine/sim/response.py) and what that has cost.
                "response_text": ("", ""),
                "response_cost_kzt": (0, KZT),
                **({"valid_targets": ("", "")} if s["id"] == "scenario-blast" else {}),
                "affected": ("", ""),
                "impact_text": ("", ""),
            }, links={"targets": by_kind[s["targets"]], "default_target": self.tid(s["default_target"])},
                spec={"causes": [{k: c[k] for k in ("id", "title", "hours", "basis")} for c in causes]} if causes else None)

    def forecast_names(self) -> list[str]:
        return [f"forecast-{n}" for n in range(1, self.world["forecasts"]["slots"] + 1)]

    def forecasts(self):
        """Slots for the results of the forecast engine: slot 1 serves the page,
        `advisor_slots` the advisor, `module_slots` one control module each, `check_slots` the stand check
        (its forecast with and without it). Only the simulator writes them."""
        w = self.world["forecasts"]
        module_of = {n: module for module, n in w["module_slots"].items()}
        titles = {m["id"]: m["name"] for m in self.world["modules"]}
        features = {
            "status": ("idle", ""),                 # idle / running / ready / error
            "request_id": ("", ""),
            "requested_by": ("", ""),
            "subject": ("", ""),
            "horizon_h": (0, H),
            "label_a": ("", ""),
            "label_b": ("", ""),
            "actions_a": ("", ""),
            "actions_b": ("", ""),
            "fork_sim_time": ("", ""),
            "ready_sim_time": ("", ""),
            "compute_s": (0.0, "s"),
        }
        for branch in ("a", "b"):
            features.update({
                f"{branch}_cu_t": (0.0, T),
                f"{branch}_ore_t": (0.0, T),
                f"{branch}_waste_t": (0.0, T),
                f"{branch}_milled_t": (0.0, T),
                # The copper fed to the mill (head grade = it / milled), and where the mill's ore came from.
                f"{branch}_milled_cu_t": (0.0, T),
                f"{branch}_from_stock_t": (0, T),
                f"{branch}_from_stock_cu_t": (0.0, T),
                f"{branch}_from_pads_t": (0, T),
                f"{branch}_from_pads_cu_t": (0.0, T),
                f"{branch}_from_pit_t": (0, T),
                f"{branch}_from_pit_cu_t": (0.0, T),
                # The clay-ore plant ОФ-2: ore processed, the copper in it and recovered, and the clay-ore stockpile
                # built up (negative: drawn down) over the horizon.
                f"{branch}_clay_milled_t": (0.0, T),
                f"{branch}_clay_milled_cu_t": (0.0, T),
                f"{branch}_clay_cu_t": (0.0, T),
                f"{branch}_to_clay_stock_t": (0, T),
                f"{branch}_ofs1_clay_pct": (0.0, PCT),
                f"{branch}_stock_end_h": (0.0, H),
                f"{branch}_diesel_l": (0.0, "l"),
                f"{branch}_energy_mwh": (0.0, "MWh"),
                f"{branch}_revenue_kzt": (0, KZT),
                f"{branch}_diesel_kzt": (0, KZT),
                f"{branch}_power_kzt": (0, KZT),
                # Variable margin over the horizon: revenue - diesel - power, as on the mine twin.
                f"{branch}_margin_kzt": (0, KZT),
                f"{branch}_rehandle_t": (0.0, T),
                f"{branch}_rehandle_kzt": (0, KZT),
                # The ore between the faces and the flotation (mine/common/economics.py): stock = grade + work + pads.
                f"{branch}_grade_kzt": (0, KZT),
                f"{branch}_work_kzt": (0, KZT),
                f"{branch}_pads_kzt": (0, KZT),
                f"{branch}_stock_kzt": (0, KZT),
                f"{branch}_strip_kzt": (0, KZT),
                # The adjustment: stock + strip - rehandle; total = margin + adjustment, what options are ranked by.
                f"{branch}_adjust_kzt": (0, KZT),
                # The shift's response resources the branch spent (second crews, a grader, a road crew).
                f"{branch}_resources_kzt": (0, KZT),
                # total = margin + adjustment - resources: what options are ranked by.
                f"{branch}_total_kzt": (0, KZT),
                f"{branch}_co2_t": (0.0, T),
                # What the control modules are judged by, over the horizon.
                f"{branch}_queue_min": (0.0, MIN),
                f"{branch}_in_band_pct": (0.0, PCT),
                f"{branch}_avail_pct": (0.0, PCT),
                f"{branch}_breakdowns": (0, ""),
                f"{branch}_mill_stops": (0, ""),
                f"{branch}_overloads": (0, ""),
                f"{branch}_series_mill_tph": ("", TPH),
                f"{branch}_series_stockpile_pct": ("", PCT),
            })
        features.update({
            "refused_actions": ("", ""),
            "delta_margin_kzt": (0, KZT),
            "delta_adjust_kzt": (0, KZT),
            "delta_total_kzt": (0, KZT),
            "delta_cu_t": (0.0, T),
            "delta_co2_t": (0.0, T),
            # Paired replicas: each one runs A and B on the same random streams, drawn anew per replica; the
            # branch figures above are means over them, the errors those of the mean differences.
            "replicas": (1, ""),
            "delta_total_se_kzt": (0, KZT),
            "delta_margin_se_kzt": (0, KZT),
            "delta_cu_se_t": (0.0, T),
            "delta_total_replicas_kzt": ("", KZT),
            "delta_cu_replicas_t": ("", T),
            # The decision figure in a sentence, where the copper difference comes from, and the whole summary.
            "verdict_text": ("", ""),
            "copper_text": ("", ""),
            "summary_text": ("", ""),
            # When the forecast takes the running incidents to end (their expected end, or as an option moved it).
            "assumption_text": ("", ""),
            "error_text": ("", ""),
        })
        for n, name in enumerate(self.forecast_names(), start=1):
            if n == 1:
                role, title, links = "page", w["page_title"], {}
            elif n in w["advisor_slots"]:
                role, title, links = "advisor", w["advisor_title"], {"advisor": self.tid("module-advisor")}
            elif n in w.get("check_slots", ()):
                role, title, links = "check", w["check_title"], {}
            else:
                module = module_of[n]
                role, title, links = "module", titles[module].lower(), {"module": self.tid(module)}
            self.add(name, "forecast", f"{w['name']} {n} ({title})", "mine",
                     features, links={"mine": self.tid("mine"), **links},
                     spec={"slot": n, "role": role, "default_horizon_h": self.params["forecast"]["horizon_h"],
                           "series_step_h": 1, **({"module": self.tid(module_of[n])} if role == "module" else {})})

    def journal(self):
        self.add("journal", "journal", self.world["journal"]["name"], "mine", {
            "seq": (0, ""),
            "severity": ("info", ""),
            "thing": ("", ""),
            "text": ("", ""),
            "sim_time": ("", ""),
        }, links={"mine": self.tid("mine")})


def build_things(world: dict | None = None, params: dict | None = None) -> list[dict]:
    """All things of the mine, ordered so that a parent precedes its children."""
    return TwinBuilder(world or load_world(), params or load_params()).build()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--ids", action="store_true", help="print thing IDs only")
    parser.add_argument("--kinds", action="store_true", help="print the number of twins of each kind")
    parser.add_argument("--roads", action="store_true", help="print length and grade of every road segment")
    args = parser.parse_args()
    things = build_things()
    if args.ids:
        print("\n".join(t["thingId"] for t in things))
    elif args.kinds:
        for kind, count in Counter(t["attributes"]["kind"] for t in things).items():
            print(f"{count:3d}  {kind}")
        print(f"{len(things):3d}  total")
    elif args.roads:
        for t in things:
            if t["attributes"]["kind"] == "road-segment":
                spec, f = t["attributes"]["spec"], t["features"]
                print(f"{t['thingId'].split(':')[1]}  {spec['length_m']:5d} m  mean {f['grade_pct']['properties']['value']:+5.1f} %"
                      f"  steepest {spec['max_grade_pct']:4.1f} %  {spec['road_class']:8s} {t['attributes']['name']}")
    else:
        print(json.dumps(things, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
