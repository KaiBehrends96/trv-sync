"""Room configuration: the merged entry dict turned into a validated object."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from .const import (
    DEFAULT_DEADBAND,
    DEFAULT_STALENESS_MINUTES,
    DEFAULT_UPDATE_INTERVAL_SECONDS,
    DEFAULT_WRITE_RATE_LIMIT_MINUTES,
)
from .logic import TrvSyncLogicError, validate_options


@dataclass(frozen=True)
class RoomConfig:
    """Validated per-room configuration.

    Built from the merged entry data + options once at setup; the coordinator
    keeps the frozen value for its whole lifetime. Changing anything goes
    through the options flow, which reloads the entry (new RoomConfig).
    """

    name: str
    trvs: tuple[str, ...]
    temp_sensor: str | None
    deadband: float
    write_rate_limit_minutes: float
    staleness_minutes: float
    update_interval_seconds: int

    @classmethod
    def build(cls, merged: Mapping[str, Any]) -> "RoomConfig":
        """Validate and build; raises TrvSyncLogicError with a fixable message."""
        name = str(merged.get("name", "")).strip()
        if not name:
            raise TrvSyncLogicError("name is empty; the room needs a display name")

        trvs_raw = merged.get("trvs") or ()
        if isinstance(trvs_raw, str):
            trvs_raw = (trvs_raw,)
        trvs = tuple(str(t).strip() for t in trvs_raw if str(t).strip())
        if not trvs:
            raise TrvSyncLogicError("trvs is empty; at least one TRV climate entity is required")
        if len(set(trvs)) != len(trvs):
            raise TrvSyncLogicError(f"trvs contains duplicates: {trvs!r}")

        temp_sensor = str(merged.get("temp_sensor") or "").strip() or None

        deadband = float(merged.get("deadband", DEFAULT_DEADBAND))
        rate = float(merged.get("write_rate_limit_minutes", DEFAULT_WRITE_RATE_LIMIT_MINUTES))
        staleness = float(merged.get("staleness_minutes", DEFAULT_STALENESS_MINUTES))
        interval = int(merged.get("update_interval_seconds", DEFAULT_UPDATE_INTERVAL_SECONDS))
        validate_options(deadband, rate, staleness, interval)

        return cls(
            name=name,
            trvs=trvs,
            temp_sensor=temp_sensor,
            deadband=deadband,
            write_rate_limit_minutes=rate,
            staleness_minutes=staleness,
            update_interval_seconds=interval,
        )