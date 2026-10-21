"""Constants for Orion's independent MQTT transport."""

DOMAIN = "orion_local"
PLATFORMS = ["climate", "switch", "light", "number", "button", "sensor", "binary_sensor"]
MIN_TEMP = 10.0  # 50 °F
MAX_TEMP = 45.0  # 113 °F
STALE_SECONDS = 30


def signal(entry_id: str) -> str:
    return f"{DOMAIN}_{entry_id}_update"
