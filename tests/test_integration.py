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


async def test_full_setup_discovery_service_and_unload(tmp_path, status):
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
        hub._message("orion/TESTBED/out", json.dumps(status).encode())
        await hass.async_block_till_done()
        assert hass.states.get("climate.orion_testbed_left").attributes["temperature"] == 19
        assert hass.states.get("climate.orion_testbed_left").attributes["min_temp"] == 10
        assert hass.states.get("climate.orion_testbed_left").attributes["max_temp"] == 45
        registry = er.async_get(hass)
        entries = er.async_entries_for_config_entry(registry, entry.entry_id)
        assert len(entries) == 20
        assert len([e for e in entries if e.disabled_by]) == 3
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
        dimmer_id = "number.orion_testbed_led_dimmer"
        assert hass.states.get(light_id).attributes["supported_color_modes"] == ["rgb"]
        assert float(hass.states.get(dimmer_id).state) == 10
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
            "number", "set_value", {"entity_id": dimmer_id, "value": 73}, blocking=True
        )
        publish.assert_awaited_once_with("TESTBED", "IB DIMMER 73")
        # Both controls remain telemetry-driven, then synchronize together.
        assert float(hass.states.get(dimmer_id).state) == 10
        status["led"].update(led_brightness=73, idle_r=255, idle_g=64, idle_b=32)
        hub._message("orion/TESTBED/out", json.dumps(status).encode())
        hub._tick(None)
        await hass.async_block_till_done()
        assert float(hass.states.get(dimmer_id).state) == 73
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
        # Persisted identities return unavailable until fresh telemetry arrives.
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        hub = entry.runtime_data
        assert "TESTBED" in hub.devices
        assert not hub.available("TESTBED")
        assert hass.states.get("climate.orion_testbed_left").state == "unavailable"
        status["serial"] = "SECOND"
        hub._message("orion/SECOND/out", json.dumps(status).encode())
        await hass.async_block_till_done()
        assert hass.states.get("climate.orion_second_left") is not None
        assert len(er.async_entries_for_config_entry(registry, entry.entry_id)) == 40
        assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_stop()
