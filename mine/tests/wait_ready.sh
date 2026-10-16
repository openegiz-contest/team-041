#!/usr/bin/env bash
#
# Wait until the mine is alive end to end: the page's gateway answers and the
# simulated clock it reads from Ditto is advancing. `docker compose up --wait`
# returns when the containers are healthy, which is a little earlier.
#
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
# shellcheck disable=SC1091
set -a; . "$ROOT/deploy/compose/.env"; set +a

UI="http://127.0.0.1:${MINE_UI_PORT:-8088}"
# The namespace of the twins: MINE_NAMESPACE if set (environment or deploy/compose/.env), else world.yaml.
NS="${MINE_NAMESPACE:-$(sed -n 's/^namespace: *//p' "$ROOT/mine/model/world.yaml")}"
CLOCK="$UI/ditto/api/2/things/$NS:mine/features/sim_time/properties/value"
deadline=$((SECONDS + ${MINE_READY_WAIT_S:-600}))       # generous: a slow first start is not a failure
first=""

printf 'Waiting for the mine simulation '
while :; do
  now="$(curl -sf -m 5 "$CLOCK" 2>/dev/null || true)"
  if [ -n "$now" ]; then
    if [ -z "$first" ]; then
      first="$now"
    elif [ "$now" != "$first" ]; then
      echo " running, simulated time ${now//\"/}"
      exit 0
    fi
  fi
  if [ "$SECONDS" -ge "$deadline" ]; then
    echo
    echo "ERROR: the simulated clock did not advance within 600 s." >&2
    if [ "$(curl -sf -m 5 "${CLOCK/sim_time/sim_state}" 2>/dev/null || true)" = '"paused"' ]; then
      echo "       The simulation is paused: press «Продолжить» on $UI." >&2
    else
      echo "       Inspect with: make mine-logs S=mine-sim   and   make mine-logs S=mine-twins" >&2
    fi
    exit 1
  fi
  printf '.'
  sleep 2
done
