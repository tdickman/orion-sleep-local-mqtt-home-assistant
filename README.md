# Orion Sleep Local MQTT

A HACS-installable Home Assistant custom integration for **local Orion Sleep bed control**. Enter your MQTT broker's IP address; all beds publishing `orion/<serial>/out` are discovered automatically, including beds that connect later.

This integration maintains its **own MQTT connection**. Home Assistant's existing MQTT integration and broker can continue running independently. No Orion account or cloud credentials are needed.

## Requirements

- Home Assistant **2026.2 or newer** (tested against Core 2026.2.3).
- HACS, or manual access to Home Assistant's `custom_components` directory.
- An anonymous MQTT broker reachable by Home Assistant and the bed, normally TCP port `1883`.
- A local DNS override for `dev.orionbed.com` pointing to the broker. Follow the Docker and router setup below before adding the integration.

Keep the anonymous broker on your LAN. You do not need to change or weaken your existing authenticated Home Assistant MQTT broker.

## 1. Run a restricted MQTT broker in Docker

**Allow Home Assistant and the Orion bed(s), then block everyone else.** Allowing only Home Assistant would prevent the bed from connecting: both HA and the bed are MQTT clients. The bed publishes telemetry to the broker and subscribes there for commands.

This example uses **Docker Engine on a native Linux host**, Docker Compose, and a host firewall managed with **nftables**. It uses host networking so MQTT traffic goes through the host's **INPUT** firewall path. Do not substitute Docker `ports:` publishing and assume these INPUT rules still protect it: Docker bridge forwarding can bypass ordinary host INPUT/UFW rules. Docker Desktop and hosts managed by UFW/firewalld need equivalent rules in their own firewall/network configuration.

### Reserve the addresses

Create DHCP reservations in your router for these devices. Replace the example addresses throughout this guide with your own:

| Device | Example address | Purpose |
| --- | --- | --- |
| Docker host / Orion MQTT broker | `192.168.64.5` | Destination for TCP 1883 and the DNS override |
| Home Assistant | `192.168.64.61` | Allowed MQTT client |
| Orion bed | `192.168.64.80` | Allowed MQTT client; **example only**, find your bed's actual lease |
| Router / local DNS resolver | `192.168.64.1` | Answers the bed's DNS requests |

Use the source IP the broker actually sees for Home Assistant. For a separate HA OS machine this is normally its LAN IP; containerized or NATed deployments can differ. Add each additional bed's reserved IP individually. A dedicated IoT VLAN with equivalent network ACLs provides stronger isolation than IP filtering on a shared LAN. This transport is anonymous, unencrypted MQTT; an IP allowlist restricts network access but is not client authentication.

### Create the broker files

Create a directory such as `orion-mqtt` and place these three files in it. Keep the files readable by the container's non-root UID (for example, directory mode `755` and file mode `644`).

**`compose.yaml`**:

```yaml
services:
  mqtt:
    image: eclipse-mosquitto:2
    network_mode: host
    user: "1883:1883"
    read_only: true
    cap_drop:
      - ALL
    security_opt:
      - no-new-privileges:true
    restart: always
    pids_limit: 64
    mem_limit: 128m
    volumes:
      - ./mosquitto.conf:/mosquitto/config/mosquitto.conf:ro
      - ./acl:/mosquitto/config/acl:ro
    logging:
      driver: json-file
      options:
        max-size: "10m"
        max-file: "3"
```

**`mosquitto.conf`** — bind only to the Docker host's LAN IPv4 address, not all interfaces:

```conf
listener 1883 192.168.64.5
socket_domain ipv4
allow_anonymous true
acl_file /mosquitto/config/acl
persistence false
log_dest stdout
connection_messages true
```

**`acl`** — permit only Orion command and telemetry topics:

```conf
topic readwrite orion/+/in
topic readwrite orion/+/out
```

Both allowed clients share these anonymous topic permissions; the ACL does not distinguish HA from a bed. `persistence false` avoids a writable broker database; fresh telemetry repopulates HA after reconnect. Do not publish retained commands. The configuration exposes no IPv6, WebSocket, or administrative listener, mounts no Docker socket, and requires no privileged container. Keep the Mosquitto image updated with `docker compose pull` followed by `docker compose up -d`.

Port 1883 on the selected host address must be free. If another broker already owns it, use a separate host/IP for Orion; changing the port alone will not work with the DNS override.

### Apply the IP allowlist before starting the container

On the **Docker host**, save the following as `/etc/nftables.d/orion-mqtt.nft` (create that directory if needed). Replace the broker, HA, and bed addresses first:

```nft
table inet orion_mqtt {
    chain input {
        type filter hook input priority -10; policy accept;
        ip daddr 192.168.64.5 tcp dport 1883 ip saddr != { 192.168.64.61, 192.168.64.80 } counter drop
    }
}
```

This drops connections to this broker's port 1883 from every source except HA and the bed. It leaves unrelated ports alone. Approved sources still need to be allowed by any other existing host or inter-VLAN firewall policy; this rule does not override a separate default-deny firewall.

Check and load the new table:

```bash
sudo nft --check --file /etc/nftables.d/orion-mqtt.nft
sudo nft --file /etc/nftables.d/orion-mqtt.nft
sudo nft list table inet orion_mqtt
```

**Make the restriction survive reboot before enabling automatic container startup.** On a system using `/etc/nftables.conf`, add this include to its existing ruleset without replacing the other rules:

```nft
include "/etc/nftables.d/orion-mqtt.nft"
```

Ensure the include is after any `flush ruleset` statement. Validate the complete file with `sudo nft --check --file /etc/nftables.conf`, and enable the firewall's boot service with `sudo systemctl enable nftables`. Do not flush a running host's entire ruleset to apply this example; it may contain Docker or other application rules.

For a systemd-managed Docker Engine, use `sudo systemctl edit docker.service` to add the following dependency so Docker starts only after the persistent firewall service has started successfully:

```ini
[Unit]
Requires=nftables.service
After=nftables.service
```

Run `sudo systemctl daemon-reload`. The table loaded above protects the current session; this dependency orders future Docker starts. Confirm the persistent rules and Docker startup after a planned host reboot. If your distribution uses another firewall manager, configure the equivalent persistent allowlist and startup ordering there instead of enabling a competing firewall service. Do not add a WAN port-forward or UPnP mapping for MQTT.

### Start and verify

From the directory containing `compose.yaml`:

```bash
docker compose config
docker compose up -d
docker compose logs --tail=50 mqtt
```

After completing the DNS step below, verify both sides of the restriction:

- **From the HA host/network context:** TCP 1883 should connect. If Mosquitto client tools are installed, `mosquitto_sub -h 192.168.64.5 -p 1883 -t 'orion/+/out' -C 1 -W 15` should receive a fresh status message once the bed connects. Alternatively, configure this integration in HA and confirm discovery.
- **From a different, non-allowlisted LAN device:** `nc -vz -w 3 192.168.64.5 1883` should time out. Inspect `sudo nft list table inet orion_mqtt` on the Docker host to see the drop counter increase.
- **In broker logs:** verify connections from the reserved bed and HA addresses. Connection alone does not prove telemetry is flowing.

The broker host itself is not automatically allowlisted as an MQTT client. Run the positive check from HA, not from a random laptop or the broker host. Host administrators retain control of the firewall and container.

## 2. Override the bed's DNS in your router

The bed normally connects to **`dev.orionbed.com`**. The local override makes that name resolve to your Docker host, directing its MQTT connection to your broker without changing the bed's Wi-Fi configuration.

1. Open your router's administration page and find **Local DNS**, **DNS host overrides**, **DNS records**, or **DNS rewrites**. This must change DNS answers; a DHCP reservation or a device display name alone is not enough.
2. Add an exact **A record / host override**:

   | Setting | Value |
   | --- | --- |
   | Hostname / fully qualified name | `dev.orionbed.com` |
   | IPv4 address | `192.168.64.5` (your MQTT Docker host) |
   | Port | None — DNS maps names to addresses, not ports |

   If the UI splits host and domain, use **host `dev`**, **domain `orionbed.com`**. Override only this hostname, not the entire `orionbed.com` domain.
3. Apply the change and ensure the **bed's network/VLAN receives this resolver through DHCP**. All DNS servers advertised to the bed must honor the override; a public secondary resolver can bypass it. If the router cannot create overrides, use a local resolver such as Pi-hole or AdGuard Home and advertise that resolver through DHCP instead.
4. Check the answer from a device using the same DNS path as the bed (replace `192.168.64.1` with your local resolver):

   ```bash
   nslookup dev.orionbed.com 192.168.64.1
   ```

   The returned **address for `dev.orionbed.com`** should be `192.168.64.5`, not its public address. Also check `nslookup -type=AAAA dev.orionbed.com 192.168.64.1`: there should be no public IPv6 answer that lets the bed bypass the IPv4 override. Configure the local resolver to suppress that hostname's AAAA response if necessary. If DNS-rebinding protection blocks the private answer, add a narrow exception for this exact hostname.
5. With the broker and firewall already running, **power-cycle/restart the bed** so it reconnects and refreshes DNS. Do not erase its Wi-Fi settings. Restarting may reset its setpoints and leave its controls disabled; select your desired temperatures afterward.
6. Check `docker compose logs --tail=50 mqtt` for the bed's connection, then confirm fresh `orion/<serial>/out` messages or automatic discovery in Home Assistant. If it still connects elsewhere, inspect the bed's DHCP DNS settings and resolver cache, and check whether its VLAN uses a different DNS server.

If HA and the bed are on separate VLANs, allow each to initiate TCP connections to the broker on port 1883, with return traffic allowed. Also allow the bed to reach its local DNS resolver over UDP/TCP 53. Router firewall rules alone usually do not filter devices communicating directly on the same LAN, which is why the broker host allowlist matters.

App/cloud reporting and schedules with this override have not been verified. To return to the original endpoint, remove the DNS override (and any hostname-specific AAAA override), apply the DNS change, and restart the bed to refresh its connection.

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

**Side orientation:** Left and right are from your perspective while lying on your back in the bed, with your head at the headboard. The **left side is on your right when standing at the foot of the bed facing the headboard**. This applies to the side-specific controls and sensors.

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
| Left / Right presence binary sensors | Enabled by default; community-documented Sleepace presence mapping |
| Left / Right heart rate sensors | Local Sleepace readings in BPM; observational mapping |
| Left / Right breathing rate sensors | Local Sleepace readings in breaths/min; observational mapping |
| Left / Right sleep sensor status | Diagnostic status and per-side freshness, including initializing, movement, signal loss, and stale data |

**Every outgoing temperature is limited to 10–45°C (50–113°F)**, including service calls, matching the setpoint range reported for the Orion mobile app. HA may display converted Fahrenheit values if configured that way; MQTT commands and the custom action below always use Celsius.

Setting a climate target enables that side and requests `SYSTEM ON` if the bed is off. The firmware chooses heating or cooling; undocumented TEC mode numbers are not translated into an HVAC action. Turning off one climate sends `CLEAR LEFT` or `CLEAR RIGHT`; the other side is left alone. Turning on a climate reuses its reported target; if that target is outside 10–45°C, set a valid temperature first.

Startup can take about a minute. While `PENDING_ON`, wait instead of repeatedly starting the bed. Entity state comes from telemetry, never merely from a broker acknowledgement.

### Command confirmation and automation timing

Starting in **0.1.5**, commands to each bed are serialized **until fresh telemetry confirms the requested state**, not just until the broker acknowledges receipt. Two automations can request changes at nearly the same time: the next command to that bed waits for confirmation of the first. Different beds can still be controlled independently.

| Command | Telemetry confirmation | Timeout after broker acknowledgement |
| --- | --- | --- |
| Set temperature | Requested side setpoint(s) match and controls are enabled | 15 seconds |
| Turn off one side | That side's control is disabled | 15 seconds |
| Turn off both sides | Both controls are disabled and system is `SYSTEM_OFF` | 15 seconds |
| Quiet mode | Reported quiet mode matches | 15 seconds |
| LED color / brightness | Reported idle RGB / brightness matches | 15 seconds per command |
| Start system | System reaches `SYSTEM_ON`; `PENDING_ON` is not sufficient | 240 seconds |
| Reboot | Uptime decreases and reported safety/fault status is healthy | 120 seconds |

Temperature confirmation means the bed **accepted the target**, not that the water has physically reached that temperature. Firmware reports setpoints to two decimal places, so confirmation allows a rounding difference of up to **0.005°C** (plus floating-point epsilon). For example, a Fahrenheit-converted command of `21.3889°C` is confirmed by telemetry reporting `21.39°C`. For a both-sides command, both targets must match in the same status frame. Previously cached state, retained MQTT messages, and a command echo do not count as confirmation.

A temperature action that starts an off bed waits for the target confirmation and then system startup, so its completion can take about a minute. The lock covers the whole action, including startup and multi-command LED changes. Commands queued behind another action wait for it to finish; their own confirmation timeout starts after their command is published and acknowledged. The broker acknowledgement itself has a separate five-second timeout.

If confirmation times out, the action raises an error in Home Assistant and stops the remaining commands in that action. It releases the lock so later actions can proceed. A broker disconnect or integration unload also fails pending confirmation; commands are not silently replayed by the integration. **A timeout means the outcome is unknown, not necessarily that the bed ignored the command.** Check current device state before retrying. MQTT QoS 1 itself can redeliver an in-flight message.

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

### RGB LED color and dimmer

Each bed has one **LED light** entity with both a color picker and brightness control. Open the LED light's more-info dialog to select RGB color and adjust brightness, or add the light entity to your dashboard.

Set color and brightness together with Home Assistant's standard light action (replace the example entity ID with your bed's entity):

```yaml
action: light.turn_on
target:
  entity_id: light.orion_YOUR_SERIAL_led
data:
  rgb_color: [32, 64, 96]
  brightness_pct: 10
```

To adjust just the brightness, preserving the color:

```yaml
action: light.turn_on
target:
  entity_id: light.orion_YOUR_SERIAL_led
data:
  brightness_pct: 25
```

Set brightness to `0`, or call `light.turn_off`, to turn the LEDs off. Setting brightness above zero uses the existing color; if that color is black (`[0, 0, 0]`), choose a non-black color to produce visible light. `light.turn_on` without brightness uses the current nonzero brightness or 100% if the light was off.

RGB components are **0–255** and use `IB RGB <R> <G> <B>`. Brightness uses `IB DIMMER <0-100>`. The light entity converts HA's 0–255 brightness scale to the bed's integer percentage scale. State comes from fresh telemetry rather than assuming the command succeeded.

Version **0.1.4** removes the duplicate LED dimmer number entity automatically on upgrade. The LED light keeps its existing entity ID. Update any dashboards or automations that referenced the old number entity to use the light's brightness control instead.

### Left and right presence

Each bed exposes **Left presence** and **Right presence** binary sensors, enabled by default. They use Home Assistant's occupancy device class (`on` means detected; `off` means clear) and the [Reddit author's mapping](https://www.reddit.com/r/Orion_Sleep/comments/1va4pe0/home_assistant_integration_for_orion_now_feature/p1kxqeu/), refined by the author's [status-code documentation](https://www.reddit.com/r/Orion_Sleep/comments/1va4pe0/home_assistant_integration_for_orion_now_feature/phz4axj/):

- Left uses `sensors.sensor1[17]`; right uses `sensors.sensor2[17]`.
- Indexes are zero-based: index 17 is the **18th array value**. A value of `5` means not present; known occupied states `0`, `2`, `3`, `4`, `6`, and `7` mean present.
- Starting in **0.1.8**, `1` (initializing), `255` (invalid), missing status, and unrecognized status codes produce **unknown**, rather than a false presence or absence.
- Missing/invalid sensor timestamps, frozen sensor data, or loss of fresh bed telemetry make the entities **unavailable**. See the per-side freshness rules below.

Version **0.1.3** automatically enables presence entities previously disabled by the integration's experimental default. Existing unique IDs and entity IDs are preserved; manually disabled entities remain disabled. If necessary, find them under **Settings → Devices & services → Orion Sleep Local MQTT → your bed → Entities** and enable them. Older entity IDs may still contain `occupancy_experimental`.

The mapping and left/right assignment are community documented. Empty-bed captures reported `5` on both sides. An occupied capture with one person on the left reported presence on **both** sensors: the sensing areas can overlap, so presence on both sides does not necessarily mean two people are in bed.

### Heart rate and breathing rate

Version **0.1.7** adds four enabled-by-default local sensors: **Left heart rate**, **Right heart rate**, **Left breathing rate**, and **Right breathing rate**. No cloud connection is required. Each uses the corresponding Sleepace array (`sensor1` for left, `sensor2` for right):

- **Index 15:** heart rate in BPM. An occupied-bed capture closely matched the user's approximate reference heart rate.
- **Index 16:** breathing rate in breaths/min. Observed values were consistent with resting breathing, but were not independently measured against a respiratory reference.
- **Index 17:** presence/status, as described above.

The mappings are supported by our occupied-bed observations and the Reddit author's firmware/SDK-derived documentation, but are not a manufacturer-verified frame specification. The earlier hypothesis that index 16 was heart rate was rejected by the occupied capture. Readings of `0` or `255`, malformed/missing values, or a side reporting no presence become **unknown** rather than zero. Both vitals require a valid, occupied status; initializing, invalid, or unrecognized status also makes them unknown. Starting in **0.1.8**, a heart-signal-loss status suppresses heart rate, and a reported breathing-pause status suppresses breathing rate. Readings are not smoothed or combined across sides.

**One person can be picked up by both sensors**, as observed during testing. Left/right identifies the sensor location, not a guaranteed separate person's pulse. Movement may affect readings; these are not medical measurements. Heart rate, breathing rate, occupancy, and raw sensor arrays are omitted from integration diagnostics. See [the sensor investigation notes](docs/sensor-investigation.md) for evidence and remaining validation limits.

### Sleep sensor freshness and status diagnostics

The bed can keep publishing MQTT status messages even if an individual Sleepace sensor has stopped updating. Starting in **0.1.8**, the integration independently tracks each sensor's internal time bytes at indexes **11–14**. They were observed changing approximately once per second on firmware 2.6.2.

- On discovery, restart, or broker reconnect, the first clock value is a baseline; sleep readings remain unavailable until that sensor's clock changes, normally about a second later.
- If the clock then stops changing for **15 seconds** (since version **0.1.9**), only that side's presence, heart rate, and breathing rate become unavailable. Continuing bed MQTT messages or changes to measurement values do not refresh the sleep-sensor timer. The timeout measures elapsed local monotonic time since the internal timestamp last changed; it does not compare the sensor's clock value to wall-clock time.
- Any valid clock change restores freshness, including changes caused by a sensor reset or counter wrap. The internal value is not interpreted as wall-clock time.
- Missing/malformed time bytes invalidate freshness immediately. A valid baseline followed by a clock change is required to recover.
- Temperature controls, lights, and the other side's sleep readings remain independent. Missing bed telemetry still makes the whole bed unavailable after 30 seconds.

Each side has a diagnostic **Sleep sensor status** entity. While the bed is online it reports:

| State | Meaning |
| --- | --- |
| `normal` | Firmware status `0`: normal/in bed |
| `initializing` | `1`: initializing or reacquiring the signal |
| `breathing_pause_reported` | `2`: device-reported breathing-pause event; breathing rate is unknown |
| `heart_signal_loss` | `3`: device-reported heart-signal stop/loss; heart rate is unknown |
| `body_movement` | `4`: body movement |
| `not_occupied` | `5`: left bed/not occupied |
| `turned_over` | `6`: turned over |
| `temporary_movement` | `7`: temporary movement |
| `invalid` | `255`: invalid/uninitialized |
| `unknown_status` | A valid byte with an undocumented status code |
| `waiting_for_update` | Clock baseline received; waiting for evidence of sensor activity |
| `missing_data` | No valid internal sensor clock is available |
| `stale` | No clock change for 15 seconds, even if bed MQTT telemetry continues |

The diagnostic includes a `status_code` attribute with the latest reported raw code (or null if missing). Freshness states take precedence over the reported status label. A malformed status with a live clock appears as unknown. These labels describe firmware observations, **not diagnoses of apnea or a cardiac event**. Downloadable diagnostics continue to exclude sleep status, sensor clocks, and biometric readings.

### LED telemetry and reboot

- LED RGB uses `led.idle_r/g/b`, the requested color. Firmware `led.r/g/b` are brightness-scaled output values. Brightness maps between HA's 0–255 and Orion's 0–100.
- **Reboot resets targets to firmware defaults (observed 27.5°C) and disables both sides.** The integration does not automatically power on or restore targets after a user-triggered reboot. Choose valid 10–45°C targets to resume.
- Fill and Wi-Fi erasure are deliberately not exposed or accepted by the command validator. There is no unrestricted raw-command action.

## Reliability and diagnostics

- Wildcard subscription `orion/+/out`, reconnect with backoff, and resubscribe after reconnect.
- Stable serial-based device and entity IDs. Known serials survive restarts; offline beds remain visible as unavailable.
- Mark unavailable after 30 seconds without fresh telemetry or on broker disconnect. Retained messages do not establish availability.
- Independently detect frozen sleep sensors from their internal timestamps; live bed messages cannot keep stale presence or biometric readings available.
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

Automated tests cover protocol validation, temperature limits, malformed telemetry, discovery/deduplication, disconnect/staleness, command formatting, light scaling, diagnostics redaction, config flow, real HA platform/entity registration, the both-sides action, and unload. Confirmation tests exercise simultaneous side requests, fresh-state matching, partial both-side updates, startup, timeouts, cancellation, disconnect/unload, and telemetry arriving before the broker acknowledgement. Tests do not connect to hardware.

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
