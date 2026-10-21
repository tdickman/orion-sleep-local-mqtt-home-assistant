"""Paho network thread bridged to the HA event loop."""

import asyncio
import uuid

import paho.mqtt.client as mqtt


class OrionMQTT:
    def __init__(self, host, port, on_message, on_connection):
        self.host = host
        self.port = port
        self.on_message = on_message
        self.on_connection = on_connection
        self.loop = asyncio.get_running_loop()
        self.ready = self.loop.create_future()
        self.closed = False
        self.connected = False
        self.client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id=f"ha-orion-{uuid.uuid4().hex[:16]}",
            clean_session=True,
        )
        self.client.reconnect_delay_set(1, 30)
        self.client.max_queued_messages_set(1)
        self.client.on_connect = self._connect
        self.client.on_subscribe = self._subscribe
        self.client.on_disconnect = self._disconnect
        self.client.on_message = self._message

    def _schedule(self, callback, *args):
        if not self.closed:
            self.loop.call_soon_threadsafe(callback, *args)

    def _connect(self, client, userdata, flags, reason, properties):
        if reason.is_failure:
            self._schedule(self._failed, ConnectionError("MQTT connection refused"))
            return
        result, _ = client.subscribe("orion/+/out", qos=1)
        if result != mqtt.MQTT_ERR_SUCCESS:
            self._schedule(self._failed, ConnectionError("MQTT subscription failed"))

    def _subscribe(self, client, userdata, mid, reasons, properties):
        if not reasons or any(r.is_failure for r in reasons):
            self._schedule(self._failed, ConnectionError("MQTT subscription refused"))
        else:
            self._schedule(self._state, True)

    def _failed(self, error):
        if not self.ready.done():
            self.ready.set_exception(error)
        self._state(False)

    def _state(self, connected):
        if self.closed:
            return
        self.connected = connected
        if connected and not self.ready.done():
            self.ready.set_result(None)
        self.on_connection(connected)

    def _disconnect(self, client, userdata, flags, reason, properties):
        self._schedule(self._state, False)

    def _message(self, client, userdata, message):
        # Retained messages cannot establish that a bed is currently online.
        if not message.retain and len(message.payload) <= 65536:
            self._schedule(self._deliver, message.topic, message.payload)

    def _deliver(self, topic, payload):
        if not self.closed:
            self.on_message(topic, payload)

    async def start(self):
        self.client.connect_async(self.host, self.port, keepalive=30)
        self.client.loop_start()
        try:
            await asyncio.wait_for(asyncio.shield(self.ready), 10)
        except BaseException:
            await self.stop()
            raise

    def _publish(self, topic, command):
        info = self.client.publish(topic, command, qos=1, retain=False)
        if info.rc != mqtt.MQTT_ERR_SUCCESS:
            raise ConnectionError("MQTT publish failed")
        info.wait_for_publish(timeout=5)
        if not info.is_published():
            raise TimeoutError("MQTT acknowledgement timed out; command outcome unknown")

    async def publish(self, serial, command):
        if not self.connected or self.closed:
            raise ConnectionError("MQTT broker disconnected")
        await self.loop.run_in_executor(None, self._publish, f"orion/{serial}/in", command)

    async def stop(self):
        self.closed = True
        self.connected = False
        if not self.ready.done():
            self.ready.cancel()
        self.client.disconnect()
        await self.loop.run_in_executor(None, self.client.loop_stop)
