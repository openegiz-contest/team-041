#!/usr/bin/env bash
#
# End-to-end check of the running Aktogay mine slice stand (`make mine-check`):
#   twins      every twin of the model exists in Ditto, every kind in the right number
#   simulation the clock runs, trucks move, the plant works, the journal is written
#   history    telemetry of a truck, a block and the mill reaches InfluxDB through Telegraf
#   page       the page is served and reads twins through its own gateway
#   modules    each of the five module services is online, switches off (its
#              decisions are withdrawn) and on again through the page's gateway
#   scenarios  each of the six scenarios starts, shows in a linked twin, and
#              is cancelled, through the page's gateway
#   forecast   the page's forecast round trip: a `forecast` message to the mine
#              with a road closure in branch B, in the check's own slot (`forecast-10`, never
#              the page's `forecast-1`), gives the slot ready, B worse than A
#   response   a crusher stop has an expected end and its basis; a second repair
#              crew, sent as a live message to the crusher twin, moves the end
#              earlier, shows in the crusher's twin and costs what it says
#   advisor    a loader breaks down (scenario shovel-failure): the advisor
#              publishes ranked options with their margin split, its
#              recommended option is applied through the gateway and shows in
#              the twin it changes; the scenario is cancelled again
#   effects    each control module has measured itself with a forecast «with
#              the module / without it» in its own forecast slot
#
# `make mine` runs only the readiness part (mine/tests/ready_check.sh); this
# is the full check, `make mine-check`.
#
# The checks leave modules and scenarios the way they found them.
#
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
COMPOSE=(docker compose -f "$ROOT/deploy/compose/docker-compose.yml" -f "$ROOT/mine/compose.yml")
# shellcheck disable=SC1091
set -a; . "$ROOT/deploy/compose/.env"; set +a

# The namespace of the twins: MINE_NAMESPACE if set (environment or deploy/compose/.env), else world.yaml.
NS="${MINE_NAMESPACE:-$(sed -n 's/^namespace: *//p' "$ROOT/mine/model/world.yaml")}"
DITTO="http://127.0.0.1:$DITTO_PORT"
UI="http://127.0.0.1:${MINE_UI_PORT:-8088}"
FAILED=0

pass() { printf '  \033[32mok\033[0m    %s\n' "$1"; }
fail() { printf '  \033[31mFAIL\033[0m  %s\n' "$1"; FAILED=1; }

# Value of one feature, read straight from Ditto; strings come without quotes.
value() { curl -sf -m 10 -u "ditto:$DITTO_PASSWORD" "$DITTO/api/2/things/$1/features/$2/properties/value" | tr -d '"'; }
http_code() { curl -s -m 10 -o /dev/null -w '%{http_code}' "$@" || true; }

# wait_value <thing> <feature> <expected> [seconds]: poll until the feature has the value.
wait_value() {
  local deadline=$((SECONDS + ${4:-20}))
  while [ "$SECONDS" -lt "$deadline" ]; do
    [ "$(value "$1" "$2" || true)" = "$3" ] && return 0
    sleep 1
  done
  return 1
}

# Live message through the page's gateway, exactly as the buttons send it.
send() { http_code -X POST -H 'Content-Type: application/json' -d "$3" "$UI/ditto/api/2/things/$1/inbox/messages/$2?timeout=0"; }
# Live message straight to Ditto, as a module service sends it (the levers the page itself does not send).
send_ditto() { http_code -X POST -u "ditto:$DITTO_PASSWORD" -H 'Content-Type: application/json' -d "$3" "$DITTO/api/2/things/$1/inbox/messages/$2?timeout=0"; }

# positive <number>: true when the argument is a number above zero.
positive() { awk -v v="${1:-0}" 'BEGIN { exit !(v + 0 > 0) }'; }
# mln <KZT>: the sum in millions, one decimal.
mln() { awk -v v="${1:-0}" 'BEGIN { printf "%.1f", v / 1e6 }'; }

echo "Twins"
ids="$("${COMPOSE[@]}" exec -T mine-sim python -m mine.model.build_twins --ids 2>/dev/null | tr -d '\r' || true)"
if [ -z "$ids" ]; then
  fail "could not list the model's twins: is the mine-sim container running? (make mine)"
  echo; echo "Stand check FAILED."; exit 1
fi
total=0; missing=()
for id in $ids; do
  total=$((total + 1))
  found=0
  for _ in 1 2 3 4 5 6; do
    if [ "$(http_code -u "ditto:$DITTO_PASSWORD" "$DITTO/api/2/things/$id")" = 200 ]; then found=1; break; fi
    sleep 2
  done
  [ "$found" = 1 ] || missing+=("$id")
done
if [ "${#missing[@]}" = 0 ]; then
  pass "all $total twins of the model exist in Ditto"
else
  fail "${#missing[@]} of $total twins are missing in Ditto: ${missing[*]} (see: make mine-logs S=mine-twins)"
fi

# Every kind of the model, counted through Ditto's search by attribute.
kinds="$("${COMPOSE[@]}" exec -T mine-sim python -m mine.model.build_twins --kinds 2>/dev/null | tr -d '\r' | grep -v ' total$' || true)"
wrong=(); kind_count=0
while read -r expected kind; do
  [ -n "$kind" ] || continue
  kind_count=$((kind_count + 1))
  found=""
  for _ in 1 2 3 4 5 6 7 8 9 10; do
    found="$(curl -sf -m 10 -u "ditto:$DITTO_PASSWORD" -G "$DITTO/api/2/search/things/count" \
      --data-urlencode "namespaces=$NS" --data-urlencode "filter=eq(attributes/kind,\"$kind\")" 2>/dev/null || true)"
    [ "$found" = "$expected" ] && break
    sleep 3                                  # the search index follows the twins with a short delay
  done
  [ "$found" = "$expected" ] || wrong+=("$kind: ${found:-?} of $expected")
done <<< "$kinds"
if [ "$kind_count" -gt 0 ] && [ "${#wrong[@]}" = 0 ]; then
  pass "all $kind_count kinds are found by attribute search in the expected numbers"
else
  fail "search by attributes/kind disagrees with the model: ${wrong[*]:-no kinds listed}"
fi

echo "Simulation"
trucks="$(printf '%s\n' $ids | grep ':truck-' || true)"
positions() { for t in $trucks; do printf '%s %s %s;' "${t#"$NS":}" "$(value "$t" pos_x || true)" "$(value "$t" pos_y || true)"; done; }
if [ "$(value "$NS:mine" sim_state || true)" = paused ]; then
  fail "the simulation is paused, trucks stand still: press «Продолжить» on $UI and run the check again"
else
  moved=0
  for _ in 1 2 3 4; do
    before="$(positions)"; sleep 5; after="$(positions)"
    if [ -n "$before" ] && [ "$before" != "$after" ]; then moved=1; break; fi
  done
  if [ "$moved" = 1 ]; then
    changed=0
    IFS=';' read -ra was <<< "$before"; IFS=';' read -ra now <<< "$after"
    for i in "${!was[@]}"; do [ "${was[$i]}" = "${now[$i]:-}" ] || changed=$((changed + 1)); done
    pass "positions of $changed of ${#was[@]} trucks changed within 5 s"
    printf '        %s\n        %s\n' "${was[0]} -> ${now[0]#* }" "${was[1]:-} -> ${now[1]#* }"
  else
    fail "truck positions did not change in 4 reads 5 s apart (see: make mine-logs S=mine-sim)"
  fi
  feed=""; level=""
  for _ in $(seq 1 15); do
    feed="$(value "$NS:mill" feed_tph || true)"; level="$(value "$NS:stockpile" level_t || true)"
    if positive "$feed" && positive "$level"; then break; fi
    sleep 2
  done
  if positive "$feed" && positive "$level"; then
    pass "the plant works: mill feed $feed t/h, stockpile $level t, recovery $(value "$NS:flotation" recovery_pct || true) %"
    sag="$(value "$NS:mill-sag" power_mw || true)"; ball="$(value "$NS:mill-ball" power_mw || true)"; total="$(value "$NS:mill" power_mw || true)"
    if awk -v s="${sag:-0}" -v b="${ball:-0}" -v t="${total:-0}" 'BEGIN { exit !(s + b - t < 0.03 && t - s - b < 0.03) }'; then
      pass "grinding: SAG mill $sag MW + ball mills $ball MW = circuit $total MW, SAG charge $(value "$NS:mill-sag" charge_pct || true) %"
    else
      fail "grinding powers do not add up: SAG '$sag' + ball '$ball' against circuit '$total' MW"
    fi
    pass "concentrate $(value "$NS:concentrate" inflow_tph || true) t/h into the store ($(value "$NS:concentrate" level_t || true) t), tailings $(value "$NS:tailings" inflow_tph || true) t/h"
  else
    fail "the plant does not work: mill feed '${feed:-?}' t/h, stockpile '${level:-?}' t"
  fi
  seq_now="$(value "$NS:journal" seq || true)"
  if positive "$seq_now"; then
    pass "the journal is written: event $seq_now, «$(value "$NS:journal" text || true)»"
  else
    fail "the journal twin has no event yet (seq '${seq_now:-?}')"
  fi
fi

echo "History"
# points <thing> <feature>: number of points of the scalar field in InfluxDB over the last 2 minutes.
points() {
  local flux="from(bucket:\"default\") |> range(start:-2m) |> filter(fn:(r) => r.thingId == \"$1\" and r._field == \"value_$2_properties_value\") |> group() |> count()"
  "${COMPOSE[@]}" exec -T influxdb influx query --org opentwins --token "$INFLUXDB_TOKEN" --raw "$flux" 2>/dev/null \
    | tr -d '\r' | awk -F, '/^,,/ {print $NF}' | head -n 1 || true
}
truck="$(printf '%s\n' $trucks | head -n 1)"
block="$(value "$NS:loader-01" block || true)"
for probe in "$truck pos_x" "${block:-$NS:block-01} tonnes_remaining" "$NS:mill feed_tph"; do
  set -- $probe
  count=""
  for _ in $(seq 1 30); do
    count="$(points "$1" "$2")"
    case "$count" in ''|0) sleep 2 ;; *) break ;; esac
  done
  case "$count" in
    ''|0) fail "InfluxDB has no points for $1 $2 in the last 2 minutes (see: make logs S=telegraf)" ;;
    *)    pass "InfluxDB has $count fresh points of $1 $2" ;;
  esac
done

echo "Page"
if [ "$(http_code "$UI/")" = 200 ] && [ "$(http_code "$UI/js/main.js")" = 200 ]; then
  pass "page answers 200 at $UI"
else
  fail "page does not answer at $UI (see: make mine-logs S=mine-ui)"
fi
listed=0
for _ in $(seq 1 15); do
  if curl -sf -m 10 "$UI/ditto/api/2/search/things?namespaces=$NS&fields=thingId&option=size(200)" 2>/dev/null \
      | grep -q "\"$NS:truck-01\""; then listed=1; break; fi
  sleep 2
done
if [ "$listed" = 1 ]; then
  pass "gateway /ditto/ returns the twins to a request without credentials"
else
  fail "gateway $UI/ditto/ did not return the twins (see: make mine-logs S=mine-ui)"
fi
if [ "$(http_code "$DITTO/api/2/things/$NS:mine")" = 401 ]; then
  pass "Ditto itself refuses the same request without credentials (401)"
else
  fail "Ditto answered a request without credentials: expected 401"
fi
if [ "$(http_code -X PUT -H 'Content-Type: application/json' -d '1' "$UI/ditto/api/2/things/$NS:stand-check-probe/attributes/x")" = 403 ]; then
  pass "gateway refuses to change twins (403)"
else
  fail "gateway accepted a write to a twin: expected 403"
fi

echo "Modules"
# The module services write their own twins; each is switched off and on
# again through the page's gateway, as the page's buttons do it.
for m in dispatch grade plant maint advisor; do
  twin="$NS:module-$m"
  if ! wait_value "$twin" status online 90 && ! wait_value "$twin" status disabled 5; then
    fail "module-$m: status '$(value "$twin" status || true)', expected online (see: make mine-logs S=mine-$m)"
    continue
  fi
  was="$(value "$twin" enabled || true)"
  code="$(send "$twin" set-enabled '{"enabled":false}')"
  if [ "$code" = 202 ] && wait_value "$twin" status disabled 20 && wait_value "$twin" enabled false 5; then
    off_ok=1
    case "$m" in
      plant) wait_value "$NS:mill" setpoint_source constant 20 || off_ok=0 ;;
      grade) wait_value "$NS:rom-pad-01" reclaim_source rule 20 || off_ok=0 ;;
    esac
    if [ "$off_ok" = 1 ]; then pass "module-$m off: twin disabled, its decisions withdrawn"
    else fail "module-$m off: twin disabled, but the mine did not return to the base rule"; fi
  else
    fail "module-$m did not switch off (HTTP $code, status '$(value "$twin" status || true)')"
  fi
  if [ "$was" = false ]; then
    echo "        (module-$m was off before the check: left off)"; continue
  fi
  code="$(send "$twin" set-enabled '{"enabled":true}')"
  if [ "$code" = 202 ] && wait_value "$twin" status online 20; then
    on_ok=1
    if [ "$m" = plant ]; then wait_value "$NS:mill" setpoint_source plant-control 60 || on_ok=0; fi
    if [ "$on_ok" = 1 ]; then
      pass "module-$m on again: online, $(value "$twin" decisions_total || true) decisions; «$(value "$twin" last_decision || true)»"
    else
      fail "module-$m on again, but its decisions did not come back within 60 s"
    fi
  else
    fail "module-$m did not switch on again (HTTP $code)"
  fi
done

echo "Scenarios"
# Every scenario is started without a target (its default) and cancelled
# again; one that was already running is left alone. The model runs at the
# highest speed meanwhile; its speed is restored afterwards.
speed="$(value "$NS:mine" speedup || true)"
case "$speed" in 1|10|20|60) ;; *) speed=20 ;; esac
send "$NS:mine" set-speed '{"speedup":60}' >/dev/null
restore_speed() { send "$NS:mine" set-speed "{\"speedup\":$speed}" >/dev/null || true; }
trap restore_speed EXIT

# wait_match <thing> <feature> <extended regex> [seconds]: poll until the feature matches.
wait_match() {
  local deadline=$((SECONDS + ${4:-30}))
  while [ "$SECONDS" -lt "$deadline" ]; do
    if value "$1" "$2" 2>/dev/null | grep -Eq "$3"; then return 0; fi
    sleep 1
  done
  return 1
}

# round_trip <scenario> <linked twin, or `target`> <feature> <regex while active> <regex after the cancel>
round_trip() {
  local s="$1" thing="$2" feature="$3" active="$4" after="$5" twin="$NS:scenario-$1" code target rem spd cancel_wait
  if [ "$(value "$twin" status || true)" != inactive ]; then
    echo "        (scenario $s was running before the check: left as it was)"; return
  fi
  code="$(send "$twin" activate '{}')"
  if [ "$code" != 202 ] || ! wait_value "$twin" status active 20; then
    fail "$s: did not become active (HTTP $code, status '$(value "$twin" status || true)')"; return
  fi
  target="$(value "$twin" target || true)"
  if [ "$thing" = target ]; then thing="$target"; fi
  if wait_match "$thing" "$feature" "$active" 60; then
    sleep 3
    end="$(value "$twin" expected_end_sim_time || true)"
    pass "$s on ${target#"$NS:"}: ${thing#"$NS:"}.$feature = $(value "$thing" "$feature" || true); «$(value "$twin" kpi1_label || true)» before $(value "$twin" kpi1_baseline || true), so far $(value "$twin" kpi1_current || true) $(value "$twin" kpi1_unit || true) (just after activation; window so far $(value "$twin" kpi_window_min || echo '?') min); expected end ${end:-none}"
  else
    fail "$s active, but ${thing#"$NS:"}.$feature is '$(value "$thing" "$feature" || true)', expected /$active/"
  fi
  code="$(send "$twin" cancel '{}')"
  # A cancel may take the scenario's own time (a road drying, a ramp): the wait covers the published
  # `cancel_remaining_min` at the current speed, plus 60 s, and never less than before.
  sleep 2
  rem="$(value "$twin" cancel_remaining_min || true)"; rem="${rem%%.*}"; rem="${rem:-0}"
  spd="$(value "$NS:mine" speedup || true)"; case "$spd" in ''|*[!0-9]*|0) spd=1 ;; esac
  cancel_wait=$(( (rem + 1) * 60 / spd + 60 ))
  [ "$cancel_wait" -ge 120 ] || cancel_wait=120
  if [ "$code" = 202 ] && wait_value "$twin" status inactive "$cancel_wait" && wait_match "$thing" "$feature" "$after" "$cancel_wait"; then
    pass "$s cancelled: scenario inactive, ${thing#"$NS:"}.$feature = '$(value "$thing" "$feature" || true)'"
  else
    fail "$s cancel: status '$(value "$twin" status || true)', ${thing#"$NS:"}.$feature = '$(value "$thing" "$feature" || true)'"
  fi
}

round_trip road-closure   target                 status       '^closed$'            '^open$'
round_trip shovel-failure target                 state        '^down$'              '^(waiting|loading)$'
round_trip crusher-stop   "$NS:crusher"          state        '^stopped$'           '^(running|idle|blocked)$'
# Heavy rain closes the steep west ramp (10 %); the main ramp (8 %) stays open.
round_trip heavy-rain     "$NS:road-segment-08"  status       '^closed$'            '^open$'
valid="$(value "$NS:scenario-blast" valid_targets || true)"
if [ -n "$valid" ]; then
  pass "blast targets offered: ${valid//$NS:/}"
else
  fail "scenario-blast offers no valid target (valid_targets is empty)"
fi
round_trip blast          "$NS:pit"              blast_state  '^clearing$'          '^idle$'
round_trip hard-ore       "$NS:stockpile"        hardness_bwi '^(1[6-9]|2[0-9])'    '^1[0-5]([.]|$)'

echo "Response resources"
# The crusher jams: the scenario says when it is expected back and why; a second repair crew, sent as a live
# message to the crusher twin (as the advisor's «Применить» sends it), brings the restart forward and is paid for.
cs="$NS:scenario-crusher-stop"
if [ "$(value "$cs" status || true)" != inactive ]; then
  echo "        (a crusher stop is already under way: the second crew is not tried)"
else
  code="$(send "$cs" activate '{"params":{"cause":"jam"}}')"
  if [ "$code" = 202 ] && wait_value "$cs" status active 20 && wait_match "$NS:crusher" restart_expected_sim_time '^20' 20; then
    first="$(value "$NS:crusher" restart_expected_sim_time)"
    pass "crusher jammed: expected back at ${first:11:5} ($(value "$cs" expected_duration_h) h: $(value "$cs" duration_basis))"
    spent0="$(value "$NS:mine" response_cost_kzt || echo 0)"
    code="$(send_ditto "$NS:crusher" expedite-repair '{}')"
    if [ "$code" = 202 ] && wait_value "$NS:crusher" repair_crews 2 20; then
      second="$(value "$NS:crusher" restart_expected_sim_time)"
      sleep 2
      cost="$(value "$cs" response_cost_kzt || echo 0)"
      spent1="$(value "$NS:mine" response_cost_kzt || echo 0)"
      if [[ "$second" < "$first" ]] && positive "$cost" && positive "$((spent1 - spent0))"; then
        pass "second crew sent (HTTP $code): 2 crews on the crusher, restart ${first:11:5} → ${second:11:5}, cost so far $(mln "$cost") mln KZT; «$(value "$cs" response_text)»"
      else
        fail "second crew: restart ${first:11:5} → ${second:11:5}, cost '$cost', mine.response_cost_kzt $spent0 → $spent1"
      fi
    else
      fail "second crew not taken (HTTP $code, repair_crews '$(value "$NS:crusher" repair_crews || true)')"
    fi
    code="$(send_ditto "$NS:crusher" expedite-repair '{}')"
    sleep 2
    if [ "$(value "$NS:crusher" repair_crews || true)" = 2 ]; then
      pass "a third crew is refused: still $(value "$NS:crusher" repair_crews) crews"
    else
      fail "a repeated second crew changed the crusher: repair_crews '$(value "$NS:crusher" repair_crews || true)'"
    fi
  else
    fail "crusher stop without its expected end (HTTP $code, restart_expected_sim_time '$(value "$NS:crusher" restart_expected_sim_time || true)')"
  fi
  code="$(send "$cs" cancel '{}')"
  if [ "$code" = 202 ] && wait_value "$cs" status inactive 30 && wait_match "$NS:crusher" state '^(running|idle|blocked)$' 60; then
    pass "crusher stop cancelled: crusher $(value "$NS:crusher" state), repair crews $(value "$NS:crusher" repair_crews)"
  else
    fail "crusher stop cancel: status '$(value "$cs" status || true)', crusher '$(value "$NS:crusher" state || true)'"
  fi
fi

echo "Forecast"
# The page's request, as its «Прогноз» button sends it: what the next 12 hours
# bring if the main ramp is closed now (branch B) against carrying on (A).
if [ "$(value "$NS:scenario-road-closure" status || true)" != inactive ]; then
  echo "        (road closure is active: forecast round trip skipped)"
else
  # The check has a forecast slot of its own (world.yaml `forecasts.check_slots`), so a page looking at its
  # forecast meanwhile is not disturbed; a newer request in that slot would replace this one: then it is sent again.
  slot="$(sed -n 's/^  check_slots: *\[\([0-9]*\).*/\1/p' "$ROOT/mine/model/world.yaml")"; slot="${slot:-10}"
  ready=0
  for attempt in 1 2 3; do
    rid="stand-check-$$-$attempt"
    body='{"slot":'"$slot"',"horizon_h":12,"request_id":"'"$rid"'","requested_by":"stand-check","label_a":"Как есть","label_b":"Закрыть участок сейчас","actions_a":[],"actions_b":[{"thing":"'"$NS"':scenario-road-closure","subject":"activate","body":{}}]}'
    started=$SECONDS
    code="$(send "$NS:mine" forecast "$body")"
    seen=0
    while [ $((SECONDS - started)) -lt 90 ]; do
      now_id="$(value "$NS:forecast-$slot" request_id || true)"
      if [ "$now_id" = "$rid" ]; then
        seen=1
        [ "$(value "$NS:forecast-$slot" status || true)" = ready ] && { ready=1; break; }
      elif [ "$seen" = 1 ] || [ $((SECONDS - started)) -ge 10 ]; then
        echo "        (slot $slot was taken by another request, «$now_id»: asking again)"; break
      fi
      sleep 1
    done
    [ "$ready" = 1 ] && break
  done
  if [ "$code" = 202 ] && [ "$ready" = 1 ]; then
    f() { value "$NS:forecast-$slot" "$1" || true; }
    a="$(f a_margin_kzt)"; b="$(f b_margin_kzt)"
    moved_a=$(( $(f a_ore_t) + $(f a_waste_t) )); moved_b=$(( $(f b_ore_t) + $(f b_waste_t) ))
    plain_ok=1
    for x in a b; do
      [ "$(f ${x}_margin_kzt)" = "$(( $(f ${x}_revenue_kzt) - $(f ${x}_diesel_kzt) - $(f ${x}_power_kzt) ))" ] || plain_ok=0
    done
    # The closure costs money in total (the decision figure), and the texts lead with it and explain the copper.
    total_ok=0
    [ "$(f delta_total_kzt)" -lt 0 ] && f verdict_text | grep -q '^Итог с поправкой' && [ -n "$(f copper_text)" ] && total_ok=1
    if [ "$plain_ok" = 1 ] && [ "$moved_b" -lt "$moved_a" ] && [ "$total_ok" = 1 ] && f summary_text | grep -q 'поправка'; then
      pass "forecast-$slot ready $((SECONDS - started)) s after the request: rock moved A $moved_a t, B $moved_b t; margin A $(mln "$a"), B $(mln "$b") mln KZT, total with the adjustment A $(mln "$(f a_total_kzt)"), B $(mln "$(f b_total_kzt)")"
      printf '        %s\n' "$(f summary_text)"
    else
      fail "forecast-$slot ready, but inconsistent: margin = revenue − diesel − power: $plain_ok; rock moved A $moved_a, B $moved_b t; total B − A $(mln "$(f delta_total_kzt)") mln KZT, texts $total_ok («$(f verdict_text)»)"
    fi
  else
    fail "forecast-$slot not ready within 90 s (HTTP $code, status '$(value "$NS:forecast-$slot" status || true)', $(value "$NS:forecast-$slot" error_text || true))"
  fi
fi

echo "Advisor"
# A loader breaks down; the advisor must rank its options by forecast, and the
# recommended one, applied through the gateway, must show in the twin it changes.
adv="$NS:module-advisor"
# check_action <one action as the advisor writes it: "thing: subject (k=v, ...)">: it shows in its twin.
check_action() {
  local action="${1# }" thing subject args val
  thing="${action%%:*}"; subject="${action#*: }"; subject="${subject%% *}"
  args="$(printf '%s' "$action" | sed -n 's/.*(\(.*\)).*/\1/p')"
  case "$subject" in
    move-to-block)
      val="$(printf '%s' "$args" | sed -n 's/.*block=\([^,]*\).*/\1/p')"
      wait_value "$NS:$thing" block "$NS:$val" 30 && echo "$thing.block = $NS:$val, state $(value "$NS:$thing" state)" ;;
    set-reclaim)
      wait_value "$NS:$thing" reclaim_source operator 30 && echo "$thing.reclaim_source = operator, reclaim_tph = $(value "$NS:$thing" reclaim_tph)" ;;
    set-feed-rate)
      wait_value "$NS:$thing" setpoint_source operator 30 && echo "$thing.setpoint_source = operator, feed_setpoint_tph = $(value "$NS:$thing" feed_setpoint_tph)" ;;
    set-destination)
      wait_value "$NS:$thing" destination_source operator 30 && echo "$thing.destination = $(value "$NS:$thing" destination) (operator)" ;;
    cancel-service)
      # Kept at work until the hold ends; a truck that broke down since the forecast is left out by the advisor.
      if [ "$(value "$NS:$thing" state)" = down ]; then echo "$thing is down: call-back left out"; return 0; fi
      wait_match "$NS:$thing" service_hold_until '^20' 30 && echo "$thing.service_hold_until = $(value "$NS:$thing" service_hold_until)" ;;
    set-enabled)
      val="$(printf '%s' "$args" | sed -n 's/.*enabled=\([A-Za-z]*\).*/\1/p' | tr 'A-Z' 'a-z')"
      wait_value "$NS:$thing" enabled "${val:-true}" 30 && echo "$thing.enabled = ${val:-true}" ;;
    expedite-repair)
      if [ "$thing" = workshop ]; then
        wait_match "$NS:workshop" overtime_until '^20' 30 && echo "workshop.overtime_until = $(value "$NS:workshop" overtime_until)"
      else
        wait_match "$NS:$thing" repair_crews '^[2-9]$' 30 && echo "$thing.repair_crews = $(value "$NS:$thing" repair_crews), repair ends $(value "$NS:$thing" repair_end_sim_time)$(value "$NS:$thing" restart_expected_sim_time)"
      fi ;;
    prioritise-repair)
      wait_match "$NS:workshop" bays_lent '^[1-9]$' 30 && echo "workshop.bays_lent = $(value "$NS:workshop" bays_lent), $thing repair ends $(value "$NS:$thing" repair_end_sim_time)" ;;
    repair-road)
      wait_match "$NS:scenario-road-closure" response_text '.' 30 && echo "scenario-road-closure: $(value "$NS:scenario-road-closure" response_text)" ;;
    treat-surface)
      wait_match "$NS:scenario-heavy-rain" response_text '.' 30 && echo "scenario-heavy-rain: $(value "$NS:scenario-heavy-rain" response_text)" ;;
    *) echo "no check for the subject «$subject»"; return 1 ;;
  esac
}
# check_applied <actions text of an option>: every action of the option shows in its twin.
check_applied() {
  local IFS=';' action out all="" failed=0
  for action in $1; do
    if out="$(check_action "$action")"; then all="${all}${all:+; }$out"
    else failed=1; all="${all}${all:+; }NOT VISIBLE: ${action# } ($out)"; fi
  done
  echo "$all"
  return "$failed"
}
if [ "$(value "$adv" status || true)" != online ]; then
  fail "module-advisor is '$(value "$adv" status || true)', expected online (see: make mine-logs S=mine-advisor)"
elif [ "$(value "$NS:scenario-shovel-failure" status || true)" != inactive ] || [ "$(value "$NS:crusher" state || true)" = stopped ]; then
  echo "        (a loader failure or a crusher stop is already under way: advisor round trip skipped)"
else
  # The failure is put on a loader at an ore face: that is the case the advisor has levers for.
  ore_loader=""
  for l in loader-01 loader-02 loader-03 loader-04 loader-05; do
    if [ "$(value "$NS:$l" material || true)" = ore ] && value "$NS:$l" state 2>/dev/null | grep -Eq '^(loading|waiting)$'; then
      ore_loader="$l"; break
    fi
  done
  code="$(send "$NS:scenario-shovel-failure" activate "{\"target\":\"$NS:${ore_loader:-loader-01}\"}")"
  wait_value "$NS:scenario-shovel-failure" status active 20 || true
  loader="$(value "$NS:scenario-shovel-failure" target || true)"
  started=$SECONDS; ranked=0
  while [ $((SECONDS - started)) -lt 120 ]; do
    if [ "$(value "$adv" incident || true)" = "loader-down:${loader#"$NS:"}" ] && [ "$(value "$adv" analysis_status || true)" = ready ]; then
      ranked=1; break
    fi
    sleep 1
  done
  if [ "$code" = 202 ] && [ "$ranked" = 1 ]; then
    rec="$(value "$adv" recommended || true)"; count="$(value "$adv" options_count || true)"
    pass "advisor ranked $count options for loader-down:${loader#"$NS:"} $((SECONDS - started)) s after the failure; recommended: option $rec"
    split_ok=1
    for n in $(seq 1 "${count:-0}"); do
      sum=0
      for part in revenue grade work rehandle diesel power resources; do sum=$((sum + $(value "$adv" "option${n}_split_${part}_kzt"))); done
      [ "$sum" = "$(value "$adv" "option${n}_delta_kzt")" ] || split_ok=0
      printf '        %s. %s ± %s mln KZT, confidence «%s» (copper %s, copper given up by the stocks %s, fleet work %s, rehandling %s, diesel %s, power %s), %s t Cu, rank %s: %s\n' "$n" \
        "$(mln "$(value "$adv" "option${n}_delta_kzt")")" "$(mln "$(value "$adv" "option${n}_gain_se_kzt")")" \
        "$(value "$adv" "option${n}_confidence")" "$(mln "$(value "$adv" "option${n}_split_revenue_kzt")")" \
        "$(mln "$(value "$adv" "option${n}_split_grade_kzt")")" "$(mln "$(value "$adv" "option${n}_split_work_kzt")")" \
        "$(mln "$(value "$adv" "option${n}_split_rehandle_kzt")")" \
        "$(mln "$(value "$adv" "option${n}_split_diesel_kzt")")" "$(mln "$(value "$adv" "option${n}_split_power_kzt")")" \
        "$(value "$adv" "option${n}_cu_delta_t")" "$(value "$adv" "option${n}_rank")" "$(value "$adv" "option${n}_label")"
      reason="$(value "$adv" "option${n}_reason" || true)"
      [ -n "$reason" ] && printf '           resources %s mln KZT; %s\n' "$(mln "$(value "$adv" "option${n}_cost_kzt")")" "$reason"
    done
    if [ "$split_ok" = 1 ]; then pass "every option's margin split adds up to its difference"; else fail "an option's margin split does not add up"; fi
    pick="$rec"
    if [ "${rec:-0}" -le 1 ]; then
      # «Change nothing» is recommended (no option gains enough at this state of the mine): the
      # round trip of `apply` is then made with the best-ranked of the other options.
      for n in $(seq 2 "${count:-0}"); do
        r="$(value "$adv" "option${n}_rank" || true)"
        if [ "$r" = 1 ] || { [ "$r" = 2 ] && [ "$(value "$adv" option1_rank || true)" = 1 ]; }; then pick="$n"; fi
      done
      echo "        («Ничего не менять» recommended at this state; applying option $pick, the best-ranked other one)"
    fi
    actions="$(value "$adv" "option${pick}_actions" || true)"
    if [ "${pick:-0}" -le 1 ] || [ -z "$actions" ]; then
      fail "advisor offers no option with an action to apply"
    else
      code="$(send "$adv" apply "{\"option\":$pick}")"
      shown=""
      if [ "$code" = 202 ] && wait_value "$adv" applied_option "$pick" 20 && shown="$(check_applied "$actions")"; then
        pass "option $pick applied through the gateway, every action visible in its twin: $shown"
        printf '        %s\n' "$(value "$adv" applied_text || true)"
      else
        fail "option $pick ($actions) not applied or not visible (HTTP $code, applied_option '$(value "$adv" applied_option || true)'): ${shown:-no action checked}"
      fi
    fi
  else
    fail "advisor did not rank options within 120 s (HTTP $code, incident '$(value "$adv" incident || true)', status '$(value "$adv" analysis_status || true)')"
  fi
  code="$(send "$NS:scenario-shovel-failure" cancel '{}')"
  if [ "$code" = 202 ] && wait_value "$NS:scenario-shovel-failure" status inactive 30; then
    pass "shovel-failure cancelled: ${loader#"$NS:"} $(value "$loader" state || true)"
  else
    fail "shovel-failure cancel: status '$(value "$NS:scenario-shovel-failure" status || true)'"
  fi
fi

echo "Module effects"
# Each control module asks for a forecast «with the module / without it» in its own slot, the
# first one ten simulated minutes after it was switched on (later for each next module).
for m in dispatch grade plant maint; do
  twin="$NS:module-$m"
  if [ "$(value "$twin" enabled || true)" != true ]; then
    echo "        (module-$m is off: no effect to measure)"; continue
  fi
  if wait_match "$twin" effect_count '^[1-9]' 420; then
    pass "module-$m: $(value "$twin" effect_text || true)"
  else
    fail "module-$m has no forecast «with / without the module» yet ($(value "$twin" effect_forecast || true) $(value "$NS:$(value "$twin" effect_forecast | sed 's/.*://')" status || true))"
  fi
done

echo
if [ "$FAILED" = 0 ]; then echo "Stand check passed."; else echo "Stand check FAILED."; exit 1; fi
