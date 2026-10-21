"""Exercise the real Home Assistant loader, registries, services and platforms."""

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

from homeassistant import config_entries, loader
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component

from custom_components.orion_local.const import DOMAIN


async def test_full_setup_discovery_service_and_unload(tmp_path, status, device_reply):
    (tmp_path / "custom_components").symlink_to(Path(__file__).parents[1] / "custom_components")
    hass = HomeAssistant(str(tmp_path))
    hass.config.skip_pip = True
    loader.async_setup(hass)
    hass.config_entries = config_entries.ConfigEntries(hass, {})
    await hass.config_entries.async_initialize()
    await dr.async_load(hass)
    await er.async_load(hass)
    assert await async_setup_component(hass, "homeassistant", {})

    async def start(client):
        client.connected = True

    with (
        patch("custom_components.orion_local.mqtt.OrionMQTT.start", start),
        patch(
            "custom_components.orion_local.mqtt.OrionMQTT.publish", new_callable=AsyncMock
        ) as publish,
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": "user"}, data={"host": "localhost", "port": 1883}
        )
        assert result["type"] == "create_entry", result
        entry = result["result"]
        await hass.async_block_till_done()
        assert entry.state is config_entries.ConfigEntryState.LOADED
        hub = entry.runtime_data

        async def confirm_publish(serial, command):
            hub._message(f"orion/{serial}/out", device_reply(command))

        publish.side_effect = confirm_publish
        hub._message("orion/TESTBED/out", json.dumps(status).encode())
        await hass.async_block_till_done()
        left_status = "sensor.orion_testbed_left_sleep_sensor_status"
        right_status = "sensor.orion_testbed_right_sleep_sensor_status"
        assert hass.states.get(left_status).state == "waiting_for_update"
        # Each sensor must demonstrate clock activity before presence/vitals load.
        status["sensors"]["sensor1"][11] += 1
        status["sensors"]["sensor2"][11] += 1
        hub._message("orion/TESTBED/out", json.dumps(status).encode())
        hub._tick(None)
        await hass.async_block_till_done()
        assert hass.states.get("climate.orion_testbed_left").attributes["temperature"] == 19
        assert hass.states.get("climate.orion_testbed_left").attributes["min_temp"] == 10
        assert hass.states.get("climate.orion_testbed_left").attributes["max_temp"] == 45
        registry = er.async_get(hass)
        entries = er.async_entries_for_config_entry(registry, entry.entry_id)
        assert len(entries) == 25
        assert len([e for e in entries if e.disabled_by]) == 1
        left_presence = registry.async_get_entity_id(
            "binary_sensor", DOMAIN, "TESTBED_left_occupancy"
        )
        right_presence = registry.async_get_entity_id(
            "binary_sensor", DOMAIN, "TESTBED_right_occupancy"
        )
        assert hass.states.get(left_presence).state == "off"
        assert hass.states.get(right_presence).state == "off"
        assert hass.states.get(left_presence).attributes["device_class"] == "occupancy"
        assert hass.states.get(left_status).state == "not_occupied"
        assert hass.states.get(left_status).attributes["device_class"] == "enum"
        assert hass.states.get(left_status).attributes["status_code"] == 5
        assert registry.async_get(left_status).entity_category.value == "diagnostic"
        status["sensors"]["sensor1"][17] = 0
        hub._message("orion/TESTBED/out", json.dumps(status).encode())
        hub._tick(None)
        await hass.async_block_till_done()
        assert hass.states.get(left_presence).state == "on"
        assert hass.states.get(right_presence).state == "off"
        status["sensors"]["sensor1"] = []
        status["sensors"]["sensor2"][17] = 4
        hub._message("orion/TESTBED/out", json.dumps(status).encode())
        hub._tick(None)
        await hass.async_block_till_done()
        assert hass.states.get(left_presence).state == "unavailable"
        assert hass.states.get(left_status).state == "missing_data"
        assert hass.states.get(right_presence).state == "on"
        left_hr = "sensor.orion_testbed_left_heart_rate"
        right_hr = "sensor.orion_testbed_right_heart_rate"
        left_br = "sensor.orion_testbed_left_breathing_rate"
        right_br = "sensor.orion_testbed_right_breathing_rate"
        assert hass.states.get(left_hr).state == "unavailable"
        status["sensors"]["sensor1"] = [0] * 15 + [60, 14, 0]
        status["sensors"]["sensor2"] = [0] * 15 + [66, 16, 4]
        hub._message("orion/TESTBED/out", json.dumps(status).encode())
        status["sensors"]["sensor1"][11] = 1
        status["sensors"]["sensor2"][11] = 1
        hub._message("orion/TESTBED/out", json.dumps(status).encode())
        hub._tick(None)
        await hass.async_block_till_done()
        for entity_id, value, unit in (
            (left_hr, 60, "bpm"),
            (right_hr, 66, "bpm"),
            (left_br, 14, "breaths/min"),
            (right_br, 16, "breaths/min"),
        ):
            state = hass.states.get(entity_id)
            assert float(state.state) == value
            assert state.attributes["unit_of_measurement"] == unit
            assert state.attributes["state_class"] == "measurement"
            assert registry.async_get(entity_id).entity_category is None
        status["sensors"]["sensor1"][17] = 5
        hub._message("orion/TESTBED/out", json.dumps(status).encode())
        hub._tick(None)
        await hass.async_block_till_done()
        assert hass.states.get(left_hr).state == "unknown"
        assert hass.states.get(left_br).state == "unknown"
        assert float(hass.states.get(right_hr).state) == 66
        # A frozen left sleep sensor must not disable right or climate controls.
        hub.sensor_changed[("TESTBED", "left")] -= 16
        hub._tick(None)
        await hass.async_block_till_done()
        assert hass.states.get(left_hr).state == "unavailable"
        assert hass.states.get(left_br).state == "unavailable"
        assert hass.states.get(left_presence).state == "unavailable"
        assert hass.states.get(left_status).state == "stale"
        assert hass.states.get(right_status).state == "body_movement"
        assert float(hass.states.get(right_hr).state) == 66
        assert hass.states.get("climate.orion_testbed_left").state != "unavailable"
        for code, label in ((1, "initializing"), (255, "invalid")):
            status["sensors"]["sensor1"][11] += 1
            status["sensors"]["sensor1"][17] = code
            hub._message("orion/TESTBED/out", json.dumps(status).encode())
            hub._tick(None)
            await hass.async_block_till_done()
            assert hass.states.get(left_status).state == label
            assert hass.states.get(left_presence).state == "unknown"
            assert hass.states.get(left_hr).state == "unknown"
            assert hass.states.get(left_br).state == "unknown"
        device = dr.async_get(hass).async_get_device(identifiers={(DOMAIN, "TESTBED")})
        await hass.services.async_call(
            DOMAIN,
            "set_temperatures",
            {"device_id": device.id, "left": 10, "right": 45},
            blocking=True,
        )
        publish.assert_awaited_once_with("TESTBED", "SET BOTH 10 45")
        publish.reset_mock()
        light_id = "light.orion_testbed_led"
        assert hass.states.get(light_id).attributes["supported_color_modes"] == ["rgb"]
        assert registry.async_get_entity_id("number", DOMAIN, "TESTBED_led_dimmer") is None
        await hass.services.async_call(
            "light",
            "turn_on",
            {"entity_id": light_id, "rgb_color": [255, 64, 32], "brightness_pct": 37},
            blocking=True,
        )
        assert [call.args for call in publish.await_args_list] == [
            ("TESTBED", "IB RGB 255 64 32"),
            ("TESTBED", "IB DIMMER 37"),
        ]
        publish.reset_mock()
        await hass.services.async_call(
            "light", "turn_on", {"entity_id": light_id, "brightness_pct": 73}, blocking=True
        )
        publish.assert_awaited_once_with("TESTBED", "IB DIMMER 73")
        # The service returns only after the simulated device reports brightness.
        assert hub.devices["TESTBED"]["brightness"] == 73
        status["led"].update(led_brightness=73, idle_r=255, idle_g=64, idle_b=32)
        hub._message("orion/TESTBED/out", json.dumps(status).encode())
        hub._tick(None)
        await hass.async_block_till_done()
        assert hass.states.get(light_id).attributes["brightness"] == 186
        assert hass.states.get(light_id).attributes["rgb_color"] == (255, 64, 32)
        publish.reset_mock()
        await hass.services.async_call("light", "turn_off", {"entity_id": light_id}, blocking=True)
        publish.assert_awaited_once_with("TESTBED", "IB DIMMER 0")
        duplicate = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": "user"}, data={"host": "localhost", "port": 1883}
        )
        assert duplicate["reason"] == "already_configured"
        assert await hass.config_entries.async_unload(entry.entry_id)
        assert hub.mqtt.closed
        assert not hass.services.has_service(DOMAIN, "set_temperatures")
        registry.async_update_entity(
            left_presence, disabled_by=er.RegistryEntryDisabler.INTEGRATION
        )
        registry.async_update_entity(right_presence, disabled_by=er.RegistryEntryDisabler.USER)
        # Simulate a renamed dimmer entity left behind by version 0.1.3.
        old_dimmer = registry.async_get_or_create(
            "number",
            DOMAIN,
            "TESTBED_led_dimmer",
            config_entry=entry,
            suggested_object_id="custom_bed_brightness",
        )
        # Persisted identities return unavailable until fresh telemetry arrives.
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        hub = entry.runtime_data
        assert "TESTBED" in hub.devices
        assert not hub.available("TESTBED")
        assert hass.states.get("climate.orion_testbed_left").state == "unavailable"
        assert registry.async_get(left_presence).disabled_by is None
        assert registry.async_get(right_presence).disabled_by is er.RegistryEntryDisabler.USER
        assert hass.states.get(left_presence).state == "unavailable"
        assert hass.states.get(left_hr).state == "unavailable"
        assert registry.async_get(old_dimmer.entity_id) is None
        assert registry.async_get_entity_id("light", DOMAIN, "TESTBED_led") == light_id
        status["serial"] = "SECOND"
        hub._message("orion/SECOND/out", json.dumps(status).encode())
        await hass.async_block_till_done()
        assert hass.states.get("climate.orion_second_left") is not None
        assert len(er.async_entries_for_config_entry(registry, entry.entry_id)) == 50
        assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_stop()
