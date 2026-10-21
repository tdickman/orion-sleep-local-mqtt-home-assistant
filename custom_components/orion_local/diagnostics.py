"""Diagnostics exclude broker address, serials, occupancy, and raw payloads."""


async def async_get_config_entry_diagnostics(hass, entry):
    hub = entry.runtime_data
    return {
        "broker_connected": hub.mqtt.connected,
        "device_count": len(hub.devices),
        "devices": [
            {
                "available": hub.available(serial),
                "firmware": data.get("firmware"),
                "system_state": data.get("system_state"),
                "safety_ok": data.get("safety_ok"),
                "fault": data.get("fault"),
            }
            for serial, data in hub.devices.items()
        ],
    }
