"""Sizing of the fleet from the two plants.

ОФ-1 and ОФ-2 take ore at their operating rates (KAZ Minerals, 2024); with
every tonne of it the pit also yields low-grade (sub-economic) ore and waste,
together `geology.design_strip_ratio` tonnes (the 2014 design). Each loader's
face is given the rate it must deliver (the sulphide faces share the ОФ-1 ore
and the low-grade ore, the clay faces the ОФ-2 ore, the waste faces the
waste), and its trucks follow from the cycle the model computes for that face
on the roads as built:

    trucks at a face = rate x (1 + reserve) x cycle / 60 / (payload x availability x use of availability)

rounded up. Shovels and drill rigs follow from the yearly tonnage. This
module repeats that arithmetic from the model itself, so that the derived
entries of params.yaml (group `plan`) and the allocation of trucks in
world.yaml (`fleet.allocation`) can be checked against it.

    python -m mine.sim.design_basis
"""
from __future__ import annotations

import math

from mine.model.build_twins import build_things, load_params, load_world
from mine.sim.model import Simulation

HOURS_PER_YEAR = 8760.0


def face_cycles(sim: Simulation) -> dict[str, dict]:
    """For each loader: its material class, destination and the truck cycle (s) without queues."""
    out = {}
    fleet, plant, roads = sim.fleet, sim.plant, sim.roads
    loaded, empty = fleet.cost(True), fleet.cost(False)
    p = sim.params
    passes = round(p["truck"]["payload_t"] / fleet.bucket_t)
    for loader in fleet.loaders.values():
        block = sim.pit.blocks[loader.block]
        sink = plant.sinks[block.destination]
        out_route = roads.route(loader.node, sink.node, loaded)
        back_route = roads.route(sink.node, loader.node, empty)
        local_s = 2 * math.dist(roads.nodes[loader.node][:2], loader.pos) / (p["truck"]["local_speed_kmh"] / 3.6)
        haul_m = roads.route_length(out_route) + math.dist(roads.nodes[loader.node][:2], loader.pos)
        cycle_s = (roads.route_time(out_route, loaded) + roads.route_time(back_route, empty) + local_s
                   + p["loader"]["spot_s"] + passes * p["loader"]["pass_s"] + sink.spot_s + sink.dump_s)
        out[loader.name] = {"material": block.material, "destination": block.destination, "cycle_s": cycle_s,
                            "haul_m": haul_m, "loaded_s": roads.route_time(out_route, loaded)}
    return out


def design_basis(world: dict | None = None, params: dict | None = None) -> dict:
    """The derived design values, computed from the model at its starting faces."""
    params = params or load_params()
    world = world or load_world(params=params)
    sim = Simulation(build_things(world, params), world, params)
    plan, truck, geo = params["plan"], params["truck"], params["geology"]
    mill_tph = params["mill"]["design_tph"]
    clay_tph = params["clay_plant"]["design_tph"]
    low_tph = (mill_tph + clay_tph) * geo["low_grade_share"]
    rock_tph = (mill_tph + clay_tph) * (1.0 + geo["design_strip_ratio"])
    waste_tph = rock_tph - mill_tph - clay_tph - low_tph
    faces = face_cycles(sim)
    ore_faces = [n for n, f in faces.items() if f["material"] not in ("waste", "clay")]
    clay_faces = [n for n, f in faces.items() if f["material"] == "clay"]
    waste_faces = [n for n, f in faces.items() if f["material"] == "waste"]
    # The sulphide faces share the ОФ-1 ore and the low-grade ore, the clay faces the ОФ-2 ore, the waste faces the waste.
    for name in ore_faces:
        faces[name]["rate_tph"] = (mill_tph + low_tph) / len(ore_faces)
    for name in clay_faces:
        faces[name]["rate_tph"] = clay_tph / len(clay_faces)
    for name in waste_faces:
        faces[name]["rate_tph"] = waste_tph / len(waste_faces)
    per_truck_tph = lambda cycle_s: truck["payload_t"] * 3600.0 / cycle_s * plan["availability"] * plan["use_of_availability"]
    allocation = {}
    for name, f in faces.items():
        f["trucks_exact"] = f["rate_tph"] * (1.0 + plan["fleet_reserve"]) / per_truck_tph(f["cycle_s"])
        allocation[name] = math.ceil(f["trucks_exact"] - 1e-9)
    trucks = sum(allocation.values())
    cycle_s = sum(allocation[n] * f["cycle_s"] for n, f in faces.items()) / trucks
    yearly_rock_t = rock_tph * HOURS_PER_YEAR * plan["plant_availability"]
    drill, rock = params["drill"], params["rock"]
    holes = yearly_rock_t / rock["insitu_density_t_m3"] / (drill["burden_m"] * drill["spacing_m"] * drill["bench_height_m"])
    metres = holes * drill["hole_depth_m"]
    per_rig_m = drill["gross_rate_m_h"] * HOURS_PER_YEAR * plan["drill_utilization"]
    journeys = HOURS_PER_YEAR * plan["availability"] * plan["use_of_availability"] * 3600.0 / cycle_s
    return {
        "mill_tph": mill_tph,
        "clay_tph": clay_tph,
        "low_grade_tph": low_tph,
        "waste_tph": waste_tph,
        "rock_tph": rock_tph,
        "ore_share": (mill_tph + clay_tph + low_tph) / rock_tph,
        "faces": faces,
        "allocation": allocation,
        "trucks": trucks,
        "cycle_min": cycle_s / 60.0,
        "fleet_tph": trucks * per_truck_tph(cycle_s),
        "journeys_per_truck": journeys,
        "yearly_rock_mt": yearly_rock_t / 1e6,
        "shovels": math.ceil(yearly_rock_t / (plan["shovel_rate_mtpa"] * 1e6) - 1e-9),
        "ore_faces": round(len(world["loaders"]) * (mill_tph + clay_tph + low_tph) / rock_tph),
        "drill_metres_per_year": metres,
        "drills": math.ceil(metres / per_rig_m - 1e-9),
        "annual_ore_mt": (mill_tph + clay_tph) * HOURS_PER_YEAR * plan["plant_availability"] / 1e6,
    }


def main() -> None:
    params = load_params()
    plan, truck, mill = params["plan"], params["truck"], params["mill"]
    world = load_world(params=params)
    d = design_basis(world, params)
    print(f"ОФ-1 + ОФ-2 at work           ({d['mill_tph']:.0f} + {d['clay_tph']:.0f}) t/h x 8760 h x {plan['plant_availability']} = {d['annual_ore_mt']:.2f} Mt/yr")
    print(f"low-grade ore / waste         {d['low_grade_tph']:.0f} / {d['waste_tph']:.0f} t/h; rock {d['rock_tph']:.0f} t/h "
          f"(params: {plan['rock_tph']}), {d['yearly_rock_mt']:.1f} Mt/yr")
    print(f"per truck                     {truck['payload_t']} t x {plan['availability']} x {plan['use_of_availability']} per cycle")
    for name, f in d["faces"].items():
        print(f"  {name}: {f['material']:9s} -> {f['destination']:10s} haul {f['haul_m'] / 1000:.2f} km, cycle {f['cycle_s'] / 60:.1f} min, "
              f"rate {f['rate_tph']:.0f} t/h x {1 + plan['fleet_reserve']:.2f}: {f['trucks_exact']:.2f} -> {d['allocation'][name]} trucks "
              f"(world.yaml: {world['fleet']['allocation'].get(name)})")
    print(f"trucks                        {d['trucks']} (params: {plan['trucks']}); 2014 design: {plan['trucks_2014']} per line")
    print(f"mean cycle                    {d['cycle_min']:.2f} min (params: {plan['cycle_min']}); "
          f"{d['journeys_per_truck']:.0f} journeys a year per truck (2014 design: {plan['journeys_2014']})")
    print(f"fleet capacity                {d['fleet_tph']:.0f} t/h against {d['rock_tph']:.0f} t/h needed")
    print(f"shovels                       {d['shovels']} (params: {plan['shovels']}), ore faces {d['ore_faces']}")
    print(f"drill rigs                    {d['drill_metres_per_year'] / 1000:.0f} km/yr -> {d['drills']} (params: {plan['drills']})")
    print(f"installed mill power          {mill['installed_mw']} MW, {mill['specific_energy_kwh_t']} kWh/t at {mill['design_tph']} t/h "
          f"= {mill['specific_energy_kwh_t'] * mill['design_tph'] / 1000:.1f} MW ({100 * mill['specific_energy_kwh_t'] * mill['design_tph'] / 1000 / mill['installed_mw']:.1f} %)")


if __name__ == "__main__":
    main()
