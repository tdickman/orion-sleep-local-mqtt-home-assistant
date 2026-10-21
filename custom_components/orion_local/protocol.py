"""Validated protocol boundary. No Home Assistant or network dependencies."""

import json
import math
import re

from .const import MAX_TEMP, MIN_TEMP

SERIAL_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def number(value):
    if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
        return value
    return None


def boolean(value):
    if isinstance(value, bool):
        return value
    if value in ("true", "false"):
        return value == "true"
    return None


def temperature(value):
    value = number(value)
    if value is None or not MIN_TEMP <= value <= MAX_TEMP:
        raise ValueError(f"Temperature must be {MIN_TEMP:g}–{MAX_TEMP:g} °C")
    return f"{value:g}"


def validate_command(command):
    """Allow only supported commands, including bounded numeric arguments."""
    parts = command.split()
    if command in {
        "SYSTEM ON",
        "REBOOT",
        "CLEAR LEFT",
        "CLEAR RIGHT",
        "CLEAR BOTH",
        "FAN QUIET ON",
        "FAN QUIET OFF",
    }:
        return command
    if len(parts) in (3, 4) and parts[0] == "SET" and parts[1] in {"LEFT", "RIGHT", "BOTH"}:
        if len(parts) == 4 and parts[1] != "BOTH":
            raise ValueError("Two temperatures require BOTH")
        return " ".join(parts[:2] + [temperature(float(v)) for v in parts[2:]])
    if parts[:2] == ["IB", "RGB"] and len(parts) == 5:
        if all(v.isdigit() and 0 <= int(v) <= 255 for v in parts[2:]):
            return " ".join(parts)
    if parts[:2] == ["IB", "DIMMER"] and len(parts) == 3:
        if parts[2].isdigit() and 0 <= int(parts[2]) <= 100:
            return " ".join(parts)
    raise ValueError("Unsupported Orion command")


def parse_status(topic: str, payload: bytes):
    """Return an allowlisted flat snapshot; never retain network or sleep data."""
    parts = topic.split("/")
    if (
        len(parts) != 3
        or parts[0] != "orion"
        or parts[2] != "out"
        or not SERIAL_RE.fullmatch(parts[1])
    ):
        return None
    if len(payload) > 65536:
        return None
    try:
        raw = json.loads(payload)
    except (ValueError, UnicodeDecodeError, RecursionError):
        return None
    if not isinstance(raw, dict) or raw.get("type") != "status" or raw.get("serial") != parts[1]:
        return None

    def get(*path):
        value = raw
        for key in path:
            if not isinstance(value, dict):
                return None
            value = value.get(key)
        return value

    def text(*path):
        value = get(*path)
        return value if isinstance(value, str) and len(value) <= 128 else None

    state = text("system_state")
    data = {
        "system_state": state,
        "firmware": text("fw", "version"),
        "inner_board_firmware": text("fw", "ib_version"),
        "uptime": number(get("uptime_ms")),
        "quiet_mode": boolean(get("fans", "quiet_mode")),
        "safety_ok": boolean(get("safetyOk")),
        "brightness": number(get("led", "led_brightness")),
    }
    for side, sensor in (("left", "sensor1"), ("right", "sensor2")):
        data[f"{side}_temperature"] = number(get("temps", f"ib_{side}"))
        data[f"{side}_target"] = number(get("tec", side, "setpoint"))
        data[f"{side}_enabled"] = boolean(get("tec", side, "control_enabled"))
        data[f"{side}_fan_rpm"] = number(get("fans", f"{side}_rpm"))
        data[f"{side}_pump"] = text("pumps", side, "status")
        arr = get("sensors", sensor)
        data[f"{side}_occupancy"] = (
            arr[17] != 5
            if isinstance(arr, list) and len(arr) > 17 and number(arr[17]) is not None
            else None
        )
    rgb = tuple(number(get("led", f"idle_{c}")) for c in "rgb")
    data["rgb"] = rgb if all(v is not None and 0 <= v <= 255 for v in rgb) else None
    if data["brightness"] is not None and not 0 <= data["brightness"] <= 100:
        data["brightness"] = None
    errors = get("errors")
    data["fault"] = (
        any(bool(v) for v in errors.values())
        if isinstance(errors, dict)
        and errors
        and all(isinstance(v, (bool, int)) and v in (0, 1) for v in errors.values())
        else None
    )
    return parts[1], data
