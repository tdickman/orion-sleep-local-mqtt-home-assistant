# Orion Sleep Local MQTT

A HACS-installable Home Assistant custom integration for **local Orion Sleep bed control**. Enter your MQTT broker's IP address; all beds publishing `orion/<serial>/out` are discovered automatically, including beds that connect later.

This integration maintains its **own MQTT connection**. Home Assistant's existing MQTT integration and broker can continue running independently. No Orion account or cloud credentials are needed.

## Requirements

- Home Assistant **2026.2 or newer** (tested against Core 2026.2.3).
- HACS, or manual access to Home Assistant's `custom_components` directory.
- An anonymous MQTT broker reachable by Home Assistant and the bed, normally TCP port `1883`.
- The bed already redirected to that broker. The community-described setup overrides `dev.orionbed.com` in local DNS to the broker's IP, then restarts the bed. DNS does not change the port. App/cloud/schedule behavior under this setup has not been verified.

Keep the anonymous broker on your LAN. You do not need to change or weaken your existing authenticated Home Assistant MQTT broker.

## Install with HACS

1. Push this repository to GitHub. The manifest assumes **`tdickman/orion-sleep-local-mqtt-home-assistant`**; update the `documentation`, `issue_tracker`, and `codeowners` entries in `custom_components/orion_local/manifest.json` if you choose another owner/name.
2. In HACS, open the menu → **Custom repositories**.
3. Add the GitHub repository URL and select **Integration** as the type.
4. Find **Orion Sleep Local MQTT** in HACS and download it.
5. Restart Home Assistant.
6. Go to **Settings → Devices & services → Add integration → Orion Sleep Local MQTT**.
7. Enter your broker IP, for example **`192.168.64.5`**, and port **`1883`**.

The setup checks the MQTT connection and subscription. Beds appear as their first fresh status messages arrive, normally within a few seconds. An empty broker can be configured before beds connect. MQTT has no general-purpose list-connected-devices command; discovery enumerates Orion devices that actually publish telemetry.

For manual installation, copy `custom_components/orion_local` to `/config/custom_components/orion_local`, restart HA, then follow steps 6–7.

## Entities per bed

| Entity | Behavior |
| --- | --- |
| Left / Right climate | Temperature, target, enable/disable; automatic heating/cooling (`heat_cool`) |
| Quiet mode switch | `FAN QUIET ON` / `OFF` |
| LED light | RGB color and brightness |
| Start system button | Starts the system if off; does not alter side setpoints or enable flags |
| Turn off both sides button | `CLEAR BOTH` |
| Reboot button | Disabled by default; enable in the entity settings if needed |
| System state sensor | Raw firmware state, including `PENDING_ON` |
| Left / Right temperature sensors | Measured pad temperatures |
| Left / Right fan speed sensors | RPM |
| Left / Right pump status sensors | Firmware-reported status |
| Uptime sensor | Seconds since boot |
| Fault / Safety problem binary sensors | Reported firmware fault / safety status |
| Left / Right occupancy binary sensors | Experimental and disabled by default |

**Every outgoing temperature is limited to 10–45°C (50–113°F)**, including service calls, matching the setpoint range reported for the Orion mobile app. HA may display converted Fahrenheit values if configured that way; MQTT commands and the custom action below always use Celsius.

Setting a climate target enables that side and requests `SYSTEM ON` if the bed is off. The firmware chooses heating or cooling; undocumented TEC mode numbers are not translated into an HVAC action. Turning off one climate sends `CLEAR LEFT` or `CLEAR RIGHT`; the other side is left alone. Turning on a climate reuses its reported target; if that target is outside 10–45°C, set a valid temperature first.

Startup can take about a minute. While `PENDING_ON`, wait instead of repeatedly starting the bed. Entity state comes from telemetry, never merely from a broker acknowledgement. The integration does not retry application commands on a timeout; MQTT QoS 1 itself can redeliver an in-flight message.

### Set both temperatures together

Use **Developer tools → Actions → Orion Sleep Local MQTT: Set both temperatures**, and select the bed. The right temperature is optional; omitting it uses the left temperature for both sides.

```yaml
action: orion_local.set_temperatures
data:
  device_id: YOUR_HOME_ASSISTANT_DEVICE_ID
  left: 19
  right: 20
```

This uses a single `SET BOTH <left> <right>` command and starts the system if off.

### LEDs, occupancy, and reboot

- LED RGB uses `led.idle_r/g/b`, the requested color. Firmware `led.r/g/b` are brightness-scaled output values. Brightness maps between HA's 0–255 and Orion's 0–100.
- Occupancy uses the Reddit author's mapping (`sensor1[17] != 5` for left and `sensor2[17] != 5` for right), with missing/short-array handling. Occupancy transitions and physical side mapping have **not** been validated.
- **Reboot resets targets to firmware defaults (observed 27.5°C) and disables both sides.** The integration does not automatically power on or restore targets after a user-triggered reboot. Choose valid 10–45°C targets to resume.
- Fill and Wi-Fi erasure are deliberately not exposed or accepted by the command validator. There is no unrestricted raw-command action.

## Reliability and diagnostics

- Wildcard subscription `orion/+/out`, reconnect with backoff, and resubscribe after reconnect.
- Stable serial-based device and entity IDs. Known serials survive restarts; offline beds remain visible as unavailable.
- Mark unavailable after 30 seconds without fresh telemetry or on broker disconnect. Retained messages do not establish availability.
- Parse/size/type checks and serial/topic matching. Missing individual readings become unknown.
- Commands use plain UTF-8 on `orion/<serial>/in`, QoS 1, **retain=false**.
- Entity updates are coalesced to about once per second.
- Diagnostics use an allowlist: broker connection status, device count, availability, firmware, system state, and safety/fault flags. Broker address, serials, Wi-Fi information, raw sleep sensor arrays, and occupancy are omitted.
- Disable any cloud integration automations that also control the same bed to avoid competing commands. This integration uses the separate `orion_local` domain.

If a bed does not appear, verify that the broker receives fresh `orion/<serial>/out` JSON with `type: status` and a matching `serial`. A successful configuration step proves broker connectivity, not bed connectivity. If the broker address changes, remove and re-add the integration with the new address.

## Testing and development

```bash
uv venv --python 3.13
uv pip install -r requirements-dev.txt
uv run pytest -q
uv run ruff check custom_components tests tools
uv run ruff format --check custom_components tests tools
```

Automated tests cover protocol validation, temperature limits, malformed telemetry, discovery/deduplication, disconnect/staleness, command formatting, light scaling, diagnostics redaction, config flow, real HA platform/entity registration, the both-sides action, and unload. Tests do not connect to hardware.

Hardware testing was performed on firmware 2.6.2 / inner board 2.6.1. See [the live test report](docs/live-test-report.md) for command-by-command results, the reboot recovery, and final state. A repeatable hardware check is available, **only when you intentionally want to change the bed and reboot it**:

```bash
uv run python tools/live_check.py \
  --host YOUR_BROKER_IP --serial YOUR_BED_SERIAL \
  --execute --report live-results.json
```

This check keeps targets within 10–45°C, skips fill and Wi-Fi erase, and attempts to restore the captured settings after testing. Run it with the bed initially healthy, `SYSTEM_ON`, and its targets already within that range. Physical LED output and reaching thermal equilibrium require observation beyond telemetry verification.

## Publishing

The repository includes a HACS manifest, versioned HA manifest, MIT license, and GitHub Actions checks. No release ZIP is needed; HACS installs the integration directory from the repository. Tag releases (for example `v0.1.0`) and update `manifest.json` for subsequent versions. Local notes and raw live reports are gitignored.

Protocol source: [original Reddit comment](https://www.reddit.com/r/Orion_Sleep/comments/1va4pe0/home_assistant_integration_for_orion_now_feature/p1kxqeu/). This is an unofficial community integration.
