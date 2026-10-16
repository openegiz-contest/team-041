#!/usr/bin/env bash
# Check of the page's gateway (mine/ui/nginx.conf) against a running stand:
#
#   - twins can be read, searched and sent the live messages of the page's
#     buttons, and nothing else: creating, changing and deleting a twin is
#     refused, a message with any other subject or to any other twin is
#     refused (forecast only to the mine, apply only to the advisor), and so is
#     the rest of the Ditto API;
#   - of InfluxDB only the two read queries the gateway writes itself can be
#     reached; a query, a write or any other endpoint sent by the caller is refused;
#   - the namespace of the twins comes from the gateway's /config.json
#     (MINE_NAMESPACE in mine/compose.yml): messages and history are allowed
#     for the namespaces it names and refused for any other;
#   - the page's link opens the Grafana dashboard «Актогай — история» without a
#     login; a visitor may read it and query the history, but not save, delete,
#     write an annotation, take a snapshot, use the admin API, enter the
#     platform's organisation, use the OpenEgiz app's proxy to Ditto, or write
#     into InfluxDB through a query;
#   - no credential of the platform appears in the page's files or in anything
#     the gateway or Grafana answers.
#
# Run from the repository root:  bash mine/ui/tests/gateway_check.sh
# Credentials are read from deploy/compose/.env to search for them; they are
# never printed.
set -uo pipefail

cd "$(dirname "$0")/../../.."
ENV_FILE=deploy/compose/.env
[ -f "$ENV_FILE" ] || { echo "no $ENV_FILE: start the stand first (make mine)"; exit 2; }
set -a; . "$ENV_FILE"; set +a
UI="http://127.0.0.1:${MINE_UI_PORT:-8088}"
# The namespaces the gateway names; the mine is in the first that holds a mine twin.
NAMESPACES=$(curl -s -m 15 "$UI/config.json" | sed -n 's/.*"namespaces": *"\([^"]*\)".*/\1/p')
NS=""
for candidate in $NAMESPACES; do
  found=$(curl -s -m 15 "$UI/ditto/api/2/search/things?namespaces=$candidate&filter=eq(attributes/kind,%22mine%22)&fields=thingId")
  if [[ "$found" == *"\"$candidate:mine\""* ]]; then NS=$candidate; break; fi
done
[ -n "$NS" ] || { echo "no mine twin in the namespaces the gateway names (${NAMESPACES:-none})"; exit 2; }
echo "namespaces named by the gateway: $NAMESPACES; the mine is in $NS"

failed=0
pass() { echo "ok    $1"; }
fail() { echo "FAIL  $1"; failed=$((failed + 1)); }
code() { curl -s -o /dev/null -m 15 -w '%{http_code}' "$@"; }
# expect <wanted code> <description> <curl arguments ...>
expect() {
  local wanted=$1 text=$2; shift 2
  local got; got=$(code "$@")
  if [ "$got" = "$wanted" ]; then pass "$text ($got)"; else fail "$text: got $got, wanted $wanted"; fi
}
JSON=(-H 'Content-Type: application/json')

echo "-- the page's settings"
config=$(curl -s -m 15 -i "$UI/config.json")
if [[ "$config" == *"application/json"* && "$config" == *"\"namespaces\": \"$NAMESPACES\""* ]]; then pass "/config.json names the namespaces ($NAMESPACES)"; else fail "/config.json: ${config:0:200}"; fi

echo "-- twins: reading is allowed"
expect 200 "GET one twin" "$UI/ditto/api/2/things/$NS:mine"
expect 200 "GET the search" "$UI/ditto/api/2/search/things?namespaces=$NS&fields=thingId&option=size(5)"

echo "-- twins: writing is refused"
expect 403 "PUT a new twin" -X PUT "${JSON[@]}" -d '{}' "$UI/ditto/api/2/things/$NS:gateway-check-probe"
expect 403 "PUT an attribute" -X PUT "${JSON[@]}" -d '1' "$UI/ditto/api/2/things/$NS:mine/attributes/probe"
expect 403 "PUT a feature value" -X PUT "${JSON[@]}" -d '1' "$UI/ditto/api/2/things/$NS:mine/features/speedup/properties/value"
expect 403 "PATCH a twin" -X PATCH -H 'Content-Type: application/merge-patch+json' -d '{"attributes":{"probe":1}}' "$UI/ditto/api/2/things/$NS:mine"
expect 403 "DELETE a twin" -X DELETE "$UI/ditto/api/2/things/$NS:truck-01"
expect 403 "POST a twin" -X POST "${JSON[@]}" -d '{}' "$UI/ditto/api/2/things"
expect 403 "POST to the search" -X POST "${JSON[@]}" -d '{}' "$UI/ditto/api/2/search/things"
expect 403 "GET on a message inbox (messages are POST only)" "$UI/ditto/api/2/things/$NS:mine/inbox/messages/pause"
expect 403 "POST to a twin's outbox" -X POST "${JSON[@]}" -d '{}' "$UI/ditto/api/2/things/$NS:mine/outbox/messages/probe"
expect 403 "POST to a feature's inbox" -X POST "${JSON[@]}" -d '{}' "$UI/ditto/api/2/things/$NS:mine/features/speedup/inbox/messages/probe"

echo "-- live messages: only those of the page's buttons pass"
# expect_passes <description> <twin> <subject> <body>: the gateway hands the
# message to Ditto (202, or 404 while that twin does not exist yet).
expect_passes() {
  local got; got=$(code -X POST "${JSON[@]}" -d "$4" "$UI/ditto/api/2/things/$2/inbox/messages/$3?timeout=0")
  if [ "$got" = 202 ] || [ "$got" = 404 ]; then pass "$1 ($got)"; else fail "$1: got $got, wanted 202 or 404"; fi
}
speed=$(curl -s -m 15 "$UI/ditto/api/2/things/$NS:mine/features/speedup/properties/value")
[[ "$speed" =~ ^[0-9]+$ ]] || speed=20
expect_passes "set-speed to the mine (the speed it already has)" "$NS:mine" set-speed "{\"speedup\":$speed}"
expect_passes "forecast to the mine (one hour, no change in either branch)" "$NS:mine" forecast \
  '{"slot":1,"horizon_h":1,"label_a":"проверка шлюза","label_b":"проверка шлюза","actions_a":[],"actions_b":[],"subject":"gateway-check","requested_by":"gateway-check"}'
expect_passes "apply to the advisor (option 0, which no incident has)" "$NS:module-advisor" apply '{"option":0}'
refused() { expect 403 "$1" -X POST "${JSON[@]}" -d '{}' "$UI/ditto/api/2/things/$2/inbox/messages/$3?timeout=0"; }
refused "forecast to a truck" "$NS:truck-01" forecast
refused "forecast to the advisor" "$NS:module-advisor" forecast
refused "forecast to a scenario" "$NS:scenario-road-closure" forecast
refused "apply to another module" "$NS:module-dispatch" apply
refused "apply to the mine" "$NS:mine" apply
refused "a module's command: assign a truck" "$NS:truck-01" assign
refused "a module's command: mill feed rate" "$NS:mill" set-feed-rate
refused "a module's command: block destination" "$NS:block-01" set-destination
refused "a module's command: schedule a service" "$NS:truck-01" schedule-service
refused "activate sent to the mine" "$NS:mine" activate
refused "set-enabled sent to a scenario" "$NS:scenario-blast" set-enabled
refused "an allowed subject in capitals" "$NS:mine" FORECAST
refused "an allowed subject on a twin of another namespace" "org.other:mine" forecast
refused "an allowed subject on a namespace that only begins like the mine's" "${NS%?}:mine" forecast
refused "an allowed subject on a namespace that extends the mine's" "${NS}x:mine" forecast
refused "a refused message with an escaped colon" "$NS%3Atruck-01" assign
refused "a refused subject behind an escaped slash" "$NS:mine" "forecast%2F..%2F..%2F..%2Ftruck-01%2Finbox%2Fmessages%2Fassign"

echo "-- the rest of Ditto is refused"
expect 403 "GET a policy" "$UI/ditto/api/2/policies/default:basic_policy"
expect 403 "GET the connections" "$UI/ditto/api/2/connections"
expect 403 "GET whoami" "$UI/ditto/api/2/whoami"
expect 403 "GET devops" "$UI/ditto/devops/config"
expect 403 "GET the Ditto root" "$UI/ditto/"

echo "-- history: the two read queries answer"
expect 200 "GET the history of a twin" "$UI/influx/query?thing=$NS:truck-01&range=5m"
expect 200 "GET the journal history" "$UI/influx/journal?ns=$NS"

echo "-- history: everything else is refused"
expect 400 "history without a twin" "$UI/influx/query?range=5m"
expect 400 "history with a window that is not offered" "$UI/influx/query?thing=$NS:truck-01&range=7d"
expect 400 "history with Flux in the twin name" "$UI/influx/query?range=5m&thing=$NS:truck-01%22)%20%7C%3E%20to(bucket:%22default%22)%20//"
expect 400 "history of another namespace" "$UI/influx/query?thing=org.other:thing&range=5m"
expect 400 "journal without a namespace" "$UI/influx/journal"
expect 400 "journal of another namespace" "$UI/influx/journal?ns=org.other"
expect 400 "journal with Flux in the namespace" "$UI/influx/journal?ns=$NS%22)%20%7C%3E%20yield()%20//"
expect 400 "journal with a namespace that only begins like the mine's" "$UI/influx/journal?ns=${NS%?}"
expect 403 "POST a query of one's own to /influx/query" -X POST -H 'Content-Type: application/vnd.flux' -d 'buckets()' "$UI/influx/query?thing=$NS:truck-01&range=5m"
expect 403 "POST to /influx/journal" -X POST -H 'Content-Type: application/vnd.flux' -d 'buckets()' "$UI/influx/journal?ns=$NS"
expect 403 "InfluxDB query API" -X POST -H 'Content-Type: application/vnd.flux' -d 'buckets()' "$UI/influx/api/v2/query?org=opentwins"
expect 403 "InfluxDB write API" -X POST -d 'probe value=1' "$UI/influx/api/v2/write?org=opentwins&bucket=default"
expect 403 "InfluxDB delete API" -X POST "${JSON[@]}" -d '{}' "$UI/influx/api/v2/delete?org=opentwins&bucket=default"
expect 403 "InfluxDB buckets" "$UI/influx/api/v2/buckets"
expect 403 "InfluxDB tokens" "$UI/influx/api/v2/authorizations"
expect 403 "InfluxDB health" "$UI/influx/health"
expect 403 "InfluxDB root" "$UI/influx/"

# A body sent with the allowed GET must not replace the gateway's own query.
body=$(curl -s -m 15 -X GET -H 'Content-Type: application/vnd.flux' -d 'buckets()' "$UI/influx/query?thing=$NS:truck-01&range=5m")
if [[ "${body%%$'
'*}" == *_time* && "$body" != *retentionPeriod* ]]; then
  pass "a body sent with the history request is ignored"
else
  fail "a body sent with the history request changed the answer"
fi

echo "-- Grafana: the history dashboard opens without a login, and nothing else does"
G="http://127.0.0.1:${GRAFANA_PORT:-3000}"
location=$(curl -s -o /dev/null -m 15 -w '%{redirect_url}' "$UI/grafana")
if [[ "$location" == */d/aktogay-history ]]; then pass "the page's link leads to the dashboard ($location)"; else fail "the page's link leads to «$location»"; fi
meta=$(curl -s -m 15 "$G/api/dashboards/uid/aktogay-history")
if [[ "$meta" == *'"title":"Актогай — история"'* && "$meta" == *'"canSave":false'* && "$meta" == *'"canEdit":false'* ]]; then
  pass "a visitor without a login reads «Актогай — история» and may not save or edit it"
else
  fail "a visitor without a login does not get the dashboard: ${meta:0:160}"
fi
expect 200 "a visitor opens the dashboard page" "$G/d/aktogay-history"
# refused_g <description> <curl arguments ...>: Grafana answers 401, 403 or 404, never 2xx.
refused_g() {
  local text=$1; shift
  local got; got=$(code "$@")
  if [[ "$got" =~ ^(401|403|404)$ ]]; then pass "$text ($got)"; else fail "$text: got $got, wanted 401, 403 or 404"; fi
}
refused_g "a visitor saves a dashboard" -X POST "${JSON[@]}" -d '{"dashboard":{"title":"gateway-check"},"overwrite":true}' "$G/api/dashboards/db"
refused_g "a visitor deletes the dashboard" -X DELETE "$G/api/dashboards/uid/aktogay-history"
refused_g "a visitor creates a folder" -X POST "${JSON[@]}" -d '{"title":"gateway-check"}' "$G/api/folders"
refused_g "a visitor creates a data source" -X POST "${JSON[@]}" -d '{"name":"gateway-check","type":"influxdb","access":"proxy","url":"http://x"}' "$G/api/datasources"
refused_g "a visitor changes the data source" -X PUT "${JSON[@]}" -d '{"name":"gateway-check","type":"influxdb","access":"proxy","url":"http://x"}' "$G/api/datasources/uid/opentwins-influxdb"
refused_g "a visitor writes an annotation" -X POST "${JSON[@]}" -d '{"text":"gateway-check","time":1}' "$G/api/annotations"
refused_g "a visitor takes a snapshot" -X POST "${JSON[@]}" -d '{"dashboard":{"title":"gateway-check"},"expires":60}' "$G/api/snapshots"
refused_g "a visitor changes the organisation's preferences" -X PUT "${JSON[@]}" -d '{"theme":"light"}' "$G/api/org/preferences"
refused_g "a visitor lists the organisation's users" "$G/api/org/users"
refused_g "admin API: settings" "$G/api/admin/settings"
refused_g "admin API: create a user" -X POST "${JSON[@]}" -d '{"name":"gateway-check","login":"gateway-check","password":"gateway-check-1"}' "$G/api/admin/users"
refused_g "admin API: the organisations" "$G/api/orgs"
refused_g "a visitor switches to the platform's organisation" -X POST "$G/api/user/using/1"
refused_g "a visitor searches the platform's organisation" "$G/api/search?orgId=1"
refused_g "the OpenEgiz app's proxy to Ditto (read)" "$G/api/plugin-proxy/ertis-opentwins-app/ditto/api/2/things?ids=$NS:mine"
refused_g "the OpenEgiz app's proxy to Ditto (write)" -X PUT "${JSON[@]}" -d '1' "$G/api/plugin-proxy/ertis-opentwins-app/ditto/api/2/things/$NS:mine/attributes/probe"
refused_g "the OpenEgiz app's proxy to Ditto devops" "$G/api/plugin-proxy/ertis-opentwins-app/dittodevops/devops/config"
query() { printf '{"queries":[{"refId":"A","datasource":{"type":"influxdb","uid":"opentwins-influxdb"},"query":%s}],"from":"now-5m","to":"now"}' "$1"; }
read_answer=$(curl -s -m 20 -X POST "${JSON[@]}" -d "$(query '"buckets()"')" "$G/api/ds/query")
if [[ "$read_answer" == *'"status":200'* ]]; then pass "a visitor's query reads the history"; else fail "a visitor's query does not read: ${read_answer:0:160}"; fi
write_answer=$(curl -s -m 20 -X POST "${JSON[@]}" -d "$(query '"import \"array\"\narray.from(rows: [{_time: now(), _measurement: \"gateway_check\", _field: \"v\", _value: 1.0}]) |> to(bucket: \"default\")"')" "$G/api/ds/query")
if [[ "$write_answer" == *"not authorized to write"* ]]; then pass "a visitor's query that writes into InfluxDB is refused (read-only token)"; else fail "a visitor's query that writes was not refused: ${write_answer:0:160}"; fi

echo "-- no credential in the page or in the answers"
SECRETS=()
for name in DITTO_PASSWORD DITTO_BASIC_AUTH DITTO_DEVOPS_PASSWORD DITTO_DEVOPS_BASIC_AUTH INFLUXDB_TOKEN INFLUXDB_ADMIN_PASSWORD GRAFANA_ADMIN_PASSWORD; do
  value=${!name:-}
  [ ${#value} -ge 6 ] && SECRETS+=("$value")
done
[ ${#SECRETS[@]} -ge 3 ] || fail "fewer than three credentials found in $ENV_FILE: nothing to search for"

# holds_secret <text>: true when the text holds any credential.
holds_secret() {
  local secret
  for secret in "${SECRETS[@]}"; do
    if grep -qF -- "$secret" <<<"$1"; then return 0; fi
  done
  return 1
}
# leaks <description> <text>: fails when the text holds any credential.
leaks() {
  if holds_secret "$2"; then fail "$1: a credential is in the text"; else pass "$1 (${#2} characters searched)"; fi
}
# The search itself must work: a credential planted in a text is found.
if holds_secret "header: x${SECRETS[${#SECRETS[@]}-1]}y" && ! holds_secret "nothing to find here"; then
  pass "the search finds a planted credential (${#SECRETS[@]} credentials are searched for)"
else
  fail "the search for credentials does not work"
fi

static=""
count=0
while IFS= read -r file; do
  static+=$(curl -s -i -m 15 "$UI/${file#mine/ui/site/}")
  count=$((count + 1))
done < <(find mine/ui/site -type f | sort)
static+=$(curl -s -i -m 15 "$UI/")
leaks "the page as served: $count files with their headers" "$static"

answers=""
answers+=$(curl -s -i -m 15 "$UI/ditto/api/2/things/$NS:mine")
answers+=$(curl -s -i -m 15 "$UI/ditto/api/2/things?ids=$NS:mine,$NS:scenario-road-closure,$NS:module-dispatch,$NS:journal,$NS:truck-01")
answers+=$(curl -s -i -m 15 "$UI/ditto/api/2/search/things?namespaces=$NS&option=size(200)")
answers+=$(curl -s -i -m 15 "$UI/influx/query?thing=$NS:truck-01&range=5m")
answers+=$(curl -s -i -m 15 "$UI/influx/journal?ns=$NS")
answers+=$(curl -s -i -m 15 "$UI/config.json")
answers+=$(curl -s -i -m 15 "$UI/grafana")
answers+=$(curl -s -i -m 15 "$UI/healthz")
answers+=$(curl -s -i -m 15 -X PUT "${JSON[@]}" -d '1' "$UI/ditto/api/2/things/$NS:mine/attributes/probe")
answers+=$(curl -s -i -m 15 "$UI/ditto/api/2/things/$NS:no-such-twin")
answers+=$(curl -s -i -m 15 "$UI/ditto/api/2/policies/default:basic_policy")
answers+=$(curl -s -i -m 15 "$UI/influx/query?thing=bad&range=5m")
answers+=$(curl -s -i -m 15 "$UI/influx/api/v2/buckets")
answers+=$(curl -s -i -m 15 "$UI/no-such-file")
answers+=$(curl -s -m 6 -N -H 'Accept: text/event-stream' "$UI/ditto/api/2/things?namespaces=$NS" | head -c 200000)
leaks "answers of the gateway: twins, search, change stream, history, redirects and refusals" "$answers"

grafana_answers=""
grafana_answers+=$(curl -s -i -m 15 "$G/api/dashboards/uid/aktogay-history")
grafana_answers+=$(curl -s -i -m 15 "$G/api/datasources")
grafana_answers+=$(curl -s -i -m 15 "$G/api/datasources/uid/opentwins-influxdb")
grafana_answers+=$(curl -s -i -m 15 "$G/api/frontend/settings")
grafana_answers+=$(curl -s -i -m 15 "$G/d/aktogay-history")
grafana_answers+="$read_answer$write_answer"
leaks "what Grafana shows a visitor: dashboard, data sources, settings, query answers" "$grafana_answers"

files=0
for secret in "${SECRETS[@]}"; do
  files=$((files + $(grep -rIlF -- "$secret" mine/ui mine/grafana 2>/dev/null | wc -l)))
done
if [ "$files" = 0 ]; then pass "no credential in the files under mine/ui and mine/grafana"; else fail "$files file(s) under mine/ui or mine/grafana hold a credential"; fi

echo
if [ "$failed" = 0 ]; then echo "all gateway checks passed"; else echo "$failed gateway check(s) failed"; fi
exit "$failed"
