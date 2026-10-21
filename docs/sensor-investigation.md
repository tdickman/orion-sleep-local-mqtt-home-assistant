# Presence and local heart-rate investigation

## Confirmed source mapping

The [Reddit comment](https://www.reddit.com/r/Orion_Sleep/comments/1va4pe0/home_assistant_integration_for_orion_now_feature/p1kxqeu/) explicitly gives `sensors.sensor1[17] != 5` for left presence and `sensors.sensor2[17] != 5` for right presence. It says the arrays contain Sleepace live sleep metrics, but provides no heart-rate index, units, or validity flags.

Version 0.1.3 enabled both presence sensors by default, including existing entities disabled by the integration, while respecting explicit user disables. Version 0.1.8 refines the original `!= 5` rule using the author's follow-up status mapping: `1` (initializing), `255` (invalid), and unrecognized codes are unknown, not occupied. Known occupied codes are 0, 2, 3, 4, 6, and 7; code 5 means absent.

## Initial empty-bed observations from the local broker

No device commands were sent during this investigation. On control-box firmware 2.6.2:

- Both `sensor1` and `sensor2` were arrays of 24 integers.
- Both index-17 values were `5` across 20 fresh status messages.
- Sensor metadata reported firmware `11.00.04`, algorithm `02.02.04`, hardware `01.00.00`.
- The sensor object also contained power, load-switch, historical-status, and historical-duration metadata; there was no named heart-rate or respiratory-rate property.
- During that sample, indexes 11, 22, and 23 changed on both sides even though both presence statuses stayed `5`. Some values wrapped through 255. This does not identify any of them as BPM; counters, framing, and checksums also change over time.

At that stage, neither an occupied sample nor a simultaneous reference pulse reading was available. Raw arrays and network identifiers are not included here or in downloadable integration diagnostics.

## Follow-up occupied-bed capture

A later read-only capture collected 60 frames over approximately 31 seconds while the user reported lying on the left side and supplied an approximate reference pulse. Index **15** of `sensor1` closely tracked that reference. Index **16** varied in a range consistent with resting respiratory rate. Both sensors' index-17 values were 0 or 4 instead of the empty-bed value 5.

The user confirmed there was only one person in bed. Both sensors nevertheless produced presence and biometric readings, consistent with cross-side pickup. This capture supports using index 15 for heart rate and index 16 for breathing rate, but does not establish independent person attribution, medical accuracy, or respiratory accuracy against a reference device.

An earlier hypothesis placed heart rate at index 16 based on ordering in other Sleepace product SDKs. The occupied capture contradicts that ordering for Orion; **the implemented heart-rate index is 15**, not 16.

## Other sources examined

- [Existing Orion cloud integration sensor platform](https://github.com/tdickman/home-assistant-orion-integration/blob/main/custom_components/orion_sleep/sensor.py): `OrionLiveHeartRateSensor` consumes the already-decoded WebSocket `status.sensors.<sensor>.heart_rate` property. Its documented invalid values are 0 and 255. This demonstrates a cloud-side heart-rate representation, **not** the corresponding local raw-array offset or validity rules.
- [Sleepace research data readers](https://github.com/EvolveaSystems/sleepace/blob/master/sleepacelib/data.py): describe heart-rate output files and a state enumeration with 5 meaning out of bed. These readers handle research/file formats; they do not document Orion's 24-value MQTT arrays.
- Public Sleepace SDK/reference searches did not establish a matching frame layout for the sensor firmware/algorithm versions above. Other Sleepace product SDKs cannot safely be assumed to share the same frame format.
- The author's [follow-up Reddit comment](https://www.reddit.com/r/Orion_Sleep/comments/1va4pe0/home_assistant_integration_for_orion_now_feature/phz4axj/) explicitly identifies indexes 15/16 as heart/breathing rate, index 17 as status, indexes 11–14 as internal sensor time, and 0/255 as invalid biometric readings. It warns that disconnected sensors can repeat stale values while the bed continues publishing. The author derived this from older firmware and an SDK and did not verify every listed command on current firmware.

## Sensor liveness and status validation

A read-only sample on firmware 2.6.2 captured 60 bed messages over about 31.4 seconds, with 33 distinct internal clock values on each side. Observed clock changes were about one second apart (quantized by the bed's roughly half-second publish cadence). Version 0.1.9 uses a **15-second per-side timeout**, reduced from the initial 30-second margin.

Each four-byte clock is treated as an opaque token. The first observation establishes a baseline; a subsequent change establishes liveness. Counter wraps and resets are accepted as activity. The timeout uses elapsed local monotonic time since that internal timestamp last changed, not the age of an MQTT message or a change in any measurement. It does not compare the sensor clock directly with wall-clock time. Repeated timestamps cannot refresh the timeout. Broker reconnect and missing/invalid clock bytes discard prior liveness and require a new baseline and change. Timestamps are not persisted across integration restarts.

If a side freezes, its presence and both biometric entities become unavailable while controls and the other side remain usable. Diagnostic status entities remain available while the bed is online, so they can explain `stale`, `missing_data`, and `waiting_for_update`. Codes 1 and 255 produce unknown presence and biometric values even if their timestamps are changing. Code 3 suppresses heart rate and code 2 suppresses breathing rate; the other metric can remain available. All known status labels and unknown-code handling are documented in the README. Status descriptions are firmware indications, not clinical interpretations.

## Implemented behavior and remaining validation

Version 0.1.7 added local left/right heart rate and breathing rate; version 0.1.8 adds sensor-clock liveness, invalid-status handling, and status diagnostics. Presence must be valid and detected. Metric values must be integer bytes between 1 and 254: the cloud decoder's 0/255 sentinel convention is now corroborated by the follow-up Reddit comment, but has not been exhaustively tested during local acquisition. Unknown/missing metrics do not become zero. Values are not retained when a fresh frame reports absence, and missing clock liveness makes them unavailable.

Further validation should cover right-only occupancy, two occupants with distinct reference pulses, acquisition and leaving-bed transitions, movement, and a simultaneous respiratory reference. A module-specific specification could confirm units, status/quality flags, checksum coverage, and sentinel behavior. The current mapping combines observations with community firmware analysis, not an official Orion frame specification. The two sensors must not be assumed to measure two distinct people. Bytes 20–21 remain undocumented: no HRV/RMSSD entity is exposed from the speculative metric mentioned in the comment. Additional raw hardware/debug commands have not been enabled.
