"""Run the model against the platform: MQTT in, MQTT out.

Once per real second the runner applies the live messages that arrived,
advances the model by `speedup` simulated seconds and publishes the twins
whose features changed (and every twin at least once per heartbeat, so a
twin restored from a backup or re-created catches up by itself).

A `forecast` message to the mine twin goes to the forecast desk
(mine/sim/forecast.py): it copies the model at the next tick and runs the
branches in worker processes, so the tick goes on undisturbed; the runner
publishes the forecast twins like any other. To run the module cores in a
forecast the way the stand runs the modules, the runner follows the module
twins' own reports (switched on or off, online or offline).

Every minute the log line gives the longest tick and how late the latest
tick started, so the effect of forecasts on the live loop can be read there.
"""
from __future__ import annotations

import logging
import os
import signal
import time
from pathlib import Path

from mine.common import envelope
from mine.common.mqtt import MqttLink
from mine.model.build_twins import build_things, load_params, load_world
from mine.sim.coupling import ALL
from mine.sim.forecast import ForecastDesk
from mine.sim.model import ALLOWED_SPEEDUPS, Simulation, short

log = logging.getLogger("mine.sim")

TICK_S = 1.0
ALIVE_FILE = Path(os.environ.get("SIM_ALIVE_FILE", "/tmp/mine-sim.alive"))


class Publisher:
    def __init__(self, link: MqttLink, namespace: str, things: list[dict], heartbeat_s: float):
        self.link, self.namespace, self.heartbeat_s = link, namespace, heartbeat_s
        self.units = {short(t["thingId"]): envelope.feature_units(t) for t in things}
        self.sent: dict[str, tuple[dict, float]] = {}

    def publish(self, name: str, values: dict, force: bool = False) -> bool:
        previous = self.sent.get(name)
        now = time.monotonic()
        if not force and previous and previous[0] == values and now - previous[1] < self.heartbeat_s:
            return False
        message = envelope.modify_features(self.namespace, name, values, self.units[name])
        self.link.publish_json(envelope.TELEMETRY_TOPIC.format(name=name), message)
        self.sent[name] = (values, now)
        return True


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    world, params = load_world(), load_params()
    things = build_things(world, params)
    namespace = world["namespace"]

    seed = int(os.environ["SIM_SEED"]) if os.environ.get("SIM_SEED") else None
    sim = Simulation(things, world, params, seed)
    speedup = int(os.environ.get("SIM_SPEEDUP") or sim.speedup)
    if speedup not in ALLOWED_SPEEDUPS:
        log.error("SIM_SPEEDUP=%s is not one of %s", speedup, ALLOWED_SPEEDUPS)
        return 2
    sim.speedup = speedup
    sim.warm_start()
    log.info("model ready at %s: %d twins, speed-up x%d", sim.sim_time(), len(things), sim.speedup)

    desk = ForecastDesk(sim, things, dump_dir=os.environ.get("FORECAST_DUMP_DIR") or None)
    module_events = tuple(f"opentwins/twin/events/{namespace}/module-{name}" for name in ALL)
    link = MqttLink(os.environ.get("MQTT_HOST", "mosquitto"), int(os.environ.get("MQTT_PORT", "1883")),
                    client_id="mine-sim",
                    subscriptions=(envelope.LIVE_MESSAGES_TOPIC.format(namespace=namespace), *module_events))
    link.connect()
    publisher = Publisher(link, namespace, things, float(os.environ.get("SIM_HEARTBEAT_S", "30")))

    # As PID 1 the process gets no default SIGTERM action; stop promptly on `docker stop`.
    def stop(signum, frame):
        raise SystemExit(0)
    signal.signal(signal.SIGTERM, stop)

    ticks = published = 0
    longest_s = late_s = 0.0
    next_tick = time.monotonic()
    try:
        while True:
            started = time.monotonic()
            late_s = max(late_s, started - next_tick)
            for topic, payload in link.drain():
                if "/twin/events/" in topic:
                    event = envelope.parse_twin_event(payload)
                    if event is not None:
                        desk.observe_module(*event)
                    continue
                message = envelope.parse_live_message(payload)
                if message is None:
                    log.warning("ignored a payload on %s that is not a live message", topic)
                    continue
                name, subject, body = message
                log.info("live message %s -> %s %s", subject, name, body)
                if name == "mine" and subject == "forecast":
                    desk.request(sim, body)
                    continue
                sim.handle_message(name, subject, body)
                if subject == "set-enabled" and isinstance(body.get("enabled"), bool):
                    desk.observe_module(name, {"enabled": body["enabled"]})
            # Forecasts asked for in this tick start from the model as it is now, before it steps.
            desk.tick(sim)

            if not sim.paused:
                for _ in range(sim.speedup):
                    sim.step()
            stepped = time.monotonic()

            for name, values in sim.values().items():
                published += publisher.publish(name, values)
            for name, values in desk.values().items():
                published += publisher.publish(name, values)
            # The journal twin carries one event per update.
            for event in sim.pop_events():
                published += publisher.publish("journal", event, force=True)

            if link.client.is_connected():
                ALIVE_FILE.touch()
            ticks += 1
            longest_s = max(longest_s, time.monotonic() - started)
            if ticks % 60 == 0:
                branches = desk.compute_s
                log.info("%s x%d%s, %d twin updates in the last minute; longest tick %.0f ms, latest start %.0f ms late; "
                         "forecast branches: %d running, %d done (%s s each)", sim.sim_time(), sim.speedup,
                         " (paused)" if sim.paused else "", published, 1000 * longest_s, 1000 * late_s,
                         desk.running(), len(branches), ", ".join(f"{s:.1f}" for s in branches) or "-")
                published, longest_s, late_s = 0, 0.0, 0.0
                desk.compute_s = []

            next_tick += TICK_S
            now = time.monotonic()
            if now < next_tick:
                time.sleep(next_tick - now)
            else:
                # Fell behind: say why and carry on from here instead of catching up.
                log.warning("tick took %.2f s (model %.2f s, publishing %.2f s)",
                            now - started, stepped - started, now - stepped)
                next_tick = now
    finally:
        desk.close()
        link.close()


if __name__ == "__main__":
    raise SystemExit(main())
