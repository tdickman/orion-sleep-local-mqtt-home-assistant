import asyncio
import json
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
from homeassistant.components.climate import HVACMode
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from custom_components.orion_local.binary_sensor import OrionBinarySensor
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
        "sensor_time",
        "sleep_status",
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


async def test_sensor_clock_freshness_is_independent_of_bed_telemetry(hub, status):
    now = 100.0
    with patch("custom_components.orion_local.hub.time", SimpleNamespace(monotonic=lambda: now)):
        hub._connection(False)
        hub._connection(True)
        hub._message("orion/TESTBED/out", json.dumps(status).encode())
        assert hub.sleep_sensor_freshness("TESTBED", "left") == "waiting_for_update"
        assert not hub.sleep_sensor_available("TESTBED", "left")
        # Repeated frames cannot establish sensor liveness.
        now += 1
        hub._message("orion/TESTBED/out", json.dumps(status).encode())
        assert not hub.sleep_sensor_available("TESTBED", "left")
        for side in ("sensor1", "sensor2"):
            status["sensors"][side][11] += 1
        hub._message("orion/TESTBED/out", json.dumps(status).encode())
        assert hub.sleep_sensor_available("TESTBED", "left")
        assert hub.sleep_sensor_available("TESTBED", "right")
        # The bed and right clock continue updating while left freezes.
        now += 14
        status["sensors"]["sensor2"][11] += 1
        # Changing readings cannot refresh a frozen internal sensor timestamp.
        status["sensors"]["sensor1"][15:18] = [60, 14, 0]
        hub._message("orion/TESTBED/out", json.dumps(status).encode())
        assert hub.sleep_sensor_available("TESTBED", "left")
        now += 1
        status["sensors"]["sensor1"][15] = 61
        hub._message("orion/TESTBED/out", json.dumps(status).encode())
        assert hub.available("TESTBED")
        assert hub.sleep_sensor_freshness("TESTBED", "left") == "stale"
        assert not hub.sleep_sensor_available("TESTBED", "left")
        assert hub.sleep_sensor_available("TESTBED", "right")
        # Resumption is immediate, and decreasing/wrapping counters are valid.
        for clock in ([0, 0, 0, 0], [255, 255, 255, 255], [0, 0, 0, 0]):
            status["sensors"]["sensor1"][11:15] = clock
            hub._message("orion/TESTBED/out", json.dumps(status).encode())
            assert hub.sleep_sensor_available("TESTBED", "left")


async def test_clock_must_advance_again_after_disconnect_or_invalid_clock(hub, status):
    status["sensors"]["sensor1"][11] += 1
    hub._message("orion/TESTBED/out", json.dumps(status).encode())
    assert hub.sleep_sensor_available("TESTBED", "left")
    hub._connection(False)
    hub._connection(True)
    hub._message("orion/TESTBED/out", json.dumps(status).encode())
    assert not hub.sleep_sensor_available("TESTBED", "left")
    status["sensors"]["sensor1"][11] += 1
    hub._message("orion/TESTBED/out", json.dumps(status).encode())
    assert hub.sleep_sensor_available("TESTBED", "left")
    status["sensors"]["sensor1"][11] = None
    hub._message("orion/TESTBED/out", json.dumps(status).encode())
    assert hub.sleep_sensor_freshness("TESTBED", "left") == "missing_data"
    assert not hub.sleep_sensor_available("TESTBED", "left")
    status["sensors"]["sensor1"][11] = 10
    hub._message("orion/TESTBED/out", json.dumps(status).encode())
    assert not hub.sleep_sensor_available("TESTBED", "left")
    status["sensors"]["sensor1"][11] = 11
    hub._message("orion/TESTBED/out", json.dumps(status).encode())
    assert hub.sleep_sensor_available("TESTBED", "left")


async def test_initial_frozen_clock_eventually_reports_stale(hub, status):
    now = 100.0
    with patch("custom_components.orion_local.hub.time", SimpleNamespace(monotonic=lambda: now)):
        hub._connection(False)
        hub._message("orion/TESTBED/out", json.dumps(status).encode())
        now += 15
        hub._message("orion/TESTBED/out", json.dumps(status).encode())
        assert hub.sleep_sensor_freshness("TESTBED", "left") == "stale"
        assert not hub.sleep_sensor_available("TESTBED", "left")


@pytest.fixture
def presence_stream(hub, status):
    clock = [100.0]
    with patch(
        "custom_components.orion_local.hub.time", SimpleNamespace(monotonic=lambda: clock[0])
    ):
        hub._connection(False)
        hub._connection(True)

        def feed(seconds, left, right=5, *, advance=True):
            clock[0] = 100.0 + seconds
            for sensor, code in (("sensor1", left), ("sensor2", right)):
                status["sensors"][sensor][17] = code
                if advance:
                    status["sensors"][sensor][11] = (status["sensors"][sensor][11] + 1) % 256
            hub._message("orion/TESTBED/out", json.dumps(status).encode())
            hub._tick(None)

        feed(0, 5)
        feed(1, 5)
        yield feed


async def test_brief_presence_pulses_do_not_trigger_but_sustained_presence_does(
    hub, presence_stream
):
    sensor = OrionBinarySensor(hub, "TESTBED", "left_occupancy", "Left presence")
    assert sensor.is_on is False
    # Reproduce observed 5 -> 0 -> 5 pulses, each shorter than one second.
    for start in (2, 4, 6):
        presence_stream(start, 0)
        assert hub.devices["TESTBED"]["left_occupancy"] is True
        assert sensor.is_on is False
        presence_stream(start + 0.6, 5)
        assert sensor.is_on is False
    presence_stream(8, 0)
    presence_stream(10.9, 0)
    assert sensor.is_on is False
    presence_stream(11, 4)  # Movement is still an occupied status.
    assert sensor.is_on is True
    presence_stream(12, 0)
    assert sensor.is_on is True
    presence_stream(13, 5)
    assert sensor.is_on is False  # No delay when leaving.


async def test_presence_confirmation_is_independent_per_side(hub, presence_stream):
    presence_stream(2, 0)
    presence_stream(3, 5, 0)
    presence_stream(5, 0, 0)
    assert hub.presence_state("TESTBED", "left") is False
    assert hub.presence_state("TESTBED", "right") is False
    presence_stream(6, 0, 0)
    assert hub.presence_state("TESTBED", "left") is False
    assert hub.presence_state("TESTBED", "right") is True
    presence_stream(8, 0, 5)
    assert hub.presence_state("TESTBED", "left") is True
    assert hub.presence_state("TESTBED", "right") is False


@pytest.mark.parametrize("invalid", [1, 255, None, 8])
async def test_invalid_presence_immediately_resets_confirmation(hub, presence_stream, invalid):
    presence_stream(2, 0)
    presence_stream(5, 0)
    assert hub.presence_state("TESTBED", "left") is True
    presence_stream(6, invalid)
    assert hub.presence_state("TESTBED", "left") is None
    presence_stream(7, 0)
    presence_stream(9.9, 0)
    assert hub.presence_state("TESTBED", "left") is None
    presence_stream(10, 0)
    assert hub.presence_state("TESTBED", "left") is True


async def test_frozen_clock_cannot_complete_presence_confirmation(hub, presence_stream):
    presence_stream(2, 0)
    presence_stream(5, 0, advance=False)
    assert hub.presence_state("TESTBED", "left") is False
    presence_stream(16, 0, advance=False)
    assert hub.presence_state("TESTBED", "left") is False
    presence_stream(17, 0, advance=False)
    assert not hub.sleep_sensor_available("TESTBED", "left")
    assert hub.presence_state("TESTBED", "left") is None
    presence_stream(18, 0)
    presence_stream(20.9, 0)
    assert hub.presence_state("TESTBED", "left") is None
    presence_stream(21, 0)
    assert hub.presence_state("TESTBED", "left") is True


async def test_reconnect_requires_new_presence_confirmation(hub, presence_stream):
    presence_stream(2, 0)
    hub._connection(False)
    hub._connection(True)
    presence_stream(3, 0)
    assert not hub.sleep_sensor_available("TESTBED", "left")
    presence_stream(4, 0)
    assert hub.sleep_sensor_available("TESTBED", "left")
    presence_stream(6.9, 0)
    assert hub.presence_state("TESTBED", "left") is None
    presence_stream(7, 0)
    assert hub.presence_state("TESTBED", "left") is True
