"""System actions. Reboot is disabled by default in the entity registry."""

from homeassistant.components.button import ButtonDeviceClass, ButtonEntity
from homeassistant.const import EntityCategory

from .entity import OrionEntity, setup_entities


async def async_setup_entry(hass, entry, async_add_entities):
    setup_entities(
        hass,
        entry,
        async_add_entities,
        lambda hub, serial: [
            OrionButton(hub, serial, key, name, command)
            for key, name, command in (
                ("start", "Start system", "SYSTEM ON"),
                ("stop", "Turn off both sides", "CLEAR BOTH"),
                ("reboot", "Reboot", "REBOOT"),
            )
        ],
    )


class OrionButton(OrionEntity, ButtonEntity):
    def __init__(self, hub, serial, key, name, command):
        super().__init__(hub, serial, key, name)
        self.command = command
        if command == "REBOOT":
            self._attr_entity_registry_enabled_default = False
            self._attr_entity_category = EntityCategory.CONFIG
            self._attr_device_class = ButtonDeviceClass.RESTART

    async def async_press(self):
        if self.command == "SYSTEM ON":
            await self.hub.commands(self.serial, [], start=True)
        else:
            await self.hub.commands(self.serial, [self.command])
