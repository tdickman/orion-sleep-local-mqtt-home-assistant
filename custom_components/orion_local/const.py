"""Constants for Orion's independent MQTT transport."""

DOMAIN = "orion_local"
PLATFORMS = ["climate", "switch", "light", "button", "sensor", "binary_sensor"]
MIN_TEMP = 10.0  # 50 °F
MAX_TEMP = 45.0  # 113 °F
STALE_SECONDS = 30
COMMAND_CONFIRM_TIMEOUT = 15
START_CONFIRM_TIMEOUT = 240
REBOOT_CONFIRM_TIMEOUT = 120


def signal(entry_id: str) -> str:
    return f"{DOMAIN}_{entry_id}_update"
