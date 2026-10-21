import json
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
from homeassistant.components.climate import HVACMode
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from custom_components.orion_local.climate import OrionClimate
from custom_components.orion_local.diagnostics import async_get_config_entry_diagnostics
from custom_components.orion_local.entity import setup_entities
from custom_components.orion_local.hub import OrionHub
from custom_components.orion_local.light import OrionLight


@pytest.fixture
async def hub(tmp_path, status):
    hass = HomeAssistant(str(tmp_path))
    entry = SimpleNamespace(
        entry_id="test", data={"host": "localhost", "port": 1883}, async_on_unload=Mock()
    )
    hub = OrionHub(hass, entry)
    entry.runtime_data = hub
    hub.mqtt = SimpleNamespace(connected=True, publish=AsyncMock(), stop=AsyncMock())
    hub.store = SimpleNamespace(async_delay_save=Mock(), async_save=AsyncMock())
    with patch("custom_components.orion_local.hub.async_dispatcher_send"):
        hub._message("orion/TESTBED/out", json.dumps(status).encode())
    yield hub
    await hass.async_stop()


async def test_availability_and_reconnection(hub):
    assert hub.available("TESTBED")
    hub.seen["TESTBED"] = time.monotonic() - 31
    assert not hub.available("TESTBED")
    with pytest.raises(HomeAssistantError):
        await hub.commands("TESTBED", ["CLEAR BOTH"])
    hub.mqtt.publish.assert_not_called()
    hub.seen["TESTBED"] = time.monotonic()
    hub._connection(False)
    hub._connection(True)
    assert not hub.available("TESTBED")  # Requires fresh telemetry.


async def test_climate_bounds_and_no_optimism(hub):
    climate = OrionClimate(hub, "TESTBED", "left")
    assert climate.hvac_mode == HVACMode.HEAT_COOL
    await climate.async_set_temperature(temperature=21)
    hub.mqtt.publish.assert_awaited_once_with("TESTBED", "SET LEFT 21")
    assert climate.target_temperature == 19  # State only from device.
    for temp in (16, 25, float("nan"), None):
        with pytest.raises(HomeAssistantError):
            await climate.async_set_temperature(temperature=temp)
    await climate.async_turn_off()
    assert hub.mqtt.publish.await_args.args == ("TESTBED", "CLEAR LEFT")


async def test_startup_not_repeated_and_explicit_stop_resets(hub):
    hub.devices["TESTBED"]["system_state"] = "SYSTEM_OFF"
    await hub.commands("TESTBED", ["SET BOTH 19"], start=True)
    await hub.commands("TESTBED", ["SET RIGHT 20"], start=True)
    assert [c.args[1] for c in hub.mqtt.publish.await_args_list].count("SYSTEM ON") == 1
    await hub.commands("TESTBED", ["CLEAR BOTH"])
    await hub.commands("TESTBED", [], start=True)
    assert [c.args[1] for c in hub.mqtt.publish.await_args_list].count("SYSTEM ON") == 2


async def test_validate_whole_batch_before_publish(hub):
    with pytest.raises(HomeAssistantError):
        await hub.commands("TESTBED", ["SET LEFT 19", "SET RIGHT 25"])
    hub.mqtt.publish.assert_not_called()


async def test_light_scaling(hub):
    light = OrionLight(hub, "TESTBED", "led", "LED")
    assert light.rgb_color == (32, 64, 96)
    assert light.brightness == 26
    await light.async_turn_on(rgb_color=(255, 128, 0), brightness=128)
    assert [c.args[1] for c in hub.mqtt.publish.await_args_list] == [
        "IB RGB 255 128 0",
        "IB DIMMER 50",
    ]
    await light.async_turn_off()
    assert hub.mqtt.publish.await_args.args[1] == "IB DIMMER 0"


async def test_dynamic_discovery_deduplicates(hub):
    add = Mock()
    with patch("custom_components.orion_local.entity.async_dispatcher_connect") as connect:
        setup_entities(hub.hass, hub.entry, add, lambda h, s: [s])
        discover = connect.call_args.args[2]
        discover()
        hub.devices["SECOND"] = {}
        discover()
    assert [c.args[0] for c in add.call_args_list] == [["TESTBED"], ["SECOND"]]


async def test_diagnostics_allowlist(hub):
    hub.devices["TESTBED"]["wifi"] = {"password": "secret"}
    text = json.dumps(await async_get_config_entry_diagnostics(hub.hass, hub.entry))
    for private in ("TESTBED", "localhost", "secret", "occupancy", "target"):
        assert private not in text


async def test_stop(hub):
    hub._cancel_timer = Mock()
    await hub.stop()
    hub._cancel_timer.assert_called_once()
    hub.mqtt.stop.assert_awaited_once()
    hub.store.async_save.assert_awaited_once_with({"serials": ["TESTBED"]})
