#!/bin/sh
# Entry point of the platform's Grafana on the mine stand (mine/compose.yml).
#
# Grafana itself starts as in the platform (/run.sh). Beside it, setup.sh
# gives visitors without an account a read-only view of the mine history: a
# separate organisation that anonymous visitors enter as viewers, holding only
# the dashboard «Бозшаколь — история» and an InfluxDB data source with a read-only
# token. The platform's own organisation, with the OpenEgiz app and its
# credentials, still needs a login.
set -u

/run.sh "$@" &
grafana=$!
trap 'kill -TERM "$grafana" 2>/dev/null' TERM INT

sh /etc/mine-grafana/setup.sh &

# `wait` returns early when a signal is trapped: wait again until Grafana has stopped.
wait "$grafana"
status=$?
while kill -0 "$grafana" 2>/dev/null; do wait "$grafana"; status=$?; done
exit "$status"
