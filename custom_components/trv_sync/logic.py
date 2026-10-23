"""Pure decision logic for trv_sync.

This module is deliberately free of Home Assistant imports so the whole
decision layer can be unit-tested offline with stdlib only. The coordinator
feeds plain floats and unix timestamps in and applies the decisions it gets
back.

Core contract: a TRV's *displayed* current_temperature already includes its
local calibration offset (displayed = raw sensor + offset). Correcting the
offset therefore works as a delta against the displayed value:

    new_offset = current_offset + (room_temp + trim - displayed)

Working against the displayed value (never against raw) is what makes the
loop convergent; recomputing the needed absolute offset from raw would apply
the same correction twice, which is the mechanism behind the ±12 °C whipsaw
that Better Thermostat 1.9.3 produced in this house on 22.10.2026.
"""

from __future__ import annotations

from statistics import fmean, median
from typing import Iterable, Mapping

from .const import (
    OFFSET_NUMBER_SUFFIX,
    TARGET_TOLERANCE,
    TRV_ENTITY_SUFFIX,
)


class TrvSyncLogicError(ValueError):
    """Malformed pure-logic input (bad ranges, invalid options)."""


def offset_number_entity_id(trv_entity_id: str) -> str:
    """Entity id of the ZHA local-temperature-offset number for a TRV climate.

    ZHA derives its number entity ids from the climate entity, stripping the
    trailing '_thermostat'. climate.heizzimmer1wand_thermostat maps to
    number.heizzimmer1wand_lokaler_temperatur_offset. Ids without the suffix
    (foreign integration) map to the same rule minus the strip.
    """
    base = trv_entity_id.removeprefix("climate.")
    if base.endswith(TRV_ENTITY_SUFFIX):
        base = base[: -len(TRV_ENTITY_SUFFIX)]
    return f"number.{base}{OFFSET_NUMBER_SUFFIX}"


def quantize(value: float, step: float = 0.1) -> float:
    """Round `value` to the nearest multiple of `step` on the given grid."""
    if step <= 0:
        raise TrvSyncLogicError(f"quantize step must be positive, got {step}")
    if value != value:  # NaN check; a NaN here would poison every later write
        raise TrvSyncLogicError("quantize received a NaN temperature")
    return round(round(value / step) * step, 10)


def clamp(value: float, lowest: float, highest: float) -> float:
    """Clamp `value` into [lowest, highest]; reversed bounds are an error."""
    if lowest > highest:
        raise TrvSyncLogicError(
            f"reversed bounds lowest={lowest} highest={highest}; check the offset limits"
        )
    return max(lowest, min(highest, value))


def is_fresh(updated: float | None, now: float, staleness_minutes: float) -> bool:
    """True when the reading exists and is not older than the staleness window.

    None (no reading at all) is never fresh. The tolerance for a slightly
    negative age absorbs ordering jitter; anything clearly in the future or
    too old counts as stale. Note staleness applies to *measurement* values
    only: offset registers and setpoints can legitimately sit unchanged in an
    entity state for hours, an old timestamp there is not a fault.
    """
    if updated is None:
        return False
    age = now - float(updated)
    return -120.0 <= age <= staleness_minutes * 60


def same_target(a: float | None, b: float | None) -> bool:
    """Comparison with the small tolerance that keeps 0.5-step roundings calm."""
    if a is None or b is None:
        return False
    return abs(a - b) < TARGET_TOLERANCE


def next_offset(
    *,
    current_offset: float | None,
    room_temp: float,
    trv_displayed: float,
    trim: float,
    deadband: float,
    offset_min: float,
    offset_max: float,
) -> float | None:
    """Delta-correct one TRV's offset. Returns a new register value or None.

    `trv_displayed` must already include `current_offset` (ZHA reports it that
    way), so the correction is delta based and cannot double-count. The
    result is quantized to the 0.1 grid and clamped to the register range
    before the deadband check; the deadband is what stops the loop from
    hammering the register with a 0.0x correction every cycle (BT 1.9.3 wrote
    dozens of times per minute).
    """
    if current_offset is None:
        return None
    proposed = quantize(current_offset + (room_temp + trim - trv_displayed))
    proposed = clamp(proposed, offset_min, offset_max)
    if abs(proposed - current_offset) < deadband:
        return None
    return proposed


def fallback_offset_proposal(
    *,
    current_offset: float | None,
    trim: float,
    deadband: float,
    offset_min: float,
    offset_max: float,
) -> float | None:
    """Offset target while the external sensor is down: the trim alone.

    With no trustworthy external reference the register drifts back to a
    neutral state: exactly the trim (0 when the trim is untouched). Going the
    long way through next_offset with room_temp == displayed would land on
    the same value via delta = trim; this explicit form is easier to test.
    """
    if current_offset is None:
        return None
    proposed = quantize(clamp(trim, offset_min, offset_max))
    if abs(proposed - current_offset) < deadband:
        return None
    return proposed


def fallback_room_temp(
    readings: Iterable[tuple[float | None, float | None]], trim: float
) -> float | None:
    """Room temperature while the external sensor is down: mean of TRV raws + trim.

    Each pair is (displayed_temp, current_offset); raw = displayed - offset
    when the offset is known, otherwise the displayed value stands in as raw
    (a TRV without a usable offset entity contributes its uncorrected reading
    instead of vanishing from the mean). Returns None when nothing is usable.
    """
    raws: list[float] = []
    for displayed, offset in readings:
        if displayed is None:
            continue
        raws.append(displayed - offset if offset is not None else displayed)
    if not raws:
        return None
    return quantize(fmean(raws) + trim)


def startup_room_target(setpoints: Iterable[float | None]) -> float | None:
    """Median of the TRV setpoints for the first cycle after a restart.

    The median tolerates one TRV carrying a stray setpoint; None inputs are
    ignored. Returns None when nothing is known, so the room target stays
    unset rather than inventing a temperature. Documented limitation: a TRV
    intentionally set against the room target loses its value across restarts
    (accepted in the plan; fixing it would need persisted state).
    """
    known = [float(s) for s in setpoints if s is not None]
    if not known:
        return None
    return float(median(known))


def changed_setpoints(
    observed: Mapping[str, float | None],
    last_observed: Mapping[str, float | None],
    room_target: float | None,
) -> tuple[dict[str, float], bool]:
    """Override detection over this poll's observed TRV setpoints.

    A setpoint counts as a manual override candidate when it differs both
    from the previous poll and (with tolerance) from the current room target.
    A change towards the current target is our own push landing on the device:
    silent no-op. The first poll (empty last_observed) never adopts, or every
    TRV would look like an override right after a restart.

    If several TRVs changed to differing values in the same poll,
    conflicts=True and the first reported candidate wins (deterministic;
    the set of candidates is built in the configured entity order).
    """
    candidates: dict[str, float] = {}
    for entity_id, value in observed.items():
        previous = last_observed.get(entity_id)
        if value is None or previous is None or value == previous:
            continue
        if same_target(value, room_target):
            continue
        candidates[entity_id] = float(value)
    conflict = len(set(candidates.values())) > 1
    return candidates, conflict


def setpoint_push_needed(observed_setpoint: float | None, room_target: float | None) -> bool:
    """True when this TRV's setpoint still differs from the room target."""
    return not same_target(observed_setpoint, room_target) and all(
        value is not None for value in (observed_setpoint, room_target)
    )


def mode_push_needed(trv_mode: str | None, room_mode: str) -> bool:
    """True when the TRV's hvac mode must be forced to match the room mode.

    Anything other than 'heat' (auto, cool, ...) counts as 'not our mode':
    the TRVZB 'auto' mode runs the device-side weekly schedule, which the
    setpoint sync must not fight (plan: devices run manual mode).
    """
    if trv_mode is None:
        return False
    if room_mode == "off":
        return trv_mode != "off"
    return trv_mode != "heat"


def room_hvac_action(actions: Iterable[str | None], room_mode: str) -> str:
    """The virtual climate's hvac_action: heating while any fresh TRV heats."""
    if room_mode == "off":
        return "off"
    return "heating" if any(action == "heating" for action in actions) else "idle"


def validate_options(
    deadband: float,
    write_rate_limit_minutes: float,
    staleness_minutes: float,
    update_interval_seconds: float,
) -> None:
    """Guard config values; raises a typed error naming the offending field."""
    for value, minimum, name in (
        (deadband, 0.0, "deadband"),
        (write_rate_limit_minutes, 0.0, "write_rate_limit_minutes"),
        (staleness_minutes, 0.0, "staleness_minutes"),
        (update_interval_seconds, 9.0, "update_interval_seconds"),
    ):
        if not isinstance(value, (int, float)) or isinstance(value, bool) or value <= minimum:
            raise TrvSyncLogicError(
                f"{name} must be greater than {minimum}, got {value!r}"
            )