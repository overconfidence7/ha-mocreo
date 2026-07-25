"""Config flow for MOCREO."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback

from .const import (
    CONF_PREFIX,
    CONF_TEMP_HUNDREDTHS,
    DEFAULT_PREFIX,
    DEFAULT_TEMP_HUNDREDTHS,
    DOMAIN,
)

SCHEMA = vol.Schema(
    {
        vol.Required(CONF_PREFIX, default=DEFAULT_PREFIX): str,
        vol.Required(CONF_TEMP_HUNDREDTHS, default=DEFAULT_TEMP_HUNDREDTHS): bool,
    }
)


class MocreoConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the MOCREO config flow."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Single step: confirm the MQTT topic prefix."""
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")

        if not self.hass.config_entries.async_entries("mqtt"):
            return self.async_abort(reason="mqtt_required")

        if user_input is not None:
            return self.async_create_entry(
                title="MOCREO",
                data={
                    CONF_PREFIX: user_input[CONF_PREFIX].strip("/") or DEFAULT_PREFIX,
                    CONF_TEMP_HUNDREDTHS: user_input[CONF_TEMP_HUNDREDTHS],
                },
            )

        return self.async_show_form(step_id="user", data_schema=SCHEMA)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> MocreoOptionsFlow:
        return MocreoOptionsFlow()


class MocreoOptionsFlow(OptionsFlow):
    """Let the user change the prefix / scaling after setup."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(
                data={
                    CONF_PREFIX: user_input[CONF_PREFIX].strip("/") or DEFAULT_PREFIX,
                    CONF_TEMP_HUNDREDTHS: user_input[CONF_TEMP_HUNDREDTHS],
                }
            )

        current_prefix = self.config_entry.options.get(
            CONF_PREFIX, self.config_entry.data.get(CONF_PREFIX, DEFAULT_PREFIX)
        )
        current_scale = self.config_entry.options.get(
            CONF_TEMP_HUNDREDTHS,
            self.config_entry.data.get(CONF_TEMP_HUNDREDTHS, DEFAULT_TEMP_HUNDREDTHS),
        )
        schema = vol.Schema(
            {
                vol.Required(CONF_PREFIX, default=current_prefix): str,
                vol.Required(CONF_TEMP_HUNDREDTHS, default=current_scale): bool,
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
