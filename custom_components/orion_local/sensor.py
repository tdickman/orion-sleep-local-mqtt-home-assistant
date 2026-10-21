"""Telemetry sensors, without speculative mappings of undocumented values."""

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.const import EntityCategory, UnitOfTemperature, UnitOfTime

from .entity import OrionEntity, setup_entities

SENSORS = [
    ("system_state", "System state", None, None),
    (
        "left_temperature",
        "Left temperature",
        SensorDeviceClass.TEMPERATURE,
        UnitOfTemperature.CELSIUS,
    ),
    (
        "right_temperature",
        "Right temperature",
        SensorDeviceClass.TEMPERATURE,
        UnitOfTemperature.CELSIUS,
    ),
    ("left_fan_rpm", "Left fan speed", None, "rpm"),
    ("right_fan_rpm", "Right fan speed", None, "rpm"),
    ("left_pump", "Left pump status", None, None),
    ("right_pump", "Right pump status", None, None),
    ("uptime", "Uptime", SensorDeviceClass.DURATION, UnitOfTime.SECONDS),
]


async def async_setup_entry(hass, entry, async_add_entities):
    setup_entities(
        hass,
        entry,
        async_add_entities,
        lambda hub, serial: [OrionSensor(hub, serial, *description) for description in SENSORS],
    )


class OrionSensor(OrionEntity, SensorEntity):
    def __init__(self, hub, serial, key, name, device_class, unit):
        super().__init__(hub, serial, key, name)
        self._attr_device_class = device_class
        self._attr_native_unit_of_measurement = unit
        if unit:
            self._attr_state_class = SensorStateClass.MEASUREMENT
        if "temperature" not in key and key != "system_state":
            self._attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def native_value(self):
        value = self.data.get(self.key)
        return value / 1000 if self.key == "uptime" and value is not None else value
