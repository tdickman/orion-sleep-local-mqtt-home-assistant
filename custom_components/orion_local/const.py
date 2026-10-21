"""Constants for Orion's independent MQTT transport."""

DOMAIN = "orion_local"
PLATFORMS = ["climate", "switch", "light", "button", "sensor", "binary_sensor"]
MIN_TEMP = 17.0
MAX_TEMP = 24.0
STALE_SECONDS = 30


def signal(entry_id: str) -> str:
    return f"{DOMAIN}_{entry_id}_update"
