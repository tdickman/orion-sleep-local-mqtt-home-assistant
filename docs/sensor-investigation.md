# Presence and local heart-rate investigation

## Confirmed source mapping

The [Reddit comment](https://www.reddit.com/r/Orion_Sleep/comments/1va4pe0/home_assistant_integration_for_orion_now_feature/p1kxqeu/) explicitly gives `sensors.sensor1[17] != 5` for left presence and `sensors.sensor2[17] != 5` for right presence. It says the arrays contain Sleepace live sleep metrics, but provides no heart-rate index, units, or validity flags.

The integration implements this presence mapping and validates that the status is an integer byte. Missing or malformed data is unknown. Version 0.1.3 enables both presence sensors by default and enables existing entities disabled by the integration, while respecting explicit user disables.

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

## Implemented behavior and remaining validation

Version 0.1.7 exposes local left/right heart rate and breathing rate as measurement sensors using the observed indexes. Presence must be valid and detected. Metric values must be integer bytes between 1 and 254: 0 and 255 are suppressed using the existing cloud decoder's no-reading convention. The sentinel convention has not been exhaustively verified during local sensor acquisition. Unknown/missing data does not become a numeric zero, and values are not retained when a fresh frame reports absence. Missing fresh device telemetry makes the sensors unavailable.

Further validation should cover right-only occupancy, two occupants with distinct reference pulses, acquisition and leaving-bed transitions, movement, and a simultaneous respiratory reference. A module-specific specification could confirm units, status/quality flags, and sentinel behavior. The current mapping is based on this firmware's observations, not a published Orion frame specification. The two sensors must not be assumed to measure two distinct people, and no quality interpretation is assigned to individual occupied status codes.
