"""Common entities and dynamic discovery shared by each platform."""

from homeassistant.core import callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import DeviceInfo, Entity

from .const import DOMAIN, signal


@callback
def setup_entities(hass, entry, async_add_entities, factory):
    known = set()

    @callback
    def discover():
        for serial in entry.runtime_data.devices:
            if serial not in known:
                known.add(serial)
                async_add_entities(factory(entry.runtime_data, serial))

    entry.async_on_unload(async_dispatcher_connect(hass, signal(entry.entry_id), discover))
    discover()


class OrionEntity(Entity):
    _attr_should_poll = False
    _attr_has_entity_name = True

    def __init__(self, hub, serial, key, name):
        self.hub = hub
        self.serial = serial
        self.key = key
        self._attr_unique_id = f"{serial}_{key}"
        self._attr_name = name

    @property
    def data(self):
        return self.hub.devices.get(self.serial, {})

    @property
    def available(self):
        return self.hub.available(self.serial)

    @property
    def device_info(self):
        return DeviceInfo(
            identifiers={(DOMAIN, self.serial)},
            name=f"Orion {self.serial}",
            manufacturer="Orion Sleep",
            model="Orion bed",
            serial_number=self.serial,
            sw_version=self.data.get("firmware"),
        )

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, signal(self.hub.entry.entry_id), self.async_write_ha_state
            )
        )
