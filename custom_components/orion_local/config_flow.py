"""UI configuration of the separate anonymous LAN MQTT broker."""

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.helpers import config_validation as cv

from .const import DOMAIN
from .mqtt import OrionMQTT


class OrionConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input=None):
        errors = {}
        if user_input is not None:
            host = user_input["host"].strip().lower()
            port = user_input["port"]
            if not host or "/" in host or any(c.isspace() for c in host):
                errors["host"] = "invalid_host"
            else:
                await self.async_set_unique_id(f"{host}:{port}")
                self._abort_if_unique_id_configured()
                client = OrionMQTT(host, port, lambda *args: None, lambda *args: None)
                try:
                    await client.start()
                except (OSError, TimeoutError, ConnectionError):
                    errors["base"] = "cannot_connect"
                finally:
                    await client.stop()
                if not errors:
                    return self.async_create_entry(
                        title=f"Orion MQTT ({host})", data={"host": host, "port": port}
                    )
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {vol.Required("host"): str, vol.Required("port", default=1883): cv.port}
            ),
            errors=errors,
        )
