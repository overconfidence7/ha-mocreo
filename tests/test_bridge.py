"""End-to-end test of bridge.py against a stubbed Home Assistant.

Home Assistant itself is not installed here (and needs Python 3.13), so this
builds just enough of the API surface bridge.py touches to exercise the real
logic: discovery, entity fan-out, device registration, persistence and restore.

Run with:  python3 tests/test_bridge.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import types

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


# ---------------------------------------------------------------------------
# Stub Home Assistant
# ---------------------------------------------------------------------------

def _mod(name):
    m = types.ModuleType(name)
    sys.modules[name] = m
    return m


ha = _mod("homeassistant")
ha.__path__ = []
core = _mod("homeassistant.core")
config_entries = _mod("homeassistant.config_entries")
helpers = _mod("homeassistant.helpers")
helpers.__path__ = []
dr_mod = _mod("homeassistant.helpers.device_registry")
disp_mod = _mod("homeassistant.helpers.dispatcher")
event_mod = _mod("homeassistant.helpers.event")
components = _mod("homeassistant.components")
components.__path__ = []
mqtt_mod = _mod("homeassistant.components.mqtt")


def callback(fn):
    return fn


class HomeAssistant:
    def __init__(self):
        self.config_entries = ConfigEntriesManager()
        self.tasks = []

    def async_create_task(self, coro):
        self.tasks.append(asyncio.ensure_future(coro))


core.HomeAssistant = HomeAssistant
core.callback = callback


class ConfigEntry:
    def __init__(self, data=None, options=None):
        self.entry_id = "abc123"
        self.data = data or {}
        self.options = options or {}

    def __class_getitem__(cls, item):
        return cls


config_entries.ConfigEntry = ConfigEntry

const_mod = _mod("homeassistant.const")


class Platform(str, _enum_base := __import__("enum").Enum):
    BINARY_SENSOR = "binary_sensor"
    SENSOR = "sensor"


class EntityCategory(str, _enum_base):
    DIAGNOSTIC = "diagnostic"


const_mod.Platform = Platform
const_mod.EntityCategory = EntityCategory

exc_mod = _mod("homeassistant.exceptions")


class ConfigEntryNotReady(Exception):
    pass


exc_mod.ConfigEntryNotReady = ConfigEntryNotReady


class ConfigEntriesManager:
    def async_update_entry(self, entry, data=None, **kw):
        if data is not None:
            entry.data = data
        return True


# device registry ------------------------------------------------------------

class _Device:
    def __init__(self, identifiers, **kw):
        self.id = repr(sorted(identifiers))
        self.identifiers = identifiers
        self.attrs = kw


class _DeviceRegistry:
    def __init__(self):
        self.devices = {}
        self.updates = []

    def async_get_or_create(self, *, config_entry_id, identifiers, **kw):
        key = repr(sorted(identifiers))
        if key not in self.devices:
            self.devices[key] = _Device(identifiers, **kw)
        return self.devices[key]

    def async_get_device(self, identifiers=None, **kw):
        return self.devices.get(repr(sorted(identifiers or set())))

    def async_update_device(self, device_id, **kw):
        self.updates.append((device_id, kw))


_REGISTRY = _DeviceRegistry()
dr_mod.async_get = lambda hass: _REGISTRY

# dispatcher -----------------------------------------------------------------

SIGNALS = []
disp_mod.async_dispatcher_send = lambda hass, signal, *a: SIGNALS.append(signal)

# event ----------------------------------------------------------------------

SCHEDULED = []


def _async_call_later(hass, delay, action):
    SCHEDULED.append((delay, action))

    def _cancel():
        if (delay, action) in SCHEDULED:
            SCHEDULED.remove((delay, action))

    return _cancel


event_mod.async_call_later = _async_call_later

# mqtt -----------------------------------------------------------------------

PUBLISHED = []
SUBSCRIBED = []


async def _async_wait_for_mqtt_client(hass):
    return True


async def _async_subscribe(hass, topic, cb, qos=0):
    SUBSCRIBED.append(topic)
    return lambda: SUBSCRIBED.remove(topic)


async def _async_publish(hass, topic, payload, qos=0, retain=False):
    PUBLISHED.append((topic, payload))


mqtt_mod.async_wait_for_mqtt_client = _async_wait_for_mqtt_client
mqtt_mod.async_subscribe = _async_subscribe
mqtt_mod.async_publish = _async_publish

# ---------------------------------------------------------------------------

HERE = os.path.dirname(os.path.abspath(__file__))
PKG_PARENT = os.path.join(HERE, "..", "custom_components")
sys.path.insert(0, PKG_PARENT)

from mocreo.bridge import MocreoBridge  # noqa: E402
from mocreo.parser import KIND_BINARY, KIND_SENSOR  # noqa: E402

HUB = "MC1234567890AB"
LS2 = "0030AEA400000100"
LW1 = "0030AEA400000201"


class Msg:
    def __init__(self, topic, payload):
        self.topic = topic
        self.payload = payload


def make_bridge(entry=None):
    hass = HomeAssistant()
    entry = entry or ConfigEntry()
    bridge = MocreoBridge(hass, entry, "mocreo", True)
    added = {KIND_SENSOR: [], KIND_BINARY: []}
    return hass, entry, bridge, added


def wire(bridge, added):
    bridge.register_platform(KIND_SENSOR, lambda items: added[KIND_SENSOR].extend(items))
    bridge.register_platform(KIND_BINARY, lambda items: added[KIND_BINARY].extend(items))


async def main():
    print("discovery from a cold start")
    hass, entry, bridge, added = make_bridge()
    bridge.async_prepare()
    wire(bridge, added)
    await bridge.async_start()
    check("subscribed to wildcard", SUBSCRIBED, ["mocreo/+/node/+/#"])

    bridge._handle_message(
        Msg(
            f"mocreo/{HUB}/node/{LS2}/data",
            json.dumps([{"measureId": 1, "timestamp": 1, "model": "LS2", "temperature": -1850}]),
        )
    )
    check("temperature sensor created", [s.key for _, s in added[KIND_SENSOR]], ["temperature"])
    check("node registered", list(bridge.nodes), [f"{HUB}:{LS2}"])
    check("value stored", bridge.nodes[f"{HUB}:{LS2}"].values["temperature"], -18.5)
    check("hub device created", len(_REGISTRY.devices), 1)
    await asyncio.sleep(0)
    check("config requested", PUBLISHED[-1][0], f"mocreo/{HUB}/node/{LS2}/config/get")

    print("\nrepeat readings do not duplicate entities")
    bridge._handle_message(
        Msg(f"mocreo/{HUB}/node/{LS2}/data", json.dumps([{"model": "LS2", "temperature": -1900}]))
    )
    check("still one sensor", len(added[KIND_SENSOR]), 1)
    check("value updated", bridge.nodes[f"{HUB}:{LS2}"].values["temperature"], -19.0)

    print("\nstate topic adds diagnostics + drives availability")
    bridge._handle_message(
        Msg(f"mocreo/{HUB}/node/{LS2}/state", json.dumps({"found": True, "battery": 72, "signal": -15, "probe": 1}))
    )
    check(
        "sensors now temp+battery+signal",
        sorted(s.key for _, s in added[KIND_SENSOR]),
        ["battery", "signal", "temperature"],
    )
    check("binaries found+probe", sorted(s.key for _, s in added[KIND_BINARY]), ["found", "probe"])
    check("node online", bridge.nodes[f"{HUB}:{LS2}"].online, True)
    bridge._handle_message(Msg(f"mocreo/{HUB}/node/{LS2}/state", json.dumps({"found": False})))
    check("node offline", bridge.nodes[f"{HUB}:{LS2}"].online, False)

    print("\nevent topic creates the rule alarm")
    bridge._handle_message(
        Msg(f"mocreo/{HUB}/node/{LS2}/event", json.dumps({"event": "trigger", "temperature": -8.2}))
    )
    check("rule_alarm added", "rule_alarm" in [s.key for _, s in added[KIND_BINARY]], True)
    check("event temp not rescaled", bridge.nodes[f"{HUB}:{LS2}"].values["temperature"], -8.2)

    print("\nconfig response updates model + units, not state")
    before = len(added[KIND_SENSOR])
    bridge._handle_message(
        Msg(
            f"mocreo/{HUB}/node/{LS2}/config",
            json.dumps({"model": "LS2", "units": {"temperature": "℃", "battery": "%", "signal": "dBm"}}),
        )
    )
    check("no new entities", len(added[KIND_SENSOR]), before)
    check("units learned", bridge.nodes[f"{HUB}:{LS2}"].units["temperature"], "℃")

    print("\nsecond node on the same hub")
    bridge._handle_message(
        Msg(
            f"mocreo/{HUB}/node/{LW1}/data",
            json.dumps([{"measureId": 3574, "water_level": 1, "model": "LW1", "timestamp": 1785609298}]),
        )
    )
    check("two nodes", len(bridge.nodes), 2)
    check("leak binary added", any(s.key == "water_leak" for _, s in added[KIND_BINARY]), True)
    check("raw level sensor added", any(s.key == "water_level" for _, s in added[KIND_SENSOR]), True)
    check("leak value is wet", bridge.nodes[f"{HUB}:{LW1}"].values["water_leak"], True)
    check("hub device still single", len(_REGISTRY.devices), 1)

    bridge._handle_message(
        Msg(f"mocreo/{HUB}/node/{LW1}/data", json.dumps([{"model": "LW1", "water_level": 0, "temperature": 1800}]))
    )
    check("leak clears", bridge.nodes[f"{HUB}:{LW1}"].values["water_leak"], False)
    check("lw1 onboard temp", bridge.nodes[f"{HUB}:{LW1}"].values["temperature"], 18.0)

    print("\nunrelated / malformed traffic is ignored")
    n_nodes = len(bridge.nodes)
    bridge._handle_message(Msg("zigbee2mqtt/whatever", "{}"))
    bridge._handle_message(Msg(f"mocreo/{HUB}/node/{LS2}/config/get", "{}"))
    bridge._handle_message(Msg(f"mocreo/{HUB}/node/{LS2}/data", "garbage"))
    check("no phantom nodes", len(bridge.nodes), n_nodes)

    print("\npersistence")
    for delay, action in list(SCHEDULED):
        action(None)
    saved = entry.data.get("discovered") or {}
    check("both nodes saved", sorted(saved), sorted([f"{HUB}:{LS2}", f"{HUB}:{LW1}"]))
    check("ls2 fields saved", sorted(saved[f"{HUB}:{LS2}"]["fields"]),
          ["battery", "found", "probe", "rule_alarm", "signal", "temperature"])
    check("model saved", saved[f"{HUB}:{LW1}"]["model"], "LW1")
    check("json round-trips", json.loads(json.dumps(saved)) == saved, True)

    await bridge.async_stop()
    check("first bridge unsubscribed", SUBSCRIBED, [])

    print("\nrestart: entities come back before any MQTT traffic")
    hass2, entry2, bridge2, added2 = make_bridge(ConfigEntry(data=dict(entry.data)))
    bridge2.async_prepare()
    wire(bridge2, added2)
    check("nodes restored", len(bridge2.nodes), 2)
    check(
        "sensors restored",
        sorted({s.key for _, s in added2[KIND_SENSOR]}),
        ["battery", "signal", "temperature", "water_level"],
    )
    check(
        "binaries restored",
        sorted({s.key for _, s in added2[KIND_BINARY]}),
        ["found", "probe", "rule_alarm", "water_leak"],
    )
    check("restored temp keeps device class",
          next(s for _, s in added2[KIND_SENSOR] if s.key == "temperature").device_class, "temperature")
    check("restored leak keeps device class",
          next(s for _, s in added2[KIND_BINARY] if s.key == "water_leak").device_class, "moisture")

    await bridge2.async_start()
    check("re-asks config for restored nodes",
          sorted(t for t, _ in PUBLISHED if "config/get" in t)[-2:],
          sorted([f"mocreo/{HUB}/node/{LS2}/config/get", f"mocreo/{HUB}/node/{LW1}/config/get"]))

    print("\nno duplicate entities after restore + fresh message")
    n_before = len(added2[KIND_SENSOR])
    bridge2._handle_message(
        Msg(f"mocreo/{HUB}/node/{LS2}/data", json.dumps([{"model": "LS2", "temperature": -2000}]))
    )
    check("no duplicates", len(added2[KIND_SENSOR]), n_before)
    check("value applied", bridge2.nodes[f"{HUB}:{LS2}"].values["temperature"], -20.0)

    print("\npersistence is idempotent (no config-entry write loop)")
    entry2.data = dict(entry2.data)
    bridge2._save_discovered()
    snapshot = json.dumps(entry2.data.get("discovered"), sort_keys=True)
    bridge2._save_discovered()
    check("second save is a no-op",
          json.dumps(entry2.data.get("discovered"), sort_keys=True), snapshot)

    print("\nteardown")
    await bridge2.async_stop()
    check("unsubscribed", SUBSCRIBED, [])

    print("\nupgrade path: a v1.0.1 cache with only water_level gains the leak entity")
    legacy = ConfigEntry(data={"discovered": {
        f"{HUB}:{LW1}": {
            "model": "LW1",
            "units": {},
            "fields": {
                "water_level": {"kind": KIND_SENSOR, "name": "Water level",
                                "device_class": None, "unit": None,
                                "state_class": None, "diagnostic": False, "truthy": []},
            },
        }
    }})
    _, _, b_up, added_up = make_bridge(legacy)
    b_up.async_prepare()
    wire(b_up, added_up)
    check("leak entity seeded on restore",
          [s.key for _, s in added_up[KIND_BINARY]], ["water_leak"])
    check("seeded leak has moisture class",
          added_up[KIND_BINARY][0][1].device_class, "moisture")
    check("raw level still restored",
          [s.key for _, s in added_up[KIND_SENSOR]], ["water_level"])
    b_up._handle_message(
        Msg(f"mocreo/{HUB}/node/{LW1}/data", json.dumps([{"model": "LW1", "water_level": 1}]))
    )
    check("no duplicate leak entity after message",
          len(added_up[KIND_BINARY]), 1)
    check("seeded entity receives the value",
          b_up.nodes[f"{HUB}:{LW1}"].values["water_leak"], True)

    print("\ncustom prefix is honoured")
    hass3 = HomeAssistant()
    entry3 = ConfigEntry()
    b3 = MocreoBridge(hass3, entry3, "sensors", True)
    added3 = {KIND_SENSOR: [], KIND_BINARY: []}
    b3.async_prepare()
    wire(b3, added3)
    await b3.async_start()
    check("subscribes with prefix", SUBSCRIBED, ["sensors/+/node/+/#"])
    b3._handle_message(Msg(f"sensors/{HUB}/node/{LS2}/data", json.dumps([{"temperature": 100}])))
    check("prefixed message parsed", len(b3.nodes), 1)
    b3._handle_message(Msg(f"mocreo/{HUB}/node/{LS2}/data", json.dumps([{"temperature": 100}])))
    check("default prefix now ignored", len(b3.nodes), 1)
    await b3.async_stop()

    print(f"\n{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


sys.exit(asyncio.run(main()))
