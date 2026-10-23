# Offline unit tests for the pure decision logic (stdlib only).
# The integration package normally imports Home Assistant in __init__.py;
# the tests register a path-only stub for the package so only const.py,
# logic.py and config.py (the HA-free modules) get loaded.
# Run: python3 tests/test_logic.py
import sys
import types
import unittest
from pathlib import Path

_PKG_DIR = Path(__file__).resolve().parents[1] / "custom_components" / "trv_sync"
_stub = types.ModuleType("trv_sync")
_stub.__path__ = [str(_PKG_DIR)]
sys.modules["trv_sync"] = _stub

from trv_sync import logic  # noqa: E402
from trv_sync.config import RoomConfig  # noqa: E402
from trv_sync.logic import TrvSyncLogicError  # noqa: E402


class QuantizeTest(unittest.TestCase):
    def test_rounds_to_grid(self):
        self.assertEqual(logic.quantize(12.66), 12.7)
        self.assertEqual(logic.quantize(12.6499), 12.6)
        self.assertEqual(logic.quantize(-0.05), -0.0)
        self.assertEqual(logic.quantize(0.3), 0.3)

    def test_rejects_bad_step_and_nan(self):
        with self.assertRaises(TrvSyncLogicError):
            logic.quantize(1.0, step=0)
        with self.assertRaises(TrvSyncLogicError):
            logic.quantize(float("nan"))


class ClampTest(unittest.TestCase):
    def test_clamps_inside_bounds(self):
        self.assertEqual(logic.clamp(12.7, -6.0, 6.0), 6.0)
        self.assertEqual(logic.clamp(-9.0, -6.0, 6.0), -6.0)
        self.assertEqual(logic.clamp(1.2, -6.0, 6.0), 1.2)

    def test_rejects_reversed_bounds(self):
        with self.assertRaises(TrvSyncLogicError):
            logic.clamp(1.0, 6.0, -6.0)


class FreshnessTest(unittest.TestCase):
    def test_none_is_never_fresh(self):
        self.assertFalse(logic.is_fresh(None, 1000.0, 30))

    def test_window(self):
        # staleness is in MINUTES: 30 -> 1800s window
        self.assertTrue(logic.is_fresh(600.0, 660.0, 30))  # 60s old
        self.assertFalse(logic.is_fresh(100.0, 2000.0, 30))  # 1900s old

    def test_jitter_tolerance_without_future_brokenness(self):
        self.assertTrue(logic.is_fresh(600.5, 600.0, 30))  # 0.5s in future: ok
        self.assertFalse(logic.is_fresh(900.0, 600.0, 30))  # 5 min in future: stale


class NextOffsetTest(unittest.TestCase):
    # reference case from the pilot room: ext sensor says 20.6, the wall TRV
    # displays raw-ish 18.9 with current offset 0 -> correction to +1.7
    def test_calibration_writes_the_delta(self):
        self.assertEqual(
            logic.next_offset(
                current_offset=0.0, room_temp=20.6, trv_displayed=18.9,
                trim=0.0, deadband=0.3, offset_min=-6.0, offset_max=6.0,
            ),
            1.7,
        )

    def test_convergent_against_double_counting(self):
        # the same room one cycle later: displayed already includes +1.3
        # (raw 18.9 + 1.3 = 20.2); new target must still be 1.7, not 3.0
        self.assertEqual(
            logic.next_offset(
                current_offset=1.3, room_temp=20.6, trv_displayed=20.2,
                trim=0.0, deadband=0.3, offset_min=-6.0, offset_max=6.0,
            ),
            1.7,
        )

    def test_deadband_holds_sub_threshold(self):
        # delta 0.2 < deadband 0.3 -> no write
        self.assertIsNone(
            logic.next_offset(
                current_offset=1.3, room_temp=20.4, trv_displayed=20.2,
                trim=0.0, deadband=0.3, offset_min=-6.0, offset_max=6.0,
            ),
        )

    def test_clamped_register_range(self):
        # 12 K correction clamps back to the device's +6 limit
        self.assertEqual(
            logic.next_offset(
                current_offset=0.0, room_temp=30.0, trv_displayed=18.0,
                trim=0.0, deadband=0.3, offset_min=-6.0, offset_max=6.0,
            ),
            6.0,
        )

    def test_unknown_offset_is_not_touched(self):
        self.assertIsNone(
            logic.next_offset(
                current_offset=None, room_temp=20.6, trv_displayed=18.9,
                trim=0.0, deadband=0.3, offset_min=-6.0, offset_max=6.0,
            ),
        )

    def test_trim_included(self):
        self.assertEqual(
            logic.next_offset(
                current_offset=0.0, room_temp=20.3, trv_displayed=20.3,
                trim=0.75, deadband=0.3, offset_min=-6.0, offset_max=6.0,
            ),
            0.8,
        )


class FallbackOffsetTest(unittest.TestCase):
    """While ext is down, offsets must return to the neutral trim value."""

    def test_garbage_register_returns_to_zero(self):
        self.assertEqual(
            logic.fallback_offset_proposal(
                current_offset=12.6, trim=0.0, deadband=0.3,
                offset_min=-6.0, offset_max=6.0,
            ),
            0.0,
        )

    def test_small_delta_blocked_by_deadband(self):
        self.assertIsNone(
            logic.fallback_offset_proposal(
                current_offset=0.2, trim=0.0, deadband=0.3,
                offset_min=-6.0, offset_max=6.0,
            ),
        )

    def test_trim_survives_fallback(self):
        self.assertEqual(
            logic.fallback_offset_proposal(
                current_offset=0.0, trim=1.3, deadband=0.3,
                offset_min=-6.0, offset_max=6.0,
            ),
            1.3,
        )


class FallbackRoomTempTest(unittest.TestCase):
    def test_mean_of_raws_plus_trim(self):
        # one TRV displays 20.2 with offset +1.3 -> raw 18.9; one has no
        # usable offset entity and contributes its displayed value as is
        self.assertEqual(
            logic.fallback_room_temp([(20.2, 1.3), (18.9, None)], trim=0.0),
            18.9,
        )
        self.assertEqual(
            logic.fallback_room_temp([(20.2, 1.3)], trim=0.5),
            19.4,
        )

    def test_none_and_empty(self):
        self.assertIsNone(logic.fallback_room_temp([], trim=0.0))
        self.assertIsNone(logic.fallback_room_temp([(None, 1.0)], trim=0.0))


class StartupTargetTest(unittest.TestCase):
    def test_median_ignores_one_stray_trv(self):
        self.assertEqual(logic.startup_room_target([21.0, 18.0, 21.0]), 21.0)
        self.assertEqual(logic.startup_room_target([21.0, 18.0]), 19.5)

    def test_none_when_nothing_known(self):
        self.assertIsNone(logic.startup_room_target([None, None]))
        self.assertIsNone(logic.startup_room_target([]))


class ChangedSetpointsTest(unittest.TestCase):
    def test_first_poll_never_adopts(self):
        candidates, conflict = logic.changed_setpoints(
            {"a": 21.0, "b": 18.0}, {}, room_target=None
        )
        self.assertEqual(candidates, {})
        self.assertFalse(conflict)

    def test_manual_change_adopts(self):
        candidates, conflict = logic.changed_setpoints(
            {"a": 18.0, "b": 21.0}, {"a": 21.0, "b": 21.0}, room_target=21.0
        )
        self.assertEqual(candidates, {"a": 18.0})
        self.assertFalse(conflict)

    def test_our_own_push_landing_is_silent(self):
        candidates, conflict = logic.changed_setpoints(
            {"a": 23.0, "b": 23.0}, {"a": 21.0, "b": 23.0}, room_target=23.0
        )
        # a moved towards our target (push landing), b unchanged: no override
        self.assertEqual(candidates, {})
        self.assertFalse(conflict)

    def test_conflicting_changes_flag_and_first_wins(self):
        candidates, conflict = logic.changed_setpoints(
            {"a": 18.0, "b": 23.0}, {"a": 21.0, "b": 21.0}, room_target=21.0
        )
        self.assertEqual(set(candidates), {"a", "b"})
        self.assertTrue(conflict)
        self.assertEqual(candidates["a"], 18.0)


class PushHelpersTest(unittest.TestCase):
    def test_setpoint_push(self):
        self.assertFalse(logic.setpoint_push_needed(None, 21.0))
        self.assertFalse(logic.setpoint_push_needed(21.0, None))
        self.assertFalse(logic.setpoint_push_needed(21.0, 21.0))
        self.assertFalse(logic.setpoint_push_needed(21.04, 21.0))
        self.assertTrue(logic.setpoint_push_needed(18.0, 21.0))

    def test_mode_push(self):
        self.assertFalse(logic.mode_push_needed(None, "heat"))
        self.assertFalse(logic.mode_push_needed("heat", "heat"))
        self.assertTrue(logic.mode_push_needed("auto", "heat"))
        self.assertTrue(logic.mode_push_needed("off", "heat"))
        self.assertTrue(logic.mode_push_needed("heat", "off"))
        self.assertFalse(logic.mode_push_needed("off", "off"))

    def test_hvac_action(self):
        self.assertEqual(logic.room_hvac_action([None, "heating"], "heat"), "heating")
        self.assertEqual(logic.room_hvac_action(["idle"], "heat"), "idle")
        self.assertEqual(logic.room_hvac_action(["heating"], "off"), "off")


class OptionsTest(unittest.TestCase):
    def test_validation_rejects_zeroes(self):
        for bad in (
            {"deadband": 0.0},
            {"write_rate_limit_minutes": 0},
            {"staleness_minutes": 0},
            {"update_interval_seconds": 5},
        ):
            with self.assertRaises(TrvSyncLogicError):
                logic.validate_options(
                    bad.get("deadband", 0.3),
                    bad.get("write_rate_limit_minutes", 10.0),
                    bad.get("staleness_minutes", 30.0),
                    bad.get("update_interval_seconds", 60),
                )

    def test_room_config_build_defaults(self):
        cfg = RoomConfig.build(
            {"name": "HeizZimmer1", "trvs": ["climate.heizzimmer1_fenster_thermostat"]}
        )
        self.assertEqual(cfg.deadband, 0.3)
        self.assertEqual(cfg.write_rate_limit_minutes, 10.0)
        self.assertEqual(cfg.update_interval_seconds, 60)
        self.assertIsNone(cfg.temp_sensor)

    def test_room_config_build_errors(self):
        with self.assertRaises(TrvSyncLogicError):
            RoomConfig.build({"name": " ", "trvs": ["climate.x"]})
        with self.assertRaises(TrvSyncLogicError):
            RoomConfig.build({"name": "x", "trvs": []})
        with self.assertRaises(TrvSyncLogicError):
            RoomConfig.build({"name": "x", "trvs": ["climate.a", "climate.a"]})


class EntityMappingTest(unittest.TestCase):
    def test_zha_offset_entity_rule(self):
        self.assertEqual(
            logic.offset_number_entity_id("climate.heizzimmer1_fenster_thermostat"),
            "number.heizzimmer1_fenster_lokaler_temperatur_offset",
        )
        self.assertEqual(
            logic.offset_number_entity_id("climate.heizwohnzimmerwand_thermostat"),
            "number.heizwohnzimmerwand_lokaler_temperatur_offset",
        )
        self.assertEqual(
            logic.offset_number_entity_id("climate.custom_name"),
            "number.custom_name_lokaler_temperatur_offset",
        )


if __name__ == "__main__":
    unittest.main()