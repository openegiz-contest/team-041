#!/bin/sh
# Read-only view of the mine history for visitors without an account; started
# by start.sh beside Grafana and repeated until it has succeeded once.
#
#   1. the organisation named in GF_AUTH_ANONYMOUS_ORG_NAME, which anonymous
#      visitors enter as viewers (Grafana creates nothing for them itself);
#   2. in it, an InfluxDB data source with the same UID as the platform's, but
#      with a token that may only read the bucket of the twin history (made
#      here once; kept while the data source answers);
#   3. the dashboard «Бозшаколь — история» from mine/grafana/dashboards, also the
#      home page of that organisation.
#
# The platform's organisation gets the same dashboard from the provisioning
# file (mine/compose.yml), so a logged-in user reaches it at the same address.
# Credentials are passed to curl on its standard input, never on its command
# line, and nothing secret is printed.
set -u

GRAFANA=http://127.0.0.1:3000
INFLUX=http://influxdb:8086
ORG_NAME=${GF_AUTH_ANONYMOUS_ORG_NAME:?}
DS_UID=opentwins-influxdb
DASHBOARD=/etc/mine-grafana/dashboards/bozshakol-history.json
UID_NOW=bozshakol-history
TITLE="Бозшаколь — история"
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT
umask 077

log() { echo "bozshakol-history: $*"; }

# grafana <curl arguments...>: a request to Grafana as its admin.
grafana() {
  printf 'user = "%s:%s"\n' "$GF_SECURITY_ADMIN_USER" "$GF_SECURITY_ADMIN_PASSWORD" \
    | curl -s -m 30 -K - -H 'Content-Type: application/json' "$@"
}
# influx <curl arguments...>: a request to InfluxDB with the platform's token.
influx() {
  printf 'header = "Authorization: Token %s"\n' "$INFLUXDB_TOKEN" \
    | curl -s -m 30 -K - -H 'Content-Type: application/json' "$@"
}
# first <key> < JSON: the first string or number value of that key.
first() { tr -d '\n' | grep -o "\"$1\": *\"\{0,1\}[^\",}]*" | head -n 1 | sed 's/.*: *"\{0,1\}//'; }

org_id() {
  grafana "$GRAFANA/api/orgs" | tr '{' '\n' | grep -F "\"name\":\"$ORG_NAME\"" | sed -n 's/.*"id":\([0-9]*\).*/\1/p' | head -n 1
}

ensure_org() {
  org=$(org_id)
  if [ -z "$org" ]; then
    # Creating an organisation must leave the admin where they were.
    home=$(grafana "$GRAFANA/api/user" | first orgId)
    printf '{"name": "%s"}' "$ORG_NAME" > "$WORK/org.json"
    org=$(grafana -X POST --data-binary "@$WORK/org.json" "$GRAFANA/api/orgs" | first orgId)
    [ -n "$home" ] && grafana -X POST "$GRAFANA/api/user/using/$home" > /dev/null
    [ -n "$org" ] && log "organisation for visitors created (id $org)"
  fi
  [ -n "$org" ]
}

# A token that may read the history bucket and nothing else.
read_token() {
  influx_org=$(influx "$INFLUX/api/v2/orgs?org=opentwins" | first id)
  bucket=$(influx "$INFLUX/api/v2/buckets?name=default&orgID=$influx_org" | first id)
  [ -n "$influx_org" ] && [ -n "$bucket" ] || return 1
  printf '{"orgID": "%s", "description": "mine-grafana-read: history for visitors", "permissions": [{"action": "read", "resource": {"type": "buckets", "id": "%s", "orgID": "%s"}}]}' \
    "$influx_org" "$bucket" "$influx_org" > "$WORK/auth.json"
  influx -X POST --data-binary "@$WORK/auth.json" "$INFLUX/api/v2/authorizations" | first token
}

ensure_datasource() {
  status=$(grafana -H "X-Grafana-Org-Id: $org" "$GRAFANA/api/datasources/uid/$DS_UID/health" | first status)
  [ "$status" = OK ] && return 0
  token=$(read_token)
  [ -n "$token" ] || { log "no read-only InfluxDB token yet"; return 1; }
  cat > "$WORK/ds.json" <<EOF
{"uid": "$DS_UID", "name": "Бозшаколь: история (только чтение)", "type": "influxdb", "access": "proxy",
 "url": "$INFLUX", "isDefault": true,
 "jsonData": {"version": "Flux", "organization": "opentwins", "defaultBucket": "default"},
 "secureJsonData": {"token": "$token"}}
EOF
  exists=$(grafana -o /dev/null -w '%{http_code}' -H "X-Grafana-Org-Id: $org" "$GRAFANA/api/datasources/uid/$DS_UID")
  if [ "$exists" = 200 ]; then
    grafana -X PUT -H "X-Grafana-Org-Id: $org" --data-binary "@$WORK/ds.json" "$GRAFANA/api/datasources/uid/$DS_UID" > /dev/null
  else
    grafana -X POST -H "X-Grafana-Org-Id: $org" --data-binary "@$WORK/ds.json" "$GRAFANA/api/datasources" > /dev/null
  fi
  rm -f "$WORK/ds.json"
  status=$(grafana -H "X-Grafana-Org-Id: $org" "$GRAFANA/api/datasources/uid/$DS_UID/health" | first status)
  [ "$status" = OK ] && log "read-only data source ready"
  [ "$status" = OK ]
}

# A dashboard of the same title under another uid (made before the uid was renamed): Grafana would save the
# new one over it and keep the old uid, so it is deleted first.
drop_renamed() {
  grafana -G -H "X-Grafana-Org-Id: $org" --data-urlencode "query=$TITLE" "$GRAFANA/api/search" \
    | tr '{' '\n' | sed -n 's/.*"uid":"\([^"]*\)".*/\1/p' | while read -r uid; do
      [ -n "$uid" ] && [ "$uid" != "$UID_NOW" ] || continue
      grafana -X DELETE -H "X-Grafana-Org-Id: $org" "$GRAFANA/api/dashboards/uid/$uid" > /dev/null && log "old dashboard uid $uid removed"
    done
}

ensure_dashboard() {
  drop_renamed
  { printf '{"overwrite": true, "message": "mine/grafana/dashboards/bozshakol-history.json", "dashboard": '; cat "$DASHBOARD"; printf '}'; } > "$WORK/dash.json"
  result=$(grafana -X POST -H "X-Grafana-Org-Id: $org" --data-binary "@$WORK/dash.json" "$GRAFANA/api/dashboards/db" | first status)
  [ "$result" = success ] || return 1
  printf '{"homeDashboardUID": "%s", "theme": "dark"}' "$UID_NOW" > "$WORK/prefs.json"
  grafana -X PUT -H "X-Grafana-Org-Id: $org" --data-binary "@$WORK/prefs.json" "$GRAFANA/api/org/preferences" > /dev/null
}

attempt=0
while [ "$attempt" -lt 90 ]; do
  attempt=$((attempt + 1))
  if curl -sf -m 5 -o /dev/null "$GRAFANA/api/health" && ensure_org && ensure_datasource && ensure_dashboard; then
    log "dashboard «Бозшаколь — история» ready for visitors (organisation $org)"
    exit 0
  fi
  sleep 10
done
log "gave up after $attempt attempts: the history is reachable only after a login"
exit 1
