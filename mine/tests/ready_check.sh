#!/usr/bin/env bash
#
# Readiness check of the Bozshakol mine model stand, run at the end of `make mine`:
#   twins      every twin of the model exists in Ditto, every kind is found by search
#   telemetry  the simulated clock advances and the trucks move
#   history    the twins' telemetry reaches InfluxDB through Telegraf
#   page       the page is served and reads the twins through its own gateway
#
# It fails only if one of these is not there after generous waits, so that a
# slow first start is not taken for a broken one. The functional round trips
# (modules, scenarios, forecasts, the advisor) are `make mine-check`.
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
WAIT_S="${MINE_READY_WAIT_S:-600}"          # the longest wait for each of the four parts
FAILED=0

pass() { printf '  \033[32mok\033[0m    %s\n' "$1"; }
fail() { printf '  \033[31mFAIL\033[0m  %s\n' "$1"; FAILED=1; }
value() { curl -sf -m 10 -u "ditto:$DITTO_PASSWORD" "$DITTO/api/2/things/$1/features/$2/properties/value" 2>/dev/null | tr -d '"'; }
http_code() { curl -s -m 10 -o /dev/null -w '%{http_code}' "$@" 2>/dev/null || true; }

# until_ok <seconds> <command...>: run the command every 5 s until it succeeds or the time is up.
until_ok() {
  local deadline=$((SECONDS + $1)); shift
  while :; do
    if "$@"; then return 0; fi
    [ "$SECONDS" -lt "$deadline" ] || return 1
    sleep 5
  done
}

echo "Readiness of the mine stand"

# -- twins -------------------------------------------------------------------------
ids=""
list_ids() { ids="$("${COMPOSE[@]}" exec -T mine-sim python -m mine.model.build_twins --ids 2>/dev/null | tr -d '\r')"; [ -n "$ids" ]; }
missing=()
all_twins() {
  missing=()
  for id in $ids; do
    [ "$(http_code -u "ditto:$DITTO_PASSWORD" "$DITTO/api/2/things/$id")" = 200 ] || missing+=("$id")
  done
  [ "${#missing[@]}" = 0 ]
}
wrong=()
all_kinds() {
  wrong=()
  while read -r expected kind; do
    [ -n "$kind" ] || continue
    found="$(curl -sf -m 10 -u "ditto:$DITTO_PASSWORD" -G "$DITTO/api/2/search/things/count" \
      --data-urlencode "namespaces=$NS" --data-urlencode "filter=eq(attributes/kind,\"$kind\")" 2>/dev/null || true)"
    [ "$found" = "$expected" ] || wrong+=("$kind: ${found:-?} of $expected")
  done < <("${COMPOSE[@]}" exec -T mine-sim python -m mine.model.build_twins --kinds 2>/dev/null | tr -d '\r' | grep -v ' total$')
  [ "${#wrong[@]}" = 0 ]
}
if ! until_ok "$WAIT_S" list_ids; then
  fail "could not list the model's twins: is the mine-sim container running? (make mine-logs S=mine-sim)"
else
  if until_ok "$WAIT_S" all_twins; then
    pass "all $(printf '%s\n' $ids | wc -l) twins of the model exist in Ditto"
  else
    fail "${#missing[@]} twins are missing in Ditto: ${missing[*]} (see: make mine-logs S=mine-twins)"
  fi
  if until_ok "$WAIT_S" all_kinds; then
    pass "every kind of twin is found by attribute search in the expected number"
  else
    fail "search by attributes/kind disagrees with the model: ${wrong[*]}"
  fi
fi

# -- telemetry ---------------------------------------------------------------------
clock_moves() {
  local a b
  a="$(value "$NS:mine" sim_time || true)"; sleep 3; b="$(value "$NS:mine" sim_time || true)"
  [ -n "$a" ] && [ -n "$b" ] && [ "$a" != "$b" ]
}
trucks_move() {
  local a b
  a="$(for t in 01 02 03 04 05 06; do value "$NS:truck-$t" pos_x || true; done | tr '\n' ' ')"; sleep 5
  b="$(for t in 01 02 03 04 05 06; do value "$NS:truck-$t" pos_x || true; done | tr '\n' ' ')"
  [ -n "${a// /}" ] && [ "$a" != "$b" ]
}
if [ "$(value "$NS:mine" sim_state || true)" = paused ]; then
  fail "the simulation is paused: press «Продолжить» on $UI"
elif until_ok "$WAIT_S" clock_moves && until_ok "$WAIT_S" trucks_move; then
  pass "telemetry flows: simulated time $(value "$NS:mine" sim_time), trucks move"
else
  fail "the simulated clock or the trucks do not move (see: make mine-logs S=mine-sim)"
fi

# -- history -----------------------------------------------------------------------
points=""
fresh_points() {
  local flux="from(bucket:\"default\") |> range(start:-5m) |> filter(fn:(r) => r.thingId == \"$NS:mill\" and r._field == \"value_feed_tph_properties_value\") |> group() |> count()"
  points="$("${COMPOSE[@]}" exec -T influxdb influx query --org opentwins --token "$INFLUXDB_TOKEN" --raw "$flux" 2>/dev/null \
    | tr -d '\r' | awk -F, '/^,,/ {print $NF}' | head -n 1 || true)"
  case "$points" in ''|0) return 1 ;; *) return 0 ;; esac
}
if until_ok "$WAIT_S" fresh_points; then
  pass "history arrives: InfluxDB has $points points of the mill feed from the last 5 minutes"
else
  fail "InfluxDB has no fresh points of the mill feed (see: make logs S=telegraf)"
fi

# -- page --------------------------------------------------------------------------
page_up() { [ "$(http_code "$UI/")" = 200 ] && [ "$(http_code "$UI/js/main.js")" = 200 ]; }
gateway_up() {
  curl -sf -m 10 "$UI/ditto/api/2/search/things?namespaces=$NS&fields=thingId&option=size(200)" 2>/dev/null | grep -q "\"$NS:truck-01\""
}
if until_ok "$WAIT_S" page_up && until_ok "$WAIT_S" gateway_up; then
  pass "the page answers at $UI and reads the twins through its gateway"
else
  fail "the page or its gateway does not answer at $UI (see: make mine-logs S=mine-ui)"
fi

echo
if [ "$FAILED" = 0 ]; then
  echo "The mine stand is ready. The full round trip of modules, scenarios, forecasts and the advisor: make mine-check"
else
  echo "The mine stand is not ready."; exit 1
fi
