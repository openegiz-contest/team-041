"""Ditto protocol envelopes: the JSON that travels over MQTT.

Outbound, a writer sends `modify` commands for a twin's whole feature set to
`telemetry/<name>`. Inbound, Ditto forwards live messages sent to a twin over
HTTP to `opentwins/live/messages/<namespace>/<name>`, and every change of a
twin as a twin event to `opentwins/twin/events/<namespace>/<name>`.
"""
from __future__ import annotations

import json

TELEMETRY_TOPIC = "telemetry/{name}"
LIVE_MESSAGES_TOPIC = "opentwins/live/messages/{namespace}/#"
TWIN_EVENTS_TOPIC = "opentwins/twin/events/{namespace}/#"


def feature_units(thing: dict) -> dict[str, str]:
    """Feature name -> unit ('' when the feature has none), from a generated thing."""
    return {name: body["properties"].get("unit", "") for name, body in thing["features"].items()}


def feature_values(thing: dict) -> dict:
    """Feature name -> value, from a thing as Ditto returns it."""
    return {name: body.get("properties", {}).get("value") for name, body in thing.get("features", {}).items()}


def _features(name: str, values: dict, units: dict[str, str]) -> dict:
    features = {}
    for feature, value in values.items():
        if not isinstance(value, (str, int, float, bool)):
            raise TypeError(f"{name}.{feature}: {type(value).__name__} is not a scalar")
        props = {"value": value}
        if units[feature]:
            props["unit"] = units[feature]
        features[feature] = {"properties": props}
    return features


def modify_features(namespace: str, name: str, values: dict, units: dict[str, str]) -> dict:
    """Envelope that replaces all features of `namespace:name`.

    `values` must hold exactly the features named in `units`: a telemetry
    message always carries the complete set, and every value is a scalar.
    """
    if values.keys() != units.keys():
        raise ValueError(f"{name}: features differ from the twin definition: "
                         f"{sorted(values.keys() ^ units.keys())}")
    return {
        "topic": f"{namespace}/{name}/things/twin/commands/modify",
        # No reply is wanted. The correlation ID is the same for every update of
        # a twin: Ditto copies it into the twin event, where the platform's
        # Telegraf stores it as a tag, and a fresh ID per message would create a
        # new series in InfluxDB for every single update.
        "headers": {"correlation-id": f"telemetry-{name}", "response-required": False},
        "path": "/features",
        "value": _features(name, values, units),
    }


def merge_features(namespace: str, name: str, values: dict, units: dict[str, str]) -> dict:
    """Envelope that changes only the named features of `namespace:name` and
    leaves the others as they are. Used for the message a module leaves with
    the broker to be delivered if its connection is lost: it marks the module
    as offline without wiping the indicators it published last."""
    return {
        "topic": f"{namespace}/{name}/things/twin/commands/merge",
        "headers": {"correlation-id": f"telemetry-{name}", "response-required": False,
                    "content-type": "application/merge-patch+json"},
        "path": "/features",
        "value": _features(name, values, units),
    }


def parse_live_message(payload: bytes | str) -> tuple[str, str, dict] | None:
    """Return (thing name, subject, body) of a live message, or None if it is not one."""
    try:
        envelope = json.loads(payload)
        namespace, name, group, channel, criterion, *rest = envelope["topic"].split("/")
    except (ValueError, KeyError, TypeError, AttributeError):
        return None
    if (group, channel, criterion) != ("things", "live", "messages"):
        return None
    subject = envelope.get("headers", {}).get("ditto-message-subject") or "/".join(rest)
    body = envelope.get("value")
    if isinstance(body, str):
        # A body sent without a JSON content type arrives as text.
        try:
            body = json.loads(body)
        except ValueError:
            body = None
    return name, subject, body if isinstance(body, dict) else {}


def parse_twin_event(payload: bytes | str) -> tuple[str, dict] | None:
    """Return (thing name, {feature: value}) of a twin event that carries
    features, or None for anything else. An event produced by a telemetry
    message carries the twin's complete feature set."""
    try:
        envelope = json.loads(payload)
        namespace, name, group, channel, criterion, *rest = envelope["topic"].split("/")
        path, value = envelope["path"], envelope["value"]
    except (ValueError, KeyError, TypeError, AttributeError):
        return None
    if (group, channel, criterion) != ("things", "twin", "events") or not isinstance(value, dict):
        return None
    if path == "/features":
        features = value
    elif path == "/":
        features = value.get("features")           # the thing was created or replaced as a whole
    else:
        return None
    if not isinstance(features, dict):
        return None
    return name, {feature: body.get("properties", {}).get("value") for feature, body in features.items()
                  if isinstance(body, dict)}
