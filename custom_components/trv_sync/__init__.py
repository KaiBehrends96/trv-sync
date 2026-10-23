"""The trv_sync integration: virtual room thermostats over real TRVs.

Design summary (plan PLAN-2026-10-23-trv-sync.md):
- One config entry per room. A virtual climate entity exposes the room
  target; every real TRV gets its local calibration offset adjusted so its
  measured temperature matches the external room sensor.
- The control loop stays in the TRV firmware. This component only calibrates
  sensors and syncs setpoints; all durable state lives in the devices, so HA
  being down does not touch room control.
"""

from __future__ import annotations

import logging

from homeassistant.const import Platform  # homeassistant.components re-export is gone in HA 2026.x
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryError

from .config import RoomConfig
from .const import DOMAIN
from .coordinator import TrvSyncRoomCoordinator

PLATFORMS = [Platform.CLIMATE, Platform.NUMBER]

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up one room: validate config, build coordinator, forward platforms."""
    try:
        cfg = RoomConfig.build({**entry.data, **entry.options})
    except Exception as err:
        # Surface the fixable validation message in the UI instead of crashing.
        raise ConfigEntryError(str(err)) from err

    coordinator = TrvSyncRoomCoordinator(hass, entry, cfg)
    # First refresh runs the startup restore (median setpoint target) and the
    # first sync cycle; states are read-only, so this cannot fail hard.
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Guard values and entity lists are editable in the options flow; applying
    # them reloads the entry so the coordinator gets a fresh frozen config.
    entry.async_on_unload(entry.add_update_listener(async_options_updated))
    return True


async def async_options_updated(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload one room; nothing to persist, coordinator state is RAM only."""
    if not await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        return False
    hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    _LOGGER.info("trv_sync room %s unloaded", entry.title)
    return True