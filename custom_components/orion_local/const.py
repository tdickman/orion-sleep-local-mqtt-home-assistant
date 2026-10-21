"""Constants for Orion's independent MQTT transport."""

DOMAIN = "orion_local"
PLATFORMS = ["climate", "switch", "light", "button", "sensor", "binary_sensor"]
MIN_TEMP = 10.0  # 50 °F
MAX_TEMP = 45.0  # 113 °F
STALE_SECONDS = 30
SLEEP_SENSOR_STALE_SECONDS = 15
SLEEP_STATUS_NAMES = {
    0: "normal",
    1: "initializing",
    2: "breathing_pause_reported",
    3: "heart_signal_loss",
    4: "body_movement",
    5: "not_occupied",
    6: "turned_over",
    7: "temporary_movement",
    255: "invalid",
}
SLEEP_STATUS_OPTIONS = [
    *SLEEP_STATUS_NAMES.values(),
    "unknown_status",
    "waiting_for_update",
    "missing_data",
    "stale",
]
COMMAND_CONFIRM_TIMEOUT = 15
START_CONFIRM_TIMEOUT = 240
REBOOT_CONFIRM_TIMEOUT = 120


def signal(entry_id: str) -> str:
    return f"{DOMAIN}_{entry_id}_update"
