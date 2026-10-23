"""Manual trim number entity, one per room (user steering for calibration).

The trim shifts what every TRV treats as "sensors agree" (room_temp + trim
vs displayed). It is an integration-owned entity so the plan avoids a second
helper class; restoring it after a restart goes through RestoreEntity.
"""

from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.restore_state import RestoreEntity

from .const import DEFAULT_TRIM, DOMAIN

_TRIM_RANGE = (-5.0, 5.0)


class TrvSyncTrimNumber(RestoreEntity, NumberEntity):
    """Room-wide calibration trim, default 0, range +-5 K."""

    _attr_native_min_value = _TRIM_RANGE[0]
    _attr_native_max_value = _TRIM_RANGE[1]
    _attr_native_step = 0.1
    _attr_mode = NumberMode.BOX
    _attr_has_entity_name = False
    _attr_name = "trv_sync trim"

    def __init__(self, entry: ConfigEntry, cfg) -> None:
        self._attr_unique_id = f"trim_{entry.entry_id}"
        self._attr_native_value = DEFAULT_TRIM
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=f"TRV Sync {cfg.name}",
            manufacturer="HomeLab",
            model="trv-sync room",
        )

    @property
    def native_value(self) -> float | None:
        return self._attr_native_value

    async def async_set_native_value(self, value: float) -> None:
        value = float(value)
        if not _TRIM_RANGE[0] <= value <= _TRIM_RANGE[1]:
            # Out-of-range values are clamped in the device UI anyway; double
            # check here so a rogue service call cannot corrupt calibration.
            value = max(_TRIM_RANGE[0], min(_TRIM_RANGE[1], value))
        self._attr_native_value = value
        self.async_write_ha_state()

    async def async_added_to_hass(self) -> None:
        """Restore the stored trim; unknown states keep the 0.0 default."""
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last is None:
            return
        try:
            value = float(last.state)
        except (TypeError, ValueError):
            return
        self._attr_native_value = max(_TRIM_RANGE[0], min(_TRIM_RANGE[1], value))