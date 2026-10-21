"""Fan quiet mode."""

from homeassistant.components.switch import SwitchEntity

from .entity import OrionEntity, setup_entities


async def async_setup_entry(hass, entry, async_add_entities):
    setup_entities(
        hass,
        entry,
        async_add_entities,
        lambda hub, serial: [QuietSwitch(hub, serial, "quiet_mode", "Quiet mode")],
    )


class QuietSwitch(OrionEntity, SwitchEntity):
    @property
    def is_on(self):
        return self.data.get("quiet_mode")

    async def async_turn_on(self, **kwargs):
        await self.hub.commands(self.serial, ["FAN QUIET ON"])

    async def async_turn_off(self, **kwargs):
        await self.hub.commands(self.serial, ["FAN QUIET OFF"])
