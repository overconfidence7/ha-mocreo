"""Entity-level tests for the derived leak sensor, against a stubbed Home Assistant.

The bug these cover: an LW1 only sends `water_level` when it *changes*, so its
scheduled reports carry no leak field at all.  Before v1.0.5 the derived
`binary_sensor.*_water_leak` therefore had no value after a restart, went
unavailable, and stayed that way -- the state it restored on the *next* restart
was itself "unavailable", so it never recovered without a wet/dry cycle.

Run with:  python3 tests/test_entities.py
"""

from __future__ import annotations

import asyncio
import enum
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
entity_mod = _mod("homeassistant.helpers.entity")
platform_mod = _mod("homeassistant.helpers.entity_platform")
restore_mod = _mod("homeassistant.helpers.restore_state")
components = _mod("homeassistant.components")
components.__path__ = []
mqtt_mod = _mod("homeassistant.components.mqtt")
bs_mod = _mod("homeassistant.components.binary_sensor")
const_mod = _mod("homeassistant.const")
exc_mod = _mod("homeassistant.exceptions")


class ConfigEntryNotReady(Exception):
    pass


exc_mod.ConfigEntryNotReady = ConfigEntryNotReady


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


class ConfigEntriesManager:
    def async_update_entry(self, entry, data=None, **kw):
        if data is not None:
            entry.data = data
        return True


class EntityCategory(str, enum.Enum):
    DIAGNOSTIC = "diagnostic"


class Platform(str, enum.Enum):
    BINARY_SENSOR = "binary_sensor"
    SENSOR = "sensor"


const_mod.EntityCategory = EntityCategory
const_mod.Platform = Platform


def DeviceInfo(**kw):
    return kw


class _Registry:
    def async_get_or_create(self, **kw):
        return types.SimpleNamespace(id="dev")

    def async_get_device(self, **kw):
        return None

    def async_update_device(self, *a, **kw):
        return None


dr_mod.DeviceInfo = DeviceInfo
dr_mod.async_get = lambda hass: _Registry()

disp_mod.async_dispatcher_send = lambda hass, signal, *a: None
disp_mod.async_dispatcher_connect = lambda hass, signal, target: (lambda: None)
event_mod.async_call_later = lambda hass, delay, action: (lambda: None)


class Entity:
    hass = None

    async def async_added_to_hass(self):
        return None

    def async_on_remove(self, fn):
        return None

    def async_write_ha_state(self):
        return None


entity_mod.Entity = Entity
platform_mod.AddEntitiesCallback = object


class RestoreEntity:
    """Returns whatever ``_fake_last_state`` the test hung on the instance."""

    async def async_get_last_state(self):
        return getattr(self, "_fake_last_state", None)


restore_mod.RestoreEntity = RestoreEntity


class BinarySensorDeviceClass(str, enum.Enum):
    MOISTURE = "moisture"
    PROBLEM = "problem"
    CONNECTIVITY = "connectivity"


class BinarySensorEntity:
    _attr_device_class = None


bs_mod.BinarySensorDeviceClass = BinarySensorDeviceClass
bs_mod.BinarySensorEntity = BinarySensorEntity


async def _async_wait_for_mqtt_client(hass):
    return True


async def _async_subscribe(hass, topic, cb, qos=0):
    return lambda: None


async def _async_publish(hass, topic, payload, qos=0, retain=False):
    return None


mqtt_mod.async_wait_for_mqtt_client = _async_wait_for_mqtt_client
mqtt_mod.async_subscribe = _async_subscribe
mqtt_mod.async_publish = _async_publish

# ---------------------------------------------------------------------------

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "custom_components"))

from mocreo.binary_sensor import MocreoBinarySensor  # noqa: E402
from mocreo.bridge import MocreoBridge  # noqa: E402
from mocreo.parser import KIND_BINARY, KIND_SENSOR  # noqa: E402

HUB = "MC1234567890AB"
LW1 = "0030AEA400000201"
KEY = f"{HUB}:{LW1}"


class Msg:
    def __init__(self, topic, payload):
        self.topic = topic
        self.payload = payload


def build(entry_data=None):
    hass = HomeAssistant()
    entry = ConfigEntry(data=entry_data or {})
    bridge = MocreoBridge(hass, entry, "mocreo", True)
    added = {KIND_SENSOR: [], KIND_BINARY: []}
    bridge.async_prepare()
    bridge.register_platform(KIND_SENSOR, lambda items: added[KIND_SENSOR].extend(items))
    bridge.register_platform(KIND_BINARY, lambda items: added[KIND_BINARY].extend(items))
    return hass, entry, bridge, added


def leak_entity(bridge, added, last_state=None):
    node_key, spec = next(
        (nk, s) for nk, s in added[KIND_BINARY] if s.key == "water_leak"
    )
    ent = MocreoBinarySensor(bridge, node_key, spec)
    if last_state is not None:
        ent._fake_last_state = types.SimpleNamespace(state=last_state)
    return ent


async def main():
    print("a wet report makes the leak sensor on")
    hass, entry, bridge, added = build()
    bridge._handle_message(
        Msg(
            f"mocreo/{HUB}/node/{LW1}/data",
            json.dumps([{"model": "LW1", "water_level": 2, "timestamp": 1}]),
        )
    )
    ent = leak_entity(bridge, added)
    await ent.async_added_to_hass()
    check("is_on while wet", ent.is_on, True)
    check("available while wet", ent.available, True)
    check("level attribute", ent.extra_state_attributes["level"], 2)
    check("level label", ent.extra_state_attributes["level_label"], "Shallow")
    check("not flagged assumed", "assumed_state" in ent.extra_state_attributes, False)

    print("\nthe scheduled report that omits water_level does not erase the state")
    bridge._handle_message(
        Msg(
            f"mocreo/{HUB}/node/{LW1}/data",
            json.dumps([{"measureId": 4104, "model": "LW1", "temperature": 1770}]),
        )
    )
    check("still wet", ent.is_on, True)

    print("\nlast value is written into the config entry cache")
    bridge._save_discovered()
    saved = entry.data["discovered"][KEY]
    check("water_level persisted", saved["values"]["water_level"], 2)
    check("derived leak persisted", saved["values"]["water_leak"], True)
    check("temperature not persisted", "temperature" in saved["values"], False)
    check("cache is json-safe", json.loads(json.dumps(saved)) == saved, True)

    print("\nrestart: the leak sensor comes back with the cached level")
    _, _, b2, added2 = build(dict(entry.data))
    ent2 = leak_entity(b2, added2)
    await ent2.async_added_to_hass()
    check("restored on", ent2.is_on, True)
    check("restored available", ent2.available, True)
    check("restored level attribute", ent2.extra_state_attributes["level"], 2)
    check("node marked restored", b2.nodes[KEY].restored, True)
    b2._handle_message(
        Msg(f"mocreo/{HUB}/node/{LW1}/data", json.dumps([{"model": "LW1", "water_level": 0}]))
    )
    check("live message clears restored flag", b2.nodes[KEY].restored, False)
    check("dry after recovery", ent2.is_on, False)

    print("\nthe v1.0.4 bug: cache with no water_level at all")
    legacy = {
        "discovered": {
            KEY: {
                "model": "LW1",
                "units": {},
                "fields": {
                    "water_level": {
                        "kind": KIND_SENSOR,
                        "name": "Water level",
                        "device_class": None,
                        "unit": None,
                        "state_class": None,
                        "diagnostic": False,
                        "truthy": [],
                    },
                    "water_leak": {
                        "kind": KIND_BINARY,
                        "name": "Water leak",
                        "device_class": "moisture",
                        "unit": None,
                        "state_class": None,
                        "diagnostic": False,
                        "truthy": [],
                    },
                },
            }
        }
    }
    _, _, b3, added3 = build(legacy)
    spec3 = next(s for _, s in added3[KIND_BINARY] if s.key == "water_leak")
    check("canonical spec wins over the stale cache", spec3.assume_off, True)
    check("moisture class kept", spec3.device_class, "moisture")

    ent3 = leak_entity(b3, added3)
    await ent3.async_added_to_hass()
    check("assumed dry rather than unknown", ent3.is_on, False)
    check("available, not unavailable", ent3.available, True)
    check("flagged as assumed", ent3.extra_state_attributes["assumed_state"], True)

    print("\nthe same cache with the keys in the other order")
    # Which spec won used to depend on this ordering: `water_leak` is in the
    # literal field table as well as the derived one, and the literal copy had
    # no assume_off.  This is the order Nate's live cache actually had.
    flipped = {"discovered": {KEY: {
        "model": "LW1",
        "units": {},
        "fields": {
            "water_leak": legacy["discovered"][KEY]["fields"]["water_leak"],
            "water_level": legacy["discovered"][KEY]["fields"]["water_level"],
        },
    }}}
    _, _, b_flip, added_flip = build(flipped)
    spec_flip = next(s for _, s in added_flip[KIND_BINARY] if s.key == "water_leak")
    check("derived spec wins either way", spec_flip.assume_off, True)
    ent_flip = leak_entity(b_flip, added_flip)
    await ent_flip.async_added_to_hass()
    check("still assumed dry", ent_flip.is_on, False)
    check("still available", ent_flip.available, True)
    check("only one leak entity", len(added_flip[KIND_BINARY]), 1)

    print("\na literal water_leak payload key assumes dry too")
    _, _, b7, added7 = build()
    b7._handle_message(
        Msg(f"mocreo/{HUB}/node/{LW1}/data", json.dumps([{"model": "SW2", "water_leak": True}]))
    )
    spec7 = next(s for _, s in added7[KIND_BINARY] if s.key == "water_leak")
    check("literal moisture spec assumes off", spec7.assume_off, True)

    print("\nand recovers from a state that was itself restored as unavailable")
    ent4 = leak_entity(b3, added3, last_state="unavailable")
    await ent4.async_added_to_hass()
    check("still reports dry", ent4.is_on, False)
    check("still available", ent4.available, True)

    print("\na real restored state still wins over the assumption")
    ent5 = leak_entity(b3, added3, last_state="on")
    await ent5.async_added_to_hass()
    check("restored on is kept", ent5.is_on, True)
    check("not flagged assumed", "assumed_state" in ent5.extra_state_attributes, False)

    print("\nan offline node is still reported unavailable, not dry")
    b3.nodes[KEY].online = False
    check("offline wins", ent3.available, False)
    b3.nodes[KEY].online = True

    print("\nnon-derived binaries are unaffected")
    _, _, b6, added6 = build()
    b6._handle_message(
        Msg(f"mocreo/{HUB}/node/{LW1}/state", json.dumps({"found": True, "probe": 1}))
    )
    probe_key, probe_spec = next(
        (nk, s) for nk, s in added6[KIND_BINARY] if s.key == "probe"
    )
    check("probe does not assume off", probe_spec.assume_off, False)
    probe = MocreoBinarySensor(b6, probe_key, probe_spec)
    await probe.async_added_to_hass()
    check("probe reads its value", probe.is_on, True)
    b6.nodes[KEY].values.pop("probe")
    check("probe with no value is unknown", probe.is_on, None)

    print(f"\n{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


sys.exit(asyncio.run(main()))
