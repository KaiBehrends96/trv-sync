"""Config flow: one entry per room; entities and guard values in one form."""

from __future__ import annotations

import logging

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import selector

from .config import RoomConfig
from .const import (
    DEFAULT_DEADBAND,
    DEFAULT_STALENESS_MINUTES,
    DEFAULT_UPDATE_INTERVAL_SECONDS,
    DEFAULT_WRITE_RATE_LIMIT_MINUTES,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)


def _room_schema(defaults: dict) -> dict:
    """Build the schema shared by the user step and the options flow."""
    return {
        **(
            {vol.Required("name"): str}
            if "name" not in defaults
            else {}
        ),  # options flow does not allow renaming
        vol.Required("trvs", default=defaults.get("trvs")): selector.EntitySelector(
            selector.EntitySelectorConfig(domain=["climate"], multiple=True)
        ),
        vol.Optional("temp_sensor", default=defaults.get("temp_sensor")): selector.EntitySelector(
            selector.EntitySelectorConfig(domain=["sensor"])
        ),
        vol.Required("deadband", default=defaults.get("deadband", DEFAULT_DEADBAND)): (
            selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=0.1, max=5.0, step=0.1, mode=selector.NumberSelectorMode.BOX
                )
            )
        ),
        vol.Required(
            "write_rate_limit_minutes",
            default=defaults.get("write_rate_limit_minutes", DEFAULT_WRITE_RATE_LIMIT_MINUTES),
        ): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=1, max=240, step=1, mode=selector.NumberSelectorMode.BOX
            )
        ),
        vol.Required(
            "staleness_minutes",
            default=defaults.get("staleness_minutes", DEFAULT_STALENESS_MINUTES),
        ): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=3, max=180, step=1, mode=selector.NumberSelectorMode.BOX
            )
        ),
        vol.Required(
            "update_interval_seconds",
            default=defaults.get("update_interval_seconds", DEFAULT_UPDATE_INTERVAL_SECONDS),
        ): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=10, max=600, step=5, mode=selector.NumberSelectorMode.BOX
            )
        ),
    }


class TrvSyncConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Create a room entry: name, TRV list, optional external sensor, guards."""

    VERSION = 1

    async def async_step_user(self, user_input=None):
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                RoomConfig.build(user_input)
            except Exception as err:  # noqa: BLE001 - flow-level validation surface
                _LOGGER.warning("trv_sync config validation failed: %s", err)
                errors["base"] = "invalid_config"
            else:
                return self.async_create_entry(title=user_input["name"], data=user_input)

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(_room_schema({})),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: config_entries.ConfigEntry):
        return TrvSyncOptionsFlow()


class TrvSyncOptionsFlow(config_entries.OptionsFlow):
    """Edit entities and guard values of an existing room."""

    async def async_step_init(self, user_input=None):
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        merged = {**self.config_entry.data, **self.config_entry.options}
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(_room_schema(merged)),
        )