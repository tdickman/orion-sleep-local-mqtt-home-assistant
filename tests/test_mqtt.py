from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from custom_components.orion_local.mqtt import OrionMQTT


async def test_suback_disconnect_retained_and_close():
    with patch("custom_components.orion_local.mqtt.mqtt.Client") as factory:
        client = factory.return_value
        client.subscribe.return_value = (0, 1)
        messages, connections = Mock(), Mock()
        transport = OrionMQTT("localhost", 1883, messages, connections)
        # Simulate thread callbacks deterministically on the event loop.
        transport._schedule = lambda cb, *args: cb(*args)
        transport._connect(client, None, None, SimpleNamespace(is_failure=False), None)
        client.subscribe.assert_called_once_with("orion/+/out", qos=1)
        assert not transport.connected
        transport._subscribe(client, None, 1, [SimpleNamespace(is_failure=False)], None)
        assert transport.connected
        transport._message(client, None, SimpleNamespace(retain=True, topic="topic", payload=b"{}"))
        messages.assert_not_called()
        transport._message(
            client, None, SimpleNamespace(retain=False, topic="topic", payload=b"{}")
        )
        messages.assert_called_once_with("topic", b"{}")
        transport._disconnect(client, None, None, None, None)
        assert not transport.connected
        with pytest.raises(ConnectionError):
            await transport.publish("BED", "CLEAR BOTH")
        await transport.stop()
        transport._deliver("topic", b"{}")
        assert messages.call_count == 1
        client.disconnect.assert_called_once()
        client.loop_stop.assert_called_once()


async def test_publish_nonretained_and_ack_timeout():
    with patch("custom_components.orion_local.mqtt.mqtt.Client") as factory:
        info = factory.return_value.publish.return_value
        info.rc = 0
        info.is_published.return_value = True
        transport = OrionMQTT("localhost", 1883, Mock(), Mock())
        transport.connected = True
        await transport.publish("BED", "SET BOTH 19")
        factory.return_value.publish.assert_called_once_with(
            "orion/BED/in", "SET BOTH 19", qos=1, retain=False
        )
        info.is_published.return_value = False
        with pytest.raises(TimeoutError):
            await transport.publish("BED", "CLEAR BOTH")
        await transport.stop()


async def test_connection_timeout_cleans_up_thread():
    with patch("custom_components.orion_local.mqtt.mqtt.Client") as factory:
        transport = OrionMQTT("localhost", 1883, Mock(), Mock())
        with patch(
            "custom_components.orion_local.mqtt.asyncio.wait_for",
            new=AsyncMock(side_effect=TimeoutError),
        ):
            with pytest.raises(TimeoutError):
                await transport.start()
        assert transport.closed
        factory.return_value.disconnect.assert_called_once()
        factory.return_value.loop_stop.assert_called_once()
