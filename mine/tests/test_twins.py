"""Checks of the generated twins, the pit geometry, the parameters, the fleet sizing and the block model.
They need no platform, only the image:

    make mine-test
"""
import math
import unittest
from collections import Counter

from mine.model import build_params_doc
from mine.model.build_twins import (build_things, bucket_load_t, full_power_fuel_lph, holes_per_block, load_params,
                                    load_params_raw, load_world, nominal_passes)
from mine.sim.design_basis import design_basis
from mine.common.roads import RoadNetwork

WORLD, PARAMS = load_world(), load_params()
THINGS = build_things(WORLD, PARAMS)
NS = WORLD["namespace"]

# kind -> allowed number of twins.
KINDS = {
    "mine": (1, 1), "pit": (1, 1), "bench": (17, 17), "block": (40, 60), "drill-rig": (3, 3), "loader": (5, 5),
    "truck": (30, 40), "road-node": (15, 20), "road-segment": (18, 24), "crusher": (1, 1), "conveyor": (1, 1),
    "stockpile": (1, 1), "rom-pad": (2, 2), "waste-dump": (1, 1), "mill": (1, 1), "sag-mill": (1, 1), "ball-mill": (1, 1),
    "flotation": (1, 1), "concentrate": (1, 1), "tailings": (1, 1),
    "substation": (1, 1), "fuel-station": (1, 1), "workshop": (1, 1), "weather": (1, 1), "module": (5, 5),
    "scenario": (6, 6), "forecast": (10, 10), "journal": (1, 1),
}
MOBILE = ("truck", "loader", "drill-rig")     # their position is a feature; `home` says where they started


def of_kind(kind: str) -> list[dict]:
    return [t for t in THINGS if t["attributes"]["kind"] == kind]


def by_name(name: str) -> dict:
    return next(t for t in THINGS if t["thingId"] == f"{NS}:{name}")


def value(thing: dict, feature: str):
    return thing["features"][feature]["properties"]["value"]


def link_targets(thing: dict) -> list[str]:
    targets = []
    for target in thing["attributes"]["links"].values():
        targets += [target] if isinstance(target, str) else target
    return targets


def inside(point, polygon) -> bool:
    """Ray casting: is the point inside the polygon?"""
    x, y = point
    hit = False
    for (x1, y1), (x2, y2) in zip(polygon, polygon[1:] + polygon[:1]):
        if (y1 > y) != (y2 > y) and x < x1 + (y - y1) * (x2 - x1) / (y2 - y1):
            hit = not hit
    return hit


def distance_to(point, polygon) -> float:
    best = math.inf
    for (x1, y1), (x2, y2) in zip(polygon, polygon[1:] + polygon[:1]):
        dx, dy = x2 - x1, y2 - y1
        f = max(0.0, min(1.0, ((point[0] - x1) * dx + (point[1] - y1) * dy) / ((dx * dx + dy * dy) or 1.0)))
        best = min(best, math.hypot(point[0] - x1 - dx * f, point[1] - y1 - dy * f))
    return best


class TwinDefinitions(unittest.TestCase):
    def test_every_kind_exists_in_its_number(self):
        counts = Counter(t["attributes"]["kind"] for t in THINGS)
        self.assertEqual(set(counts), set(KINDS))
        for kind, (low, high) in KINDS.items():
            self.assertTrue(low <= counts[kind] <= high, f"{kind}: {counts[kind]} twins")
        ids = [t["thingId"] for t in THINGS]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(counts["truck"], PARAMS["plan"]["trucks"])
        self.assertEqual(counts["loader"], PARAMS["plan"]["shovels"])
        self.assertEqual(counts["drill-rig"], PARAMS["plan"]["drills"])
        for name in ("module-dispatch", "module-grade", "module-plant", "module-maint", "module-advisor",
                     "forecast-1", "forecast-5", "scenario-road-closure",
                     "scenario-shovel-failure", "scenario-crusher-stop", "scenario-heavy-rain", "scenario-blast",
                     "scenario-hard-ore", "mine", "journal", "truck-32", "rom-pad-02"):
            self.assertIn(f"{NS}:{name}", ids)

    def test_one_namespace_setting(self):
        self.assertEqual(NS, "kz.aktogay")
        self.assertTrue(all(t["thingId"].startswith(f"{NS}:") for t in THINGS))
        mine = by_name("mine")["attributes"]
        self.assertIn("Актогай", mine["name"])
        for key in ("object", "data_basis", "disclaimer", "operator"):
            self.assertTrue(mine["about"][key], key)
        self.assertIn("не связан", mine["about"]["disclaimer"])

    def test_attributes(self):
        ids = {t["thingId"] for t in THINGS}
        for t in THINGS:
            attributes = t["attributes"]
            self.assertEqual(t["policyId"], "default:basic_policy")
            self.assertIs(attributes["_isType"], False)
            mobile = attributes["kind"] in MOBILE
            for key in ("name", "kind", "links", "home" if mobile else "geo", "spec"):
                self.assertIn(key, attributes, t["thingId"])
            if mobile:
                # No stale position in the attributes of a machine that moves.
                self.assertNotIn("geo", attributes, t["thingId"])
                self.assertEqual(set(attributes["home"]), {"x", "y", "z", "label"}, t["thingId"])
                self.assertIn("pos_x", t["features"], t["thingId"])
                self.assertTrue(attributes["spec"]["model"], t["thingId"])
            if attributes["kind"] != "mine":
                self.assertIn(attributes["_parents"], ids, t["thingId"])
            self.assertTrue(attributes["links"], f"{t['thingId']} has no links")

    def test_parents_come_first(self):
        seen = set()
        for t in THINGS:
            parent = t["attributes"].get("_parents")
            if parent:
                self.assertIn(parent, seen, t["thingId"])
            seen.add(t["thingId"])

    def test_links_point_at_existing_twins(self):
        ids = {t["thingId"] for t in THINGS}
        for t in THINGS:
            for target in link_targets(t):
                self.assertIn(target, ids, f"{t['thingId']} links to {target}")

    def test_the_mine_is_one_connected_graph(self):
        neighbours: dict[str, set] = {t["thingId"]: set() for t in THINGS}
        for t in THINGS:
            for target in link_targets(t) + [t["attributes"].get("_parents")]:
                if target:
                    neighbours[t["thingId"]].add(target)
                    neighbours[target].add(t["thingId"])
        reached, frontier = {f"{NS}:mine"}, [f"{NS}:mine"]
        while frontier:
            for nxt in neighbours[frontier.pop()]:
                if nxt not in reached:
                    reached.add(nxt)
                    frontier.append(nxt)
        self.assertEqual(reached, set(neighbours))

    def test_process_chain_is_linked(self):
        chain = ["crusher", "conveyor", "stockpile", "mill-sag", "mill-ball", "flotation"]
        for upstream, downstream in zip(chain, chain[1:]):
            links = by_name(upstream)["attributes"]["links"]
            self.assertIn(f"{NS}:{downstream}", [links.get("feeds"), links.get("to")], upstream)
        self.assertEqual(by_name("flotation")["attributes"]["links"]["feeds"], [f"{NS}:concentrate", f"{NS}:tailings"])
        for end in ("concentrate", "tailings"):
            self.assertEqual(by_name(end)["attributes"]["links"]["fed_by"], f"{NS}:flotation")
        self.assertEqual(by_name("mill")["attributes"]["links"]["units"], [f"{NS}:mill-sag", f"{NS}:mill-ball"])
        for unit in ("mill-sag", "mill-ball"):
            self.assertEqual(by_name(unit)["attributes"]["_parents"], f"{NS}:mill")
        supplied = set(by_name("substation")["attributes"]["links"]["supplies"])
        loaders = {t["thingId"] for t in of_kind("loader")}
        self.assertEqual(supplied, loaders | {f"{NS}:{n}" for n in ("crusher", "conveyor", "mill-sag", "mill-ball", "flotation")})
        for block in of_kind("block"):
            bench = next(t for t in THINGS if t["thingId"] == block["attributes"]["links"]["bench"])
            self.assertEqual(block["attributes"]["_parents"], bench["thingId"])
            self.assertIn(block["thingId"], bench["attributes"]["links"]["blocks"])
            self.assertEqual(bench["attributes"]["_parents"], f"{NS}:pit")

    def test_forecast_slots_have_their_roles(self):
        roles = {t["thingId"].split(":")[1]: (t["attributes"]["spec"]["role"], t["attributes"]["spec"].get("module"))
                 for t in of_kind("forecast")}
        self.assertEqual(roles["forecast-1"], ("page", None))
        self.assertEqual(roles["forecast-10"], ("check", None))              # the stand check's own slot
        self.assertEqual({n for n, (r, _) in roles.items() if r == "advisor"}, {f"forecast-{n}" for n in (2, 3, 4, 5)})
        self.assertEqual({m for r, m in roles.values() if r == "module"},
                         {f"{NS}:module-{m}" for m in ("dispatch", "grade", "plant", "maint")})
        for module in ("dispatch", "grade", "plant", "maint"):
            twin = by_name(f"module-{module}")
            slot = twin["attributes"]["links"]["effect_forecast"]
            self.assertEqual(next(t for t in THINGS if t["thingId"] == slot)["attributes"]["spec"]["module"], twin["thingId"])
            for feature in ("effect_text", "effect_cu_t", "effect_margin_kzt", "effect_basis"):
                self.assertIn(feature, twin["features"], module)

    def test_feature_values_are_scalars(self):
        for t in THINGS:
            for name, body in t["features"].items():
                self.assertIsInstance(body["properties"]["value"], (str, int, float, bool), f"{t['thingId']}.{name}")


class Geometry(unittest.TestCase):
    """The pit as a deep hard-rock pit: benches, walls, ramps, exits and hauls of realistic size."""

    def test_pit_size_depth_and_benches(self):
        pit = by_name("pit")["attributes"]
        rim = pit["geo"]["polygon"]
        xs, ys = [p[0] for p in rim], [p[1] for p in rim]
        self.assertAlmostEqual(max(xs) - min(xs), 2300, delta=50)
        self.assertAlmostEqual(max(ys) - min(ys), 1400, delta=50)
        self.assertLessEqual(max(xs) - min(xs), PARAMS["pit"]["final_length_m"])      # a slice, short of the final pit
        elevations = sorted((value(b, "elevation_m") for b in of_kind("bench")), reverse=True)
        self.assertEqual(len(elevations), 17)
        self.assertEqual({a - b for a, b in zip(elevations, elevations[1:])}, {15.0})
        depth = pit["spec"]["rim_elevation_m"] - elevations[-1]
        self.assertEqual(depth, PARAMS["pit"]["depth_m"])
        self.assertEqual(value(by_name("pit"), "depth_m"), depth)
        self.assertTrue(200 <= depth < PARAMS["pit"]["final_depth_m"], "a working floor several hundred metres down")
        self.assertIn("допущение", pit["spec"]["depth_basis"])
        self.assertAlmostEqual(pit["spec"]["rim_elevation_m"], PARAMS["pit"]["rim_elevation_m"], delta=5)

    def test_final_wall_angle(self):
        """Rim to floor on the north wall, where no pushback widens the benches: 35-45 degrees."""
        benches = sorted(of_kind("bench"), key=lambda b: -value(b, "elevation_m"))
        rim_y = max(p[1] for p in by_name("pit")["attributes"]["geo"]["polygon"])
        floor_y = max(p[1] for p in benches[-1]["attributes"]["geo"]["polygon"])
        depth = by_name("pit")["attributes"]["spec"]["depth_m"]
        angle = math.degrees(math.atan(depth / (rim_y - floor_y)))
        self.assertTrue(35 <= angle <= 45, f"final wall {angle:.1f} deg")
        self.assertTrue(35 <= by_name("pit")["attributes"]["spec"]["final_wall_angle_deg"] <= 45)
        # Each bench toe lies inside the one above: the wall steps down, it never overhangs.
        for upper, lower in zip(benches, benches[1:]):
            for point in lower["attributes"]["geo"]["polygon"][::10]:
                self.assertTrue(inside(point, upper["attributes"]["geo"]["polygon"]), lower["thingId"])

    def test_blocks_sit_on_the_floor_of_their_bench(self):
        """Every corner of a block lies between the toe of its bench and the crest of the face below."""
        benches = {t["thingId"]: t["attributes"]["geo"] for t in of_kind("bench")}
        for block in of_kind("block"):
            geo = benches[block["attributes"]["links"]["bench"]]
            for corner in block["attributes"]["geo"]["polygon"]:
                self.assertTrue(inside(corner, geo["polygon"]) or distance_to(corner, geo["polygon"]) < 1.0, block["thingId"])
                if geo.get("crest"):
                    self.assertFalse(inside(corner, geo["crest"]) and distance_to(corner, geo["crest"]) > 1.0,
                                     f"{block['thingId']} reaches over the crest of its bench")
            self.assertEqual(block["attributes"]["geo"]["z"], geo["z"])

    def test_working_benches_and_the_rest_of_the_wall(self):
        working = [b for b in of_kind("bench") if b["attributes"]["spec"]["working"]]
        self.assertEqual(len(working), 5)
        for bench in of_kind("bench"):
            has_blocks = bool(bench["attributes"]["links"]["blocks"])
            self.assertEqual(has_blocks, bench["attributes"]["spec"]["working"], bench["thingId"])
            self.assertEqual("access_node" in bench["attributes"]["links"], has_blocks, bench["thingId"])

    def test_ramps_run_along_the_walls_at_8_to_10_percent(self):
        ramps = {r["id"]: r for r in WORLD["ramp_info"]}
        self.assertEqual({r["grade_pct"] for r in ramps.values()}, {8.0, 10.0})
        depth = by_name("pit")["attributes"]["spec"]["depth_m"]
        for r in ramps.values():
            self.assertGreaterEqual(r["switchbacks"], 1, r["id"])
            self.assertAlmostEqual(r["length_m"], depth / (r["grade_pct"] / 100), delta=0.05 * r["length_m"])
        for t in of_kind("road-segment"):
            spec = t["attributes"]["spec"]
            self.assertLessEqual(spec["max_grade_pct"], PARAMS["road"]["ramp_grade_max_pct"] + 0.3, t["thingId"])
            if spec["road_class"] == "ramp":
                grade = ramps[spec["ramp"]]["grade_pct"]
                self.assertAlmostEqual(abs(value(t, "grade_pct")), grade, delta=0.4, msg=t["thingId"])
                self.assertAlmostEqual(spec["max_grade_pct"], grade, delta=0.3, msg=t["thingId"])
            # A ramp segment follows the wall: every point of its path lies between the rim and the floor.
            for x, y, z in t["attributes"]["geo"]["path"]:
                self.assertTrue(WORLD["pit"]["floor_z"] - 1e-6 <= z <= WORLD["pit"]["rim_z"] + 15, t["thingId"])

    def test_closing_any_one_segment_leaves_every_node_reachable(self):
        roads = RoadNetwork.from_things(THINGS, PARAMS["road"])
        length = lambda seg, node: seg.length
        nodes = list(roads.nodes)
        for closed in roads.segments.values():
            roads.close(closed, "test")
            for goal in nodes[1:]:
                self.assertIsNotNone(roads.route(nodes[0], goal, length), f"{closed.name} closed: no way to {goal}")
            roads.reopen(closed, "test")

    def test_two_exits_from_every_working_bench_to_the_crusher(self):
        """The main ramp, and an alternate by the west ramp that shares no segment with it and is longer."""
        roads = RoadNetwork.from_things(THINGS, PARAMS["road"])
        length = lambda seg, node: seg.length
        crusher = by_name("crusher")["attributes"]["links"]["access_node"].split(":")[1]
        for bench in of_kind("bench"):
            if not bench["attributes"]["spec"]["working"]:
                continue
            start = bench["attributes"]["links"]["access_node"].split(":")[1]
            main = roads.route(start, crusher, length)
            used = [roads.segment_between(a, b) for a, b in zip(main, main[1:])]
            for seg in used:
                roads.close(seg, "test")
            alternate = roads.route(start, crusher, length)
            for seg in used:
                roads.reopen(seg, "test")
            self.assertIsNotNone(alternate, bench["thingId"])
            self.assertGreater(roads.route_length(alternate), roads.route_length(main), bench["thingId"])
            self.assertTrue(1000 <= roads.route_length(main) <= 6000, bench["thingId"])

    def test_hauls_and_the_plant_at_realistic_distances(self):
        d = design_basis(WORLD, PARAMS)
        hauls = {name: f["haul_m"] for name, f in d["faces"].items()}
        ore = [h for name, h in hauls.items() if d["faces"][name]["material"] != "waste"]
        self.assertTrue(all(2000 <= h <= 6000 for h in ore), hauls)
        self.assertGreaterEqual(max(ore), 4000, "the deepest ore face hauls more than 4 km to the crusher")
        conveyor = by_name("conveyor")["attributes"]["spec"]
        self.assertAlmostEqual(conveyor["length_m"], 3800, delta=150)          # KAZ Minerals 2014: 3.8 km
        rim = by_name("pit")["attributes"]["geo"]["polygon"]
        for kind in ("rom-pad", "waste-dump", "tailings"):
            for t in of_kind(kind):
                for corner in t["attributes"]["geo"]["polygon"]:
                    self.assertFalse(inside(corner, rim), f"{t['thingId']} lies in the pit")
        for kind in ("crusher", "fuel-station", "workshop", "stockpile", "mill", "flotation", "substation"):
            geo = of_kind(kind)[0]["attributes"]["geo"]
            self.assertFalse(inside((geo["x"], geo["y"]), rim), kind)
        self.assertGreater(by_name("stockpile")["attributes"]["geo"]["x"] - max(p[0] for p in rim), 3500)

    def test_every_facility_has_its_own_node(self):
        nodes = {}
        for kind in ("crusher", "rom-pad", "waste-dump", "fuel-station", "workshop"):
            for t in of_kind(kind):
                nodes.setdefault(t["attributes"]["links"]["access_node"], []).append(kind)
        self.assertEqual(sorted(map(sorted, nodes.values())),
                         [["crusher"], ["fuel-station"], ["rom-pad", "rom-pad"], ["waste-dump"], ["workshop"]])


class Parameters(unittest.TestCase):
    def test_every_entry_is_documented(self):
        raw = load_params_raw()      # raises on a missing field, an empty source or an unknown status
        statuses = Counter(entry["status"] for entries in raw.values() for entry in entries.values())
        self.assertGreater(statuses["sourced"], 40)
        self.assertEqual(sum(statuses.values()), sum(len(entries) for entries in raw.values()))

    def test_generated_document_is_current(self):
        path = build_params_doc.DOC_PATH
        self.assertTrue(path.exists(), f"{path} is missing: run python -m mine.model.build_params_doc --write "
                                       f"(make mine-test mounts docs/mine into the container)")
        self.assertEqual(path.read_text(encoding="utf-8"), build_params_doc.render(),
                         "docs/mine/parameters.md is stale: run python -m mine.model.build_params_doc --write")

    def test_aktogay_figures(self):
        """The numbers read in KAZ Minerals' reports and the equipment data sheets."""
        p = PARAMS
        self.assertEqual((p["geology"]["mean_head_grade_pct"], p["flotation"]["recovery_ref_pct"]), (0.41, 87.2))
        self.assertEqual((p["crusher"]["design_tph"], p["crusher"]["motor_kw"]), (4186, 750))
        self.assertEqual((p["mill"]["sag_installed_mw"], p["mill"]["ball_mills"], p["mill"]["ball_installed_mw"],
                          p["mill"]["sag_design_tph"]), (28, 2, 44, 3425))
        self.assertEqual((p["conveyor"]["belt_speed_ms"], p["conveyor"]["design_tph"], p["conveyor"]["installed_kw"]),
                         (5.0, 4880, 3640))
        self.assertEqual((p["plan"]["trucks_2014"], p["plan"]["journeys_2014"], p["plan"]["shovel_rate_mtpa"]), (18, 18000, 11))
        truck, loader = p["truck"], p["loader"]
        self.assertEqual((truck["payload_t"], truck["empty_mass_t"], truck["engine_kw"], truck["max_speed_kmh"],
                          truck["tank_l"], truck["tyres"]), (136, 102.1, 1005, 54.7, 1892, 6))
        self.assertEqual((loader["bucket_m3"], loader["power_rated_kw"], loader["travel_kmh"],
                          p["rock"]["loose_density_t_m3"]), (21, 1200, 2.2, 1.8))
        self.assertAlmostEqual(bucket_load_t(p), 34.0, delta=0.1)
        self.assertEqual(nominal_passes(p), 4)
        self.assertAlmostEqual(full_power_fuel_lph(p), 253.7, delta=0.1)
        self.assertTrue(172 <= p["drill"]["hole_diameter_mm"] <= 254)            # the D55SP range
        w = p["weather"]
        self.assertEqual((w["oct_mean_c"], w["oct_max_c"], w["oct_min_c"], w["oct_precip_mm"], w["oct_wet_days"]),
                         (7.6, 15.4, 1.3, 20.0, 4.4))
        self.assertEqual((w["nov_mean_c"], w["nov_precip_mm"], w["annual_precip_mm"]), (-2.0, 26.9, 195.6))
        rel = p["reliability"]
        self.assertEqual((rel["truck_mtbf_h"], rel["truck_mttr_h"], rel["pm_interval_h"], rel["pm_duration_h"]),
                         (500, 12, 250, 4))
        self.assertEqual((p["blast"]["exclusion_radius_m"], p["blast"]["reentry_min"]), (200, 15))

    def test_figures_derived_from_kaz_reports(self):
        p = PARAMS
        self.assertAlmostEqual(p["geology"]["strip_ratio"], 26.7 / 73.486, delta=0.0005)        # 2024
        self.assertAlmostEqual(p["geology"]["low_grade_share"], 75.512 / 62.095 - 1, delta=0.0005)  # 2025
        self.assertAlmostEqual(p["economics"]["payable_cu_pct"], 100 * 210.4 / 220.3, delta=0.05)  # 2025
        self.assertAlmostEqual(p["plan"]["mill_rate_tph"], 30.909e6 / (8760 * p["plan"]["plant_availability"]), delta=1)
        self.assertEqual(p["mill"]["design_tph"], p["plan"]["mill_rate_tph"])
        # Recovery against grade: the least-squares slope through 2025 over the published points.
        points = [(0.43, 87.9), (0.50, 87.7), (0.42, 86.8), (0.40, 86.6)]
        xs = [math.log(g / 0.41) for g, _ in points]
        slope = sum(x * (r - 87.2) for x, (_, r) in zip(xs, points)) / sum(x * x for x in xs)
        self.assertAlmostEqual(p["flotation"]["grade_gain_pct"], slope, delta=0.05)
        self.assertAlmostEqual(p["crusher"]["rated_tph"], p["crusher"]["design_tph"] * p["mill"]["design_tph"] / p["mill"]["sag_design_tph"], delta=5)

    def test_derived_values_follow_from_their_inputs(self):
        p = PARAMS
        drill, blast, mill = p["drill"], p["blast"], p["mill"]
        self.assertAlmostEqual(drill["hole_depth_m"], drill["bench_height_m"] + drill["subdrill_m"])
        self.assertGreaterEqual(drill["bench_height_m"], drill["hole_diameter_mm"] / 15)            # Dyno Nobel rule
        self.assertTrue(25 <= drill["burden_m"] * 1000 / drill["hole_diameter_mm"] <= 40)
        self.assertAlmostEqual(drill["spacing_m"], 1.15 * drill["burden_m"], delta=0.15)
        self.assertTrue(2 <= drill["bench_height_m"] / drill["burden_m"] <= 3.5)
        mean_pf = (blast["powder_factor_min_kg_m3"] + blast["powder_factor_max_kg_m3"]) / 2
        self.assertAlmostEqual(blast["charge_per_hole_kg"],
                               mean_pf * drill["burden_m"] * drill["spacing_m"] * drill["bench_height_m"], delta=0.1)
        per_hole_min = drill["hole_depth_m"] / drill["penetration_m_h"] * 60 + drill["move_min_per_hole"]
        self.assertAlmostEqual(drill["hole_depth_m"] / per_hole_min * 60, drill["gross_rate_m_h"], delta=0.5)
        self.assertEqual(holes_per_block(p, 2400), 57)
        self.assertEqual(p["truck"]["tyre_temp_limit_c"],
                         p["truck"]["tyre_tkph_ref_ambient_c"] + p["truck"]["tyre_temp_rise_at_rating_c"])
        self.assertAlmostEqual(p["truck"]["overload_limit"], (249.48 - p["truck"]["empty_mass_t"]) / p["truck"]["payload_t"], delta=0.01)
        self.assertAlmostEqual(p["loader"]["fill_factor"], p["truck"]["payload_t"] / (4 * p["loader"]["bucket_m3"]
                                                                                       * p["rock"]["loose_density_t_m3"]), delta=0.005)
        self.assertAlmostEqual(p["plan"]["ore_share"], 1 / (1 + p["geology"]["strip_ratio"]), delta=0.001)
        self.assertEqual(mill["bwi_ref_kwh_t"], p["geology"]["bwi_mean_kwh_t"])
        # Grinding: the installed power of the 2014 design at the drive load of the operating point.
        self.assertEqual(mill["sag_installed_mw"] + mill["ball_installed_mw"], mill["installed_mw"])
        self.assertAlmostEqual(mill["specific_energy_kwh_t"], mill["installed_mw"] * 1000 * mill["drive_load"] / mill["design_tph"],
                               delta=0.01)
        self.assertAlmostEqual(mill["sag_kwh_t"] + mill["ball_kwh_t"], mill["specific_energy_kwh_t"], delta=0.005)
        self.assertAlmostEqual(mill["sag_kwh_t"] / mill["ball_kwh_t"], mill["sag_installed_mw"] / mill["ball_installed_mw"], delta=0.005)
        self.assertLessEqual(mill["sag_installed_mw"], 30.0)               # no single SAG mill draws more
        self.assertLessEqual(mill["ball_installed_mw"] / mill["ball_mills"], 22.0)
        self.assertTrue(25 <= mill["charge_idle_pct"] < mill["charge_full_pct"] < mill["charge_trip_pct"] <= 35)
        self.assertEqual(p["stockpile"]["capacity_t"], p["stockpile"]["live_hours"] * mill["design_tph"])
        # The conveyor's own formula at its design flow stays within its installed power.
        c = p["conveyor"]
        spec = by_name("conveyor")["attributes"]["spec"]
        design_kw = c["empty_kw"] + c["design_tph"] * 9.81 * (spec["lift_m"] + c["friction_coefficient"] * spec["length_m"]) / 3600 / c["drive_efficiency"]
        self.assertLess(design_kw, c["installed_kw"])
        # Rehandling: loader diesel at the diesel price plus the other costs.
        rom, econ = p["rom"], p["economics"]
        self.assertAlmostEqual(rom["rehandle_kzt_t"], rom["rehandle_diesel_l_t"] * econ["diesel_kzt_l"]
                               + rom["rehandle_other_kzt_t"], delta=0.5)
        # A recommended gain lies beyond the scatter: at least two standard errors of the mean over the replicas,
        # the error never taken below the measured paired noise of one forecast (advisor.min_t, its note: 9.2 mln KZT).
        adv = p["advisor"]
        self.assertGreaterEqual(adv["min_t"], 2.0)
        self.assertGreaterEqual(adv["replicas"], 3)
        self.assertAlmostEqual(adv["min_t"] * p["forecast"]["noise_kzt"] / adv["replicas"] ** 0.5, 9.24e6, delta=0.05e6)
        # Copper as a hard bar: a loss above it is beyond the copper scatter of the mean over the replicas.
        self.assertGreaterEqual(adv["max_cu_loss_t"], adv["min_t"] * p["forecast"]["noise_cu_t"] / adv["replicas"] ** 0.5)


class DesignBasis(unittest.TestCase):
    """The fleet is sized from the concentrator line: params.yaml and world.yaml repeat the calculation."""

    @classmethod
    def setUpClass(cls):
        cls.d = design_basis(WORLD, PARAMS)

    def test_rates_follow_from_the_line(self):
        d, p = self.d, PARAMS
        self.assertAlmostEqual(d["annual_ore_mt"], 30.909, delta=0.05)                      # Sulphide 1, 2025
        self.assertAlmostEqual(d["rock_tph"], p["plan"]["rock_tph"], delta=1.0)
        self.assertAlmostEqual(d["ore_share"], p["plan"]["ore_share"], delta=0.001)
        self.assertAlmostEqual(d["waste_tph"] / (d["mill_tph"] + d["low_grade_tph"]), p["geology"]["strip_ratio"], delta=1e-6)

    def test_fleet_size_and_allocation(self):
        d, p = self.d, PARAMS
        self.assertEqual(d["allocation"], WORLD["fleet"]["allocation"])
        self.assertEqual(d["trucks"], p["plan"]["trucks"])
        self.assertEqual(sum(WORLD["fleet"]["allocation"].values()), len(of_kind("truck")))
        self.assertAlmostEqual(d["cycle_min"], p["plan"]["cycle_min"], delta=0.02 * d["cycle_min"])
        self.assertGreaterEqual(d["fleet_tph"], d["rock_tph"])                  # the fleet carries the line with its reserve
        self.assertGreater(d["trucks"], p["plan"]["trucks_2014"])               # a deeper pit than the 2014 design's
        self.assertLess(d["journeys_per_truck"], p["plan"]["journeys_2014"])
        for name, f in d["faces"].items():
            self.assertGreaterEqual(d["allocation"][name], f["trucks_exact"], name)
            self.assertLess(d["allocation"][name] - f["trucks_exact"], 1.0, name)

    def test_shovels_drills_and_ore_faces(self):
        d, p = self.d, PARAMS
        self.assertEqual(d["shovels"], p["plan"]["shovels"])
        self.assertEqual(d["drills"], p["plan"]["drills"])
        self.assertEqual(d["ore_faces"], p["loader"]["ore_faces"])
        self.assertAlmostEqual(d["yearly_rock_mt"], p["plan"]["rock_tph"] * 8760 * p["plan"]["plant_availability"] / 1e6, delta=0.05)


class BlockModel(unittest.TestCase):
    def setUp(self):
        self.blocks = of_kind("block")

    def test_class_shares_and_grades_follow_kaz_figures(self):
        geology = PARAMS["geology"]
        tonnes = lambda blocks: sum(b["attributes"]["spec"]["tonnes_initial"] for b in blocks)
        classes = {m: [b for b in self.blocks if value(b, "material") == m] for m in ("ore", "low-grade", "waste")}
        total = tonnes(self.blocks)
        mean = sum(value(b, "grade_pct") * b["attributes"]["spec"]["tonnes_initial"] for b in classes["ore"]) / tonnes(classes["ore"])
        self.assertAlmostEqual(mean, geology["mean_head_grade_pct"], delta=0.015)
        waste_share = geology["strip_ratio"] / (1 + geology["strip_ratio"])
        low_share = (1 - waste_share) * geology["low_grade_share"] / (1 + geology["low_grade_share"])
        self.assertAlmostEqual(tonnes(classes["waste"]) / total, waste_share, delta=0.03)
        self.assertAlmostEqual(tonnes(classes["low-grade"]) / total, low_share, delta=0.03)
        low_grade_pad = next(p["id"] for p in WORLD["rom_pads"] if p["role"] == "low-grade")
        for b in self.blocks:
            grade = value(b, "grade_pct")
            material = value(b, "material")
            self.assertEqual(material == "waste", grade < geology["cutoff_grade_pct"], b["thingId"])
            self.assertEqual(material == "low-grade", geology["cutoff_grade_pct"] <= grade < geology["low_grade_below_pct"])
            self.assertEqual(value(b, "destination"),
                             {"waste": "waste-dump", "low-grade": low_grade_pad, "ore": "crusher"}[material])
            self.assertTrue(geology["bwi_min_kwh_t"] <= value(b, "hardness_bwi") <= geology["bwi_max_kwh_t"])

    def test_months_of_rock_in_the_pit(self):
        """Broken and in-situ rock for months of the fleet's work, not weeks."""
        total = sum(b["attributes"]["spec"]["tonnes_initial"] for b in self.blocks)
        days = total / (PARAMS["plan"]["rock_tph"] * 24)
        self.assertGreaterEqual(days, 60, f"{days:.0f} days of rock")

    def test_neighbouring_blocks_are_alike(self):
        """Spatial correlation: blocks of one bench that touch differ less than blocks far apart."""
        near, far = [], []
        for a in self.blocks:
            for b in self.blocks:
                if a["thingId"] >= b["thingId"] or a["attributes"]["links"]["bench"] != b["attributes"]["links"]["bench"]:
                    continue
                sa, sb = a["attributes"]["spec"], b["attributes"]["spec"]
                distance = math.dist((sa["centre_x"], sa["centre_y"]), (sb["centre_x"], sb["centre_y"]))
                difference = abs(value(a, "hardness_bwi") - value(b, "hardness_bwi"))
                if distance < 130:
                    near.append(difference)
                elif distance > 300:
                    far.append(difference)
        self.assertLess(sum(near) / len(near), sum(far) / len(far))

    def test_block_tonnage_follows_geometry(self):
        for b in self.blocks:
            spec = b["attributes"]["spec"]
            expected = spec["area_m2"] * spec["height_m"] * PARAMS["rock"]["insitu_density_t_m3"]
            self.assertAlmostEqual(spec["tonnes_initial"], expected, delta=0.01 * expected)

    def test_start_has_four_loaders_on_ore_and_one_on_waste(self):
        materials = sorted(value(next(t for t in THINGS if t["thingId"] == value(l, "block")), "material") != "waste"
                           for l in of_kind("loader"))
        self.assertEqual(materials, [False, True, True, True, True])
        statuses = Counter(value(b, "status") for b in self.blocks)
        for status in ("insitu", "drilling", "drilled", "charged", "blasted", "mining"):
            self.assertGreaterEqual(statuses[status], 1, status)
        # A block being drilled is the one a rig stands on; a charged block has its charge.
        drilled = {value(r, "block") for r in of_kind("drill-rig")}
        self.assertEqual({b["thingId"] for b in self.blocks if value(b, "status") == "drilling"}, drilled)
        for b in self.blocks:
            self.assertEqual(value(b, "powder_factor_kg_m3") > 0,
                             value(b, "status") in ("charged", "blasted", "mining", "depleted"), b["thingId"])
        for r in of_kind("drill-rig"):
            self.assertAlmostEqual(value(r, "metres_drilled"), value(r, "holes_done") * PARAMS["drill"]["hole_depth_m"], delta=0.1)


if __name__ == "__main__":
    unittest.main()
