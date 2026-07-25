"""Standalone tests for the MOCREO payload parser (no Home Assistant needed).

Run with:  python3 tests/test_parser.py
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(
    0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "custom_components", "mocreo")
)

import parser as P  # noqa: E402

PASS = 0
FAIL = 0


def check(label, got, want):
    global PASS, FAIL
    if got == want:
        PASS += 1
        print(f"  ok   {label}")
    else:
        FAIL += 1
        print(f"  FAIL {label}\n         got:  {got!r}\n         want: {want!r}")


def readings_by_key(readings):
    return {r.spec.key: r.value for r in readings}


HUB = "MC1234567890AB"
NODE = "0030AEA400000100"


def t_topic():
    print("parse_topic")
    check(
        "data topic",
        P.parse_topic("mocreo", f"mocreo/{HUB}/node/{NODE}/data").__dict__,
        {"hub_sn": HUB, "node_id": NODE, "action": "data", "sub": None},
    )
    check("wrong prefix", P.parse_topic("mocreo", f"foo/{HUB}/node/{NODE}/data"), None)
    check("config/get ignored", P.parse_topic("mocreo", f"mocreo/{HUB}/node/{NODE}/config/get"), None)
    check("too short", P.parse_topic("mocreo", "mocreo/x/node"), None)
    check(
        "multi-segment prefix",
        P.parse_topic("home/mocreo", "home/mocreo/X/node/Y/state").__dict__,
        {"hub_sn": "X", "node_id": "Y", "action": "state", "sub": None},
    )
    check(
        "multi-segment prefix rejects config/get",
        P.parse_topic("home/mocreo", "home/mocreo/X/node/Y/config/get"),
        None,
    )
    check(
        "prefix must match at a boundary",
        P.parse_topic("mocreo", "mocreo2/X/node/Y/data"),
        None,
    )
    check("bare prefix", P.parse_topic("mocreo", "mocreo"), None)


def t_ls2_data():
    print("\nLS2 freezer temperature (data topic, hundredths as int)")
    topic = P.parse_topic("mocreo", f"mocreo/{HUB}/node/{NODE}/data")
    payload = json.dumps([{"measureId": 1, "timestamp": 1760682172, "model": "LS2", "temperature": -1850}])
    readings, meta = P.extract_readings(topic, payload)
    check("model captured", meta.get("model"), "LS2")
    check("temperature scaled", readings_by_key(readings), {"temperature": -18.5})
    spec = readings[0].spec
    check("device class", spec.device_class, "temperature")
    check("unit", spec.unit, "°C")
    check("state class", spec.state_class, "measurement")
    check("measureId dropped", "measureid" in readings_by_key(readings), False)


def t_scaling_off():
    print("\nScaling can be disabled")
    topic = P.parse_topic("mocreo", f"mocreo/{HUB}/node/{NODE}/data")
    payload = json.dumps([{"model": "LS2", "temperature": -18}])
    readings, _ = P.extract_readings(topic, payload, temp_hundredths=False)
    check("raw value kept", readings_by_key(readings), {"temperature": -18})


def t_event():
    print("\nEvent topic (rule trigger/recover)")
    topic = P.parse_topic("mocreo", f"mocreo/{HUB}/node/{NODE}/event")
    trig, meta = P.extract_readings(topic, json.dumps({"timestamp": 1, "event": "trigger", "temperature": 28.6}))
    vals = readings_by_key(trig)
    check("alarm on", vals.get("rule_alarm"), True)
    check("float temp not rescaled", vals.get("temperature"), 28.6)
    check("meta event", meta.get("event"), "trigger")

    rec, _ = P.extract_readings(topic, json.dumps({"event": "recover", "temperature": 3.1}))
    check("alarm off", readings_by_key(rec).get("rule_alarm"), False)


def t_state():
    print("\nState topic")
    topic = P.parse_topic("mocreo", f"mocreo/{HUB}/node/{NODE}/state")
    readings, _ = P.extract_readings(
        topic, json.dumps({"found": True, "battery": 72, "signal": -15, "probe": 1})
    )
    vals = readings_by_key(readings)
    check("found -> bool", vals.get("found"), True)
    check("battery", vals.get("battery"), 72)
    check("signal", vals.get("signal"), -15)
    check("probe 1 -> True", vals.get("probe"), True)
    kinds = {r.spec.key: r.spec.kind for r in readings}
    check("found is binary", kinds["found"], P.KIND_BINARY)
    check("battery is sensor", kinds["battery"], P.KIND_SENSOR)
    check("battery diagnostic", next(r.spec.diagnostic for r in readings if r.spec.key == "battery"), True)

    off, _ = P.extract_readings(topic, json.dumps({"found": False, "probe": 0}))
    check("offline", readings_by_key(off), {"found": False, "probe": False})


def t_config():
    print("\nConfig response (units learned, no state)")
    topic = P.parse_topic("mocreo", f"mocreo/{HUB}/node/{NODE}/config")
    readings, meta = P.extract_readings(
        topic,
        json.dumps({"model": "LS2", "units": {"battery": "%", "temperature": "℃", "signal": "dBm"}}),
    )
    check("no readings", readings, [])
    check("model", meta.get("model"), "LS2")
    check("units captured", meta["units"]["temperature"], "℃")
    check("unit normalised", P.normalise_unit("℃"), "°C")


def t_lw1_variants():
    """MOCREO has not published LW1 field names, so every plausible shape must work."""
    print("\nLW1 water leak - unknown field names must still produce a moisture entity")
    topic = P.parse_topic("mocreo", f"mocreo/{HUB}/node/{NODE}/data")
    for payload in (
        {"model": "LW1", "water": 1},
        {"model": "LW1", "leak": True},
        {"model": "LW1", "wet": "true"},
        {"model": "LW1", "waterLeak": 1},
        {"model": "LW1", "water_leak": "wet"},
        {"model": "LW1", "moisture": True},
    ):
        readings, _ = P.extract_readings(topic, json.dumps(payload))
        moisture = [r for r in readings if r.spec.device_class == "moisture"]
        key = next(k for k in payload if k != "model")
        check(f"{key} -> moisture on", (len(moisture), moisture[0].value if moisture else None), (1, True))

    dry, _ = P.extract_readings(topic, json.dumps({"model": "LW1", "water": 0}))
    check("water 0 -> off", readings_by_key(dry), {"water": False})


def t_lw1_severity():
    print("\nLW1 leak severity")
    topic = P.parse_topic("mocreo", f"mocreo/{HUB}/node/{NODE}/data")
    readings, _ = P.extract_readings(topic, json.dumps({"model": "LW1", "water": 1, "level": "High"}))
    vals = readings_by_key(readings)
    check("level passthrough", vals.get("level"), "High")
    check("severity rank high", P.severity_sort_key("High"), P.SEVERITY_ORDER.index("high"))
    check("severity rank numeric", P.severity_sort_key(2), 2)
    check("severity rank unknown", P.severity_sort_key("banana"), -1)


def t_unknown_fields():
    print("\nUnknown fields still surface (nothing is silently dropped)")
    topic = P.parse_topic("mocreo", f"mocreo/{HUB}/node/{NODE}/data")
    readings, _ = P.extract_readings(
        topic,
        json.dumps({"model": "ZZ9", "sparkleIndex": 42, "gizmoActive": True, "mode": "auto"}),
        units={"sparkleIndex": "lux"},
    )
    specs = {r.spec.key: r.spec for r in readings}
    check("numeric unknown -> sensor", specs["sparkleindex"].kind, P.KIND_SENSOR)
    check("unit from config units", specs["sparkleindex"].unit, "lux")
    check("camelCase name", specs["sparkleindex"].name, "Sparkle index")
    check("bool unknown -> binary", specs["gizmoactive"].kind, P.KIND_BINARY)
    check("string unknown -> sensor", specs["mode"].kind, P.KIND_SENSOR)


def t_unit_conflicts():
    print("\nHub-declared units must never be paired with an invalid device class")
    topic = P.parse_topic("mocreo", f"mocreo/{HUB}/node/{NODE}/data")

    ok, _ = P.extract_readings(
        topic, json.dumps({"temperature": 6800}), units={"temperature": "℉"}
    )
    check("compatible unit kept with device class",
          (ok[0].spec.unit, ok[0].spec.device_class), ("°F", "temperature"))

    bad, _ = P.extract_readings(topic, json.dumps({"battery": 3}), units={"battery": "V"})
    check("incompatible unit drops device class",
          (bad[0].spec.unit, bad[0].spec.device_class), ("V", None))

    weird, _ = P.extract_readings(
        topic, json.dumps({"humidity": 50}), units={"humidity": "g/m3"}
    )
    check("humidity in g/m3 loses device class",
          (weird[0].spec.unit, weird[0].spec.device_class), ("g/m3", None))

    plain, _ = P.extract_readings(topic, json.dumps({"signal": -20}), units={"signal": "dB"})
    check("dB is valid for signal_strength",
          (plain[0].spec.unit, plain[0].spec.device_class), ("dB", "signal_strength"))


def t_robustness():
    print("\nMalformed input must never raise")
    topic = P.parse_topic("mocreo", f"mocreo/{HUB}/node/{NODE}/data")
    for bad in ("", "   ", "not json", "[]", "null", "123", '{"a": {"b": 1}}', b"\xff\xfe"):
        readings, meta = P.extract_readings(topic, bad)
        check(f"survives {bad!r}", isinstance(readings, list), True)
    multi, _ = P.extract_readings(
        topic, json.dumps([{"model": "LS2", "temperature": 100}, {"temperature": 200}])
    )
    check("array with 2 objects -> last wins per key", [r.value for r in multi], [1.0, 2.0])


def t_bytes_payload():
    print("\nBytes payloads (paho hands us bytes)")
    topic = P.parse_topic("mocreo", f"mocreo/{HUB}/node/{NODE}/data")
    readings, _ = P.extract_readings(topic, b'[{"model":"LS2","temperature":500}]')
    check("bytes decoded", readings_by_key(readings), {"temperature": 5.0})


for fn in (
    t_topic,
    t_ls2_data,
    t_scaling_off,
    t_event,
    t_state,
    t_config,
    t_lw1_variants,
    t_lw1_severity,
    t_unknown_fields,
    t_unit_conflicts,
    t_robustness,
    t_bytes_payload,
):
    fn()

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
