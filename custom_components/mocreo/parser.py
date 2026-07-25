"""Pure-Python parsing layer for MOCREO hub MQTT payloads.

This module deliberately has **no Home Assistant imports** so that it can be
unit-tested standalone.  Everything that needs to know about the shape of
MOCREO's MQTT traffic lives here; the Home Assistant entity code only consumes
the neutral :class:`Reading` objects produced below.

Topic grammar (per MOCREO's "MQTT Topic" documentation):

    <prefix>/<hub_sn>/node/<node_id>/data      hub -> broker, measurements
    <prefix>/<hub_sn>/node/<node_id>/event     hub -> broker, rule trigger/recover
    <prefix>/<hub_sn>/node/<node_id>/state     hub -> broker, online/battery/signal
    <prefix>/<hub_sn>/node/<node_id>/config    hub -> broker, model + units
    <prefix>/<hub_sn>/node/<node_id>/config/get  broker -> hub, request config

Payloads are JSON.  ``data`` is documented as a JSON *array* of measurement
objects; the others are plain objects.  Both shapes are accepted everywhere.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from typing import Any

# --------------------------------------------------------------------------
# Field kinds
# --------------------------------------------------------------------------

KIND_SENSOR = "sensor"
KIND_BINARY = "binary_sensor"


@dataclass(frozen=True)
class FieldSpec:
    """Describes how one JSON key should surface in Home Assistant."""

    key: str
    kind: str = KIND_SENSOR
    name: str | None = None
    device_class: str | None = None
    unit: str | None = None
    state_class: str | None = None
    diagnostic: bool = False
    # Multiply integer values from the ``data`` topic by this factor.
    data_scale: float = 1.0
    icon: str | None = None
    # For binary fields: values that mean "on".
    truthy: tuple[Any, ...] = ()
    enabled_default: bool = True


def _spec(key: str, **kwargs: Any) -> FieldSpec:
    return FieldSpec(key=key, **kwargs)


# Known keys.  Anything not listed here still gets an entity via the
# fallback logic in :func:`spec_for` -- that is what makes the integration
# survive MOCREO adding fields or shipping a model we have not seen.
KNOWN_FIELDS: dict[str, FieldSpec] = {
    "temperature": _spec(
        "temperature",
        name="Temperature",
        device_class="temperature",
        unit="°C",
        state_class="measurement",
        data_scale=0.01,
    ),
    "humidity": _spec(
        "humidity",
        name="Humidity",
        device_class="humidity",
        unit="%",
        state_class="measurement",
        data_scale=0.01,
    ),
    "pressure": _spec(
        "pressure",
        name="Pressure",
        device_class="atmospheric_pressure",
        unit="hPa",
        state_class="measurement",
        data_scale=0.01,
    ),
    "battery": _spec(
        "battery",
        name="Battery",
        device_class="battery",
        unit="%",
        state_class="measurement",
        diagnostic=True,
    ),
    "voltage": _spec(
        "voltage",
        name="Battery voltage",
        device_class="voltage",
        unit="mV",
        state_class="measurement",
        diagnostic=True,
    ),
    "signal": _spec(
        "signal",
        name="Signal strength",
        device_class="signal_strength",
        unit="dBm",
        state_class="measurement",
        diagnostic=True,
    ),
    "rssi": _spec(
        "rssi",
        name="Signal strength",
        device_class="signal_strength",
        unit="dBm",
        state_class="measurement",
        diagnostic=True,
    ),
    "snr": _spec(
        "snr",
        name="Signal-to-noise ratio",
        unit="dB",
        state_class="measurement",
        diagnostic=True,
    ),
    "found": _spec(
        "found",
        kind=KIND_BINARY,
        name="Connectivity",
        device_class="connectivity",
        diagnostic=True,
        truthy=(True, 1, "1", "true", "online"),
    ),
    "probe": _spec(
        "probe",
        kind=KIND_BINARY,
        name="Probe connected",
        device_class="connectivity",
        diagnostic=True,
        truthy=(True, 1, "1", "true", "connected"),
    ),
    # --- water leak -------------------------------------------------------
    # MOCREO has not published the LW1 field names, so every plausible spelling
    # is mapped to the same moisture entity shape.
    "water": _spec(
        "water",
        kind=KIND_BINARY,
        name="Water leak",
        device_class="moisture",
        truthy=(True, 1, "1", "true", "wet", "water", "leak", "alarm"),
    ),
    "leak": _spec(
        "leak",
        kind=KIND_BINARY,
        name="Water leak",
        device_class="moisture",
        truthy=(True, 1, "1", "true", "wet", "water", "leak", "alarm"),
    ),
    "wet": _spec(
        "wet",
        kind=KIND_BINARY,
        name="Water leak",
        device_class="moisture",
        truthy=(True, 1, "1", "true", "wet", "water", "leak", "alarm"),
    ),
    "moisture": _spec(
        "moisture",
        kind=KIND_BINARY,
        name="Water leak",
        device_class="moisture",
        truthy=(True, 1, "1", "true", "wet", "water", "leak", "alarm"),
    ),
    "waterleak": _spec(
        "waterleak",
        kind=KIND_BINARY,
        name="Water leak",
        device_class="moisture",
        truthy=(True, 1, "1", "true", "wet", "water", "leak", "alarm"),
    ),
    "water_leak": _spec(
        "water_leak",
        kind=KIND_BINARY,
        name="Water leak",
        device_class="moisture",
        truthy=(True, 1, "1", "true", "wet", "water", "leak", "alarm"),
    ),
    # LW1 advertises "leak severity tracking" (Low / Medium / High).
    "level": _spec("level", name="Leak level", icon="mdi:water-percent"),
    "waterlevel": _spec("waterlevel", name="Water level", icon="mdi:water-percent"),
    "water_level": _spec("water_level", name="Water level", icon="mdi:water-percent"),
    "severity": _spec("severity", name="Leak severity", icon="mdi:water-alert"),
    "leaklevel": _spec("leaklevel", name="Leak level", icon="mdi:water-percent"),
    "leak_level": _spec("leak_level", name="Leak level", icon="mdi:water-percent"),
    # --- misc -------------------------------------------------------------
    "alarm": _spec(
        "alarm",
        kind=KIND_BINARY,
        name="Alarm",
        device_class="problem",
        truthy=(True, 1, "1", "true", "on", "trigger"),
    ),
    "buzzer": _spec(
        "buzzer",
        kind=KIND_BINARY,
        name="Buzzer",
        truthy=(True, 1, "1", "true", "on"),
        diagnostic=True,
    ),
}

# Keys that carry bookkeeping rather than state and must never become entities.
IGNORED_KEYS = frozenset(
    {
        "measureid",
        "measure_id",
        "event",
        "timestamp",
        "ts",
        "time",
        "model",
        "units",
        "unit",
        "sn",
        "id",
        "nodeid",
        "node_id",
        "name",
        "type",
        "version",
        "fw",
        "hw",
    }
)

# Units reported by the hub's ``config`` response, normalised to what Home
# Assistant expects.
UNIT_ALIASES = {
    "℃": "°C",
    "C": "°C",
    "degC": "°C",
    "℉": "°F",
    "F": "°F",
    "degF": "°F",
    "%RH": "%",
}

# Units Home Assistant accepts for each device class we assign.  If the hub
# declares something else, the unit wins and the device class is dropped --
# keeping both would make Home Assistant reject the state and break statistics.
DEVICE_CLASS_UNITS: dict[str, frozenset[str]] = {
    "temperature": frozenset({"°C", "°F", "K"}),
    "humidity": frozenset({"%"}),
    "battery": frozenset({"%"}),
    "atmospheric_pressure": frozenset(
        {"cbar", "bar", "hPa", "mmHg", "inHg", "kPa", "mbar", "Pa", "psi"}
    ),
    "voltage": frozenset({"V", "mV", "µV", "kV", "MV"}),
    "signal_strength": frozenset({"dB", "dBm"}),
}

# Word forms the hub may use for a leak severity, lowest to highest.
SEVERITY_ORDER = ("none", "dry", "low", "medium", "mid", "high", "critical")


@dataclass
class Topic:
    """A parsed MOCREO MQTT topic."""

    hub_sn: str
    node_id: str
    action: str
    sub: str | None = None


@dataclass
class Reading:
    """One field extracted from a payload, ready to become/refresh an entity."""

    hub_sn: str
    node_id: str
    spec: FieldSpec
    value: Any
    source: str
    raw_key: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def node_key(self) -> str:
        return f"{self.hub_sn}:{self.node_id}"

    @property
    def unique_key(self) -> str:
        return f"{self.hub_sn}:{self.node_id}:{self.spec.key}"


# --------------------------------------------------------------------------
# Topic / payload helpers
# --------------------------------------------------------------------------


def parse_topic(prefix: str, topic: str) -> Topic | None:
    """Return a :class:`Topic` for hub->broker traffic, else ``None``.

    ``prefix`` may itself contain slashes (e.g. ``home/mocreo``), which is why
    the prefix is stripped as a string rather than compared segment-by-segment.
    """
    if not topic.startswith(f"{prefix}/"):
        return None
    parts = topic[len(prefix) + 1 :].split("/")
    if len(parts) < 4 or parts[1] != "node":
        return None
    action = parts[3]
    sub = parts[4] if len(parts) > 4 else None
    # ``config/get`` is our own outbound request echoed by a shared broker.
    if action == "config" and sub == "get":
        return None
    return Topic(hub_sn=parts[0], node_id=parts[2], action=action, sub=sub)


def decode_payload(payload: str | bytes) -> list[dict[str, Any]]:
    """Decode a payload into a list of dicts. Never raises."""
    if isinstance(payload, bytes):
        try:
            payload = payload.decode("utf-8", "replace")
        except Exception:  # pragma: no cover - defensive
            return []
    payload = (payload or "").strip()
    if not payload:
        return []
    try:
        data = json.loads(payload)
    except (ValueError, TypeError):
        return []
    if isinstance(data, dict):
        return [data]
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    return []


def normalise_unit(unit: Any) -> str | None:
    if not isinstance(unit, str):
        return None
    unit = unit.strip()
    return UNIT_ALIASES.get(unit, unit) or None


def _normalise_key(key: str) -> str:
    return key.strip().lower()


def _title(key: str) -> str:
    cleaned = key.replace("_", " ").replace("-", " ").strip()
    # camelCase -> spaced
    out: list[str] = []
    for i, ch in enumerate(cleaned):
        if ch.isupper() and i and not cleaned[i - 1].isupper() and cleaned[i - 1] != " ":
            out.append(" ")
        out.append(ch)
    words = "".join(out).split()
    if not words:
        return key
    # Home Assistant entity names are sentence case: only the first word is
    # capitalised, except for acronyms the device sent in caps.
    rendered = [words[0][:1].upper() + words[0][1:]]
    rendered += [w if w.isupper() and len(w) > 1 else w.lower() for w in words[1:]]
    return " ".join(rendered)


def spec_for(key: str, value: Any, units: dict[str, Any] | None = None) -> FieldSpec | None:
    """Return the :class:`FieldSpec` for a payload key, or ``None`` to skip it."""
    norm = _normalise_key(key)
    if norm in IGNORED_KEYS:
        return None
    if isinstance(value, (dict, list)):
        return None

    known = KNOWN_FIELDS.get(norm)
    if known is not None:
        unit = normalise_unit((units or {}).get(key)) or normalise_unit(
            (units or {}).get(norm)
        )
        if unit and known.kind == KIND_SENSOR and unit != known.unit:
            # Trust the hub's own declared unit over our default, but keep the
            # device class only when the unit is one Home Assistant accepts for
            # it.  A device class paired with a foreign unit makes HA reject the
            # state outright, so the device class is what gives way.
            allowed = DEVICE_CLASS_UNITS.get(known.device_class or "")
            if allowed is not None and unit not in allowed:
                known = replace(known, unit=unit, device_class=None)
            else:
                known = replace(known, unit=unit)
        return known

    # Unknown key -> best-effort entity so nothing is silently dropped.
    if isinstance(value, bool):
        return FieldSpec(
            key=norm,
            kind=KIND_BINARY,
            name=_title(key),
            truthy=(True, 1, "1", "true", "on"),
        )
    unit = normalise_unit((units or {}).get(key)) or normalise_unit((units or {}).get(norm))
    if isinstance(value, (int, float)):
        return FieldSpec(
            key=norm,
            kind=KIND_SENSOR,
            name=_title(key),
            unit=unit,
            state_class="measurement",
        )
    if isinstance(value, str):
        return FieldSpec(key=norm, kind=KIND_SENSOR, name=_title(key), unit=unit)
    return None


def coerce_value(spec: FieldSpec, value: Any, source: str, temp_hundredths: bool) -> Any:
    """Apply scaling / boolean coercion for one reading."""
    if spec.kind == KIND_BINARY:
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(value)
        if isinstance(value, str):
            return value.strip().lower() in {str(t).lower() for t in spec.truthy}
        return None

    if isinstance(value, bool):
        return int(value)

    if spec.data_scale != 1.0 and source == "data" and isinstance(value, int):
        # MOCREO sends hundredths of a degree as an integer on the data topic,
        # and already-scaled floats on the event topic.  Users can turn the
        # divide-by-100 off if a future firmware changes this.
        if spec.device_class in ("temperature", "humidity") and not temp_hundredths:
            return value
        scaled = value * spec.data_scale
        return round(scaled, 4)

    return value


def extract_readings(
    topic: Topic,
    payload: str | bytes,
    units: dict[str, Any] | None = None,
    temp_hundredths: bool = True,
) -> tuple[list[Reading], dict[str, Any]]:
    """Turn one MQTT message into readings plus node metadata.

    Returns ``(readings, meta)`` where ``meta`` may contain ``model``,
    ``units``, ``timestamp`` and ``event``.
    """
    objects = decode_payload(payload)
    meta: dict[str, Any] = {}
    readings: list[Reading] = []

    for obj in objects:
        if isinstance(obj.get("model"), str):
            meta["model"] = obj["model"]
        if isinstance(obj.get("units"), dict):
            meta["units"] = obj["units"]
            units = {**(units or {}), **obj["units"]}
        for tkey in ("timestamp", "ts", "time"):
            if isinstance(obj.get(tkey), (int, float)):
                meta["timestamp"] = obj[tkey]
                break

        if topic.action == "config":
            # Config responses carry metadata only; no state to publish.
            continue

        if topic.action == "event":
            evt = obj.get("event")
            if isinstance(evt, str):
                meta["event"] = evt
                readings.append(
                    Reading(
                        hub_sn=topic.hub_sn,
                        node_id=topic.node_id,
                        spec=FieldSpec(
                            key="rule_alarm",
                            kind=KIND_BINARY,
                            name="Rule alarm",
                            device_class="problem",
                            truthy=("trigger",),
                        ),
                        value=evt.strip().lower() == "trigger",
                        source=topic.action,
                        raw_key="event",
                        extra={
                            k: v
                            for k, v in obj.items()
                            if k not in ("event",) and not isinstance(v, (dict, list))
                        },
                    )
                )

        for key, value in obj.items():
            spec = spec_for(key, value, units)
            if spec is None:
                continue
            coerced = coerce_value(spec, value, topic.action, temp_hundredths)
            if coerced is None:
                continue
            readings.append(
                Reading(
                    hub_sn=topic.hub_sn,
                    node_id=topic.node_id,
                    spec=spec,
                    value=coerced,
                    source=topic.action,
                    raw_key=key,
                )
            )

    return readings, meta


def severity_sort_key(value: Any) -> int:
    """Rank a leak severity word so automations can compare them."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return int(value)
    if isinstance(value, str):
        low = value.strip().lower()
        if low in SEVERITY_ORDER:
            return SEVERITY_ORDER.index(low)
    return -1
