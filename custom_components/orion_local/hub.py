"""Discovery, availability, storage, and serialized device commands."""

import asyncio
import time
from datetime import timedelta

from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.storage import Store

from .const import (
    COMMAND_CONFIRM_TIMEOUT,
    DOMAIN,
    REBOOT_CONFIRM_TIMEOUT,
    SLEEP_SENSOR_STALE_SECONDS,
    STALE_SECONDS,
    START_CONFIRM_TIMEOUT,
    signal,
)
from .mqtt import OrionMQTT
from .protocol import SERIAL_RE, command_confirmed, parse_status, validate_command


class OrionHub:
    def __init__(self, hass, entry):
        self.hass = hass
        self.entry = entry
        self.devices = {}
        self.seen = {}
        self.sensor_times = {}
        self.sensor_changed = {}
        self.sensor_confirmed = set()
        self.locks = {}
        self.start_requested = {}
        self.pending = {}
        self._stopped = False
        self.dirty = False
        self._available = set()
        self._sensor_freshness = {}
        self._cancel_timer = None
        self.store = Store(hass, 1, f"{DOMAIN}.{entry.entry_id}")
        self.mqtt = OrionMQTT(
            entry.data["host"], entry.data["port"], self._message, self._connection
        )

    async def start(self):
        saved = await self.store.async_load() or {}
        for serial in saved.get("serials", []):
            if isinstance(serial, str) and SERIAL_RE.fullmatch(serial):
                self.devices[serial] = {}
        await self.mqtt.start()
        self._cancel_timer = async_track_time_interval(self.hass, self._tick, timedelta(seconds=1))

    async def stop(self):
        self._stopped = True
        self._fail_pending("Orion integration stopped before command confirmation; outcome unknown")
        if self._cancel_timer:
            self._cancel_timer()
        await self.mqtt.stop()
        await self.store.async_save({"serials": sorted(self.devices)})

    def available(self, serial):
        return (
            not self._stopped
            and self.mqtt.connected
            and time.monotonic() - self.seen.get(serial, float("-inf")) < STALE_SECONDS
        )

    def sleep_sensor_freshness(self, serial, side):
        key = (serial, side)
        if key not in self.sensor_times:
            return "missing_data"
        if time.monotonic() - self.sensor_changed[key] >= SLEEP_SENSOR_STALE_SECONDS:
            return "stale"
        if key not in self.sensor_confirmed:
            return "waiting_for_update"
        return "fresh"

    def sleep_sensor_available(self, serial, side):
        return self.available(serial) and self.sleep_sensor_freshness(serial, side) == "fresh"

    @callback
    def _connection(self, connected):
        if not connected:
            self.seen.clear()
            self.sensor_times.clear()
            self.sensor_changed.clear()
            self.sensor_confirmed.clear()
            self._fail_pending("MQTT disconnected before command confirmation; outcome unknown")
        self.dirty = True
        async_dispatcher_send(self.hass, signal(self.entry.entry_id))

    @callback
    def _message(self, topic, payload):
        parsed = parse_status(topic, payload)
        if parsed is None:
            return
        serial, data = parsed
        new = serial not in self.devices
        self.devices[serial] = data
        if data.get("system_state") == "SYSTEM_ON":
            self.start_requested.pop(serial, None)
        self.seen[serial] = time.monotonic()
        for side in ("left", "right"):
            key = (serial, side)
            sensor_time = data.get(f"{side}_sensor_time")
            if sensor_time is None:
                self.sensor_times.pop(key, None)
                self.sensor_changed.pop(key, None)
                self.sensor_confirmed.discard(key)
            elif key not in self.sensor_times:
                # A single frame may already be frozen. Require a clock change
                # after discovery/reconnect before exposing sleep measurements.
                self.sensor_times[key] = sensor_time
                self.sensor_changed[key] = self.seen[serial]
            elif sensor_time != self.sensor_times[key]:
                self.sensor_times[key] = sensor_time
                self.sensor_changed[key] = self.seen[serial]
                self.sensor_confirmed.add(key)
        if pending := self.pending.get(serial):
            command, before, future = pending
            if not future.done() and command_confirmed(command, data, before):
                future.set_result(None)
        self.dirty = True
        if new:
            self.store.async_delay_save(lambda: {"serials": sorted(self.devices)}, 5)
            async_dispatcher_send(self.hass, signal(self.entry.entry_id))

    @callback
    def _tick(self, now):
        available = {s for s in self.devices if self.available(s)}
        freshness = {
            (serial, side): self.sleep_sensor_freshness(serial, side)
            for serial in self.devices
            for side in ("left", "right")
        }
        if self.dirty or available != self._available or freshness != self._sensor_freshness:
            self.dirty = False
            self._available = available
            self._sensor_freshness = freshness
            async_dispatcher_send(self.hass, signal(self.entry.entry_id))

    @callback
    def _fail_pending(self, reason):
        for _, _, future in self.pending.values():
            if not future.done():
                # Use a result rather than an exception: a publish failure may
                # prevent the caller awaiting this future, and still needs cleanup.
                future.set_result(reason)

    async def _send_confirmed(self, serial, command):
        before = self.devices[serial].copy()
        if command == "REBOOT" and before.get("uptime") is None:
            raise HomeAssistantError("Cannot confirm reboot without a current uptime reading")
        timeout = COMMAND_CONFIRM_TIMEOUT
        if command == "SYSTEM ON":
            timeout = START_CONFIRM_TIMEOUT
        elif command == "REBOOT":
            timeout = REBOOT_CONFIRM_TIMEOUT
        future = self.hass.loop.create_future()
        # Register before publishing: telemetry may arrive before broker PUBACK.
        # Cached state cannot satisfy this future; only _message can confirm it.
        self.pending[serial] = (command, before, future)
        try:
            await self.mqtt.publish(serial, command)
            try:
                error = await asyncio.wait_for(future, timeout)
            except TimeoutError as err:
                raise HomeAssistantError(
                    f"Orion did not confirm '{command}' within {timeout:g} seconds; "
                    "command outcome unknown. Check device state before trying again."
                ) from err
            if error:
                raise HomeAssistantError(error)
        finally:
            self.pending.pop(serial, None)
            if not future.done():
                future.cancel()

    async def commands(self, serial, commands, *, start=False):
        try:
            validated = [validate_command(cmd) for cmd in commands]
        except ValueError as err:
            raise HomeAssistantError(str(err)) from err
        async with self.locks.setdefault(serial, asyncio.Lock()):
            if not self.available(serial):
                raise HomeAssistantError("Orion bed is unavailable")
            try:
                for command in validated:
                    await self._send_confirmed(serial, command)
                    if command in ("CLEAR BOTH", "REBOOT"):
                        self.start_requested.pop(serial, None)
                if (
                    start
                    and self.devices[serial].get("system_state") == "SYSTEM_OFF"
                    and time.monotonic() - self.start_requested.get(serial, float("-inf"))
                    > START_CONFIRM_TIMEOUT
                ):
                    self.start_requested[serial] = time.monotonic()
                    await self._send_confirmed(serial, "SYSTEM ON")
            except (ConnectionError, TimeoutError, RuntimeError, ValueError) as err:
                raise HomeAssistantError(str(err)) from err
