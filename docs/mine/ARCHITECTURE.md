# Architecture of the Bozshakol mine model on OpenEgiz

This document describes how the mine model in `mine/` is built and how it uses the OpenEgiz platform. The model is «модель рудника Бозшаколь: карьер, сульфидная фабрика ОФ-1 и глинистая фабрика ОФ-2 (упрощённо)»: the whole pit of the KAZ Minerals mine in Pavlodar Region at its 2023 plan size, the sulphide concentrator ОФ-1 and the clay-ore plant ОФ-2 at their 2024 rates, the fleet sized for them by the design basis, and clay (kaolinised) ore as the mine's own problem: where to send it. It reflects the code as it is in the repository; where a number is quoted, its source is named (`mine/model/params.yaml`, `mine/model/world.yaml` or the module that computes it).

## 1. What runs

`make mine` starts two layers with Docker Compose: the platform from `deploy/compose/docker-compose.yml` and the mine overlay `mine/compose.yml`.

| Layer | Service | Image | Role |
|---|---|---|---|
| Platform | `mosquitto` | eclipse-mosquitto 2.0 | MQTT broker for telemetry in, twin events and live messages out |
| Platform | `policies`, `things`, `things-search`, `connectivity`, `gateway` | Eclipse Ditto 3.3.7 | Digital twins, search, MQTT connections |
| Platform | `ditto-nginx` | nginx | Ditto HTTP API with basic auth, port 8080 |
| Platform | `mongodb` | mongo 6.0 | Ditto persistence |
| Platform | `telegraf` | telegraf 1.37 | Copies twin events from MQTT to InfluxDB |
| Platform | `influxdb` | influxdb 2.7 | Time series of every twin feature |
| Platform | `grafana`, `grafana-plugins`, `extended-api` | Grafana 12.3 with the OpenEgiz app | Platform UI; the mine overlay adds a read-only history dashboard |
| Platform | `init` | one-shot | Ditto policy and the two MQTT connections |
| Mine | `mine-twins` | `bozshakol/mine:local` | One-shot: creates or updates the 195 things (`python -m mine.model.setup_twins`) |
| Mine | `mine-sim` | `bozshakol/mine:local` | The simulator and the forecast engine (`python -m mine.sim`) |
| Mine | `mine-dispatch`, `mine-grade`, `mine-plant`, `mine-maint`, `mine-advisor` | `bozshakol/mine:local` | One service per decision module (`python -m mine.modules.<name>`) |
| Mine | `mine-ui` | nginx 1.25 | The mine page on port 8088 and its gateway to Ditto and InfluxDB |

The mine image is built from `mine/Dockerfile` (Python 3.12, `paho-mqtt` and `PyYAML` only). All mine services share it; the page is static files served by nginx with the vendored three.js (`mine/ui/site/vendor/three`).

Readiness: `mine/tests/wait_ready.sh` waits until the page's gateway answers and the simulated clock advances; `mine/tests/ready_check.sh` then checks twins, telemetry, history and the page. `make mine-check` runs the full end-to-end check `mine/tests/stand_check.sh`.

## 2. Data path

```
                       world.yaml + params.yaml
                                 │ build_twins.py
                                 ▼
 mine-twins ── HTTP PUT ──▶  Eclipse Ditto  ◀── HTTP GET / search / SSE ── mine-ui (nginx) ◀── browser
                              ▲      │                                     ▲  │
          MQTT telemetry/<name>      │ MQTT opentwins/twin/events/<ns>/<name>  │ HTTP POST …/inbox/messages/<subject>
          (Ditto modify commands)    │ MQTT opentwins/live/messages/<ns>/<name>│ (page buttons, allow-listed)
                              │      ▼                                     │
 mine-sim ── publishes ───────┘   mosquitto ──▶ telegraf ──▶ InfluxDB ──▶ Grafana «Бозшаколь — история»
     ▲                               │                          ▲
     │ live messages (MQTT)          │ twin events (MQTT)       └── mine-ui /influx/query, /influx/journal
     │                               ▼
     └──────── Ditto ◀── HTTP POST live messages ── module services (dispatch, grade, plant, maint, advisor)
```

1. **Telemetry in.** The simulator publishes, for each twin whose features changed (and every twin at least every 30 s, `SIM_HEARTBEAT_S`), a Ditto-protocol `modify` command on the MQTT topic `telemetry/<name>`. Ditto's source connection `mosquitto-source-connection` (`deploy/compose/init/`) subscribes to `telemetry/#` and applies the command to the thing.
2. **Twin events out.** Ditto's target connection `mosquitto-target-connection` publishes every twin event to `opentwins/twin/events/<namespace>/<name>`, with the extra fields `thingId` and `attributes/_parents`, and every live message to `opentwins/live/messages/<namespace>/<name>`.
3. **History.** The platform's Telegraf subscribes to `opentwins/#` and writes each event to InfluxDB (organisation `opentwins`, bucket `default`, measurement `mqtt_consumer`, tag `thingId`). A feature `<f>` becomes the field `value_<f>_properties_value`.
4. **Decisions.** A module service reads all things once over the Ditto HTTP API, then follows twin events on MQTT. It acts by sending Ditto live messages over HTTP (`POST /api/2/things/<id>/inbox/messages/<subject>?timeout=0`). Ditto delivers them to MQTT, where the simulator receives them.
5. **The page.** The browser talks only to `mine-ui`. Its nginx (`mine/ui/nginx.conf`) forwards GET requests for things, search and server-sent events to Ditto, forwards only the allow-listed live messages (section 5.3), writes the two InfluxDB read queries itself, and adds the credentials; none reach the browser.

No mine component reads another component's state except through Ditto: the simulator receives commands only as live messages, the modules see the mine only through twins, and the page only through the gateway.

## 3. Twin model

### 3.1 Where twins are defined

`mine/model/build_twins.py` is the single definition of every thing: ID, attributes, links, feature names, units and initial values. It reads the geometry, topology and initial state from `mine/model/world.yaml` (expanded by `mine/model/layout.py` for the pit and by `mine/model/geology.py` for the block model) and every number from `mine/model/params.yaml`. The setup job, the simulator, the module services and the stand check all build the same list from this code, so nothing is typed twice.

`python -m mine.model.build_twins --kinds` prints the count per kind.

### 3.2 Conventions

- Thing ID: `<namespace>:<name>`; namespace `kz.bozshakol` (`world.yaml`, overridable with `MINE_NAMESPACE`); policy `default:basic_policy`. The page reads the namespaces from `/config.json` (`MINE_NAMESPACES` = `MINE_NAMESPACE` and `MINE_NAMESPACE_PREVIOUS`, by default `kz.bozshakol kz.aktogay`) and shows the first that holds a mine twin, so it follows the move from the Aktogay model by itself.
- `attributes.name`: Russian title shown on the page. `attributes.kind`: the type of object. `attributes._isType`: false.
- `attributes._parents`: thing ID of the parent (the OpenEgiz convention for the twin hierarchy; Telegraf stores it as the tag `parent`). `mine` is the root; `pit`, road graph, plant, fleet and service twins hang under `mine`; `bench` under `pit`; `block` under its `bench`; `drill-rig` and `loader` under `pit`; `sag-mill` and `ball-mill` under `mill`; `desliming` and `clay-flotation` under `clay-plant`.
- `attributes.links`: static relations by name, each a thing ID or a list of them (for example `crusher.links.feeds = conveyor`, `road-segment.links.from/to`, `weather.links.affects = [all road segments]`, `module.links.controls`).
- `attributes.geo`: position of a fixed object in metres on the mine grid (`polygon` + `z`, `x/y/z`, or `path` for roads and the conveyor). A machine that moves has `attributes.home` (where it started) instead, and its live position in the features `pos_x`, `pos_y` (`pos_z` for trucks).
- `attributes.spec`: static nameplate and design values (payload, installed power, capacities, design basis text).
- Features: every feature is one scalar `properties.value` with an optional `properties.unit`. A telemetry message always carries the complete feature set of the twin (`mine/common/envelope.py` refuses anything else).
- Writers: the setup job creates things and updates attributes; the simulator is the only writer of the features of the physical twins, the scenario twins, the forecast twins and the journal; each module service is the only writer of its own module twin.
- Dynamic relations are features holding thing IDs: `truck.assigned_loader`, `truck.assigned_dump`, `truck.segment`, `truck.route` (node IDs joined by `>`), `loader.block`, `loader.truck`, `drill-rig.block`, `scenario.target`, `scenario.affected`, `module-advisor.incident_thing`.
- Operator orders carry an expiry in the lever's twin: `block.destination_hold_until`, `rom-pad.reclaim_hold_until`, `mill.setpoint_hold_until`, `truck.service_hold_until`. The `…_source` feature next to each lever names who set it (`cutoff` / `grade-control` / `operator`, `rule` / …, `constant` / `plant-control` / …, `static` / `dispatch` / `fallback` / `overflow`).

### 3.3 Kinds

195 things: 171 physical or environmental twins and 24 service twins.

| Kind | Count | Parent | Main links | Main features |
|---|---|---|---|---|
| mine | 1 | — | pit, crusher, mill, clay_plant, clay_stockpile, concentrate, tailings, substation, weather, journal, modules, scenarios, forecasts | sim clock and speed, shift and its start; copper, ore, waste and processed ore since the shift began (reset at 08:00 and 20:00) and since the model started; ОФ-1 mill feed; ore processed by both plants, their head grade and recovery (`processed_tph`, `head_grade_pct`, `recovery_pct`: the mine as KAZ Minerals reports it); each plant on its own (`ofs1_head_grade_pct`, `ofs1_recovery_pct`, `ofs1_clay_pct`, `ofs2_feed_tph`, `ofs2_head_grade_pct`, `ofs2_recovery_pct`, `ofs2_cu_tph`); clay ore mined and its share, the clay-ore stockpile; Cu t/h, cycle, queue, fleet availability over the last day, diesel, energy, revenue, costs, margin, CO₂ |
| pit | 1 | mine | benches, loaders, drill_rigs | depth, broken stock, blast state, blast block and radius, next blast |
| bench | 15 | pit | blocks, access_node | elevation, blocks and tonnes remaining, pushback |
| block | 52 | bench | bench, access_node | status (`insitu` … `depleted`), grade, Bond work index, clay (`clay_pct`: the block model's estimate until the face shows otherwise), tonnes, material (`ore` sulphide / `clay` / `low-grade` / `waste`, by the block model), P80, powder factor, destination and its source, reason and hold |
| road-node | 19 | mine | segments, serves | trucks waiting |
| road-segment | 22 | mine | from, to | status, closed_by, speed limit, grade, rolling resistance, trucks on, surface |
| drill-rig | 4 | pit | pit | state, block, holes done/planned, penetration, metres, position |
| loader | 6 | pit | pit, power | state, block, truck, passes, dig rate, queue, power, health, service, position |
| truck | 31 | mine | home_node, home_loader, fuel_station, workshop | state, payload, material, grade, speed, position, heading, segment, assignments, route, fuel, engine and tyre temperature, health, service, trips, cycle |
| crusher | 1 | mine | access_node, feeds, power | state, throughput (window and now), capacity, feed grade and P80, hopper, queue, power |
| conveyor | 1 | mine | from, to, power | state, load t/h, load t, power |
| stockpile | 1 | mine | fed_by, feeds, circuit | level t and %, grade, hardness, clay, hours of feed |
| mill | 1 | mine | fed_by, feeds, power, units | state, feed and setpoint, setpoint source and hold, limit (`max_feed_tph`: the power limit at the feed's hardness times the clay factor), `power_limit_tph`, `clay_factor_pct`, `feed_clay_pct`, power, specific energy, feed BWI, overload, stops, `peak_min_shift` (minutes of this shift with the setpoint above the working rate 3,557 t/h, reset at 08:00 and 20:00) |
| sag-mill, ball-mill | 1 + 1 | mill | circuit, fed_by, feeds, power | state (`reline` during a planned reline), throughput, power, installed power, drive load, specific energy, feed BWI; SAG charge; the planned reline on the SAG mill and on `mill` (`reline_state` `planned` / `reline` / '', `reline_start_sim_time`, `reline_end_sim_time`) |
| flotation | 1 | mine | fed_by, circuit, power, feeds | feed grade, feed clay and the recovery it takes (`clay_loss_pct`), recovery, concentrate grade and t/h, Cu t/h, tails grade and t/h |
| clay-plant | 1 | mine | access_node, feeds, fed_by, power | ОФ-2: state (`running` / `stopped` / `starting` / `idle`), feed and setpoint, licence capacity, its feed stockpile (`bin_t`, `bin_pct`, `bin_hours`, `room_t`) where trucks tip, feed grade and clay, recovery, Cu and concentrate t/h, power, queue, reclaim from the clay-ore stockpile, totals, stops, expected restart and repair crews while stopped; `geo` has a point and a polygon |
| desliming | 1 | clay-plant | fed_by, feeds, circuit | state, feed t/h and clay, slimes share of the solids and of the copper, slimes and sands t/h |
| clay-flotation | 1 | clay-plant | fed_by, circuit, feeds | state, feed grade, recovery of the plant and of the sands and slime flotation, concentrate grade and t/h, Cu t/h, tails |
| clay-stockpile | 1 | mine | access_node, feeds | level t and %, room, grade, clay, queue, reclaim t/h |
| concentrate | 1 | mine | fed_by (flotation, clay-flotation) | level, Cu, inflow (both plants), produced, shipped, shipments |
| tailings | 1 | mine | fed_by (flotation, clay-flotation) | inflow, grade, stored t and m³, Cu lost |
| rom-pad | 2 | mine | access_node, feeds | level, grade, hardness, clay, role, queue, reclaim t/h, reclaim source and hold |
| waste-dump | 1 | mine | access_node | tonnes, queue |
| substation | 1 | mine | supplies (loaders, crusher, conveyor, mills, flotation, clay-plant) | load, plant and pit MW, energy per shift |
| fuel-station, workshop | 1 + 1 | mine | access_node | queue, litres issued; bays busy, jobs, breakdowns |
| weather | 1 | mine | affects (all road segments) | condition, rain, precipitation class, road surface, visibility, temperature, climate normals |
| module | 5 | mine | controls, effect_forecast | enabled, status, decisions, last decision, own KPI, effect (Cu, margin, basis, forecast), module-specific features |
| scenario | 8 | mine | targets, default_target | status, target, elapsed, four chain indicators before/now with significance, delay text, affected twins, impact text |
| forecast | 10 | mine | mine, advisor or module | status, request, labels and actions of branches A and B, totals per branch, hourly series, deltas, summary |
| journal | 1 | mine | mine | seq, severity, thing, text, sim time (one event per update) |

Service twins: 5 modules, 8 scenarios, 10 forecast slots and the journal. The forecast slots are assigned in `world.yaml`: slot 1 for the page, slots 2–5 for the advisor, slots 6–9 for the "with / without the module" forecasts of dispatch, grade, plant and maintenance, slot 10 for the stand check (`make mine-check`), so that the check never takes the page's slot.

## 4. Simulator (`mine/sim`)

`mine/sim/runner.py` runs the loop: once per real second it applies the live messages that arrived, advances the model by `speedup` simulated seconds (one `step` is one simulated second, so results do not depend on the speed), publishes changed twins and the journal events, and touches its health file. Allowed speed-ups are 1, 10, 20 and 60 (`mine/sim/model.py`); the default is 20 (`sim.default_speedup`). Before the first publish the model runs `clock.warm_start_min` = 120 simulated minutes silently.

| Module | Subsystem |
|---|---|
| `model.py` | The simulation object, message dispatch (`handle_message`), published values |
| `pit.py` | Blocks and their life cycle `insitu → drilling → drilled → charged → blasted → mining → depleted → (pushback)`, drill rigs, routine blasts with exclusion zone and re-entry, short-term plan, pushbacks. A rig starts a new block while fewer than `drill.drill_ahead_blocks` blocks stand ahead of the shovels (blasted, charged, drilled or being drilled); the limit stands one block above the start state, so the rigs drill from the first hour and then start a block each time a shovel moves onto a blasted one: drilling keeps pace with the digging (`test_modules`: a week's metres against the metres the rock dug needed) |
| `fleet.py` | Trucks, loaders, fuel station; routing at every node over the road graph; the fixed plan used without the dispatch module |
| `plant.py` | Dump points, crusher, conveyor, coarse-ore stockpile, ROM pads with wheel-loader reclaim, waste dump, SAG and ball mills, flotation, the clay-ore plant ОФ-2 (feed stockpile, desliming, sands and slime flotation) and the clay-ore stockpile with its reclaim to ОФ-2, the shared concentrate and tailings, substation. Rock moves as parcels carrying tonnes, copper, hardness, fragment size and clay, so the mass and copper balances close over both plants |
| `reliability.py` | Wear, random defects that develop before a failure (drifting engine or tyre temperature or health), workshop bays. A share of defects (`reliability.sudden_share_pct`) gives no warning and fails at once, so the maintenance module cannot prevent every failure: truck availability with all modules stays within the 92–94 % of the best mines, about 87 % without them |
| `weather.py` | Air temperature and precipitation from the 1991–2020 normals of the Ekibastuz station (73 km east of the mine) |
| `scenarios.py` | The eight what-if scenarios |
| `durations.py` | How long each incident scenario is expected to last, its causes and their basis (section 4.1) |
| `response.py` | The shift supervisor's response resources: second repair crews, the workshop's crew, a grader, an emergency road crew, workshop overtime (section 4.2) |
| `chain.py` | Indicators along a scenario's causal chain over equal windows, with the noise test |
| `kpis.py` | Mine indicators and the material balance, computed from what the subsystems report |
| `design_basis.py` | Fleet sizing from the concentrator line, to check the derived entries of `params.yaml` |
| `forecast.py` | The forecast engine (section 6) |
| `coupling.py` | The simulator and the module cores in one process, used by the forecast engine and the tests |

Randomness: every random process draws from a stream of its own, keyed by its entity and fixed by `sim.seed` (`Simulation.rng`): the pace of each truck on each segment and its tipping time, the bucket fill and pass time of each loader, the defects and repairs of each machine, the charge of each block, the precipitation of each day. What one entity draws never shifts another's draws, so two forecast branches with the same actions are identical and a small action changes only what it causes (common random numbers). `Simulation.reseed(salt)` restarts every stream from a key (a forecast replica) and draws the hours to each machine's next defect again, which the memoryless onset allows.

Shared physics and helpers in `mine/common`: `physics.py` (truck speed and fuel from engine power, grade and rolling resistance), `roads.py` (road graph, closures, wet surface, shortest-time routing), `haulage.py` (travel times), `process.py` (wear, the ОФ-1 recovery with its clay term, the clay handling factor of the ОФ-1 mill, the ОФ-2 desliming model), `economics.py` (money and CO₂), `envelope.py` (Ditto protocol), `mqtt.py`, `ditto.py`.

### 4.0 The world and the clay

- **Pit** (`world.yaml`, `layout.py`): the rim is a stadium of the 2023 plan size, 4,070 × 880 m (ecoportal 29174), at +230 m; 15 benches of 15 m down to a working floor at +5 m (225 m, a labelled assumption by the deepening stages to 0 m in 2024 and −40 m by 2033; the mining allocation is 8.19 km², 430 m deep). Six benches are worked: waste at 215 m (east end) and 155 m (south-west), clay ore at 200 m (west end, where the kaolinised ore is), sulphide ore at 140 m and 95 m (north wall) and on the floor. The main ramp (8 %) runs down the north wall from the east end, the west ramp (10 %) down the south wall from the west end; neither needs a switchback. Every node lies on a loop (`test_twins`: closing any one segment leaves every node reachable).
- **Plants**: the crusher of ОФ-1 at the east exit, the 3.8 km conveyor north-west to the plant site; ОФ-2, its feed stockpile and the clay-ore stockpile beside ОФ-1, reached from the west exit; the shared concentrate store and tailings north of the site; the waste dump south of the pit.
- **Clay** (`geology.py`, group `clay`): a block's clay is the background clay of sulphide ore (3 %, log-normal) plus kaolinisation `exp(−depth / 60 m)` + a western bias + fault zones of a random field, mapped linearly from 0 (index 0.40) to fully kaolinised (40 %, index 0.70). Ore with at least 15 % clay is clay ore (`clay`), its grade × 1.2 (Q4 2020: 0.61 vs 0.51 %). Base rule: sulphide ore to the crusher, clay ore to ОФ-2, low-grade ore to its pad, waste to the dump.
- **ОФ-1 with clay**: recovery = 81.1 + 3.3 ln(g / 0.39) − 20 (BWI / 14 − 1) − 0.6 (clay − 3.4) − overfeed (`process.recovery_pct`); the circuit grinds the power limit × (1 − 2 % per point of clay above 3.4 %), not below half (`clay_handling`). At the grade-control limit of 8 % clay ОФ-1 still passes its 3,557 t/h; at 12 % it does not. No published source gives either coefficient (0.6 points and 2 % per point of clay) or the 8 % and 15 % thresholds: the literature confirms the direction of each effect, not its size (`docs/mine/clay-literature.md`, cited as [Л…] and [Б…] in the notes of `params.yaml`); `mine/tests/sensitivity_clay.py` scales both coefficients by 0.5 and 1.5.
- **ОФ-2**: draws its constant 755 t/h from its feed stockpile (35,736 t, ecoportal 66983); the desliming cyclones send 5 % + the clay share of the solids and 0.8 of that share of the copper to the slimes; sands flotation 80.8 % at the reference grade, slime flotation 35 %: 66.5 % on the reference clay ore (34 % clay, 0.47 % Cu), the figure derived for 2024. A wheel loader feeds the feed stockpile from the clay-ore stockpile when it falls below 30 % (to 50 %). A truck whose load does not fit on the feed stockpile tips on the clay-ore stockpile, and if that is full too, at the crusher of ОФ-1.
- **The mine**: at the reference feeds the two plants give 78.1 % (`test_model`); seven days without modules give 78.6 % at 0.41 % Cu and 111 kt of copper a year (2024: 78.1 %, 0.40 %, 109.6 kt).

Every number with a physical meaning comes from `params.yaml`; `docs/mine/parameters.md` is generated from it by `mine/model/build_params_doc.py`, and a test checks that the two match.

### 4.1 Scenarios

A scenario is a twin started by the live message `activate {target?, params?}` and stopped by `cancel {}`. It changes the mine only through its own subsystem; everything downstream follows from the model. Several scenarios may run at once.

| Twin | Change | Chain indicators (`kpi1…kpi4`) | Cancel |
|---|---|---|---|
| `scenario-road-closure` | closes one road segment; trucks re-route at the next node | loaded haul km → trip cycle → ore to crusher → stockpile hours | segment reopens |
| `scenario-shovel-failure` | forces a loader down; its trucks go to other loaders | dig rate → queue per trip → ore to crusher → crusher feed grade | loader repaired |
| `scenario-crusher-stop` | stops the primary crusher; trucks wait, then tip on ROM pads; the mill draws the stockpile down | crusher rate → ore to ROM pads → stockpile hours → mill feed | crusher restarts |
| `scenario-heavy-rain` | heavy rain and wet snow (default rate from `weather.rain_heavy_mm_h`), all roads wet, ramp sections at or above `road.wet_closed_grade_pct` closed unless the closure would cut a face off (on this world: the top section of the west ramp) | mean speed limit → trip time without waiting → diesel l/t → ore to crusher | status `cancelling` until the roads have dried |
| `scenario-blast` | fires a drilled or charged block outside the plan: clearance, exclusion zone with road closures and withdrawn machines, blast, re-entry wait | dig rate → trip cycle → broken stock → ore to crusher | before the blast: called off; after: zone lifted |
| `scenario-clay-face` | the block a loader digs (default `loader-03`, a sulphide face) turns out clay-rich: its `clay_pct` becomes `scenario.clay_face_pct` (32 %, `params.clay_pct` 15–40) while its class by the block model stays sulphide ore; without the grade-control module its ore goes on to ОФ-1, with it the module reroutes the face to ОФ-2 or the clay-ore stockpile | clay in the ОФ-1 feed → ОФ-1 recovery → ОФ-1 mill limit → clay ore to ОФ-2 and the clay-ore stockpile | the block gets its clay of the block model back |
| `scenario-clay-plant-stop` | stops ОФ-2: its feed stockpile fills from the pit, then the trucks tip on the clay-ore stockpile; ОФ-2 makes no copper | ОФ-2 feed → its feed stockpile → clay ore to the clay-ore stockpile → ОФ-2 copper | ОФ-2 starts again after `clay_plant.restart_min` (state `starting`) |
| `scenario-sag-reline` | a planned reline of the SAG mill (`params.start`: `next-shift` by default, `shift-after`, `now`, or `start_in_h`; `params.cause`: `full` 72 h, `slow` 100 h, or `expected_h` 12–100 h): phase `planned` until it begins, then `reline`: the mill grinds out its charge and the whole ОФ-1 milling stands (one SAG mill feeds both ball mills); the crusher fills the stockpile until it is full, then the ore goes to the ROM pads by the shift rule (or where the dispatch module sends the trucks); ОФ-2 runs on. `plan-reline` to `mill-sag` moves a planned start | ОФ-1 mill feed → crusher rate → stockpile tonnes → ore to the ROM pads | before the start: the plan is dropped; during the stop: it ends and the mill starts again after `mill.restart_min` |

Every scenario but the blast has an **expected end** (`mine/sim/durations.py`): what the shift supervisor is told when it starts and what every forecast assumes. It publishes `expected_end_sim_time`, `expected_duration_h`, `expected_remaining_h` and `duration_basis` (Russian; before the start the twin shows the default duration and its basis), and the causes it can be started with in `attributes.spec.causes`:

| Scenario | Cause (`params.cause`) | Expected duration | Basis (`params.yaml`) |
|---|---|---|---|
| crusher stop | `mechanical` (default): hydraulics, lubrication or drive, a part from the store | 8 h | `crusher.repair_mechanical_h`, assumption; a planned mantle change takes about 60 h (Metso, La Caridad) and is not a shift decision |
| crusher stop | `jam`: the chamber blocked by oversize or tramp metal | 3 h | `crusher.repair_jam_h`, assumption |
| shovel failure | breakdown at the face | 8 h | `reliability.loader_mttr_h`, the model's mean loader repair |
| road closure | a slide onto the road, cleared by the road crew | 6 h | `road.closure_expected_h`, assumption |
| heavy rain | 10–15 mm at 4 mm/h | 3 h, then `road.drying_min` | `weather.heavy_event_h`, derived from the climate normals |
| SAG reline | `full`: the full reline of a 40-ft SAG mill (planned, not a failure) | 72 h | `mill.reline_h`, assumption from Bradken's estimate for a 12 m (40 ft) SAG mill (The Australian Mining Review, 8 June 2021) |
| SAG reline | `slow`: the same by older methods | 100 h | `mill.reline_slow_h`, the same source («historically upwards of 100 hours») |
| clay face | `fault-zone`: a zone of kaolinisation along a fault the block model did not show | 12 h | `scenario.clay_face_expected_h`, assumption |
| ОФ-2 stop | `mechanical` (default): a slurry pump, the SAG drive or a conveyor, a part from the store | 6 h | `clay_plant.repair_mechanical_h`, assumption; the long stops of ОФ-2 (three months in H1 2019) were planned upgrades |
| ОФ-2 stop | `slurry`: the desliming cyclones and slurry lines choked with clay | 3 h | `clay_plant.repair_slurry_h`, assumption |
| ОФ-2 stop | `pipeline`: one slurry line blocked, flushed and cleared without replacing parts | 1.5 h | `clay_plant.repair_pipeline_h`, assumption |

`activate` also takes `params.expected_h` (0.25–24 h) to set the duration by hand. On the stand a scenario still runs until it is cancelled; if it outlasts its expected end, the estimate moves on by `scenario.overrun_extend_share` of the original duration («ремонт затянулся»). In a forecast branch (`Simulation.auto_end`) the scenario ends at its expected end. The crusher's twin shows the expected restart (`restart_expected_sim_time`), a broken-down loader when its repair ends (`repair_end_sim_time`: the scenario's expected end, or for a natural breakdown the mechanics' estimate, the repair time drawn at the failure).

### 4.2 Response resources

`mine/sim/response.py`: what the supervisor can spend on an incident besides the levers the modules pull. Each is a live message to the twin it acts on (section 5.2); what it costs accrues while it works (`mine.response_cost_kzt`, per scenario `response_cost_kzt`, and in a forecast the branch's `resources_kzt`). Rates and factors are in `params.yaml`, group `response`.

| Resource | Message | Effect | Cost |
|---|---|---|---|
| second repair crew | `expedite-repair` to the crusher, ОФ-2 (`clay-plant`) or a broken-down loader | the rest of the repair × `second_crew_factor` (0.6); a scenario's expected end moves earlier, a natural repair's timer shortens | own fitters on overtime (`own_crew_kzt_h` = 55 400 KZT/h, from the mining wage of the Bureau of National Statistics × 1.5 overtime × 1.2 payroll × 6) plus two contractor specialists at 150 USD/h, at least 8 h of them (1.08 M KZT at once) |
| workshop crew to the face | `prioritise-repair` to a broken-down loader | its repair × `workshop_lend_factor` (0.75); one workshop bay takes no job meanwhile (`workshop.bays_lent`) | no cash; planned truck services wait |
| emergency road crew | `repair-road` to a segment closed by the road closure | the segment opens after `road_repair_h` (2 h) at `repaired_kmh` (15 km/h, `speed_cap_kmh`) until the full repair is due | 358 800 KZT/h for 2 h |
| grader and topping | `treat-surface` to a ramp the rain closed | the ramp opens after `grading_h` (1.5 h) at `treated_wet_kmh` (15 km/h) while wet | the topping (5 cm of own crushed waste at the fleet work per tonne) at once, the grader by the hour while it rains |
| workshop overtime | `expedite-repair` to the workshop | every repair of a broken-down truck × 0.6 for `workshop_overtime_h` (12 h) | own crew rate while a repair is under way |

A resource that cannot be applied is refused with a journal warning. Putting off a planned service (`cancel-service` with `hold_min`, an existing lever) keeps a truck at work; its failure rate then grows by `reliability.overdue_hazard_slope` per service interval overdue (0.4 % per hour), as the advisor's label states.

The blast twin publishes `valid_targets`; the page offers only those. Each indicator is compared over two windows of equal length (at most `scenario.kpi_window_max_min`), and `kpiN_significant` is true only if the change exceeds `scenario.kpi_noise_k` times the swing measured before the start, or is large (`scenario.kpi_large_change_pct`). The «before» window must be clean: no other scenario in it, nor within `scenario.kpi_recovery_min` before it; when scenarios run back to back the latest clean window of the same length further back is used, ending at most `scenario.kpi_baseline_max_age_min` (60 min) before the start, and `kpi_baseline_note` says where it lies; if there is none that recent, «база недоступна» with `kpiN_baseline_known` false and no significance (an older window shows another state of the mine). The page takes `kpiN_significant` false as «в пределах колебаний» whatever the size of the change, except a change from or to zero. The full-length «before» side is fixed at the start, so a scenario that runs longer than the kept history (`scenario.kpi_history_h`) keeps its baseline. A road closure's trip indicators (loaded haul, trip time) count only trips begun after the closure; until `scenario.route_trips_min` of them have finished, «now» of the loaded haul, the trip time and the ore to the crusher is the forecast from the open routes (the value before times the ratio of the route figure now to the one at the start: haul length, expected cycle, the ore the fleet can bring) and `kpiN_forecast` is true. A clay face is read the same way for the clay ore tipped at ОФ-2 and the clay-ore stockpile: until `scenario.route_trips_min` trips begun after the start have finished, «now» is the value before plus the face's dig rate if its ore goes to one of them now («прогноз по назначению руды забоя»). A scenario whose cancel takes time (heavy rain: the roads dry for `road.drying_min`) is `cancelling` meanwhile and publishes `cancel_remaining_min`, the model minutes left; both are bounded by those parameters. `effect_delay_text` states when the effect reaches the plant at the current speed.

## 5. Message contracts

### 5.1 Telemetry (MQTT, simulator and module services → Ditto)

Topic `telemetry/<name>`, payload a Ditto protocol envelope (`mine/common/envelope.py`):

```json
{"topic": "kz.bozshakol/truck-05/things/twin/commands/modify",
 "headers": {"correlation-id": "telemetry-truck-05", "response-required": false},
 "path": "/features",
 "value": {"state": {"properties": {"value": "hauling"}},
           "speed_kmh": {"properties": {"value": 12.4, "unit": "km/h"}}, "…": {}}}
```

The correlation ID is fixed per twin, so Telegraf's `correlationId` tag does not create a new series per update. A module service registers a last-will `merge` envelope that sets its twin's `status` to `offline` if its connection drops.

### 5.2 Live messages (HTTP → Ditto → MQTT → simulator or module)

| Addressed twin | Subject | Body | Sent by | Effect |
|---|---|---|---|---|
| `scenario-*` | `activate` | `{target?, params?}` | page | starts the scenario; `params.cause` and `params.expected_h` set its expected end |
| `scenario-*` | `cancel` | `{}` | page | stops it |
| `mine` | `set-speed` | `{speedup: 1/10/20/60}` | page | simulated speed |
| `mine` | `pause`, `resume` | `{}` | page | pauses the model |
| `mine` | `forecast` | `{slot, horizon_h?, replicas?, label_a?, label_b?, actions_a?, actions_b?, requested_by?, subject?, request_id?}`, action = `{thing, subject, body}` | page, advisor, modules | two-branch forecast into `forecast-<slot>`, `replicas` paired replicas (default 1) |
| `module-*` | `set-enabled` | `{enabled}` | page, advisor option | module on or off |
| `module-advisor` | `apply` | `{option}` | page | the advisor sends the option's messages |
| `truck-*` | `assign`, `release` | `{loader?, dump?, route?}` | dispatch | next loader, dump, route (`node>node>…`) |
| `block-*` | `set-destination`, `release` | `{destination, reason?, hold_min?}` | grade, advisor | where the block's rock goes: `crusher`, a ROM pad, `waste-dump`, `clay-plant` (the ОФ-2 feed stockpile) or `clay-stockpile` |
| `rom-pad-*` | `set-reclaim`, `release` | `{tph, hold_min?}` | grade, advisor | wheel-loader feed from the pad to the crusher |
| `mill` | `set-feed-rate`, `release` | `{tph, hold_min?}` | plant, advisor | mill feed setpoint |
| `mill-sag` | `plan-reline` | `{start: now / next-shift / shift-after}` or `{start_in_h}` | advisor | moves the start of a planned reline of the SAG mill; refused once it has begun or when none is planned |
| `truck-*`, `loader-*` | `schedule-service` | `{reason, …}` | maintenance | booking for the workshop |
| `truck-*`, `loader-*` | `cancel-service` | `{hold_min?}` | maintenance, advisor | withdraws a booking |
| `loader-*` | `move-to-block` | `{block}` | advisor | relocates a loader |
| `crusher`, `clay-plant`, `loader-*`, `workshop` | `expedite-repair` | `{}` | advisor | a second repair crew (the workshop: an overtime crew), section 4.2 |
| `loader-*` | `prioritise-repair` | `{}` | advisor | the crew of a workshop bay repairs the loader at its face |
| `road-segment-*` | `repair-road`, `treat-surface` | `{}` | advisor | an emergency road crew on a closed segment; a grader and a topping on a ramp the rain closed |
| `journal` | `record` | `{text, severity?, thing?}` | advisor | journal entry |

A module decision without `hold_min` lapses unless it is repeated within `sim.decision_timeout_min` = 30 simulated minutes, so a stopped module leaves no stale orders. A message with `hold_min` is an operator order: the simulator keeps it for that time and ignores the owning module's decisions for that lever meanwhile. A message that cannot be applied is refused with a journal warning.

### 5.3 Page gateway (`mine/ui/nginx.conf`)

| Path | Method | Purpose |
|---|---|---|
| `/config.json` | GET | the namespaces the page should show |
| `/ditto/api/2/things…`, `/ditto/api/2/search/things` | GET | things, search, server-sent events |
| `/ditto/api/2/things/<ns>:scenario-*/inbox/messages/(activate\|cancel)` | POST | scenario buttons |
| `/ditto/api/2/things/<ns>:module-*/inbox/messages/set-enabled` | POST | module switches |
| `/ditto/api/2/things/<ns>:mine/inbox/messages/(set-speed\|pause\|resume\|forecast)` | POST | speed, pause, forecasts |
| `/ditto/api/2/things/<ns>:module-advisor/inbox/messages/apply` | POST | «Применить» |
| `/influx/query?thing=<id>&range=5m…3h` | GET | numeric history of one twin, written as Flux by nginx |
| `/influx/journal?ns=<ns>` | GET | the last 80 journal events within 6 h |
| `/grafana` | GET | redirect to `http://<host>:3000/d/bozshakol-history` |

Everything else under `/ditto/` and `/influx/` is refused. `mine/ui/tests/gateway_check.sh` checks these rules and that no credential appears in the page or in any answer.

## 6. Forecast engine (`mine/sim/forecast.py`)

A `forecast` message to the mine twin asks for two branches from the same moment. At the next tick the simulator deep-copies its live model and gives each branch to a worker process forked from the simulator, so the live loop does not wait. A worker applies its branch's actions as if they had arrived as live messages, then runs the copy to the horizon (12 h by default, at most 24 h; while a reline of the SAG mill is planned or under way at most `forecast.planned_max_horizon_h` = 128 h, so that the advisor sees the whole stop of every option) together with the module cores (`coupling.py`), each in the state the stand reports for its module, exchanging with the model every `forecast.period_s` = 20 simulated seconds as on the stand. Up to `forecast.max_parallel` = 12 branches run at once (never more than the processors less one); the page's two branches start beside them at once, the advisor's go before the modules', and a module's branch that holds a worker when the advisor's branches arrive is stopped and started again later from the same copy. A branch is stopped after `forecast.timeout_s` = 120 s, for a horizon beyond a day after proportionally longer (`timeout_s × horizon / 24 h`). The same model state with the same actions gives the same result, and because every random process has its own stream (section 4), branch B shares A's random numbers draw for draw. A request with `replicas` = K runs K paired replicas: replica 0 on the live streams, replica k on streams drawn anew from the fork moment and k, the same for A and B.

A branch ends every running scenario at its expected end (section 4.1), unless an action moves that end; `assumption_text` says when each branch takes the incident to end. Results go to `forecast-<slot>`: per branch copper, ore, waste, milled tonnes and the copper fed to the mill (head grade), where the mill's ore came from (stockpile drawdown, ROM pads, pit, each in tonnes and copper), the stockpile's hours of feed at the end, diesel, energy, revenue, costs, variable margin, rehandling, the end-of-horizon adjustment in its parts (`grade_kzt`, `work_kzt`, `pads_kzt`, `strip_kzt`), what its response resources cost (`resources_kzt`), total = margin + adjustment − resources, CO₂, module indicators (queue, grade in band, availability, breakdowns, mill stops, overload trips) and hourly series of mill feed and stockpile level (means over the replicas; series of replica 0); the differences B − A with their standard error over the replicas (`replicas`, `delta_total_se_kzt`, `delta_margin_se_kzt`, `delta_cu_se_t`, `delta_total_replicas_kzt`, `delta_cu_replicas_t`); and three Russian texts: `verdict_text` (the decision figure: which branch is better by the total and whether that is within the forecast's scatter), `copper_text` (the copper difference split into tonnes milled, head grade and recovery, the sources of the mill's ore, what the total counts of it, the fleet work, the overload trips, the stockpile left) and `summary_text` (all of it with the branches' figures and the basis of the money).

Branch figures added for the two plants: `clay_milled_t`, `clay_milled_cu_t`, `clay_cu_t` (ОФ-2), `to_clay_stock_t` (the clay-ore stockpile built up), `ofs1_clay_pct` (the clay of the ОФ-1 feed); `cu_t` is the copper of both plants, `milled_t` and `milled_cu_t` stay ОФ-1's; `copper_text` splits the copper difference into ОФ-1 tonnes, grade and recovery (with its clay) and ОФ-2.

Two bases (`economics.py`): ore in the ОФ-1 chain and on the ROM pads is valued on the ОФ-1 basis (its copper above the sulphide ore's average 0.39 % at 81.1 %, less 0.6 points of recovery per point of clay above 3.4 % of the copper of an average feed), ore on the ОФ-2 feed stockpile and the clay-ore stockpile on the ОФ-2 basis (above 0.47 % at 66.5 %, less the slope of the desliming model, about 0.37 points per point of clay above 34 %); ore in trucks and in the pit by what the rock is at the face. Moving ore between heaps of one plant changes nothing; sending clay ore to the other plant changes its basis when it is tipped, which is what blending it there costs or gains.

Money (`mine/common/economics.py`): the variable margin is payable copper at the copper price less smelter charges (TC/RC), less truck diesel and electricity; labour, repairs, explosives, reagents and depreciation are excluded. The adjustment values what the horizon leaves behind on one basis for every ore inventory — broken ore in the pit, truck bodies, the crusher's pocket, the belt, the coarse-ore stockpile, the ROM pads, the ore held in the mill: the copper a tonne will yield at the reference recovery and the copper price, net of what it still costs to turn it into concentrate. The mill is the bottleneck, so the largest of those costs is the mill time the tonne takes, in which the mill would grind a tonne of the mine's average ore (`geology.mean_head_grade_pct`); processing energy per tonne is the same for both and cancels. A tonne of ore is therefore worth `cu value × (its Cu − average grade × its tonnes)`, plus the fleet work it saves once dug (`economics.stock_kzt_t` a tonne), less the rehandling it still needs on a ROM pad (`rom.rehandle_kzt_t`, charged when it is tipped there and paid when the loader feeds it back). The value is linear in tonnes and copper: mixing heaps changes nothing, and moving ore between heaps changes the total only by what the move costs. Milling a tonne turns its value into revenue: the copper above the average grade is copper the stocks give up (`grade_kzt`), so drawing down a rich stock or digging a rich face earns nothing by itself, and what earns is the mill time, the recovery and the costs. Broken ore in the pit counts by what is loaded from it, so blasts and pushbacks create no value. Waste stripped counts at the same fleet work per tonne (`strip_kzt`). Options are compared by `total = margin + adjust − resources`; the advisor splits a gain into copper, copper given up by the stocks, fleet work, rehandling, diesel, power and the shift's resources («ресурсы смены»), and does not recommend one whose money of the horizon (all but the fleet work) is below `advisor.min_gain_kzt` while the copper hardly differs or that money is less than half the gain.

Paired noise, measured at 16 fork moments two hours apart with every module on (`forecast.noise_kzt`, `forecast.noise_cu_t`): a branch with one truck carrying 300 l less fuel differs from «change nothing» by a standard deviation of 7.6 M KZT and 0.68 t Cu over 12 h (11.2 M KZT before the streams were keyed by entity); 5 m more to drive for one truck, 0 (16.8 M KZT before). What remains is not random numbers but the dispatch module answering a small shift in time with other assignments, one or two truckloads of ore more or less, or from another face; without modules the same shift gives 0.9 M KZT. The noise is taken as 8 M KZT and 0.7 t per replica.

Users of the engine:

- the page (slot 1): «Прогноз на 12 ч» on a scenario card (B = the scenario started, or cancelled if it runs) and «Что даёт модуль?» on a module card (the module on in one branch and off in the other);
- each control module (slots 6–9), every `forecast.module_period_h` = 3 simulated hours: A with the module, B with it switched off; the mean of the last `forecast.module_results_kept` = 4 results is the module's published effect;
- the advisor (slots 2–5): A = change nothing, B = one response option, `advisor.replicas` = 3 paired replicas each (4 slots × 3 replicas, the shared «change nothing» branch computed once per replica: 12 processes). Over 12 h; for a planned reline of the SAG mill over one horizon for every option: from now to the end of the stop of the latest option plus `scenario.reline_recovery_h` (4 h): 97 h for a 72-h reline planned at the next shift change with «a shift later» among the options; the advisor publishes it as `analysis_horizon_h`, and its forecasts take 90–100 s on the 24-core stand.

## 7. Decision modules (`mine/modules`)

`runtime.py` is the shell every module service runs: read all twins over HTTP once the twin set is stable, subscribe to `opentwins/twin/events/<ns>/#` and to its own live messages, call the core every 0.5 s, send its messages over HTTP, publish its twin on change and at least every 30 s, honour `set-enabled` (the switch survives a restart through the twin's `enabled` feature). `base.py` holds the shared core: the picture of the mine built from twin reports, the decision count, and the effect measurement with forecasts.

| Module | Lever (live message → twin) | Rule | Without it |
|---|---|---|---|
| dispatch | `assign` → trucks | for an empty truck, the loader with the least travel + queue + loading (+ crusher wait for ore), corrected to keep ore and waste in the planned proportion, and a clay face made to cost up to `dispatch.clay_full_min` while the ОФ-2 feed stockpile has little room; a loaded truck tips where its block is destined unless the crusher wait costs more than tipping on a ROM pad (clay ore: on ОФ-2 or the clay-ore stockpile) | each truck serves its home loader; an ore truck tips on the ROM pad only when `crusher.divert_queue_len` trucks already wait at the crusher, or the crusher has been stopped longer than `crusher.stop_wait_min` |
| grade | `set-destination` → blocks, `set-reclaim` → ROM pads | classifies blasted blocks against the cut-off and by the clay the face shows (again when a face's clay changes: a clay-rich face of a sulphide block becomes clay ore); picks the combination of face destinations — a clay face to ОФ-2 while its feed stockpile has more than `grade_control.ofs2_room_t` of room, to the clay-ore stockpile, or into the ОФ-1 blend while the predicted clay of the ОФ-1 feed stays within `grade_control.clay_limit_pct` (8 %) — and wheel-loader reclaim that keeps the predicted stockpile grade in the target band and the clay within its limit, with the least rehandling | every block goes where the block model put it: sulphide ore to the crusher (a clay-rich face too), clay ore to ОФ-2 |
| plant | `set-feed-rate` → mill | `min(ceiling, max(lowest stable feed, ore arriving + (stockpile − reserve) / horizon))`, the horizon shortened to the crusher's expected restart while it is stopped (`crusher.restart_expected_sim_time`), and for one trend window after a restart the ore taken to arrive as fast as the mill takes it (the trucks are on their way back). The ceiling (`power_limit_tph`) has two levels: the sustained one, `plant_control.sustained_tph` = 3,557 t/h, ОФ-1's 2024 average (28.67 Mt over 8,059 operating hours), and short peaks up to `plant_control.max_feed_tph` = 3,902 t/h, the 2024 trial rate (ecoportal 68959; 3,900 in steps of 10), only while the stockpile is at least `peak_from_pct` = 65 % full (a started peak lasts down to `peak_hold_pct` = 55 %), the crusher runs and the shift has peak time left: `peak_h_shift` = 2 h a shift, counted by the mill itself (`mill.peak_min_shift`), so a forecast's copy of the mine knows what the shift has used. The shift average is then at most (10 × 3,557 + 2 × 3,900) / 12 = 3,614 t/h, `shift_margin_pct` = 1.6 % above the sustained ceiling (checked for every shift of the 7-day runs). Both levels are capped at `plant_control.power_margin` = 97 % of the mill's power limit (`mill.max_feed_tph`: installed power / specific energy at the hardness on the stockpile, times the clay factor). The twin publishes `sustained_tph`, `peak_tph` and `peak_left_min` | constant setpoint 3,557 t/h, the same rate: on hard or clay-rich ore the mill overloads and stops |
| maint | `schedule-service`, `cancel-service` → trucks, loaders | detects developing defects from the gap below the wear line and its trend, books the workshop so few machines are out at once, one bay kept for breakdowns | run to failure |
| advisor | `forecast` → mine; on `apply`, the option's messages | on an incident (crusher stop, ОФ-2 stop, a planned or running reline of the SAG mill, loader down, clay face, road closed, rain, truck cluster out, hard ore) reads its expected end and basis; for a clay face it offers moving its shovel to a free blasted sulphide block, the face to the clay-ore stockpile, into the ОФ-1 blend above the module's limit (to `advisor.clay_blend_limit_pct`) or straight to ОФ-2 (with the grade-control module on, labelled as holding the face on ОФ-2 for the hold time, since the module would also blend it into ОФ-1; the incident text names who routes the face now and is refreshed when that changes); for a planned SAG reline to begin it now, at the next shift change or a shift later instead of the plan (`plan-reline` to `mill-sag`) and to move the ore shovels to free blasted waste blocks for the stop, every option over one horizon that holds the whole stop of the latest of them plus `scenario.reline_recovery_h` (published as `analysis_horizon_h`; the wait before asking again grows with it); for an ОФ-2 stop a second crew on ОФ-2, the clay faces to the clay-ore stockpile, the clay shovel to a sulphide block, the clay into the ОФ-1 blend; each clay option carries the situation in its reason (room on the stockpiles, the clay ОФ-1 would get and the recovery it costs, the move time, the hours to the restart) (`incident_end_sim_time`, `incident_basis`), builds up to four options, always starting with «Ничего не менять» and, if modules that answer the incident are off, switching them on as one option, then the supervisor's resources (section 4.2: a second crew on the crusher or the loader, the workshop's crew, an emergency road crew, a grader on the rain-closed ramp, workshop overtime) before the levers the modules also pull; an option's orders stand until the incident's expected end; publishes `optionN_cost_kzt` and `optionN_reason`, one sentence on why it pays («без этого мельница встанет через N ч», or, when the feed is only cut, «через N ч модуль управления фабрикой начнёт снижать подачу мельницы (склада на X ч питания не хватит до пуска дробилки в HH:MM) и снизит её до Y т/ч») or not («склада хватает до конца ремонта»); a gain that rests on inventory says «меди не прибавляет» only when the copper differs by less than `advisor.min_cu_t`, else where the copper comes from («медь +2,9 т взята из запасов богатой руды…»); forecasts each over `advisor.replicas` paired replicas and ranks by the mean gain in total, then copper; recommends only an option whose mean gain is at least `advisor.min_t` standard errors (the error over the replicas, not below `forecast.noise_kzt` / √replicas) and `advisor.min_gain_kzt`, that loses at most `advisor.max_cu_loss_t` copper (a hard bar) and does not owe its gain to inventory alone; publishes `optionN_gain_se_kzt` and `optionN_confidence` (высокая / средняя / низкая / нет); when nothing is recommended, names the modules that are on and already answer the incident; analyses a lasting incident again when the stockpile has lost `advisor.reanalyse_stock_h` hours of feed | incidents run under the base rules |

## 8. Page (`mine/ui/site`)

A static single page (`index.html`, ES modules in `js/`). The 3D view of the mine fills the window; everything else floats over it: the top bar (search, «Объекты», simulated clock with shift and weather, speed, «Пауза», «О модели», «Справка»), the ribbon of six shift figures under it, the right stack (advisor, what-if scenarios, decision modules; each row expands into its card), the left drawer with the object list and the selected object's card, and the bottom bar (3D or plan, the points of view «Обзор», «Забои», «Съезды», «Фабрика», zoom, «Слои и знаки», the one-line journal that opens the full journal with the Grafana link).

| File | Role |
|---|---|
| `main.js` | start-up: loads the twins, keeps them current, connects the 3D view, the plan and the panels; the view is part of the address (`?view=plan`, `?view=3d`), the selected twin too (`#/twin/<name>`) |
| `store.js`, `api.js` | every twin kept current from Ditto server-sent events, relations from links, parents and relation features; all requests go through the page's own gateway |
| `scene3d.js` | the 3D view (three.js): pit, steppe, roads, dump, ROM pads, plant and the machines where they report; points of view, selection, captions, scenario marks (closures, exclusion zone, affected twins); falls back to `softrender.js` (2D canvas) without WebGL |
| `terrain3d.js`, `pitgeom.js`, `geom3d.js`, `textures3d.js` | the ground: the pit bench by bench with rock colour by depth, haul roads with windrows, the terraced dump; vertex-lit geometry (lighting computed on the CPU, so a frame stays cheap with software WebGL) and procedural textures, nothing downloaded |
| `models3d.js`, `plant3d.js`, `motion.js` | low-poly models in real sizes: Caterpillar 785C trucks, Hitachi EX3600 shovels, Sandvik D55SP drills (instanced, with moving parts and two levels of detail); crusher, conveyor gantry, stockpile, mill hall, flotation, thickeners, tailings, substation; trucks travel along the road geometry between reports |
| `sky3d.js` | the sun at the mine for the simulated time (day, dusk, night with headlights, floodlight towers at the faces and plant lights), sky, haze, rain and snow from the weather twin, dust behind trucks and over a blast; `?time=HH:MM` previews the lighting of that hour and changes nothing else |
| `map.js`, `theme.js` | the 2D plan on two canvases (static and moving layer) and its colours and legend |
| `topbar.js`, `planui.js`, `plant.js`, `rates.js` | the top bar and the six figures with their detail (the process chain from the faces to the flotation); the bottom bar, layers, journal popover, scenario chips; the chain and the flows from the same 15-minute rates everywhere |
| `control.js`, `advisor.js`, `forecast.js` | scenario and module cards, the advisor card with «± SE» and confidence per option and «Применить», the two-branch forecast panel |
| `objects.js`, `labels.js`, `dom.js`, `about.js` | the object list and card with history sparklines from `/influx/query`; Russian names of kinds, features and states; «О модели» with the live model summary |

## 9. History dashboard (`mine/grafana`)

`mine/compose.yml` replaces Grafana's entrypoint with `mine/grafana/start.sh`, which starts Grafana and runs `setup.sh`: it creates the organisation «Бозшаколь» that anonymous visitors enter as viewers, an InfluxDB data source in it with a read-only token for the history bucket, and the dashboard «Бозшаколь — история» from `mine/grafana/dashboards/bozshakol-history.json`, uid `bozshakol-history`, which the page's `/grafana` redirect (`mine/ui/nginx.conf`) names; a dashboard of the same title under an older uid is deleted first. The platform's own organisation gets the same dashboard by provisioning. Panels: ore along the chain, stockpile level, Cu in concentrate, variable margin, CO₂, diesel per tonne, scenario states, trucks by state, truck state timeline.

## 10. Tests

| Suite | Runs | What it checks |
|---|---|---|
| `make mine-test` (`mine/tests/test_*.py`, 219 tests) | in the mine image, no platform | `test_twins`: generated things, pit geometry, parameters and their document, fleet sizing, block model. `test_model`: haulage physics, pit cycle, plant, failures, messages. `test_scenarios`: each scenario's chain and return to normal, also with modules on; the clean «before» window, «база недоступна», the route forecast of a road closure, the bounded cancel of heavy rain, the clay face (clay in the ОФ-1 feed, recovery and mill limit down, the grade-control module rerouting the face) and the ОФ-2 stop (feed stockpile, clay-ore stockpile, restart); the planned SAG reline (planned, the stop with the stockpile filling and the ore to the ROM pads, `plan-reline`, refusals, its end in a forecast). `test_modules`: each module against the same mine without it over 7 simulated days (plant control also against the other three modules alone, and every shift's mill average within the sustained ceiling plus its 1.6 % margin, at most 2 h of peaks a shift), determinism, lapsing decisions. `test_forecast`: money and CO₂ arithmetic, the forked copy, determinism, forecast twins, paired replicas, entity-keyed streams (identical actions give identical branches, a negligible change changes almost nothing, the paired noise with modules within `forecast.noise_kzt`). `test_response`: each incident's expected end and its basis, the cause and a given duration, the end in a forecast and the overrun on the stand; each resource changes what it should and costs what `params.yaml` says (second crew on the crusher and on a natural loader breakdown, the workshop's crew and its idle bay, the emergency road crew and its speed limit, grader and topping, workshop overtime), the failure rate of a postponed service. `test_forecast` also: the incident ends in the branch and runs on the stand, a second crew moves the end and costs its price, `assumption_text`. `test_advisor`: incidents, options, ranking, the standard-error rule and confidence, the same crusher jam flipping the second crew's recommendation with the stockpile level, the second crew on ОФ-2 flipping with the hours to its restart, the clay face's answer flipping with a free sulphide block to move its shovel to, copper as a hard bar, modules switched on as one option, the text when nothing is recommended, re-analysis of a lasting incident, `apply`, orders vs modules; the reline options over one horizon that holds every option's whole stop (and that over 12 h the later start would look better), their ranking, `apply` of «begin now». `test_soak`: 7 simulated days with and without modules against the Bozshakol basis ranges |
| `python -m mine.tests.sensitivity_clay` (not in `make mine-test`, about 6 min) | in the mine image, no platform | the two clay coefficients of ОФ-1 scaled together by 0.5, 1 and 1.5: recovery of the mine and of each plant and copper a year over 7 days without and with modules, and whether the advisor's clay decisions (clay face with and without a free sulphide block, ОФ-2 stopped for 6 and 1.5 h) keep their recommendation |
| `make mine` → `ready_check.sh` | on the stand | twins, telemetry, history, page |
| `make mine-check` → `stand_check.sh` | on the stand | twins, simulation, history, page, each module switched off and on, each scenario started and cancelled with its expected end (the SAG reline: planned, begun by `plan-reline`, cancelled), a second crew sent to a jammed crusher (restart earlier, its cost), the page forecast round trip, the advisor applied (its resources' actions checked in their twins), module effects measured |
| `mine/ui/tests/ui_check.mjs` | headless Chrome against the stand | the full-window 3D view and its first frame, every machine of the twins on the scene, points of view, captions without overlap, the six figures and their chain, the object drawer and search, the road closure through the page, a module switch, forecasts, the scenario cards' expected duration and end with their basis and the crusher's causes, the advisor's expected end, resource costs and reasons, its ranking within 25 s, and «Применить», the SAG reline from its card (durations and start, «Запланирована на …», the SAG mill's card, the advisor's options over one horizon), blast targets, the plan, deep links, the Grafana link, no console errors in three minutes, the 3D view without WebGL |
| `mine/ui/tests/gateway_check.sh` | against the stand | the gateway's allow-list and that no credential leaks |
| `mine/ui/tests/shots.mjs` | headless Chrome | screenshots of the points of view, a truck close-up, dusk or night, a truck card, a road closure, a blast, the advisor, the plan, a forecast, help, «О модели» and Grafana; `LIGHT_TIME=HH:MM` lights the scene as at that hour |
