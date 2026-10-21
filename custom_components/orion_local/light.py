"""LED idle color and percent brightness, verified on firmware 2.6.2."""

from homeassistant.components.light import ATTR_BRIGHTNESS, ATTR_RGB_COLOR, ColorMode, LightEntity

from .entity import OrionEntity, setup_entities


async def async_setup_entry(hass, entry, async_add_entities):
    setup_entities(
        hass, entry, async_add_entities, lambda hub, serial: [OrionLight(hub, serial, "led", "LED")]
    )


class OrionLight(OrionEntity, LightEntity):
    _attr_supported_color_modes = {ColorMode.RGB}
    _attr_color_mode = ColorMode.RGB

    @property
    def is_on(self):
        value = self.data.get("brightness")
        return value > 0 if value is not None else None

    @property
    def brightness(self):
        value = self.data.get("brightness")
        return round(value * 255 / 100) if value is not None else None

    @property
    def rgb_color(self):
        return self.data.get("rgb")

    async def async_turn_on(self, **kwargs):
        commands = []
        if ATTR_RGB_COLOR in kwargs:
            r, g, b = kwargs[ATTR_RGB_COLOR]
            commands.append(f"IB RGB {r} {g} {b}")
        brightness = kwargs.get(ATTR_BRIGHTNESS, self.brightness or 255)
        percent = max(1, round(brightness * 100 / 255)) if brightness else 0
        commands.append(f"IB DIMMER {percent}")
        await self.hub.commands(self.serial, commands)

    async def async_turn_off(self, **kwargs):
        await self.hub.commands(self.serial, ["IB DIMMER 0"])
