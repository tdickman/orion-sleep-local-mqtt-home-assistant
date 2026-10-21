"""Direct percent dimmer control, sharing telemetry with the RGB light."""

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.const import PERCENTAGE
from homeassistant.exceptions import HomeAssistantError

from .entity import OrionEntity, setup_entities
from .protocol import number


async def async_setup_entry(hass, entry, async_add_entities):
    setup_entities(
        hass,
        entry,
        async_add_entities,
        lambda hub, serial: [OrionDimmer(hub, serial, "led_dimmer", "LED dimmer")],
    )


class OrionDimmer(OrionEntity, NumberEntity):
    _attr_native_min_value = 0
    _attr_native_max_value = 100
    _attr_native_step = 1
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_mode = NumberMode.SLIDER
    _attr_icon = "mdi:brightness-percent"

    @property
    def native_value(self):
        return self.data.get("brightness")

    async def async_set_native_value(self, value):
        value = number(value)
        if value is None or not 0 <= value <= 100 or value % 1:
            raise HomeAssistantError("LED dimmer must be a whole percentage between 0 and 100")
        await self.hub.commands(self.serial, [f"IB DIMMER {int(value)}"])
