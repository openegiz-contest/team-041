# Architecture of the Aktogay mine slice on OpenEgiz

This document describes how the mine slice in `mine/` is built and how it uses the OpenEgiz platform. It reflects the code as it is in the repository; where a number is quoted, its source is named (`mine/model/params.yaml`, `mine/model/world.yaml` or the module that computes it).

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
| Mine | `mine-twins` | `aktogay/mine:local` | One-shot: creates or updates the 182 things (`python -m mine.model.setup_twins`) |
| Mine | `mine-sim` | `aktogay/mine:local` | The simulator and the forecast engine (`python -m mine.sim`) |
| Mine | `mine-dispatch`, `mine-grade`, `mine-plant`, `mine-maint`, `mine-advisor` | `aktogay/mine:local` | One service per decision module (`python -m mine.modules.<name>`) |
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
 mine-sim ── publishes ───────┘   mosquitto ──▶ telegraf ──▶ InfluxDB ──▶ Grafana «Актогай — история»
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

- Thing ID: `<namespace>:<name>`; namespace `kz.aktogay` (`world.yaml`, overridable with `MINE_NAMESPACE`); policy `default:basic_policy`.
- `attributes.name`: Russian title shown on the page. `attributes.kind`: the type of object. `attributes._isType`: false.
- `attributes._parents`: thing ID of the parent (the OpenEgiz convention for the twin hierarchy; Telegraf stores it as the tag `parent`). `mine` is the root; `pit`, road graph, plant, fleet and service twins hang under `mine`; `bench` under `pit`; `block` under its `bench`; `drill-rig` and `loader` under `pit`; `sag-mill` and `ball-mill` under `mill`.
- `attributes.links`: static relations by name, each a thing ID or a list of them (for example `crusher.links.feeds = conveyor`, `road-segment.links.from/to`, `weather.links.affects = [all road segments]`, `module.links.controls`).
- `attributes.geo`: position of a fixed object in metres on the mine grid (`polygon` + `z`, `x/y/z`, or `path` for roads and the conveyor). A machine that moves has `attributes.home` (where it started) instead, and its live position in the features `pos_x`, `pos_y` (`pos_z` for trucks).
- `attributes.spec`: static nameplate and design values (payload, installed power, capacities, design basis text).
- Features: every feature is one scalar `properties.value` with an optional `properties.unit`. A telemetry message always carries the complete feature set of the twin (`mine/common/envelope.py` refuses anything else).
- Writers: the setup job creates things and updates attributes; the simulator is the only writer of the features of the physical twins, the scenario twins, the forecast twins and the journal; each module service is the only writer of its own module twin.
- Dynamic relations are features holding thing IDs: `truck.assigned_loader`, `truck.assigned_dump`, `truck.segment`, `truck.route` (node IDs joined by `>`), `loader.block`, `loader.truck`, `drill-rig.block`, `scenario.target`, `scenario.affected`, `module-advisor.incident_thing`.
- Operator orders carry an expiry in the lever's twin: `block.destination_hold_until`, `rom-pad.reclaim_hold_until`, `mill.setpoint_hold_until`, `truck.service_hold_until`. The `…_source` feature next to each lever names who set it (`cutoff` / `grade-control` / `operator`, `rule` / …, `constant` / `plant-control` / …, `static` / `dispatch` / `fallback` / `overflow`).

### 3.3 Kinds

182 things: 160 physical or environmental twins and 22 service twins.

| Kind | Count | Parent | Main links | Main features |
|---|---|---|---|---|
| mine | 1 | — | pit, crusher, mill, concentrate, tailings, substation, weather, journal, modules, scenarios, forecasts | sim clock and speed, shift and its start; copper, ore, waste and milled ore since the shift began (reset at 08:00 and 20:00) and since the model started; mill feed, head grade, recovery, Cu t/h, cycle, queue, fleet availability over the last day, diesel, energy, revenue, costs, margin, CO₂ |
| pit | 1 | mine | benches, loaders, drill_rigs | depth, broken stock, blast state, blast block and radius, next blast |
| bench | 17 | pit | blocks, access_node | elevation, blocks and tonnes remaining, pushback |
| block | 48 | bench | bench, access_node | status (`insitu` … `depleted`), grade, Bond work index, tonnes, material, P80, powder factor, destination and its source, reason and hold |
| road-node | 17 | mine | segments, serves | trucks waiting |
| road-segment | 20 | mine | from, to | status, closed_by, speed limit, grade, rolling resistance, trucks on, surface |
| drill-rig | 3 | pit | pit | state, block, holes done/planned, penetration, metres, position |
| loader | 5 | pit | pit, power | state, block, truck, passes, dig rate, queue, power, health, service, position |
| truck | 32 | mine | home_node, home_loader, fuel_station, workshop | state, payload, material, grade, speed, position, heading, segment, assignments, route, fuel, engine and tyre temperature, health, service, trips, cycle |
| crusher | 1 | mine | access_node, feeds, power | state, throughput (window and now), capacity, feed grade and P80, hopper, queue, power |
| conveyor | 1 | mine | from, to, power | state, load t/h, load t, power |
| stockpile | 1 | mine | fed_by, feeds, circuit | level t and %, grade, hardness, hours of feed |
| mill | 1 | mine | fed_by, feeds, power, units | state, feed and setpoint, setpoint source and hold, power limit, power, specific energy, feed BWI, overload, stops |
| sag-mill, ball-mill | 1 + 1 | mill | circuit, fed_by, feeds, power | state, throughput, power, installed power, drive load, specific energy, feed BWI; SAG charge |
| flotation | 1 | mine | fed_by, circuit, power, feeds | feed grade, recovery, concentrate grade and t/h, Cu t/h, tails grade and t/h |
| concentrate | 1 | mine | fed_by | level, Cu, inflow, produced, shipped, shipments |
| tailings | 1 | mine | fed_by | inflow, grade, stored t and m³, Cu lost |
| rom-pad | 2 | mine | access_node, feeds | level, grade, hardness, role, queue, reclaim t/h, reclaim source and hold |
| waste-dump | 1 | mine | access_node | tonnes, queue |
| substation | 1 | mine | supplies (loaders, crusher, conveyor, mills, flotation) | load, plant and pit MW, energy per shift |
| fuel-station, workshop | 1 + 1 | mine | access_node | queue, litres issued; bays busy, jobs, breakdowns |
| weather | 1 | mine | affects (all road segments) | condition, rain, precipitation class, road surface, visibility, temperature, climate normals |
| module | 5 | mine | controls, effect_forecast | enabled, status, decisions, last decision, own KPI, effect (Cu, margin, basis, forecast), module-specific features |
| scenario | 6 | mine | targets, default_target | status, target, elapsed, four chain indicators before/now with significance, delay text, affected twins, impact text |
| forecast | 10 | mine | mine, advisor or module | status, request, labels and actions of branches A and B, totals per branch, hourly series, deltas, summary |
| journal | 1 | mine | mine | seq, severity, thing, text, sim time (one event per update) |

Service twins: 5 modules, 6 scenarios, 10 forecast slots and the journal. The forecast slots are assigned in `world.yaml`: slot 1 for the page, slots 2–5 for the advisor, slots 6–9 for the "with / without the module" forecasts of dispatch, grade, plant and maintenance, slot 10 for the stand check (`make mine-check`), so that the check never takes the page's slot.

## 4. Simulator (`mine/sim`)

`mine/sim/runner.py` runs the loop: once per real second it applies the live messages that arrived, advances the model by `speedup` simulated seconds (one `step` is one simulated second, so results do not depend on the speed), publishes changed twins and the journal events, and touches its health file. Allowed speed-ups are 1, 10, 20 and 60 (`mine/sim/model.py`); the default is 20 (`sim.default_speedup`). Before the first publish the model runs `clock.warm_start_min` = 120 simulated minutes silently.

| Module | Subsystem |
|---|---|
| `model.py` | The simulation object, message dispatch (`handle_message`), published values |
| `pit.py` | Blocks and their life cycle `insitu → drilling → drilled → charged → blasted → mining → depleted → (pushback)`, drill rigs, routine blasts with exclusion zone and re-entry, short-term plan, pushbacks. A rig starts a new block while fewer than `drill.drill_ahead_blocks` blocks stand ahead of the shovels (blasted, charged, drilled or being drilled); the limit stands one block above the start state, so the rigs drill from the first hour and then start a block each time a shovel moves onto a blasted one: drilling keeps pace with the digging (`test_modules`: a week's metres against the metres the rock dug needed) |
| `fleet.py` | Trucks, loaders, fuel station; routing at every node over the road graph; the fixed plan used without the dispatch module |
| `plant.py` | Dump points, crusher, conveyor, coarse-ore stockpile, ROM pads with wheel-loader reclaim, waste dump, SAG and ball mills, flotation, concentrate, tailings, substation. Rock moves as parcels carrying tonnes, copper, hardness and fragment size, so the mass balance closes |
| `reliability.py` | Wear, random defects that develop before a failure (drifting engine or tyre temperature or health), workshop bays. A share of defects (`reliability.sudden_share_pct`) gives no warning and fails at once, so the maintenance module cannot prevent every failure: truck availability with all modules stays within the 92–94 % of the best mines, about 87 % without them |
| `weather.py` | Air temperature and precipitation from the WMO 1991–2020 normals of the Aktogay station |
| `scenarios.py` | The six what-if scenarios |
| `durations.py` | How long each incident scenario is expected to last, its causes and their basis (section 4.1) |
| `response.py` | The shift supervisor's response resources: second repair crews, the workshop's crew, a grader, an emergency road crew, workshop overtime (section 4.2) |
| `chain.py` | Indicators along a scenario's causal chain over equal windows, with the noise test |
| `kpis.py` | Mine indicators and the material balance, computed from what the subsystems report |
| `design_basis.py` | Fleet sizing from the concentrator line, to check the derived entries of `params.yaml` |
| `forecast.py` | The forecast engine (section 6) |
| `coupling.py` | The simulator and the module cores in one process, used by the forecast engine and the tests |

Randomness: every random process draws from a stream of its own, keyed by its entity and fixed by `sim.seed` (`Simulation.rng`): the pace of each truck on each segment and its tipping time, the bucket fill and pass time of each loader, the defects and repairs of each machine, the charge of each block, the precipitation of each day. What one entity draws never shifts another's draws, so two forecast branches with the same actions are identical and a small action changes only what it causes (common random numbers). `Simulation.reseed(salt)` restarts every stream from a key (a forecast replica) and draws the hours to each machine's next defect again, which the memoryless onset allows.

Shared physics and helpers in `mine/common`: `physics.py` (truck speed and fuel from engine power, grade and rolling resistance), `roads.py` (road graph, closures, wet surface, shortest-time routing), `haulage.py` (travel times), `process.py` (wear and recovery formulas), `economics.py` (money and CO₂), `envelope.py` (Ditto protocol), `mqtt.py`, `ditto.py`.

Every number with a physical meaning comes from `params.yaml`; `docs/mine/parameters.md` is generated from it by `mine/model/build_params_doc.py`, and a test checks that the two match.

### 4.1 Scenarios

A scenario is a twin started by the live message `activate {target?, params?}` and stopped by `cancel {}`. It changes the mine only through its own subsystem; everything downstream follows from the model. Several scenarios may run at once.

| Twin | Change | Chain indicators (`kpi1…kpi4`) | Cancel |
|---|---|---|---|
| `scenario-road-closure` | closes one road segment; trucks re-route at the next node | loaded haul km → trip cycle → ore to crusher → stockpile hours | segment reopens |
| `scenario-shovel-failure` | forces a loader down; its trucks go to other loaders | dig rate → queue per trip → ore to crusher → crusher feed grade | loader repaired |
| `scenario-crusher-stop` | stops the primary crusher; trucks wait, then tip on ROM pads; the mill draws the stockpile down | crusher rate → ore to ROM pads → stockpile hours → mill feed | crusher restarts |
| `scenario-heavy-rain` | heavy rain and wet snow (default rate from `weather.rain_heavy_mm_h`), all roads wet, ramps at or above `road.wet_closed_grade_pct` closed | mean speed limit → trip time without waiting → diesel l/t → ore to crusher | status `cancelling` until the roads have dried |
| `scenario-blast` | fires a drilled or charged block outside the plan: clearance, exclusion zone with road closures and withdrawn machines, blast, re-entry wait | dig rate → trip cycle → broken stock → ore to crusher | before the blast: called off; after: zone lifted |
| `scenario-hard-ore` | ore with a higher Bond work index reaches the mill over `scenario.hard_ore_ramp_min` | feed BWI → power-limited feed → specific energy → Cu t/h | status `cancelling` while the hardness ramps back |

Every scenario but the blast has an **expected end** (`mine/sim/durations.py`): what the shift supervisor is told when it starts and what every forecast assumes. It publishes `expected_end_sim_time`, `expected_duration_h`, `expected_remaining_h` and `duration_basis` (Russian; before the start the twin shows the default duration and its basis), and the causes it can be started with in `attributes.spec.causes`:

| Scenario | Cause (`params.cause`) | Expected duration | Basis (`params.yaml`) |
|---|---|---|---|
| crusher stop | `mechanical` (default): hydraulics, lubrication or drive, a part from the store | 8 h | `crusher.repair_mechanical_h`, assumption; a planned mantle change takes about 60 h (Metso, La Caridad) and is not a shift decision |
| crusher stop | `jam`: the chamber blocked by oversize or tramp metal | 3 h | `crusher.repair_jam_h`, assumption |
| shovel failure | breakdown at the face | 8 h | `reliability.loader_mttr_h`, the model's mean loader repair |
| road closure | a slide onto the road, cleared by the road crew | 6 h | `road.closure_expected_h`, assumption |
| heavy rain | 10–15 mm at 4 mm/h | 3 h, then `road.drying_min` | `weather.heavy_event_h`, derived from the climate normals |
| hard ore | the zone the face opened | 12 h | `scenario.hard_ore_expected_h`, assumption |

`activate` also takes `params.expected_h` (0.25–24 h) to set the duration by hand. On the stand a scenario still runs until it is cancelled; if it outlasts its expected end, the estimate moves on by `scenario.overrun_extend_share` of the original duration («ремонт затянулся»). In a forecast branch (`Simulation.auto_end`) the scenario ends at its expected end. The crusher's twin shows the expected restart (`restart_expected_sim_time`), a broken-down loader when its repair ends (`repair_end_sim_time`: the scenario's expected end, or for a natural breakdown the mechanics' estimate, the repair time drawn at the failure).

### 4.2 Response resources

`mine/sim/response.py`: what the supervisor can spend on an incident besides the levers the modules pull. Each is a live message to the twin it acts on (section 5.2); what it costs accrues while it works (`mine.response_cost_kzt`, per scenario `response_cost_kzt`, and in a forecast the branch's `resources_kzt`). Rates and factors are in `params.yaml`, group `response`.

| Resource | Message | Effect | Cost |
|---|---|---|---|
| second repair crew | `expedite-repair` to the crusher or a broken-down loader | the rest of the repair × `second_crew_factor` (0.6); a scenario's expected end moves earlier, a natural repair's timer shortens | own fitters on overtime (`own_crew_kzt_h` = 55 400 KZT/h, from the mining wage of the Bureau of National Statistics × 1.5 overtime × 1.2 payroll × 6) plus two contractor specialists at 150 USD/h, at least 8 h of them (1.08 M KZT at once) |
| workshop crew to the face | `prioritise-repair` to a broken-down loader | its repair × `workshop_lend_factor` (0.75); one workshop bay takes no job meanwhile (`workshop.bays_lent`) | no cash; planned truck services wait |
| emergency road crew | `repair-road` to a segment closed by the road closure | the segment opens after `road_repair_h` (2 h) at `repaired_kmh` (15 km/h, `speed_cap_kmh`) until the full repair is due | 358 800 KZT/h for 2 h |
| grader and topping | `treat-surface` to a ramp the rain closed | the ramp opens after `grading_h` (1.5 h) at `treated_wet_kmh` (15 km/h) while wet | the topping (5 cm of own crushed waste at the fleet work per tonne) at once, the grader by the hour while it rains |
| workshop overtime | `expedite-repair` to the workshop | every repair of a broken-down truck × 0.6 for `workshop_overtime_h` (12 h) | own crew rate while a repair is under way |

A resource that cannot be applied is refused with a journal warning. Putting off a planned service (`cancel-service` with `hold_min`, an existing lever) keeps a truck at work; its failure rate then grows by `reliability.overdue_hazard_slope` per service interval overdue (0.4 % per hour), as the advisor's label states.

The blast twin publishes `valid_targets`; the page offers only those. Each indicator is compared over two windows of equal length (at most `scenario.kpi_window_max_min`), and `kpiN_significant` is true only if the change exceeds `scenario.kpi_noise_k` times the swing measured before the start, or is large (`scenario.kpi_large_change_pct`). The «before» window must be clean: no other scenario in it, nor within `scenario.kpi_recovery_min` before it; when scenarios run back to back the latest clean window of the same length further back is used, and `kpi_baseline_note` says where it lies, or, if there is none, «база недоступна» with `kpiN_baseline_known` false. The full-length «before» side is fixed at the start, so a scenario that runs longer than the kept history (`scenario.kpi_history_h`) keeps its baseline. A road closure's trip indicators (loaded haul, trip time) count only trips begun after the closure; until `scenario.route_trips_min` of them have finished, «now» of the loaded haul, the trip time and the ore to the crusher is the forecast from the open routes (the value before times the ratio of the route figure now to the one at the start: haul length, expected cycle, the ore the fleet can bring) and `kpiN_forecast` is true. A scenario whose cancel takes time (heavy rain: the roads dry for `road.drying_min`; hard ore: the hardness ramps back over `scenario.hard_ore_ramp_min`) is `cancelling` meanwhile and publishes `cancel_remaining_min`, the model minutes left; both are bounded by those parameters. `effect_delay_text` states when the effect reaches the plant at the current speed.

## 5. Message contracts

### 5.1 Telemetry (MQTT, simulator and module services → Ditto)

Topic `telemetry/<name>`, payload a Ditto protocol envelope (`mine/common/envelope.py`):

```json
{"topic": "kz.aktogay/truck-05/things/twin/commands/modify",
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
| `block-*` | `set-destination`, `release` | `{destination, reason?, hold_min?}` | grade, advisor | where the block's rock goes |
| `rom-pad-*` | `set-reclaim`, `release` | `{tph, hold_min?}` | grade, advisor | wheel-loader feed from the pad to the crusher |
| `mill` | `set-feed-rate`, `release` | `{tph, hold_min?}` | plant, advisor | mill feed setpoint |
| `truck-*`, `loader-*` | `schedule-service` | `{reason, …}` | maintenance | booking for the workshop |
| `truck-*`, `loader-*` | `cancel-service` | `{hold_min?}` | maintenance, advisor | withdraws a booking |
| `loader-*` | `move-to-block` | `{block}` | advisor | relocates a loader |
| `crusher`, `loader-*`, `workshop` | `expedite-repair` | `{}` | advisor | a second repair crew (the workshop: an overtime crew), section 4.2 |
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
| `/grafana` | GET | redirect to `http://<host>:3000/d/aktogay-history` |

Everything else under `/ditto/` and `/influx/` is refused. `mine/ui/tests/gateway_check.sh` checks these rules and that no credential appears in the page or in any answer.

## 6. Forecast engine (`mine/sim/forecast.py`)

A `forecast` message to the mine twin asks for two branches from the same moment. At the next tick the simulator deep-copies its live model and gives each branch to a worker process forked from the simulator, so the live loop does not wait. A worker applies its branch's actions as if they had arrived as live messages, then runs the copy to the horizon (12 h by default, at most 24 h) together with the module cores (`coupling.py`), each in the state the stand reports for its module, exchanging with the model every `forecast.period_s` = 20 simulated seconds as on the stand. Up to `forecast.max_parallel` = 12 branches run at once (never more than the processors less one); the page's two branches start beside them at once, the advisor's go before the modules', and a module's branch that holds a worker when the advisor's branches arrive is stopped and started again later from the same copy. A branch is stopped after `forecast.timeout_s` = 120 s. The same model state with the same actions gives the same result, and because every random process has its own stream (section 4), branch B shares A's random numbers draw for draw. A request with `replicas` = K runs K paired replicas: replica 0 on the live streams, replica k on streams drawn anew from the fork moment and k, the same for A and B.

A branch ends every running scenario at its expected end (section 4.1), unless an action moves that end; `assumption_text` says when each branch takes the incident to end. Results go to `forecast-<slot>`: per branch copper, ore, waste, milled tonnes and the copper fed to the mill (head grade), where the mill's ore came from (stockpile drawdown, ROM pads, pit, each in tonnes and copper), the stockpile's hours of feed at the end, diesel, energy, revenue, costs, variable margin, rehandling, the end-of-horizon adjustment in its parts (`grade_kzt`, `work_kzt`, `pads_kzt`, `strip_kzt`), what its response resources cost (`resources_kzt`), total = margin + adjustment − resources, CO₂, module indicators (queue, grade in band, availability, breakdowns, mill stops, overload trips) and hourly series of mill feed and stockpile level (means over the replicas; series of replica 0); the differences B − A with their standard error over the replicas (`replicas`, `delta_total_se_kzt`, `delta_margin_se_kzt`, `delta_cu_se_t`, `delta_total_replicas_kzt`, `delta_cu_replicas_t`); and three Russian texts: `verdict_text` (the decision figure: which branch is better by the total and whether that is within the forecast's scatter), `copper_text` (the copper difference split into tonnes milled, head grade and recovery, the sources of the mill's ore, what the total counts of it, the fleet work, the overload trips, the stockpile left) and `summary_text` (all of it with the branches' figures and the basis of the money).

Money (`mine/common/economics.py`): the variable margin is payable copper at the copper price less smelter charges (TC/RC), less truck diesel and electricity; labour, repairs, explosives, reagents and depreciation are excluded. The adjustment values what the horizon leaves behind on one basis for every ore inventory — broken ore in the pit, truck bodies, the crusher's pocket, the belt, the coarse-ore stockpile, the ROM pads, the ore held in the mill: the copper a tonne will yield at the reference recovery and the copper price, net of what it still costs to turn it into concentrate. The mill is the bottleneck, so the largest of those costs is the mill time the tonne takes, in which the mill would grind a tonne of the mine's average ore (`geology.mean_head_grade_pct`); processing energy per tonne is the same for both and cancels. A tonne of ore is therefore worth `cu value × (its Cu − average grade × its tonnes)`, plus the fleet work it saves once dug (`economics.stock_kzt_t` a tonne), less the rehandling it still needs on a ROM pad (`rom.rehandle_kzt_t`, charged when it is tipped there and paid when the loader feeds it back). The value is linear in tonnes and copper: mixing heaps changes nothing, and moving ore between heaps changes the total only by what the move costs. Milling a tonne turns its value into revenue: the copper above the average grade is copper the stocks give up (`grade_kzt`), so drawing down a rich stock or digging a rich face earns nothing by itself, and what earns is the mill time, the recovery and the costs. Broken ore in the pit counts by what is loaded from it, so blasts and pushbacks create no value. Waste stripped counts at the same fleet work per tonne (`strip_kzt`). Options are compared by `total = margin + adjust − resources`; the advisor splits a gain into copper, copper given up by the stocks, fleet work, rehandling, diesel, power and the shift's resources («ресурсы смены»), and does not recommend one whose money of the horizon (all but the fleet work) is below `advisor.min_gain_kzt` while the copper hardly differs or that money is less than half the gain.

Paired noise, measured at 16 fork moments two hours apart with every module on (`forecast.noise_kzt`, `forecast.noise_cu_t`): a branch with one truck carrying 300 l less fuel differs from «change nothing» by a standard deviation of 7.6 M KZT and 0.68 t Cu over 12 h (11.2 M KZT before the streams were keyed by entity); 5 m more to drive for one truck, 0 (16.8 M KZT before). What remains is not random numbers but the dispatch module answering a small shift in time with other assignments, one or two truckloads of ore more or less, or from another face; without modules the same shift gives 0.9 M KZT. The noise is taken as 8 M KZT and 0.7 t per replica.

Users of the engine:

- the page (slot 1): «Прогноз на 12 ч» on a scenario card (B = the scenario started, or cancelled if it runs) and «Что даёт модуль?» on a module card (the module on in one branch and off in the other);
- each control module (slots 6–9), every `forecast.module_period_h` = 3 simulated hours: A with the module, B with it switched off; the mean of the last `forecast.module_results_kept` = 4 results is the module's published effect;
- the advisor (slots 2–5): A = change nothing, B = one response option, `advisor.replicas` = 3 paired replicas each (4 slots × 3 replicas, the shared «change nothing» branch computed once per replica: 12 processes).

## 7. Decision modules (`mine/modules`)

`runtime.py` is the shell every module service runs: read all twins over HTTP once the twin set is stable, subscribe to `opentwins/twin/events/<ns>/#` and to its own live messages, call the core every 0.5 s, send its messages over HTTP, publish its twin on change and at least every 30 s, honour `set-enabled` (the switch survives a restart through the twin's `enabled` feature). `base.py` holds the shared core: the picture of the mine built from twin reports, the decision count, and the effect measurement with forecasts.

| Module | Lever (live message → twin) | Rule | Without it |
|---|---|---|---|
| dispatch | `assign` → trucks | for an empty truck, the loader with the least travel + queue + loading (+ crusher wait for ore), corrected to keep ore and waste in the planned proportion; a loaded truck tips where its block is destined unless the crusher wait costs more than tipping on a ROM pad | each truck serves its home loader; an ore truck tips on the ROM pad only when `crusher.divert_queue_len` trucks already wait at the crusher, or the crusher has been stopped longer than `crusher.stop_wait_min` |
| grade | `set-destination` → blocks, `set-reclaim` → ROM pads | classifies blasted blocks against the cut-off; picks the combination of face destinations and wheel-loader reclaim that keeps the predicted stockpile grade in the target band with the least rehandling | everything above cut-off goes to the crusher |
| plant | `set-feed-rate` → mill | `min(module ceiling, max(lowest stable feed, ore arriving + (stockpile − reserve) / horizon))`, the horizon shortened to the crusher's expected restart while it is stopped (`crusher.restart_expected_sim_time`), and for one trend window after a restart the ore taken to arrive as fast as the mill takes it (the trucks are on their way back); the module ceiling (`power_limit_tph`) is `plant_control.power_margin` = 97 % of the mill's power limit (`mill.max_feed_tph`: installed power / specific energy at the hardness on the stockpile), at most the largest setpoint | constant design setpoint 3,835 t/h |
| maint | `schedule-service`, `cancel-service` → trucks, loaders | detects developing defects from the gap below the wear line and its trend, books the workshop so few machines are out at once, one bay kept for breakdowns | run to failure |
| advisor | `forecast` → mine; on `apply`, the option's messages | on an incident (crusher stop, loader down, road closed, rain, truck cluster out, hard ore) reads its expected end and basis (`incident_end_sim_time`, `incident_basis`), builds up to four options, always starting with «Ничего не менять» and, if modules that answer the incident are off, switching them on as one option, then the supervisor's resources (section 4.2: a second crew on the crusher or the loader, the workshop's crew, an emergency road crew, a grader on the rain-closed ramp, workshop overtime) before the levers the modules also pull; an option's orders stand until the incident's expected end; publishes `optionN_cost_kzt` and `optionN_reason`, one sentence on why it pays («без этого мельница встанет через N ч») or not («склада хватает до конца ремонта»); forecasts each over `advisor.replicas` paired replicas and ranks by the mean gain in total, then copper; recommends only an option whose mean gain is at least `advisor.min_t` standard errors (the error over the replicas, not below `forecast.noise_kzt` / √replicas) and `advisor.min_gain_kzt`, that loses at most `advisor.max_cu_loss_t` copper (a hard bar) and does not owe its gain to inventory alone; publishes `optionN_gain_se_kzt` and `optionN_confidence` (высокая / средняя / низкая / нет); when nothing is recommended, names the modules that are on and already answer the incident; analyses a lasting incident again when the stockpile has lost `advisor.reanalyse_stock_h` hours of feed | incidents run under the base rules |

## 8. Page (`mine/ui/site`)

A static single page (`index.html`, ES modules in `js/`). The 3D view of the mine fills the window; everything else floats over it: the top bar (search, «Объекты», simulated clock with shift and weather, speed, «Пауза», «О модели», «Справка»), the ribbon of six shift figures under it, the right stack (advisor, what-if scenarios, decision modules; each row expands into its card), the left drawer with the object list and the selected object's card, and the bottom bar (3D or plan, the points of view «Обзор», «Забои», «Съезды», «Фабрика», zoom, «Слои и знаки», the one-line journal that opens the full journal with the Grafana link).

| File | Role |
|---|---|
| `main.js` | start-up: loads the twins, keeps them current, connects the 3D view, the plan and the panels; the view is part of the address (`?view=plan`, `?view=3d`), the selected twin too (`#/twin/<name>`) |
| `store.js`, `api.js` | every twin kept current from Ditto server-sent events, relations from links, parents and relation features; all requests go through the page's own gateway |
| `scene3d.js` | the 3D view (three.js): pit, steppe, roads, dump, ROM pads, plant and the machines where they report; points of view, selection, captions, scenario marks (closures, exclusion zone, affected twins); falls back to `softrender.js` (2D canvas) without WebGL |
| `terrain3d.js`, `pitgeom.js`, `geom3d.js`, `textures3d.js` | the ground: the pit bench by bench with rock colour by depth, haul roads with windrows, the terraced dump; vertex-lit geometry (lighting computed on the CPU, so a frame stays cheap with software WebGL) and procedural textures, nothing downloaded |
| `models3d.js`, `plant3d.js`, `motion.js` | low-poly models in real sizes: Caterpillar 785C trucks, Hitachi EX3600 shovels, Sandvik D55SP drills (instanced, with moving parts and two levels of detail); crusher, conveyor gantry, stockpile, mill hall, flotation, thickeners, tailings, substation; trucks travel along the road geometry between reports |
| `sky3d.js` | the sun at Aktogay for the simulated time (day, dusk, night with headlights, floodlight towers at the faces and plant lights), sky, haze, rain and snow from the weather twin, dust behind trucks and over a blast; `?time=HH:MM` previews the lighting of that hour and changes nothing else |
| `map.js`, `theme.js` | the 2D plan on two canvases (static and moving layer) and its colours and legend |
| `topbar.js`, `planui.js`, `plant.js`, `rates.js` | the top bar and the six figures with their detail (the process chain from the faces to the flotation); the bottom bar, layers, journal popover, scenario chips; the chain and the flows from the same 15-minute rates everywhere |
| `control.js`, `advisor.js`, `forecast.js` | scenario and module cards, the advisor card with «± SE» and confidence per option and «Применить», the two-branch forecast panel |
| `objects.js`, `labels.js`, `dom.js`, `about.js` | the object list and card with history sparklines from `/influx/query`; Russian names of kinds, features and states; «О модели» with the live model summary |

## 9. History dashboard (`mine/grafana`)

`mine/compose.yml` replaces Grafana's entrypoint with `mine/grafana/start.sh`, which starts Grafana and runs `setup.sh`: it creates the organisation «Актогай» that anonymous visitors enter as viewers, an InfluxDB data source in it with a read-only token for the history bucket, and the dashboard `aktogay-history` («Актогай — история») from `mine/grafana/dashboards/aktogay-history.json`. The platform's own organisation gets the same dashboard by provisioning. Panels: ore along the chain, stockpile level, Cu in concentrate, variable margin, CO₂, diesel per tonne, scenario states, trucks by state, truck state timeline.

## 10. Tests

| Suite | Runs | What it checks |
|---|---|---|
| `make mine-test` (`mine/tests/test_*.py`, 194 tests) | in the mine image, no platform | `test_twins`: generated things, pit geometry, parameters and their document, fleet sizing, block model. `test_model`: haulage physics, pit cycle, plant, failures, messages. `test_scenarios`: each scenario's chain and return to normal, also with modules on; the clean «before» window, «база недоступна», the route forecast of a road closure, the bounded cancel of heavy rain and hard ore. `test_modules`: each module against the same mine without it over 7 simulated days, determinism, lapsing decisions. `test_forecast`: money and CO₂ arithmetic, the forked copy, determinism, forecast twins, paired replicas, entity-keyed streams (identical actions give identical branches, a negligible change changes almost nothing, the paired noise with modules within `forecast.noise_kzt`). `test_response`: each incident's expected end and its basis, the cause and a given duration, the end in a forecast and the overrun on the stand; each resource changes what it should and costs what `params.yaml` says (second crew on the crusher and on a natural loader breakdown, the workshop's crew and its idle bay, the emergency road crew and its speed limit, grader and topping, workshop overtime), the failure rate of a postponed service. `test_forecast` also: the incident ends in the branch and runs on the stand, a second crew moves the end and costs its price, `assumption_text`. `test_advisor`: incidents, options, ranking, the standard-error rule and confidence, the same crusher jam flipping the second crew's recommendation with the stockpile level, copper as a hard bar, modules switched on as one option, the text when nothing is recommended, re-analysis of a lasting incident, `apply`, orders vs modules. `test_soak`: 7 simulated days with and without modules against the Aktogay basis ranges |
| `make mine` → `ready_check.sh` | on the stand | twins, telemetry, history, page |
| `make mine-check` → `stand_check.sh` | on the stand | twins, simulation, history, page, each module switched off and on, each scenario started and cancelled with its expected end, a second crew sent to a jammed crusher (restart earlier, its cost), the page forecast round trip, the advisor applied (its resources' actions checked in their twins), module effects measured |
| `mine/ui/tests/ui_check.mjs` | headless Chrome against the stand | the full-window 3D view and its first frame, every machine of the twins on the scene, points of view, captions without overlap, the six figures and their chain, the object drawer and search, the road closure through the page, a module switch, forecasts, the scenario cards' expected duration and end with their basis and the crusher's causes, the advisor's expected end, resource costs and reasons, its ranking within 25 s, and «Применить», blast targets, the plan, deep links, the Grafana link, no console errors in three minutes, the 3D view without WebGL |
| `mine/ui/tests/gateway_check.sh` | against the stand | the gateway's allow-list and that no credential leaks |
| `mine/ui/tests/shots.mjs` | headless Chrome | screenshots of the points of view, a truck close-up, dusk or night, a truck card, a road closure, a blast, the advisor, the plan, a forecast, help, «О модели» and Grafana; `LIGHT_TIME=HH:MM` lights the scene as at that hour |
