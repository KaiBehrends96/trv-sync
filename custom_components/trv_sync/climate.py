"""Virtual climate entity: one per room, the facade the user controls."""

from __future__ import annotations

from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.const import UnitOfTemperature
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import TrvSyncRoomCoordinator

_ROOM_MODE_TO_HVAC = {"heat": HVACMode.HEAT, "off": HVACMode.OFF}
_ACTION_TO_HVAC = {
    "off": HVACAction.OFF,
    "idle": HVACAction.IDLE,
    "heating": HVACAction.HEATING,
}


class TrvSyncClimate(CoordinatorEntity, ClimateEntity):
    """Room thermostat facade: target + mode come from here.

    The virtual entity is deliberately thin. It renders the coordinator
    snapshot and routes user actions back into the coordinator; there is no
    temperature logic in the entity itself.
    """

    _attr_hvac_modes = [HVACMode.HEAT, HVACMode.OFF]
    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE
        | ClimateEntityFeature.TURN_ON
        | ClimateEntityFeature.TURN_OFF
    )
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_target_temperature_step = 0.5
    # Keep the entity id predictable for the rename-at-cutover strategy:
    # name "trv_sync HeizZimmer1" leads to climate.trv_sync_heizzimmer1.
    _attr_has_entity_name = False

    def __init__(self, coordinator: TrvSyncRoomCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._entry = entry
        cfg = coordinator.cfg
        self._attr_unique_id = f"room_{entry.entry_id}"
        self._attr_name = f"trv_sync {cfg.name}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=f"TRV Sync {cfg.name}",
            manufacturer="HomeLab",
            model="trv-sync room",
        )

    @property
    def current_temperature(self) -> float | None:
        return self.coordinator.data.room_temp

    @property
    def target_temperature(self) -> float | None:
        return self.coordinator.data.room_target

    @property
    def hvac_mode(self) -> HVACMode | str | None:
        return _ROOM_MODE_TO_HVAC.get(self.coordinator.data.room_mode)

    @property
    def hvac_action(self) -> HVACAction | None:
        return _ACTION_TO_HVAC.get(self.coordinator.data.hvac_action)

    @property
    def extra_state_attributes(self) -> dict:
        snapshot = self.coordinator.data
        return {
            "trv_entities": list(self.coordinator.cfg.trvs),
            "fallback_active": snapshot.fallback_active,
            "trim": snapshot.trim,
        }

    async def async_set_temperature(self, **kwargs) -> None:
        temperature = kwargs.get("temperature")
        if temperature is not None:
            await self.coordinator.async_virtual_set_temperature(float(temperature))

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        await self.coordinator.async_virtual_set_hvac_mode(str(hvac_mode))


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Add the room's single virtual climate entity."""
    coordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([TrvSyncClimate(coordinator, entry)])