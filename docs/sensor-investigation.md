# Presence and local heart-rate investigation

## Confirmed source mapping

The [Reddit comment](https://www.reddit.com/r/Orion_Sleep/comments/1va4pe0/home_assistant_integration_for_orion_now_feature/p1kxqeu/) explicitly gives `sensors.sensor1[17] != 5` for left presence and `sensors.sensor2[17] != 5` for right presence. It says the arrays contain Sleepace live sleep metrics, but provides no heart-rate index, units, or validity flags.

The integration implements this presence mapping and validates that the status is an integer byte. Missing or malformed data is unknown. Version 0.1.3 enables both presence sensors by default and enables existing entities disabled by the integration, while respecting explicit user disables.

## Read-only observations from the local broker

No device commands were sent during this investigation. On control-box firmware 2.6.2:

- Both `sensor1` and `sensor2` were arrays of 24 integers.
- Both index-17 values were `5` across 20 fresh status messages.
- Sensor metadata reported firmware `11.00.04`, algorithm `02.02.04`, hardware `01.00.00`.
- The sensor object also contained power, load-switch, historical-status, and historical-duration metadata; there was no named heart-rate or respiratory-rate property.
- During that sample, indexes 11, 22, and 23 changed on both sides even though both presence statuses stayed `5`. Some values wrapped through 255. This does not identify any of them as BPM; counters, framing, and checksums also change over time.

Neither an occupied sample nor a simultaneous reference pulse reading was available. Raw arrays and network identifiers are not included here or in downloadable integration diagnostics.

## Other sources examined

- [Existing Orion cloud integration sensor platform](https://github.com/tdickman/home-assistant-orion-integration/blob/main/custom_components/orion_sleep/sensor.py): `OrionLiveHeartRateSensor` consumes the already-decoded WebSocket `status.sensors.<sensor>.heart_rate` property. Its documented invalid values are 0 and 255. This demonstrates a cloud-side heart-rate representation, **not** the corresponding local raw-array offset or validity rules.
- [Sleepace research data readers](https://github.com/EvolveaSystems/sleepace/blob/master/sleepacelib/data.py): describe heart-rate output files and a state enumeration with 5 meaning out of bed. These readers handle research/file formats; they do not document Orion's 24-value MQTT arrays.
- Public Sleepace SDK/reference searches did not establish a matching frame layout for the sensor firmware/algorithm versions above. Other Sleepace product SDKs cannot safely be assumed to share the same frame format.

## Conclusion and next validation step

Presence can be implemented from the published mapping. Heart-rate measurement is available in Orion's decoded cloud interface, but a reliable extraction from **this local MQTT payload** remains unverified. No arbitrary array index is presented as a heart-rate sensor.

To implement local BPM, obtain either a frame specification/decoder for this Sleepace module or a controlled capture paired with a known heart-rate reading. A useful capture covers empty bed, left-only occupancy, right-only occupancy, stillness long enough for acquisition, and leaving the bed, with timestamps and a reference reading. Verify the offset, byte order/scaling, update cadence, status/quality flags, and no-reading sentinels before enabling a BPM entity. Physical left/right assignment should also be checked during that capture.
