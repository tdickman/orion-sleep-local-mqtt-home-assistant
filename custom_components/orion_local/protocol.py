"""Validated protocol boundary. No Home Assistant or network dependencies."""

import json
import math
import re

from .const import MAX_TEMP, MIN_TEMP, SLEEP_STATUS_NAMES

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


def command_confirmed(command: str, data: dict, before: dict) -> bool:
    """Match a validated command against normalized, fresh device telemetry."""
    parts = command.split()
    if parts[0] == "SET":
        sides = ("left", "right") if parts[1] == "BOTH" else (parts[1].lower(),)
        targets = [float(value) for value in parts[2:]]
        if len(sides) == 2 and len(targets) == 1:
            targets *= 2
        return all(
            (reported := number(data.get(f"{side}_target"))) is not None
            # Firmware reports setpoints to two decimal places. Fahrenheit
            # conversions can request e.g. 21.3889 but report 21.39. Allow half
            # the reporting resolution, plus epsilon for floating-point ties.
            and math.isclose(reported, target, rel_tol=0, abs_tol=0.005 + 1e-9)
            and data.get(f"{side}_enabled") is True
            for side, target in zip(sides, targets, strict=True)
        )
    if parts[0] == "CLEAR":
        sides = ("left", "right") if parts[1] == "BOTH" else (parts[1].lower(),)
        return all(data.get(f"{side}_enabled") is False for side in sides) and (
            parts[1] != "BOTH" or data.get("system_state") == "SYSTEM_OFF"
        )
    if command == "SYSTEM ON":
        return data.get("system_state") == "SYSTEM_ON"
    if parts[:2] == ["FAN", "QUIET"]:
        return data.get("quiet_mode") is (parts[2] == "ON")
    if parts[:2] == ["IB", "RGB"]:
        return data.get("rgb") == tuple(int(value) for value in parts[2:])
    if parts[:2] == ["IB", "DIMMER"]:
        return data.get("brightness") == int(parts[2])
    if command == "REBOOT":
        uptime = data.get("uptime")
        previous = before.get("uptime")
        return (
            uptime is not None
            and previous is not None
            and uptime < previous
            and data.get("safety_ok") is True
            and data.get("fault") is False
        )
    return False


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
        status = (
            arr[17]
            if isinstance(arr, list)
            and len(arr) > 17
            and type(arr[17]) is int
            and 0 <= arr[17] <= 255
            else None
        )
        data[f"{side}_sleep_status_code"] = status
        data[f"{side}_sleep_status"] = (
            SLEEP_STATUS_NAMES.get(status, "unknown_status") if status is not None else None
        )
        data[f"{side}_occupancy"] = status != 5 if status in (0, 2, 3, 4, 5, 6, 7) else None
        # Treat the internal clock as an opaque four-byte token: changes prove
        # activity without assuming wall time, endianness, or monotonic counters.
        data[f"{side}_sensor_time"] = (
            tuple(arr[11:15])
            if isinstance(arr, list)
            and len(arr) > 17
            and all(type(value) is int and 0 <= value <= 255 for value in arr[11:15])
            else None
        )
        # Observed on Orion/Sleepace: index 15 tracks heart rate, index 16
        # breathing rate. Suppress absent/invalid readings rather than reporting
        # zero BPM; 0 and 255 are the cloud decoder's no-reading sentinels.
        for metric, index in (("heart_rate", 15), ("breathing_rate", 16)):
            value = arr[index] if data[f"{side}_occupancy"] is True else None
            if (metric == "heart_rate" and status == 3) or (
                metric == "breathing_rate" and status == 2
            ):
                value = None
            data[f"{side}_{metric}"] = value if type(value) is int and 0 < value < 255 else None
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
