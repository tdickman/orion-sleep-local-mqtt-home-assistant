"""Safety and per-side presence using the community-documented mapping."""

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.const import EntityCategory
from homeassistant.helpers import entity_registry as er

from .entity import OrionEntity, setup_entities


async def async_setup_entry(hass, entry, async_add_entities):
    # Earlier releases disabled these entities by integration default. Enable
    # those on upgrade, preserving explicit user disables and stable IDs.
    registry = er.async_get(hass)
    for entity in er.async_entries_for_config_entry(registry, entry.entry_id):
        if (
            entity.domain == "binary_sensor"
            and entity.unique_id.endswith(("_left_occupancy", "_right_occupancy"))
            and entity.disabled_by is er.RegistryEntryDisabler.INTEGRATION
        ):
            registry.async_update_entity(entity.entity_id, disabled_by=None)
    setup_entities(
        hass,
        entry,
        async_add_entities,
        lambda hub, serial: [
            OrionBinarySensor(hub, serial, key, name)
            for key, name in (
                ("fault", "Fault"),
                ("safety_ok", "Safety problem"),
                ("left_occupancy", "Left presence"),
                ("right_occupancy", "Right presence"),
            )
        ],
    )


class OrionBinarySensor(OrionEntity, BinarySensorEntity):
    def __init__(self, hub, serial, key, name):
        super().__init__(hub, serial, key, name)
        if "occupancy" in key:
            self._attr_device_class = BinarySensorDeviceClass.OCCUPANCY
        else:
            self._attr_device_class = BinarySensorDeviceClass.PROBLEM
            self._attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def is_on(self):
        if self.key.endswith("_occupancy"):
            return self.hub.presence_state(self.serial, self.key.split("_", 1)[0])
        value = self.data.get(self.key)
        return not value if self.key == "safety_ok" and value is not None else value
