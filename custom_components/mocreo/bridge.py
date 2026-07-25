"""MQTT bridge: subscribes to the MOCREO hub and tracks node state."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.dispatcher import async_dispatcher_send

from .const import (
    CONF_DISCOVERED,
    DOMAIN,
    MANUFACTURER,
    SIGNAL_NODE_UPDATED,
)
from .parser import (
    KIND_BINARY,
    KIND_SENSOR,
    FieldSpec,
    Reading,
    extract_readings,
    parse_topic,
)

_LOGGER = logging.getLogger(__name__)

SAVE_DEBOUNCE = 20.0


class MocreoNode:
    """Runtime state for a single MOCREO sensor node."""

    def __init__(self, hub_sn: str, node_id: str) -> None:
        self.hub_sn = hub_sn
        self.node_id = node_id
        self.model: str | None = None
        self.units: dict[str, Any] = {}
        self.values: dict[str, Any] = {}
        self.extra: dict[str, dict[str, Any]] = {}
        self.online: bool = True
        self.last_seen: float | None = None
        self.last_payloads: dict[str, Any] = {}

    @property
    def key(self) -> str:
        return f"{self.hub_sn}:{self.node_id}"

    @property
    def name(self) -> str:
        model = self.model or "Sensor"
        return f"MOCREO {model} {self.node_id[-6:]}"


class MocreoBridge:
    """Owns the MQTT subscription and fans readings out to entity platforms."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        prefix: str,
        temp_hundredths: bool,
    ) -> None:
        self.hass = hass
        self.entry = entry
        self.prefix = prefix
        self.temp_hundredths = temp_hundredths
        self.nodes: dict[str, MocreoNode] = {}
        self.specs: dict[str, FieldSpec] = {}
        self._unsub: Callable[[], None] | None = None
        self._add_entities: dict[str, Callable[[list[tuple[str, FieldSpec]]], None]] = {}
        self._pending: dict[str, list[tuple[str, FieldSpec]]] = {
            KIND_SENSOR: [],
            KIND_BINARY: [],
        }
        self._known: set[str] = set()
        self._save_handle: Any = None
        self._mqtt: Any = None

    # -- lifecycle ---------------------------------------------------------

    @callback
    def async_prepare(self) -> None:
        """Recreate entities discovered in earlier runs.

        Must run *before* the platforms are set up so the restored entities are
        handed straight to ``async_add_entities``.
        """
        self._restore_discovered()

    async def async_start(self) -> None:
        """Subscribe to the hub's MQTT traffic."""
        from homeassistant.components import mqtt  # local import: soft dep at import time

        self._mqtt = mqtt
        for node in list(self.nodes.values()):
            self._ensure_devices(node)

        await mqtt.async_wait_for_mqtt_client(self.hass)
        topic = f"{self.prefix}/+/node/+/#"
        self._unsub = await mqtt.async_subscribe(self.hass, topic, self._handle_message, 0)
        _LOGGER.debug("Subscribed to %s", topic)

        # Ask every known node to re-announce its model/units.  Snapshot the
        # dict first: the subscription above is already live, so awaiting here
        # can let _handle_message insert a new node mid-iteration.
        for node in list(self.nodes.values()):
            await self.async_request_config(node)

    async def async_stop(self) -> None:
        if self._unsub is not None:
            self._unsub()
            self._unsub = None
        if self._save_handle is not None:
            self._save_handle()
            self._save_handle = None
            self._save_discovered()

    async def async_request_config(self, node: MocreoNode) -> None:
        """Ask the hub for a node's model and units."""
        if self._mqtt is None:
            return
        topic = f"{self.prefix}/{node.hub_sn}/node/{node.node_id}/config/get"
        try:
            await self._mqtt.async_publish(self.hass, topic, "{}", 0, False)
        except Exception as err:  # pragma: no cover - broker hiccups
            _LOGGER.debug("Could not request config for %s: %s", node.key, err)

    # -- platform registration --------------------------------------------

    @callback
    def register_platform(
        self, kind: str, add_entities: Callable[[list[tuple[str, FieldSpec]]], None]
    ) -> None:
        """Called by sensor.py / binary_sensor.py during their setup."""
        self._add_entities[kind] = add_entities
        pending = self._pending.get(kind) or []
        if pending:
            self._pending[kind] = []
            add_entities(pending)

    @callback
    def _dispatch_new(self, kind: str, items: list[tuple[str, FieldSpec]]) -> None:
        add = self._add_entities.get(kind)
        if add is None:
            self._pending.setdefault(kind, []).extend(items)
        else:
            add(items)

    # -- message handling --------------------------------------------------

    @callback
    def _handle_message(self, msg: Any) -> None:
        topic = parse_topic(self.prefix, msg.topic)
        if topic is None:
            return

        node = self.nodes.get(f"{topic.hub_sn}:{topic.node_id}")
        if node is None:
            node = MocreoNode(topic.hub_sn, topic.node_id)
            self.nodes[node.key] = node
            self.hass.async_create_task(self.async_request_config(node))

        readings, meta = extract_readings(
            topic,
            msg.payload,
            units=node.units,
            temp_hundredths=self.temp_hundredths,
        )

        node.last_payloads[topic.action] = _safe_payload(msg.payload)
        node.last_seen = time.time()
        model_changed = bool(meta.get("model")) and meta["model"] != node.model
        if meta.get("model"):
            node.model = meta["model"]
        if meta.get("units"):
            node.units.update(meta["units"])
        self._ensure_devices(node, update_model=model_changed)

        new_items: dict[str, list[tuple[str, FieldSpec]]] = {}
        for reading in readings:
            node.values[reading.spec.key] = reading.value
            if reading.extra:
                node.extra[reading.spec.key] = reading.extra
            if reading.spec.key == "found":
                node.online = bool(reading.value)
            if reading.unique_key not in self._known:
                self._known.add(reading.unique_key)
                self.specs[reading.unique_key] = reading.spec
                new_items.setdefault(reading.spec.kind, []).append(
                    (node.key, reading.spec)
                )

        for kind, items in new_items.items():
            self._dispatch_new(kind, items)

        if new_items or meta.get("model") or meta.get("units"):
            self._schedule_save()

        async_dispatcher_send(self.hass, SIGNAL_NODE_UPDATED.format(node.key))

    # -- device registry ---------------------------------------------------

    @callback
    def _ensure_devices(self, node: MocreoNode, update_model: bool = False) -> None:
        """Make sure the hub device exists so nodes can hang off it."""
        registry = dr.async_get(self.hass)
        registry.async_get_or_create(
            config_entry_id=self.entry.entry_id,
            identifiers={(DOMAIN, node.hub_sn)},
            manufacturer=MANUFACTURER,
            name=f"MOCREO Hub {node.hub_sn}",
            model="Smart Hub",
            serial_number=node.hub_sn,
        )
        if not update_model:
            return
        device = registry.async_get_device(identifiers={(DOMAIN, node.key)})
        if device is not None:
            registry.async_update_device(
                device.id, model=node.model, name=node.name
            )

    # -- persistence -------------------------------------------------------

    def _restore_discovered(self) -> None:
        """Recreate entities discovered in earlier runs.

        LoRa nodes report slowly, so without this every entity would vanish
        from the dashboard after a restart until the next transmission.
        """
        stored = self.entry.data.get(CONF_DISCOVERED) or {}
        from .parser import KNOWN_FIELDS  # noqa: PLC0415

        for node_key, info in stored.items():
            if ":" not in node_key:
                continue
            hub_sn, node_id = node_key.split(":", 1)
            node = MocreoNode(hub_sn, node_id)
            node.model = info.get("model")
            node.units = dict(info.get("units") or {})
            self.nodes[node_key] = node
            for field_key, saved in (info.get("fields") or {}).items():
                spec = KNOWN_FIELDS.get(field_key)
                if spec is None:
                    spec = FieldSpec(
                        key=field_key,
                        kind=saved.get("kind", KIND_SENSOR),
                        name=saved.get("name"),
                        device_class=saved.get("device_class"),
                        unit=saved.get("unit"),
                        state_class=saved.get("state_class"),
                        diagnostic=saved.get("diagnostic", False),
                        truthy=tuple(saved.get("truthy") or ()),
                    )
                unique = f"{node_key}:{field_key}"
                self._known.add(unique)
                self.specs[unique] = spec
                self._pending.setdefault(spec.kind, []).append((node_key, spec))

    @callback
    def _schedule_save(self) -> None:
        from homeassistant.helpers.event import async_call_later  # noqa: PLC0415

        if self._save_handle is not None:
            self._save_handle()
        self._save_handle = async_call_later(
            self.hass, SAVE_DEBOUNCE, self._save_discovered_cb
        )

    @callback
    def _save_discovered_cb(self, _now: Any) -> None:
        self._save_handle = None
        self._save_discovered()

    def _save_discovered(self) -> None:
        payload: dict[str, Any] = {}
        for node_key, node in self.nodes.items():
            fields: dict[str, Any] = {}
            for unique, spec in self.specs.items():
                if not unique.startswith(f"{node_key}:"):
                    continue
                fields[spec.key] = {
                    "kind": spec.kind,
                    "name": spec.name,
                    "device_class": spec.device_class,
                    "unit": spec.unit,
                    "state_class": spec.state_class,
                    "diagnostic": spec.diagnostic,
                    "truthy": list(spec.truthy),
                }
            payload[node_key] = {
                "model": node.model,
                "units": node.units,
                "fields": fields,
            }
        if payload == (self.entry.data.get(CONF_DISCOVERED) or {}):
            return
        self.hass.config_entries.async_update_entry(
            self.entry, data={**self.entry.data, CONF_DISCOVERED: payload}
        )

    # -- helpers -----------------------------------------------------------

    def node(self, node_key: str) -> MocreoNode | None:
        return self.nodes.get(node_key)

    def diagnostics(self) -> dict[str, Any]:
        return {
            "prefix": self.prefix,
            "temp_hundredths": self.temp_hundredths,
            "nodes": {
                key: {
                    "model": node.model,
                    "units": node.units,
                    "online": node.online,
                    "last_seen": node.last_seen,
                    "values": node.values,
                    "last_payloads": node.last_payloads,
                }
                for key, node in self.nodes.items()
            },
        }


def _safe_payload(payload: Any) -> Any:
    if isinstance(payload, bytes):
        return payload.decode("utf-8", "replace")
    return payload


def hub_device_identifier(hub_sn: str) -> tuple[str, str]:
    return (DOMAIN, hub_sn)


__all__ = ["MocreoBridge", "MocreoNode", "Reading", "hub_device_identifier"]
