"""Telemetry sensors, including observationally mapped Sleepace readings."""

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.const import EntityCategory, UnitOfTemperature, UnitOfTime

from .const import SLEEP_STATUS_OPTIONS
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
    ("left_heart_rate", "Left heart rate", None, "bpm"),
    ("right_heart_rate", "Right heart rate", None, "bpm"),
    ("left_breathing_rate", "Left breathing rate", None, "breaths/min"),
    ("right_breathing_rate", "Right breathing rate", None, "breaths/min"),
    ("left_sleep_status", "Left sleep sensor status", SensorDeviceClass.ENUM, None),
    ("right_sleep_status", "Right sleep sensor status", SensorDeviceClass.ENUM, None),
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
        if key.endswith("_sleep_status"):
            self._attr_options = SLEEP_STATUS_OPTIONS
        if unit:
            self._attr_state_class = SensorStateClass.MEASUREMENT
        if key.endswith("_heart_rate"):
            self._attr_icon = "mdi:heart-pulse"
        elif key.endswith("_breathing_rate"):
            self._attr_icon = "mdi:lungs"
        elif "temperature" not in key and key != "system_state":
            self._attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def native_value(self):
        if self.key.endswith("_sleep_status"):
            freshness = self.hub.sleep_sensor_freshness(self.serial, self.key.split("_", 1)[0])
            if freshness != "fresh":
                return freshness
        value = self.data.get(self.key)
        return value / 1000 if self.key == "uptime" and value is not None else value

    @property
    def extra_state_attributes(self):
        if self.key.endswith("_sleep_status"):
            return {"status_code": self.data.get(f"{self.key}_code")}
        return None
