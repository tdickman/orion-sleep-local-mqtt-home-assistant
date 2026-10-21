"""Per-side temperature control with explicit Celsius bounds."""

from homeassistant.components.climate import ClimateEntity, ClimateEntityFeature, HVACMode
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.exceptions import HomeAssistantError

from .const import MAX_TEMP, MIN_TEMP
from .entity import OrionEntity, setup_entities
from .protocol import temperature


async def async_setup_entry(hass, entry, async_add_entities):
    setup_entities(
        hass,
        entry,
        async_add_entities,
        lambda hub, serial: [OrionClimate(hub, serial, side) for side in ("left", "right")],
    )


class OrionClimate(OrionEntity, ClimateEntity):
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_min_temp = MIN_TEMP
    _attr_max_temp = MAX_TEMP
    _attr_target_temperature_step = 0.5
    _attr_hvac_modes = [HVACMode.OFF, HVACMode.HEAT_COOL]
    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE
        | ClimateEntityFeature.TURN_ON
        | ClimateEntityFeature.TURN_OFF
    )

    def __init__(self, hub, serial, side):
        super().__init__(hub, serial, f"{side}_climate", side.title())
        self.side = side

    @property
    def current_temperature(self):
        return self.data.get(f"{self.side}_temperature")

    @property
    def target_temperature(self):
        return self.data.get(f"{self.side}_target")

    @property
    def hvac_mode(self):
        enabled = self.data.get(f"{self.side}_enabled")
        if enabled is None:
            return None
        return (
            HVACMode.HEAT_COOL
            if enabled and self.data.get("system_state") != "SYSTEM_OFF"
            else HVACMode.OFF
        )

    async def async_set_temperature(self, **kwargs):
        try:
            target = temperature(kwargs[ATTR_TEMPERATURE])
        except (KeyError, ValueError) as err:
            raise HomeAssistantError("Temperature must be between 17 and 24 °C") from err
        await self.hub.commands(self.serial, [f"SET {self.side.upper()} {target}"], start=True)

    async def async_set_hvac_mode(self, hvac_mode):
        if hvac_mode == HVACMode.OFF:
            await self.async_turn_off()
        elif hvac_mode == HVACMode.HEAT_COOL:
            await self.async_turn_on()
        else:
            raise HomeAssistantError("Unsupported HVAC mode")

    async def async_turn_on(self):
        await self.async_set_temperature(temperature=self.target_temperature)

    async def async_turn_off(self):
        await self.hub.commands(self.serial, [f"CLEAR {self.side.upper()}"])
