import json

import pytest

from custom_components.orion_local.protocol import command_confirmed, parse_status, validate_command


def test_real_shape(status):
    serial, data = parse_status("orion/TESTBED/out", json.dumps(status).encode())
    assert serial == "TESTBED"
    assert data["rgb"] == (32, 64, 96)  # Not the brightness-scaled actual RGB.
    assert data["left_target"] == 19
    assert data["safety_ok"] is True
    assert data["fault"] is False
    assert data["left_occupancy"] is False
    assert "secret" not in json.dumps(data)
    assert "sensors" not in data


@pytest.mark.parametrize(
    "topic,payload",
    [
        ("orion/OTHER/out", b'{"type":"status","serial":"TESTBED"}'),
        ("orion/TESTBED/in", b"{}"),
        ("orion/+/out", b"{}"),
        ("orion/TESTBED/out", b"[]"),
        ("orion/TESTBED/out", b"\xff"),
        ("orion/TESTBED/out", b"{"),
        ("orion/TESTBED/out", b" " * 65537),
    ],
)
def test_invalid_payload(topic, payload):
    assert parse_status(topic, payload) is None


def test_missing_and_malformed_optional_fields(status):
    status.update(
        tec=[],
        sensors={"sensor1": [0], "sensor2": [None] * 24},
        led=None,
        safetyOk="invalid",
        errors={"flag": "0"},
        temps={"ib_left": float("nan")},
    )
    _, data = parse_status("orion/TESTBED/out", json.dumps(status).encode())
    for key in (
        "left_target",
        "left_temperature",
        "left_occupancy",
        "right_occupancy",
        "rgb",
        "brightness",
        "safety_ok",
        "fault",
    ):
        assert data[key] is None


@pytest.mark.parametrize(
    "command",
    [
        "SET LEFT 10",
        "SET RIGHT 45",
        "SET BOTH 10 45",
        "SET BOTH 27.5",
        "SET BOTH 19",
        "SET BOTH 19.5 20",
        "CLEAR LEFT",
        "CLEAR RIGHT",
        "CLEAR BOTH",
        "SYSTEM ON",
        "FAN QUIET ON",
        "FAN QUIET OFF",
        "IB RGB 0 128 255",
        "IB DIMMER 0",
        "IB DIMMER 100",
        "REBOOT",
    ],
)
def test_allowed_commands(command):
    assert validate_command(command) == command


@pytest.mark.parametrize(
    "command",
    [
        "FILL START",
        "ERASE WIFI",
        "SYSTEM OFF",
        "SET LEFT 9.9",
        "SET BOTH 19 45.1",
        "SET LEFT 19 20",
        "SET BOTH nan",
        "SET RIGHT inf",
        "SET BOTH -100",
        "IB RGB 256 0 0",
        "IB DIMMER 101",
        "IB DIMMER -1",
        "SET BOTH 19\nREBOOT",
    ],
)
def test_reject_commands(command):
    with pytest.raises(ValueError):
        validate_command(command)


@pytest.mark.parametrize(
    "value,expected",
    [
        (5, False),
        (0, True),
        (1, None),
        (4, True),
        (6, True),
        (7, True),
        (8, None),
        (255, None),
        (None, None),
        (True, None),
        ("5", None),
        (-1, None),
        (256, None),
        (4.5, None),
        (float("nan"), None),
    ],
)
def test_presence_mapping_and_invalid_status(status, value, expected):
    status["sensors"]["sensor1"][17] = value
    _, data = parse_status("orion/TESTBED/out", json.dumps(status).encode())
    assert data["left_occupancy"] is expected
    assert data["right_occupancy"] is False


@pytest.mark.parametrize(
    "sensors", [None, {}, {"sensor1": []}, {"sensor1": [5] * 17}, {"sensor1": "not an array"}]
)
def test_presence_missing_data_is_unknown(status, sensors):
    status["sensors"] = sensors
    _, data = parse_status("orion/TESTBED/out", json.dumps(status).encode())
    assert data["left_occupancy"] is None
    assert data["right_occupancy"] is None


@pytest.mark.parametrize(
    "command,matching,not_matching",
    [
        (
            "SET BOTH 20",
            {"left_target": 20, "right_target": 20, "left_enabled": True, "right_enabled": True},
            {"right_enabled": False},
        ),
        ("CLEAR LEFT", {"left_enabled": False}, {"left_enabled": True}),
        ("CLEAR RIGHT", {"right_enabled": False}, {"right_enabled": True}),
        (
            "CLEAR BOTH",
            {"left_enabled": False, "right_enabled": False, "system_state": "SYSTEM_OFF"},
            {"system_state": "SYSTEM_ON"},
        ),
        ("SYSTEM ON", {"system_state": "SYSTEM_ON"}, {"system_state": "PENDING_ON"}),
        ("FAN QUIET ON", {"quiet_mode": True}, {"quiet_mode": False}),
        ("FAN QUIET OFF", {"quiet_mode": False}, {"quiet_mode": True}),
        ("IB RGB 32 64 96", {"rgb": (32, 64, 96)}, {"rgb": (3, 6, 9)}),
        ("IB DIMMER 10", {"brightness": 10}, {"brightness": 0}),
        ("REBOOT", {"uptime": 100, "safety_ok": True, "fault": False}, {"uptime": 1100}),
        ("REBOOT", {"uptime": 100, "safety_ok": True, "fault": False}, {"fault": True}),
    ],
)
def test_command_confirmation(command, matching, not_matching):
    before = {"uptime": 1000}
    assert command_confirmed(command, matching, before)
    assert not command_confirmed(command, matching | not_matching, before)
    assert not command_confirmed(command, {}, before)


@pytest.mark.parametrize(
    "reported,enabled,expected",
    [
        (21.39, True, True),
        (21.38, True, False),
        (21.40, True, False),
        (21.39, False, False),
        (None, True, False),
        (True, True, False),
    ],
)
def test_fahrenheit_conversion_confirmation(status, reported, enabled, expected):
    # Actual live observation: SET RIGHT 21.3889 -> setpoint 21.39.
    status["tec"]["right"].update(setpoint=reported, control_enabled=enabled)
    _, data = parse_status("orion/TESTBED/out", json.dumps(status).encode())
    assert command_confirmed("SET RIGHT 21.3889", data, {}) is expected


@pytest.mark.parametrize("reported,expected", [(21.38, True), (21.39, True), (21.391, False)])
def test_confirmation_rounding_halfway_boundary(reported, expected):
    data = {"left_target": reported, "left_enabled": True}
    assert command_confirmed("SET LEFT 21.385", data, {}) is expected


def test_both_converted_setpoints_confirm_independently():
    data = {
        "left_target": 21.39,
        "right_target": 21.11,
        "left_enabled": True,
        "right_enabled": True,
    }
    assert command_confirmed("SET BOTH 21.3889 21.1111", data, {})
    data["right_target"] = 21.12
    assert not command_confirmed("SET BOTH 21.3889 21.1111", data, {})


def test_vital_signs_use_observed_indexes_and_keep_sides_separate(status):
    status["sensors"]["sensor1"][15:18] = [60, 14, 0]
    status["sensors"]["sensor2"][15:18] = [66, 16, 4]
    _, data = parse_status("orion/TESTBED/out", json.dumps(status).encode())
    assert data["left_heart_rate"] == 60
    assert data["left_breathing_rate"] == 14
    assert data["right_heart_rate"] == 66
    assert data["right_breathing_rate"] == 16


@pytest.mark.parametrize("value", [0, 255, -1, 256, None, True, "60", 60.5])
def test_invalid_vital_readings_are_unknown(status, value):
    status["sensors"]["sensor1"][15:18] = [value, value, 0]
    _, data = parse_status("orion/TESTBED/out", json.dumps(status).encode())
    assert data["left_heart_rate"] is None
    assert data["left_breathing_rate"] is None


@pytest.mark.parametrize(
    "sensor", [None, [], [60] * 17, [0] * 15 + [60, 14, 5], [0] * 15 + [60, 14, None]]
)
def test_vital_signs_require_valid_presence(status, sensor):
    status["sensors"]["sensor1"] = sensor
    _, data = parse_status("orion/TESTBED/out", json.dumps(status).encode())
    assert data["left_heart_rate"] is None
    assert data["left_breathing_rate"] is None


def test_vital_metrics_validate_independently(status):
    status["sensors"]["sensor1"][15:18] = [255, 14, 0]
    _, data = parse_status("orion/TESTBED/out", json.dumps(status).encode())
    assert data["left_heart_rate"] is None
    assert data["left_breathing_rate"] == 14


@pytest.mark.parametrize(
    "code,label,occupied,heart,breathing",
    [
        (0, "normal", True, 60, 14),
        (1, "initializing", None, None, None),
        (2, "breathing_pause_reported", True, 60, None),
        (3, "heart_signal_loss", True, None, 14),
        (4, "body_movement", True, 60, 14),
        (5, "not_occupied", False, None, None),
        (6, "turned_over", True, 60, 14),
        (7, "temporary_movement", True, 60, 14),
        (255, "invalid", None, None, None),
        (8, "unknown_status", None, None, None),
        (None, None, None, None, None),
    ],
)
def test_sleep_status_decoding_and_quality(status, code, label, occupied, heart, breathing):
    status["sensors"]["sensor1"][15:18] = [60, 14, code]
    _, data = parse_status("orion/TESTBED/out", json.dumps(status).encode())
    assert data["left_sleep_status_code"] == code
    assert data["left_sleep_status"] == label
    assert data["left_occupancy"] is occupied
    assert data["left_heart_rate"] == heart
    assert data["left_breathing_rate"] == breathing


@pytest.mark.parametrize(
    "clock,expected",
    [
        ([0, 0, 0, 0], (0, 0, 0, 0)),
        ([255, 254, 203, 99], (255, 254, 203, 99)),
        ([256, 0, 0, 0], None),
        ([True, 0, 0, 0], None),
        ([None, 0, 0, 0], None),
        ([1.5, 0, 0, 0], None),
    ],
)
def test_internal_sensor_clock_validation(status, clock, expected):
    status["sensors"]["sensor1"][11:15] = clock
    _, data = parse_status("orion/TESTBED/out", json.dumps(status).encode())
    assert data["left_sensor_time"] == expected
