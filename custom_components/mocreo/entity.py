"""Shared entity base for MOCREO."""

from __future__ import annotations

from typing import Any

from homeassistant.const import EntityCategory
from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import Entity

from .bridge import MocreoBridge, MocreoNode
from .const import DOMAIN, MANUFACTURER, SIGNAL_NODE_UPDATED
from .parser import (
    LEVEL_KEYS,
    WATER_LEVEL_MAX,
    FieldSpec,
    water_level_label,
)

# Fields that describe the radio link rather than the measurement; these stay
# available even when the node is reported offline.
LINK_FIELDS = frozenset({"found", "signal", "rssi", "battery"})


class MocreoEntity(Entity):
    """Base class wiring one field of one node to Home Assistant."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, bridge: MocreoBridge, node_key: str, spec: FieldSpec) -> None:
        self.bridge = bridge
        self.node_key = node_key
        self.spec = spec
        hub_sn, node_id = node_key.split(":", 1)
        self._hub_sn = hub_sn
        self._node_id = node_id
        self._attr_unique_id = f"{node_key}:{spec.key}".replace(":", "_").lower()
        self._attr_name = spec.name or spec.key.replace("_", " ").capitalize()
        if spec.diagnostic:
            self._attr_entity_category = EntityCategory.DIAGNOSTIC
        if spec.icon:
            self._attr_icon = spec.icon
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, node_key)},
            manufacturer=MANUFACTURER,
            model=self.node.model if self.node else None,
            name=self.node.name if self.node else f"MOCREO {node_id[-6:]}",
            via_device=(DOMAIN, hub_sn),
            serial_number=node_id,
        )

    @property
    def node(self) -> MocreoNode | None:
        return self.bridge.node(self.node_key)

    @property
    def raw_value(self) -> Any:
        node = self.node
        if node is None:
            return None
        return node.values.get(self.spec.key)

    @property
    def available(self) -> bool:
        node = self.node
        if node is None:
            return False
        if self.spec.key in LINK_FIELDS:
            return True
        if not node.online:
            return False
        return self.raw_value is not None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        node = self.node
        if node is None:
            return None
        attrs: dict[str, Any] = {}
        if node.model:
            attrs["model"] = node.model
        attrs["node_id"] = self._node_id
        attrs["hub_serial"] = self._hub_sn
        # Surface the depth in words on both the level sensor and the leak
        # binary sensor, so a notification template needs only one entity.
        if self.spec.key in LEVEL_KEYS or self.spec.key == "water_leak":
            for level_key in LEVEL_KEYS:
                if level_key not in node.values:
                    continue
                label = water_level_label(node.values[level_key])
                if label is not None:
                    attrs["level"] = node.values[level_key]
                    attrs["level_label"] = label
                    attrs["level_scale_max"] = WATER_LEVEL_MAX
                break

        extra = node.extra.get(self.spec.key)
        if extra:
            attrs.update(extra)
        return attrs

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                SIGNAL_NODE_UPDATED.format(self.node_key),
                self._handle_update,
            )
        )

    @callback
    def _handle_update(self) -> None:
        self.async_write_ha_state()
