"""Per-room coordinator for trv_sync.

Polls the room's entities every update interval, feeds the pure logic layer,
and performs the heavily rate-limited writes back to the TRVs.

Everything here is intentionally RAM only: the durable state is the TRV
hardware itself (offset + setpoint registers). A restart re-derives the room
target from the TRV setpoints, which removes the Better Thermostat defect
class where every restart wiped the targets.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import timedelta

from homeassistant.core import HomeAssistant, State
from homeassistant.helpers import entity_registry as er_helper
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from . import logic
from .config import RoomConfig
from .const import (
    DEFAULT_OFFSET_LIMITS,
    DEFAULT_TRIM,
    DOMAIN,
    MUTE_RETRY_MULTIPLIER,
    PUSH_FAIL_MUTE_STREAK,
)

_LOGGER = logging.getLogger(__name__)

_TRV_OFFLINE = ("unavailable", "unknown")


@dataclass
class TrvReading:
    """One TRV's state snapshot as scraped out of HA (integration path)."""

    entity_id: str
    offset_entity_id: str | None
    available: bool
    displayed_temp: float | None
    displayed_fresh: bool
    setpoint: float | None
    offset_value: float | None
    offset_min: float
    offset_max: float
    hvac_mode: str | None
    hvac_action: str | None


@dataclass
class TrvRuntime:
    """Per-TRV write bookkeeping, RAM only (gone at the next restart)."""

    last_observed_setpoint: float | None = None
    offset_written_at: float = 0.0
    last_write_attempt: float = 0.0
    push_fail_streak: int = 0
    mute_warned: bool = False

    def is_muted(self) -> bool:
        return self.push_fail_streak >= PUSH_FAIL_MUTE_STREAK


@dataclass
class RoomSnapshot:
    """What the virtual climate entity renders."""

    room_temp: float | None
    room_target: float | None
    room_mode: str
    hvac_action: str
    fallback_active: bool
    trim: float


def _parse_float(raw: object) -> float | None:
    """Float from an entity state/attribute; anything non-numeric is None."""
    try:
        return float(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _state_updated_ts(state: State) -> float | None:
    """Unix timestamp of the state's last update; None when missing."""
    try:
        return float(dt_util.as_timestamp(state.last_updated))
    except (AttributeError, TypeError, ValueError):
        return None


def _is_offline(state: State | None) -> bool:
    return state is None or state.state in _TRV_OFFLINE


class TrvSyncRoomCoordinator(DataUpdateCoordinator[RoomSnapshot]):
    """One instance per room entry; owns all room decisions and writes."""

    def __init__(self, hass: HomeAssistant, entry, cfg: RoomConfig) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN} {cfg.name}",
            update_interval=timedelta(seconds=cfg.update_interval_seconds),
        )
        self._entry = entry
        self._cfg = cfg
        self._runtime = {entity_id: TrvRuntime() for entity_id in cfg.trvs}
        self._room_target: float | None = None
        self._room_mode: str = "heat"
        self._fallback_active = False
        self._seen_first_poll = False
        self._startup_done = False
        self._trim_entity_id: str | None = None

    @property
    def cfg(self) -> RoomConfig:
        """Public readonly config for the entity modules."""
        return self._cfg

    # ---------------------------------------------------------------- reads

    def _scrape_trvs(self, now: float) -> dict[str, TrvReading]:
        """Snapshot all TRV states; per-TRV problems degrade, never abort."""
        readings: dict[str, TrvReading] = {}
        for entity_id in self._cfg.trvs:
            try:
                readings[entity_id] = self._scrape_one_trv(entity_id, now)
            except Exception as err:  # noqa: BLE001 - one dead TRV must not kill the room
                _LOGGER.warning(
                    "trv_sync %s: reading %s failed: %s", self._cfg.name, entity_id, err
                )
                readings[entity_id] = TrvReading(
                    entity_id=entity_id,
                    offset_entity_id=None,
                    available=False,
                    displayed_temp=None,
                    displayed_fresh=False,
                    setpoint=None,
                    offset_value=None,
                    offset_min=DEFAULT_OFFSET_LIMITS[0],
                    offset_max=DEFAULT_OFFSET_LIMITS[1],
                    hvac_mode=None,
                    hvac_action=None,
                )
        return readings

    def _scrape_one_trv(self, entity_id: str, now: float) -> TrvReading:
        hass = self.hass
        state = hass.states.get(entity_id)
        available = not _is_offline(state)
        ts = _state_updated_ts(state) if state is not None else None

        displayed: float | None = None
        displayed_fresh = False
        if state is not None:
            displayed = _parse_float(state.attributes.get("current_temperature"))
            displayed_fresh = displayed is not None and logic.is_fresh(
                ts, now, self._cfg.staleness_minutes
            )

        setpoint = state.attributes.get("temperature") if state is not None else None
        offset_entity_id = logic.offset_number_entity_id(entity_id)
        offset_state = hass.states.get(offset_entity_id)
        offset_min, offset_max = DEFAULT_OFFSET_LIMITS
        offset_value: float | None = None
        if offset_state is not None and offset_state.state not in _TRV_OFFLINE:
            offset_value = _parse_float(offset_state.state)
            offset_min = _parse_float(offset_state.attributes.get("min_value")) or offset_min
            offset_max = _parse_float(offset_state.attributes.get("max_value")) or offset_max
        else:
            # Without a usable offset register the TRV runs setpoint-sync only;
            # its calibration stays whatever the device holds.
            offset_entity_id = None

        hvac_mode = state.state if available else None
        hvac_action = None
        if state is not None:
            hvac_action = state.attributes.get("hvac_action") or None

        return TrvReading(
            entity_id=entity_id,
            offset_entity_id=offset_entity_id,
            available=available,
            displayed_temp=displayed,
            displayed_fresh=displayed_fresh,
            setpoint=_parse_float(setpoint),
            offset_value=offset_value,
            offset_min=offset_min,
            offset_max=offset_max,
            hvac_mode=hvac_mode,
            hvac_action=hvac_action,
        )

    def _read_ext_sensor(self, now: float) -> tuple[float | None, bool]:
        """(value, fresh) for the optional external room sensor."""
        if not self._cfg.temp_sensor:
            return None, False
        state = self.hass.states.get(self._cfg.temp_sensor)
        if state is None:
            return None, False
        value = _parse_float(state.state)
        ts = _state_updated_ts(state)
        fresh = (
            value is not None
            and ts is not None
            and logic.is_fresh(ts, now, self._cfg.staleness_minutes)
        )
        return value, fresh

    def _read_trim(self) -> float:
        """Current manual trim; the trim number entity resolves lazily."""
        if self._trim_entity_id is None:
            registry = er_helper.async_get(self.hass)
            self._trim_entity_id = registry.async_get_entity_id(
                "number", DOMAIN, f"trim_{self._entry.entry_id}"
            )
            if self._trim_entity_id is not None:
                _LOGGER.debug(
                    "trv_sync %s: trim entity resolved to %s", self._cfg.name, self._trim_entity_id
                )
        state = self.hass.states.get(self._trim_entity_id) if self._trim_entity_id else None
        value = _parse_float(state.state) if state is not None else None
        if value is None:
            return DEFAULT_TRIM
        return value

    # --------------------------------------------------------------- writes

    async def _write_offset(
        self, reading: TrvReading, runtime: TrvRuntime, value: float, now: float
    ) -> None:
        """Write one TRV offset register, respecting the per-TRV rate limit."""
        if reading.offset_entity_id is None:
            return
        if now - runtime.offset_written_at < self._cfg.write_rate_limit_minutes * 60:
            _LOGGER.debug(
                "trv_sync %s: rate limit holds offset write for %s",
                self._cfg.name,
                reading.entity_id,
            )
            return
        runtime.offset_written_at = now
        _LOGGER.info(
            "trv_sync %s: offset %s -> %.1f", self._cfg.name, reading.entity_id, value
        )
        try:
            await self.hass.services.async_call(
                "number",
                "set_value",
                {"entity_id": reading.offset_entity_id, "value": value},
                blocking=True,
            )
        except Exception as err:  # noqa: BLE001 - logged, never blocks the room
            _LOGGER.warning(
                "trv_sync %s: offset write to %s failed: %s", self._cfg.name, reading.entity_id, err
            )

    async def _push_setpoint(self, reading: TrvReading, runtime: TrvRuntime) -> None:
        """Write the room target to one TRV setpoint; mute counter guards loops."""
        if self._room_target is None:
            return
        mute_retry = self._cfg.update_interval_seconds * MUTE_RETRY_MULTIPLIER
        if runtime.is_muted():
            if now_in_mute(runtime, mute_retry):
                if not runtime.mute_warned:
                    runtime.mute_warned = True
                    _LOGGER.warning(
                        "trv_sync %s: %s muted after %d failed pushes, retry in ~%ds",
                        self._cfg.name,
                        reading.entity_id,
                        runtime.push_fail_streak,
                        int(mute_retry - (time.time() - runtime.last_write_attempt)),
                    )
                return
            # Cool-off over: allow writes again; failures will re-mute cleanly.
            _LOGGER.info(
                "trv_sync %s: retrying muted TRV %s", self._cfg.name, reading.entity_id
            )
        runtime.last_write_attempt = time.time()
        try:
            await self.hass.services.async_call(
                "climate",
                "set_temperature",
                {"entity_id": reading.entity_id, "temperature": self._room_target},
                blocking=True,
            )
        except Exception as err:  # noqa: BLE001
            runtime.push_fail_streak += 1
            runtime.mute_warned = False
            _LOGGER.warning(
                "trv_sync %s: setpoint push to %s failed (%d): %s",
                self._cfg.name,
                reading.entity_id,
                runtime.push_fail_streak,
                err,
            )

    def _note_setpoint_success(self, entity_id: str, value: float | None) -> None:
        """Reset the mute counter once a TRV's setpoint matches the target."""
        if logic.same_target(value, self._room_target):
            self._runtime[entity_id].push_fail_streak = 0
            self._runtime[entity_id].mute_warned = False

    async def _push_mode(self, reading: TrvReading, runtime: TrvRuntime) -> None:
        """Force the TRV hvac mode into the room mode (heat/off)."""
        try:
            await self.hass.services.async_call(
                "climate",
                "set_hvac_mode",
                {"entity_id": reading.entity_id, "hvac_mode": self._room_mode},
                blocking=True,
            )
        except Exception as err:  # noqa: BLE001
            runtime.push_fail_streak += 1
            _LOGGER.warning(
                "trv_sync %s: hvac mode push to %s failed (%d): %s",
                self._cfg.name,
                reading.entity_id,
                runtime.push_fail_streak,
                err,
            )

    # ---------------------------------------------------------------- setup

    def _startup_restore(self, readings: dict[str, TrvReading]) -> None:
        """Polls after (re)start: derive target + mode from the devices.

        Retries every poll until it saw a setpoint: the first refresh runs
        while HA is still starting, and there the Zigbee stack is frequently
        not ready yet, so every device answers unavailable. A one-shot
        restore in that state leaves the room without a target forever
        (observed-change adoption never fires from a None base).
        """
        self._room_target = logic.startup_room_target(
            r.setpoint for r in readings.values() if r.available
        )
        modes = [r.hvac_mode for r in readings.values() if r.available and r.hvac_mode]
        self._room_mode = "heat" if (not modes or "heat" in modes) else "off"
        if self._room_target is None:
            _LOGGER.debug(
                "trv_sync %s: startup restore deferred, no setpoint seen yet", self._cfg.name
            )
            return
        self._startup_done = True
        _LOGGER.info(
            "trv_sync %s: startup restore, target=%s mode=%s from TRV setpoints",
            self._cfg.name,
            self._room_target,
            self._room_mode,
        )

    # ----------------------------------------------------------------- poll

    async def _async_update_data(self) -> RoomSnapshot:
        now = time.time()
        readings = self._scrape_trvs(now)
        trim = self._read_trim()
        ext_value, ext_fresh = self._read_ext_sensor(now)

        if not self._startup_done:
            self._startup_restore(readings)

        # Room temperature: external sensor when fresh, else TRV fallback mean.
        was_fallback = self._fallback_active
        if ext_fresh and ext_value is not None:
            room_temp = ext_value
            self._fallback_active = False
        else:
            # Fresh readings only: a stale/frozen internal temp must not become
            # the room reference. When nothing is fresh the snapshot stays None
            # and this poll skips all writes, which is exactly the desired
            # behaviour when the radio stack itself is down (TRVs self-control).
            room_temp = logic.fallback_room_temp(
                ((r.displayed_temp, r.offset_value)
                 for r in readings.values() if r.displayed_fresh),
                trim,
            )
            self._fallback_active = True
        if was_fallback != self._fallback_active:
            _LOGGER.info(
                "trv_sync %s: fallback mode %s",
                self._cfg.name,
                "entered (no fresh external sensor)" if self._fallback_active else "exited",
            )

        # Manual override / propagation, per the observed-change semantics.
        observed = {r.entity_id: r.setpoint for r in readings.values()}
        last_observed = {eid: rt.last_observed_setpoint for eid, rt in self._runtime.items()}
        candidates, conflicts = logic.changed_setpoints(observed, last_observed, self._room_target)
        if conflicts:
            _LOGGER.warning(
                "trv_sync %s: conflicting manual changes in one poll %s; first candidate wins",
                self._cfg.name,
                candidates,
            )
        if candidates:
            winner_id, winner_value = next(iter(candidates.items()))
            self._room_target = winner_value
            _LOGGER.info(
                "trv_sync %s: manual override adopted from %s, room target now %s",
                self._cfg.name,
                winner_id,
                winner_value,
            )
        for entity_id, value in observed.items():
            self._runtime[entity_id].last_observed_setpoint = value
            self._note_setpoint_success(entity_id, value)

        await self._apply_writes(now, readings, room_temp, trim)

        action = logic.room_hvac_action(
            (r.hvac_action for r in readings.values() if r.available and r.displayed_fresh),
            self._room_mode,
        )
        self._seen_first_poll = True
        return RoomSnapshot(
            room_temp=room_temp,
            room_target=self._room_target,
            room_mode=self._room_mode,
            hvac_action=action,
            fallback_active=self._fallback_active,
            trim=trim,
        )

    async def _apply_writes(
        self, now: float, readings: dict[str, TrvReading], room_temp: float | None, trim: float
    ) -> None:
        """Offset writes, setpoint pushes and mode pushes for one poll."""
        if room_temp is None:
            _LOGGER.debug(
                "trv_sync %s: no room temperature known yet, writes skipped", self._cfg.name
            )
            return

        for reading in readings.values():
            runtime = self._runtime[reading.entity_id]
            if not reading.available:
                continue

            if logic.setpoint_push_needed(reading.setpoint, self._room_target):
                await self._push_setpoint(reading, runtime)

            if logic.mode_push_needed(reading.hvac_mode, self._room_mode) and not runtime.is_muted():
                await self._push_mode(reading, runtime)

            offset_proposal = self._offset_for_poll(reading, room_temp, trim)
            if offset_proposal is not None:
                await self._write_offset(reading, runtime, offset_proposal, now)

    def _offset_for_poll(
        self, reading: TrvReading, room_temp: float, trim: float
    ) -> float | None:
        """Offset proposal for one TRV: calibration in ext mode, trim in fallback.

        Only fresh temperature readings feed the calibration: calibrating against
        a frozen internal value would pin a stale delta into a device register.
        """
        if reading.offset_entity_id is None:
            return None
        if self._fallback_active:
            return logic.fallback_offset_proposal(
                current_offset=reading.offset_value,
                trim=trim,
                deadband=self._cfg.deadband,
                offset_min=reading.offset_min,
                offset_max=reading.offset_max,
            )
        if reading.displayed_temp is None or not reading.displayed_fresh:
            return None
        return logic.next_offset(
            current_offset=reading.offset_value,
            room_temp=room_temp,
            trv_displayed=reading.displayed_temp,
            trim=trim,
            deadband=self._cfg.deadband,
            offset_min=reading.offset_min,
            offset_max=reading.offset_max,
        )

    # ------------------------------------------------------- virtual climate

    async def async_virtual_set_temperature(self, temperature: float) -> None:
        """Called by the virtual climate: user changed the room target."""
        value = float(temperature)
        self._room_target = value
        _LOGGER.info(
            "trv_sync %s: room target set to %.1f via virtual", self._cfg.name, value
        )
        self.async_update_listeners()
        await self.async_request_refresh()

    async def async_virtual_set_hvac_mode(self, mode: str) -> None:
        """Called by the virtual climate: user changed the room mode."""
        if mode not in ("heat", "off"):
            _LOGGER.warning("trv_sync %s: ignoring unsupported room mode %r", self._cfg.name, mode)
            return
        self._room_mode = mode
        _LOGGER.info("trv_sync %s: room mode set to %s via virtual", self._cfg.name, mode)
        self.async_update_listeners()
        await self.async_request_refresh()


def now_in_mute(runtime: TrvRuntime, mute_retry: float) -> bool:
    """True while the mute cool-off window is still running."""
    return time.time() - runtime.last_write_attempt < mute_retry