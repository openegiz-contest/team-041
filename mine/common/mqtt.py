"""Thin wrapper around paho-mqtt 2.x: connect with retries, publish JSON,
and hand incoming messages to the caller through a thread-safe queue."""
from __future__ import annotations

import json
import logging
import queue
import time

import paho.mqtt.client as mqtt

log = logging.getLogger(__name__)


class MqttLink:
    def __init__(self, host: str, port: int, client_id: str, subscriptions: tuple[str, ...] = (),
                 will: tuple[str, dict] | None = None):
        """`will` is (topic, JSON payload): the broker publishes it for this
        client if the connection is lost without a clean disconnect."""
        self.host, self.port = host, port
        self.subscriptions = subscriptions
        self.inbox: queue.Queue[tuple[str, bytes]] = queue.Queue()
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=client_id)
        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect
        self.client.on_message = self._on_message
        self.client.reconnect_delay_set(min_delay=1, max_delay=10)
        if will is not None:
            self.client.will_set(will[0], json.dumps(will[1], ensure_ascii=False), qos=1)

    def connect(self, attempts: int = 60, delay_s: float = 2.0) -> None:
        """Block until the broker accepts the connection, then run the network loop
        in a background thread. paho reconnects by itself after that."""
        for attempt in range(1, attempts + 1):
            try:
                self.client.connect(self.host, self.port, keepalive=30)
                break
            except OSError as exc:
                if attempt == attempts:
                    raise
                log.warning("MQTT %s:%s not reachable (%s), retry %d of %d",
                            self.host, self.port, exc, attempt, attempts)
                time.sleep(delay_s)
        self.client.loop_start()

    def _on_connect(self, client, userdata, flags, reason_code, properties):
        if reason_code.is_failure:
            log.error("MQTT connection refused: %s", reason_code)
            return
        log.info("MQTT connected to %s:%s", self.host, self.port)
        # Subscriptions do not survive a reconnect with a clean session.
        for topic in self.subscriptions:
            client.subscribe(topic, qos=1)

    def _on_disconnect(self, client, userdata, flags, reason_code, properties):
        log.warning("MQTT disconnected: %s", reason_code)

    def _on_message(self, client, userdata, message):
        self.inbox.put((message.topic, message.payload))

    def publish_json(self, topic: str, payload: dict, qos: int = 0, wait_s: float = 0.0) -> None:
        info = self.client.publish(topic, json.dumps(payload, ensure_ascii=False), qos=qos)
        if wait_s > 0:
            try:
                info.wait_for_publish(wait_s)
            except (RuntimeError, ValueError):
                pass

    def drain(self) -> list[tuple[str, bytes]]:
        """All messages received since the last call, oldest first."""
        messages = []
        while True:
            try:
                messages.append(self.inbox.get_nowait())
            except queue.Empty:
                return messages

    def close(self) -> None:
        self.client.loop_stop()
        self.client.disconnect()
