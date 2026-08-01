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


def t_lw1_real_payloads():
    """Verbatim payloads captured from a real LW1 on an H5 Pro hub."""
    print("\nLW1 real captured payloads (water_level 1 = wet, 0 = dry)")
    topic = P.parse_topic("mocreo", f"mocreo/{HUB}/node/{NODE}/data")

    wet, _ = P.extract_readings(
        topic,
        json.dumps([{"measureId": 3574, "water_level": 1, "model": "LW1", "timestamp": 1785609298}]),
    )
    vals = readings_by_key(wet)
    check("derived leak binary is on", vals.get("water_leak"), True)
    check("raw level kept", vals.get("water_level"), 1)
    leak = next(r.spec for r in wet if r.spec.key == "water_leak")
    check("leak is a binary_sensor", leak.kind, P.KIND_BINARY)
    check("leak device class", leak.device_class, "moisture")
    check("leak is not diagnostic", leak.diagnostic, False)
    check("raw level is diagnostic",
          next(r.spec.diagnostic for r in wet if r.spec.key == "water_level"), True)
    check("wet payload has no temperature", "temperature" in vals, False)

    dry, _ = P.extract_readings(
        topic,
        json.dumps([{"measureId": 3576, "timestamp": 1785609301, "water_level": 0,
                     "model": "LW1", "temperature": 1800}]),
    )
    vals = readings_by_key(dry)
    check("derived leak binary is off", vals.get("water_leak"), False)
    check("raw level 0", vals.get("water_level"), 0)
    check("onboard temperature scaled", vals.get("temperature"), 18.0)

    print("  severity values above 1 must still read as wet")
    for level in (2, 3, "High", "low"):
        r, _ = P.extract_readings(topic, json.dumps([{"model": "LW1", "water_level": level}]))
        check(f"water_level {level!r} -> wet", readings_by_key(r).get("water_leak"), True)
    for level in (0, "dry", "none"):
        r, _ = P.extract_readings(topic, json.dumps([{"model": "LW1", "water_level": level}]))
        check(f"water_level {level!r} -> dry", readings_by_key(r).get("water_leak"), False)

    print("  non-leak keys must not sprout a leak entity")
    r, _ = P.extract_readings(topic, json.dumps([{"model": "LS2", "temperature": 500, "battery": 90}]))
    check("no spurious water_leak", "water_leak" in readings_by_key(r), False)


def t_captured_session():
    """The full MQTT capture from a real H5 Pro: LW1 wet/dry cycles + an LS2 freezer report."""
    print("\nFull captured session (verbatim from hardware)")
    LW = "0030AE1117515500"
    LS = "0030AEA4059AB600"
    HUBSN = "MCFC012CD696D4"

    def feed(node, action, payload):
        t = P.parse_topic("mocreo", f"mocreo/{HUBSN}/node/{node}/{action}")
        return P.extract_readings(t, json.dumps(payload))

    # Message 9 / 10: severity escalates to 2 while wet
    r, _ = feed(LW, "data", [{"measureId": -1, "water_level": 2, "model": "LW1", "timestamp": 1785609538}])
    v = readings_by_key(r)
    check("data water_level 2 -> wet", v.get("water_leak"), True)
    check("severity 2 preserved", v.get("water_level"), 2)
    check("measureId -1 ignored", "measureid" in v, False)

    r, _ = feed(LW, "event", {"event": "trigger", "water_level": 2, "timestamp": 1785609539})
    v = readings_by_key(r)
    check("event trigger -> wet", v.get("water_leak"), True)
    check("no duplicate rule_alarm on a leak event", "rule_alarm" in v, False)

    # Message 11 / 12: recovery
    r, _ = feed(LW, "data", [{"measureId": -1, "timestamp": 1785609552, "water_level": 0,
                              "model": "LW1", "temperature": 1800}])
    v = readings_by_key(r)
    check("recovery data -> dry", v.get("water_leak"), False)
    check("recovery carries temperature", v.get("temperature"), 18.0)

    r, meta = feed(LW, "event", {"event": "recover", "water_level": 0, "timestamp": 1785609553})
    v = readings_by_key(r)
    check("event recover -> dry", v.get("water_leak"), False)
    check("meta records the event", meta.get("event"), "recover")

    # Message 6: the LS2 freezer probe
    r, meta = feed(LS, "data", [{"measureId": 21111, "temperature": -2420,
                                 "model": "LS2", "timestamp": 1785609496}])
    v = readings_by_key(r)
    check("freezer temperature", v.get("temperature"), -24.2)
    check("LS2 model", meta.get("model"), "LS2")
    check("LS2 grows no leak entity", "water_leak" in v, False)

    # A threshold rule on a non-leak sensor must still produce the generic alarm.
    r, _ = feed(LS, "event", {"event": "trigger", "temperature": -800, "timestamp": 1785609600})
    v = readings_by_key(r)
    check("temperature rule -> rule_alarm", v.get("rule_alarm"), True)
    check("integer event temp is scaled", v.get("temperature"), -8.0)
    r, _ = feed(LS, "event", {"event": "trigger", "temperature": -8.0, "timestamp": 1785609600})
    check("float event temp untouched", readings_by_key(r).get("temperature"), -8.0)


def t_water_level_labels():
    print("\nWater level labels (0 dry, 1 base contacts, 2-4 rising up the side)")
    check("0", P.water_level_label(0), "Dry")
    check("1", P.water_level_label(1), "Surface")
    check("2", P.water_level_label(2), "Shallow")
    check("3", P.water_level_label(3), "Rising")
    check("4", P.water_level_label(4), "Deep")
    check("scale max", P.WATER_LEVEL_MAX, 4)

    print("  values off the assumed scale must not be mislabelled")
    check("5 falls back", P.water_level_label(5), "Level 5")
    check("9 falls back", P.water_level_label(9), "Level 9")
    check("negative falls back", P.water_level_label(-1), "Level -1")

    print("  odd types survive")
    check("float 2.0", P.water_level_label(2.0), "Shallow")
    check("float 2.5", P.water_level_label(2.5), "Level 2.5")
    check("string '3'", P.water_level_label("3"), "Rising")
    check("word passthrough", P.water_level_label("high"), "High")
    check("None", P.water_level_label(None), None)
    check("bool rejected", P.water_level_label(True), None)
    check("empty string", P.water_level_label(""), None)

    print("  every level still reads as wet except 0")
    topic = P.parse_topic("mocreo", f"mocreo/{HUB}/node/{NODE}/data")
    for level in (1, 2, 3, 4, 5):
        r, _ = P.extract_readings(topic, json.dumps([{"model": "LW1", "water_level": level}]))
        check(f"level {level} wet", readings_by_key(r).get("water_leak"), True)
    r, _ = P.extract_readings(topic, json.dumps([{"model": "LW1", "water_level": 0}]))
    check("level 0 dry", readings_by_key(r).get("water_leak"), False)

    print("  level sensor graphs in history")
    spec = next(r.spec for r in r if r.spec.key == "water_level")
    check("state class", spec.state_class, "measurement")
    check("still diagnostic", spec.diagnostic, True)


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
    t_lw1_real_payloads,
    t_captured_session,
    t_water_level_labels,
    t_lw1_severity,
    t_unknown_fields,
    t_unit_conflicts,
    t_robustness,
    t_bytes_payload,
):
    fn()

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
