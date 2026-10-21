import json

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


@pytest.fixture
def device_reply(status):
    """Simulate device state changes in raw telemetry, not broker ACKs."""

    def reply(command):
        parts = command.split()
        if parts[0] in ("SET", "CLEAR"):
            sides = ("left", "right") if parts[1] == "BOTH" else (parts[1].lower(),)
            for index, side in enumerate(sides):
                status["tec"][side]["control_enabled"] = parts[0] == "SET"
                if parts[0] == "SET":
                    status["tec"][side]["setpoint"] = float(parts[min(2 + index, len(parts) - 1)])
            if command == "CLEAR BOTH":
                status["system_state"] = "SYSTEM_OFF"
        elif command == "SYSTEM ON":
            status["system_state"] = "SYSTEM_ON"
        elif parts[:2] == ["FAN", "QUIET"]:
            status["fans"]["quiet_mode"] = parts[2] == "ON"
        elif parts[:2] == ["IB", "RGB"]:
            status["led"].update(zip(("idle_r", "idle_g", "idle_b"), map(int, parts[2:])))
        elif parts[:2] == ["IB", "DIMMER"]:
            status["led"]["led_brightness"] = int(parts[2])
        return json.dumps(status).encode()

    return reply
