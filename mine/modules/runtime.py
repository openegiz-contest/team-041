"""The shell that runs a module core against the platform.

    state in     the twins once over the Ditto HTTP API, then every change as
                 a twin event on MQTT (opentwins/twin/events/<namespace>/#)
    decisions    Ditto live messages over HTTP: POST .../inbox/messages/<subject>
    own twin     MQTT telemetry/<module twin>, the complete feature set
    switch       the live message `set-enabled {enabled}` addressed to the module's twin

Each module is the only writer of its own twin. To make a stopped service
visible without a second writer, the module leaves a last-will message with
the broker: if its connection is lost, the broker publishes on the module's
behalf that its status is `offline`. On a regular stop the module publishes
that itself. The decisions of a stopped module do not linger either: the
simulator drops those that are not repeated within its time-out.
"""
from __future__ import annotations

import logging
import os
import signal
import time
from pathlib import Path

from mine.common import envelope
from mine.common.ditto import DittoClient, DittoError
from mine.common.mqtt import MqttLink
from mine.model.build_twins import build_things, load_params, load_world

TICK_S = 0.5
HEARTBEAT_S = 30.0


def _initial_things(ditto: DittoClient, namespace: str, own: str, log: logging.Logger) -> list[dict]:
    """Every twin of the mine, read once the twin set is complete and stable."""
    previous: list[str] = []
    while True:
        ids = sorted(ditto.search_ids(namespace))
        if ids and ids == previous and f"{namespace}:{own}" in ids:
            things = ditto.get_things(ids)
            if len(things) == len(ids):
                return things
        previous = ids
        log.info("waiting for the twins: %d found so far", len(ids))
        time.sleep(3)


def run(core_class, namespace: str | None = None) -> int:
    # One setting for every service and the page: world.yaml, overridden by MINE_NAMESPACE.
    params = load_params()
    world = load_world(params=params)
    namespace = namespace or world["namespace"]
    own = core_class.name
    service = own.replace("module-", "mine-")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    log = logging.getLogger(service)
    password = os.environ.get("DITTO_PASSWORD")
    if not password:
        log.error("DITTO_PASSWORD is not set")
        return 2
    url, user = os.environ.get("DITTO_URL", "http://ditto-nginx:80"), os.environ.get("DITTO_USER", "ditto")
    alive = Path(os.environ.get("MODULE_ALIVE_FILE", "/tmp/mine-module.alive"))

    things = _initial_things(DittoClient(url, user, password), namespace, own, log)
    own_thing = next(t for t in things if t["thingId"] == f"{namespace}:{own}")
    # The features of the module's twin as the model defines them now: a twin created by an
    # earlier version of the model may lack some of them in Ditto until the module first writes it.
    units = envelope.feature_units(next(t for t in build_things(world, params) if t["thingId"] == f"{namespace}:{own}"))
    # The switch survives a restart of the service: a twin left `enabled: false` keeps the module off.
    was = envelope.feature_values(own_thing)
    enabled = os.environ.get("MODULE_ENABLED", "true").lower() != "false" and was.get("enabled") is not False
    core = core_class(things, params, enabled=enabled)
    core.effect_live = True             # the service on the stand measures its effect with forecasts of its own
    log.info("%d twins read, module %s", len(things), "enabled" if enabled else "disabled")

    topic = envelope.TELEMETRY_TOPIC.format(name=own)
    offline = {"status": "offline", "effect_text": "Сервис модуля остановлен: рудник работает по базовым правилам."}
    will = envelope.merge_features(namespace, own, offline, units)
    link = MqttLink(os.environ.get("MQTT_HOST", "mosquitto"), int(os.environ.get("MQTT_PORT", "1883")),
                    client_id=service, will=(topic, will),
                    subscriptions=(envelope.TWIN_EVENTS_TOPIC.format(namespace=namespace),
                                   f"opentwins/live/messages/{namespace}/{own}"))
    link.connect()
    sender = DittoClient(url, user, password, attempts=2, delay_s=0.5)

    def stop(signum, frame):
        raise SystemExit(0)
    signal.signal(signal.SIGTERM, stop)

    def send(messages) -> None:
        for name, subject, body in messages:
            try:
                status = sender.send_message(f"{namespace}:{name}", subject, body)
                if status != 202:
                    log.warning("message %s to %s was answered %s", subject, name, status)
            except DittoError as exc:
                log.warning("message %s to %s was not delivered: %s", subject, name, exc)

    published: tuple[dict, float] | None = None
    sent = events = 0
    last_report = time.monotonic()
    try:
        while True:
            started = time.monotonic()
            for message_topic, payload in link.drain():
                if "/live/messages/" in message_topic:
                    message = envelope.parse_live_message(payload)
                    if message and message[0] == own and message[1] == "set-enabled" and isinstance(message[2].get("enabled"), bool):
                        log.info("set-enabled %s", message[2]["enabled"])
                        reverts = core.set_enabled(message[2]["enabled"])
                        send(reverts)
                        sent += len(reverts)
                    elif message and message[0] == own and message[1] != "set-enabled":
                        log.info("command %s %s", message[1], message[2])
                        answer = core.command(message[1], message[2])
                        send(answer)
                        sent += len(answer)
                    continue
                event = envelope.parse_twin_event(payload)
                if event is not None and event[0] != own and event[0] in core.world.attrs:
                    core.observe(*event)
                    events += 1
            messages = core.step()
            send(messages)
            sent += len(messages)

            twin = core.twin()
            now = time.monotonic()
            if published is None or published[0] != twin or now - published[1] >= HEARTBEAT_S:
                link.publish_json(topic, envelope.modify_features(namespace, own, twin, units))
                published = (twin, now)
            if link.client.is_connected():
                alive.touch()
            if now - last_report >= 60.0:
                log.info("%d twin events, %d messages sent in the last minute; decisions in total: %d",
                         events, sent, core.decisions)
                sent = events = 0
                last_report = now
            time.sleep(max(0.0, TICK_S - (time.monotonic() - started)))
    finally:
        # A regular stop: say so in the twin, then leave.
        gone = core.twin(status="offline")
        gone["effect_text"] = offline["effect_text"]
        link.publish_json(topic, envelope.modify_features(namespace, own, gone, units), qos=1, wait_s=3.0)
        link.close()
