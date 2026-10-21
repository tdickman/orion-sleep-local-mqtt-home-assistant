import asyncio
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


@pytest.fixture
def auto_reply(hub, device_reply):
    async def publish(serial, command):
        hub._message(f"orion/{serial}/out", device_reply(command))

    hub.mqtt.publish.side_effect = publish


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


async def test_climate_bounds_and_no_optimism(hub, device_reply):
    climate = OrionClimate(hub, "TESTBED", "left")
    assert climate.hvac_mode == HVACMode.HEAT_COOL
    task = asyncio.create_task(climate.async_set_temperature(temperature=21))
    await asyncio.sleep(0)
    hub.mqtt.publish.assert_awaited_once_with("TESTBED", "SET LEFT 21")
    assert climate.target_temperature == 19  # State only from device.
    assert not task.done()
    hub._message("orion/TESTBED/out", device_reply("SET LEFT 21"))
    await task
    assert climate.target_temperature == 21
    for temp in (9.9, 45.1, float("nan"), None):
        with pytest.raises(HomeAssistantError):
            await climate.async_set_temperature(temperature=temp)
    task = asyncio.create_task(climate.async_turn_off())
    await asyncio.sleep(0)
    hub._message("orion/TESTBED/out", device_reply("CLEAR LEFT"))
    await task
    assert hub.mqtt.publish.await_args.args == ("TESTBED", "CLEAR LEFT")


async def test_startup_not_repeated_and_explicit_stop_resets(hub, status, auto_reply):
    hub.devices["TESTBED"]["system_state"] = "SYSTEM_OFF"
    status["system_state"] = "SYSTEM_OFF"
    await hub.commands("TESTBED", ["SET BOTH 19"], start=True)
    await hub.commands("TESTBED", ["SET RIGHT 20"], start=True)
    assert [c.args[1] for c in hub.mqtt.publish.await_args_list].count("SYSTEM ON") == 1
    await hub.commands("TESTBED", ["CLEAR BOTH"])
    await hub.commands("TESTBED", [], start=True)
    assert [c.args[1] for c in hub.mqtt.publish.await_args_list].count("SYSTEM ON") == 2


async def test_validate_whole_batch_before_publish(hub):
    with pytest.raises(HomeAssistantError):
        await hub.commands("TESTBED", ["SET LEFT 19", "SET RIGHT 46"])
    hub.mqtt.publish.assert_not_called()


async def test_light_scaling(hub, auto_reply):
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
    hub.devices["TESTBED"].update(left_heart_rate=60, right_breathing_rate=14)
    text = json.dumps(await async_get_config_entry_diagnostics(hub.hass, hub.entry))
    for private in (
        "TESTBED",
        "localhost",
        "secret",
        "occupancy",
        "target",
        "heart_rate",
        "breathing_rate",
    ):
        assert private not in text


async def test_stop(hub):
    hub._cancel_timer = Mock()
    await hub.stop()
    hub._cancel_timer.assert_called_once()
    hub.mqtt.stop.assert_awaited_once()
    hub.store.async_save.assert_awaited_once_with({"serials": ["TESTBED"]})


async def wait_for_publishes(hub, count):
    async with asyncio.timeout(1):
        while hub.mqtt.publish.await_count < count:
            await asyncio.sleep(0)


async def test_concurrent_sides_wait_for_matching_fresh_telemetry(hub, status, device_reply):
    first = asyncio.create_task(hub.commands("TESTBED", ["SET LEFT 20"]))
    await wait_for_publishes(hub, 1)
    second = asyncio.create_task(hub.commands("TESTBED", ["SET RIGHT 21"]))
    await asyncio.sleep(0)
    hub._message("orion/TESTBED/out", json.dumps(status).encode())  # Old target, new frame.
    hub._message("orion/TESTBED/out", b"invalid JSON")
    status["tec"]["left"].update(setpoint=20, control_enabled=False)
    hub._message("orion/TESTBED/out", json.dumps(status).encode())
    await asyncio.sleep(0)
    assert hub.mqtt.publish.await_count == 1
    assert not first.done()
    hub._message("orion/TESTBED/out", device_reply("SET LEFT 20"))
    await first
    await wait_for_publishes(hub, 2)
    assert not second.done()
    hub._message("orion/TESTBED/out", device_reply("SET RIGHT 21"))
    await second
    assert [c.args[1] for c in hub.mqtt.publish.await_args_list] == ["SET LEFT 20", "SET RIGHT 21"]
    assert not hub.pending


async def test_timeout_aborts_batch_without_retry_and_releases_lock(hub, device_reply):
    # Cached state already matches: it must not be accepted as confirmation.
    with patch("custom_components.orion_local.hub.COMMAND_CONFIRM_TIMEOUT", 0.01):
        with pytest.raises(HomeAssistantError, match="did not confirm 'SET LEFT 19'"):
            await hub.commands("TESTBED", ["SET LEFT 19", "SET RIGHT 21"])
    hub.mqtt.publish.assert_awaited_once_with("TESTBED", "SET LEFT 19")
    assert not hub.pending
    assert not hub.locks["TESTBED"].locked()
    next_task = asyncio.create_task(hub.commands("TESTBED", ["SET RIGHT 21"]))
    await wait_for_publishes(hub, 2)
    hub._message("orion/TESTBED/out", device_reply("SET RIGHT 21"))
    await next_task


@pytest.mark.parametrize("stop", [False, True])
async def test_disconnect_or_unload_fails_waiters_and_queued_commands(hub, stop):
    first = asyncio.create_task(hub.commands("TESTBED", ["SET LEFT 20"]))
    await wait_for_publishes(hub, 1)
    queued = asyncio.create_task(hub.commands("TESTBED", ["SET RIGHT 21"]))
    await asyncio.sleep(0)
    if stop:
        await hub.stop()
    else:
        hub._connection(False)
    with pytest.raises(HomeAssistantError, match="outcome unknown"):
        await first
    with pytest.raises(HomeAssistantError, match="unavailable"):
        await queued
    assert hub.mqtt.publish.await_count == 1
    assert not hub.pending


async def test_cancelled_command_cleans_up_waiter(hub):
    task = asyncio.create_task(hub.commands("TESTBED", ["SET LEFT 20"]))
    await wait_for_publishes(hub, 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not hub.pending
    assert not hub.locks["TESTBED"].locked()


async def test_publish_failure_cleans_up_waiter(hub):
    hub.mqtt.publish.side_effect = ConnectionError("MQTT publish failed")
    with pytest.raises(HomeAssistantError, match="MQTT publish failed"):
        await hub.commands("TESTBED", ["SET LEFT 20"])
    assert not hub.pending


async def test_both_targets_must_match_in_same_frame(hub, status, device_reply):
    task = asyncio.create_task(hub.commands("TESTBED", ["SET BOTH 20 21"]))
    await wait_for_publishes(hub, 1)
    hub._message("orion/TESTBED/out", device_reply("SET LEFT 20"))
    await asyncio.sleep(0)
    assert not task.done()
    status["tec"]["left"]["setpoint"] = 19
    hub._message("orion/TESTBED/out", device_reply("SET RIGHT 21"))
    await asyncio.sleep(0)
    assert not task.done()
    hub._message("orion/TESTBED/out", device_reply("SET BOTH 20 21"))
    await task


async def test_startup_waits_through_pending_state(hub, status, device_reply):
    status["system_state"] = "SYSTEM_OFF"
    hub._message("orion/TESTBED/out", json.dumps(status).encode())
    task = asyncio.create_task(hub.commands("TESTBED", ["SET LEFT 20"], start=True))
    await wait_for_publishes(hub, 1)
    hub._message("orion/TESTBED/out", device_reply("SET LEFT 20"))
    await wait_for_publishes(hub, 2)
    status["system_state"] = "PENDING_ON"
    hub._message("orion/TESTBED/out", json.dumps(status).encode())
    await asyncio.sleep(0)
    assert not task.done()
    hub._message("orion/TESTBED/out", device_reply("SYSTEM ON"))
    await task
    assert [c.args[1] for c in hub.mqtt.publish.await_args_list] == ["SET LEFT 20", "SYSTEM ON"]


async def test_confirmation_can_arrive_before_puback(hub, device_reply):
    ack = asyncio.Event()

    async def publish(serial, command):
        hub._message(f"orion/{serial}/out", device_reply(command))
        await ack.wait()

    hub.mqtt.publish.side_effect = publish
    task = asyncio.create_task(hub.commands("TESTBED", ["SET LEFT 20"]))
    await wait_for_publishes(hub, 1)
    assert not task.done()
    ack.set()
    await task
    assert not hub.pending


async def test_other_bed_can_proceed_but_cannot_confirm_first_bed(hub, status, device_reply):
    status["serial"] = "SECOND"
    hub._message("orion/SECOND/out", json.dumps(status).encode())
    first = asyncio.create_task(hub.commands("TESTBED", ["SET LEFT 20"]))
    second = asyncio.create_task(hub.commands("SECOND", ["SET LEFT 20"]))
    await wait_for_publishes(hub, 2)
    hub._message("orion/SECOND/out", device_reply("SET LEFT 20"))
    await second
    assert not first.done()
    status["serial"] = "TESTBED"
    hub._message("orion/TESTBED/out", device_reply("SET LEFT 20"))
    await first


@pytest.mark.parametrize(
    "command,timeout_constant",
    [
        ("SYSTEM ON", "START_CONFIRM_TIMEOUT"),
        ("REBOOT", "REBOOT_CONFIRM_TIMEOUT"),
    ],
)
async def test_system_commands_use_their_own_timeout(hub, command, timeout_constant):
    hub.devices["TESTBED"]["uptime"] = 1000
    with patch(f"custom_components.orion_local.hub.{timeout_constant}", 0.01):
        with pytest.raises(HomeAssistantError, match="within 0.01 seconds"):
            await hub.commands("TESTBED", [command])
    assert not hub.pending


async def test_reboot_requires_known_uptime_before_sending(hub):
    with pytest.raises(HomeAssistantError, match="uptime"):
        await hub.commands("TESTBED", ["REBOOT"])
    hub.mqtt.publish.assert_not_called()


async def test_rounded_live_setpoint_confirms_without_timeout(hub, status):
    task = asyncio.create_task(hub.commands("TESTBED", ["SET RIGHT 21.3889"]))
    await wait_for_publishes(hub, 1)
    status["tec"]["right"].update(setpoint=21.39, control_enabled=True)
    hub._message("orion/TESTBED/out", json.dumps(status).encode())
    await asyncio.wait_for(task, 1)
    assert not hub.pending
    hub.mqtt.publish.assert_awaited_once_with("TESTBED", "SET RIGHT 21.3889")
