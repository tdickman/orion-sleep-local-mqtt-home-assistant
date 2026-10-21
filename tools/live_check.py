"""Explicit opt-in hardware acceptance check; never sends fill or Wi-Fi erase."""

import argparse
import copy
import json
import queue
import time
from pathlib import Path

import paho.mqtt.client as mqtt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", required=True)
    parser.add_argument("--serial", required=True)
    parser.add_argument("--execute", action="store_true", required=True)
    parser.add_argument("--report", required=True)
    args = parser.parse_args()
    frames = queue.Queue()
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    client.on_connect = lambda c, u, f, r, p: c.subscribe(f"orion/{args.serial}/out", 1)

    def receive(c, u, m):
        if not m.retain:
            frames.put(json.loads(m.payload))

    client.on_message = receive
    client.connect(args.host, 1883, 30)
    client.loop_start()
    results = []

    def safe(d):
        return str(d.get("safetyOk")).lower() == "true" and not any(d.get("errors", {}).values())

    def summary(d):
        return {
            k: d.get(k)
            for k in (
                "system_state",
                "tec",
                "fans",
                "led",
                "uptime_ms",
                "safetyOk",
                "errors",
                "previous_command_received",
            )
        }

    def run(command, predicate, timeout=30):
        while not frames.empty():
            frames.get_nowait()
        info = client.publish(f"orion/{args.serial}/in", command, qos=1, retain=False)
        info.wait_for_publish(5)
        start = time.monotonic()
        consecutive = 0
        last = {}
        while time.monotonic() - start < timeout:
            try:
                last = frames.get(timeout=2)
            except queue.Empty:
                continue
            if not safe(last) and command == "REBOOT":
                consecutive = 0
                continue
            if not safe(last):
                raise RuntimeError("Device reported safety fault; stopped test")
            consecutive = consecutive + 1 if predicate(last) else 0
            if consecutive >= 3:
                break
        result = {
            "command": command,
            "verified": consecutive >= 3,
            "seconds": round(time.monotonic() - start, 1),
            "telemetry": summary(last),
        }
        results.append(result)
        print(json.dumps(result), flush=True)
        Path(args.report).write_text(
            json.dumps({"initial": summary(initial), "results": results}, indent=2) + "\n"
        )
        return last

    initial = copy.deepcopy(frames.get(timeout=20))
    if not safe(initial) or initial["system_state"] != "SYSTEM_ON":
        client.disconnect()
        client.loop_stop()
        raise RuntimeError("Begin with the bed healthy and SYSTEM_ON")
    left = initial["tec"]["left"]["setpoint"]
    right = initial["tec"]["right"]["setpoint"]
    if not all(10 <= value <= 45 for value in (left, right)):
        client.disconnect()
        client.loop_stop()
        raise RuntimeError("Initial targets outside 10–45 °C; set valid targets before testing")
    test_left = left + 0.5 if left <= 44.5 else left - 0.5
    test_right = right + 0.5 if right <= 44.5 else right - 0.5

    def target(left_value=None, right_value=None):
        return lambda d: all(
            d["tec"][side]["setpoint"] == val and d["tec"][side]["control_enabled"]
            for side, val in (("left", left_value), ("right", right_value))
            if val is not None
        )

    try:
        run(f"SET LEFT {test_left:g}", target(left_value=test_left))
        run(f"SET RIGHT {test_right:g}", target(right_value=test_right))
        run(f"SET BOTH {left:g}", target(left, left))
        run(f"SET BOTH {test_left:g} {right:g}", target(test_left, right))
        for side in ("LEFT", "RIGHT", "BOTH"):
            sides = ("left", "right") if side == "BOTH" else (side.lower(),)
            run(f"CLEAR {side}", lambda d: all(not d["tec"][s]["control_enabled"] for s in sides))
            if side != "BOTH":
                run(f"SET BOTH {left:g} {right:g}", target(left, right))
        run("SYSTEM ON", lambda d: d["system_state"] == "SYSTEM_ON", 240)
        run(f"SET BOTH {left:g} {right:g}", target(left, right))
        for value in (False, True):
            run(f"FAN QUIET {'ON' if value else 'OFF'}", lambda d: d["fans"]["quiet_mode"] is value)
        run(
            "IB RGB 32 64 96",
            lambda d: all(
                d["led"][c] == v for c, v in zip(("idle_r", "idle_g", "idle_b"), (32, 64, 96))
            ),
        )
        run("IB DIMMER 10", lambda d: d["led"]["led_brightness"] == 10)
        before = frames.get(timeout=5)["uptime_ms"]
        run("REBOOT", lambda d: d["uptime_ms"] < before, 120)
    finally:
        # Reboot can report transient initialization faults. Wait read-only for
        # three healthy frames before attempting to restore any settings.
        healthy = 0
        deadline = time.monotonic() + 120
        while healthy < 3 and time.monotonic() < deadline:
            try:
                frame = frames.get(timeout=2)
            except queue.Empty:
                continue
            healthy = healthy + 1 if safe(frame) else 0
        if healthy < 3:
            client.disconnect()
            client.loop_stop()
            raise RuntimeError("Bed did not recover; restoration not attempted")
        # Restoration is recorded separately, with the same state verification.
        run(f"SET BOTH {left:g} {right:g}", target(left, right), 60)
        if initial["system_state"] == "SYSTEM_ON":
            run("SYSTEM ON", lambda d: d["system_state"] == "SYSTEM_ON", 240)
        for side in ("left", "right"):
            if not initial["tec"][side]["control_enabled"]:
                run(f"CLEAR {side.upper()}", lambda d: not d["tec"][side]["control_enabled"])
        quiet = initial["fans"]["quiet_mode"]
        run(f"FAN QUIET {'ON' if quiet else 'OFF'}", lambda d: d["fans"]["quiet_mode"] is quiet)
        led = initial["led"]
        run(
            f"IB DIMMER {led['led_brightness']}",
            lambda d: d["led"]["led_brightness"] == led["led_brightness"],
        )
        run(
            f"IB RGB {led['idle_r']} {led['idle_g']} {led['idle_b']}",
            lambda d: all(d["led"][c] == led[c] for c in ("idle_r", "idle_g", "idle_b")),
        )
        client.disconnect()
        client.loop_stop()


if __name__ == "__main__":
    main()
