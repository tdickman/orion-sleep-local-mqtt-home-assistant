import pytest


@pytest.fixture
def status():
    return {
        "type": "status",
        "serial": "TESTBED",
        "system_state": "SYSTEM_ON",
        "tec": {side: {"setpoint": 19, "control_enabled": True} for side in ("left", "right")},
        "temps": {"ib_left": 19.8, "ib_right": 20.2},
        "fans": {"quiet_mode": True},
        "led": {
            "r": 3,
            "g": 6,
            "b": 9,
            "idle_r": 32,
            "idle_g": 64,
            "idle_b": 96,
            "led_brightness": 10,
        },
        "sensors": {"sensor1": [5] * 24, "sensor2": [5] * 24},
        "errors": {"PUMP_CURRENT": 0},
        "safetyOk": "true",
        "fw": {"version": "2.6.2"},
        "wifi": {"password": "secret"},
    }
