"""Safety and opt-in experimental occupancy indicators."""

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.const import EntityCategory

from .entity import OrionEntity, setup_entities


async def async_setup_entry(hass, entry, async_add_entities):
    setup_entities(
        hass,
        entry,
        async_add_entities,
        lambda hub, serial: [
            OrionBinarySensor(hub, serial, key, name)
            for key, name in (
                ("fault", "Fault"),
                ("safety_ok", "Safety problem"),
                ("left_occupancy", "Left occupancy (experimental)"),
                ("right_occupancy", "Right occupancy (experimental)"),
            )
        ],
    )


class OrionBinarySensor(OrionEntity, BinarySensorEntity):
    def __init__(self, hub, serial, key, name):
        super().__init__(hub, serial, key, name)
        if "occupancy" in key:
            self._attr_device_class = BinarySensorDeviceClass.OCCUPANCY
            self._attr_entity_registry_enabled_default = False
        else:
            self._attr_device_class = BinarySensorDeviceClass.PROBLEM
            self._attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def is_on(self):
        value = self.data.get(self.key)
        return not value if self.key == "safety_ok" and value is not None else value
