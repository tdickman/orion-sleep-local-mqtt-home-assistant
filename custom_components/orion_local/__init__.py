"""Orion Sleep Local MQTT integration."""

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_HOMEASSISTANT_STOP
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady, HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr

from .const import DOMAIN, MAX_TEMP, MIN_TEMP, PLATFORMS
from .hub import OrionHub

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    hub = OrionHub(hass, entry)
    try:
        await hub.start()
    except (OSError, TimeoutError, ConnectionError) as err:
        raise ConfigEntryNotReady("Cannot connect to Orion MQTT broker") from err
    entry.runtime_data = hub

    async def stop_on_shutdown(event):
        await hub.stop()

    entry.async_on_unload(hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, stop_on_shutdown))
    try:
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    except BaseException:
        await hub.stop()
        raise
    if not hass.services.has_service(DOMAIN, "set_temperatures"):

        async def set_temperatures(call):
            device = dr.async_get(hass).async_get(call.data["device_id"])
            if device:
                for config in hass.config_entries.async_entries(DOMAIN):
                    runtime = getattr(config, "runtime_data", None)
                    if not runtime or config.entry_id not in device.config_entries:
                        continue
                    for domain, serial in device.identifiers:
                        if domain == DOMAIN and serial in runtime.devices:
                            left, right = (
                                call.data["left"],
                                call.data.get("right", call.data["left"]),
                            )
                            await runtime.commands(
                                serial, [f"SET BOTH {left:g} {right:g}"], start=True
                            )
                            return
            raise HomeAssistantError("Select an available Orion Local device")

        hass.services.async_register(
            DOMAIN,
            "set_temperatures",
            set_temperatures,
            schema=vol.Schema(
                {
                    vol.Required("device_id"): cv.string,
                    vol.Required("left"): vol.All(
                        vol.Coerce(float), vol.Range(min=MIN_TEMP, max=MAX_TEMP)
                    ),
                    vol.Optional("right"): vol.All(
                        vol.Coerce(float), vol.Range(min=MIN_TEMP, max=MAX_TEMP)
                    ),
                }
            ),
        )
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    if await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        await entry.runtime_data.stop()
        if not any(
            e.entry_id != entry.entry_id
            and getattr(e, "runtime_data", None)
            and not e.runtime_data.mqtt.closed
            for e in hass.config_entries.async_entries(DOMAIN)
        ):
            hass.services.async_remove(DOMAIN, "set_temperatures")
        return True
    return False
