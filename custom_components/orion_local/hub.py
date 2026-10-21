"""Discovery, availability, storage, and serialized device commands."""

import asyncio
import time
from datetime import timedelta

from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.storage import Store

from .const import DOMAIN, STALE_SECONDS, signal
from .mqtt import OrionMQTT
from .protocol import SERIAL_RE, parse_status, validate_command


class OrionHub:
    def __init__(self, hass, entry):
        self.hass = hass
        self.entry = entry
        self.devices = {}
        self.seen = {}
        self.locks = {}
        self.start_requested = {}
        self.dirty = False
        self._available = set()
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
        if self._cancel_timer:
            self._cancel_timer()
        await self.mqtt.stop()
        await self.store.async_save({"serials": sorted(self.devices)})

    def available(self, serial):
        return (
            self.mqtt.connected
            and time.monotonic() - self.seen.get(serial, float("-inf")) < STALE_SECONDS
        )

    @callback
    def _connection(self, connected):
        if not connected:
            self.seen.clear()
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
        self.dirty = True
        if new:
            self.store.async_delay_save(lambda: {"serials": sorted(self.devices)}, 5)
            async_dispatcher_send(self.hass, signal(self.entry.entry_id))

    @callback
    def _tick(self, now):
        available = {s for s in self.devices if self.available(s)}
        if self.dirty or available != self._available:
            self.dirty = False
            self._available = available
            async_dispatcher_send(self.hass, signal(self.entry.entry_id))

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
                    await self.mqtt.publish(serial, command)
                    if command in ("CLEAR BOTH", "REBOOT"):
                        self.start_requested.pop(serial, None)
                if (
                    start
                    and self.devices[serial].get("system_state") == "SYSTEM_OFF"
                    and time.monotonic() - self.start_requested.get(serial, float("-inf")) > 240
                ):
                    await self.mqtt.publish(serial, "SYSTEM ON")
                    self.start_requested[serial] = time.monotonic()
            except (ConnectionError, TimeoutError, RuntimeError, ValueError) as err:
                raise HomeAssistantError(str(err)) from err
