# Live acceptance test — 2026-10-20

Broker: the user-provided anonymous LAN broker, TCP 1883. One Orion bed discovered. Control-box firmware **2.6.2**, inner-board firmware **2.6.1**.

Initial state: `SYSTEM_ON`, both sides enabled with **19°C** targets, quiet mode enabled, LED brightness 0 and idle color white `(255, 255, 255)`. All reported fault flags were zero and `safetyOk` was `"true"`.

Commands were plain UTF-8, non-retained, QoS 1. Unless noted below, each success was confirmed against the relevant changed state in **three fresh telemetry frames**. This verifies device-reported state, not physical observation or thermal equilibrium. No `FILL START` or `ERASE WIFI` command was sent.

| Command | Result |
| --- | --- |
| `SET LEFT 19.5` | Left target became 19.5°C, control enabled |
| `SET RIGHT 19.5` | Right target became 19.5°C, control enabled |
| `SET BOTH 19` | Both targets became 19°C, both enabled |
| `SET BOTH 19.5 19` | Independent left/right targets matched |
| `CLEAR LEFT` | Left control disabled; right remained enabled |
| `CLEAR RIGHT` | Right control disabled; left remained enabled (targets restored between tests) |
| `CLEAR BOTH` | Both disabled, `SYSTEM_OFF`, fan enable false |
| `SYSTEM ON` | Reached `SYSTEM_ON` after approximately **56.5 seconds**. Previously cleared side controls stayed disabled until SET was sent |
| `FAN QUIET OFF` | Quiet false; fan target PWM changed to 100 |
| `FAN QUIET ON` | Quiet true; fan target PWM changed to 70 |
| `IB RGB 32 64 96` | Idle RGB became `(32, 64, 96)`. Initial test checked the wrong fields and timed out; corrected test using the integration transport/parser verified three frames |
| `IB DIMMER 10` | Brightness became 10; output RGB was `(3, 6, 9)`; repeated successfully with integration transport |
| `REBOOT` | Device uptime reset from ~1,295 seconds to seconds. Brief initialization safety faults interrupted the original test script. Read-only follow-up showed healthy `SYSTEM_OFF`, both controls disabled, default targets 27.5°C |
| `FILL START` | **Excluded; not sent** |
| `ERASE WIFI` | **Excluded; not sent** |

## Reboot recovery and restoration

The initial test's automatic restoration attempted `SET BOTH 19 19` too early during reboot and stopped on startup fault telemetry. A subsequent fresh read showed all faults clear and the firmware's default 27.5°C targets, both disabled. After this healthy read, an explicit restoration sent `SET BOTH 19 19`, `SYSTEM ON`, `FAN QUIET ON`, `IB DIMMER 0`, and `IB RGB 255 255 255`.

Final restoration was confirmed over three fresh frames:

- `SYSTEM_ON`
- Left and right targets **19°C**, both controls enabled
- Quiet mode true; fan RPM approximately 1320 / 1290
- LED brightness **0**, idle RGB **255 / 255 / 255**
- `safetyOk: "true"`, all error flags zero

The firmware selected 27.5°C during reboot; no test command requested that temperature. Every temperature command actually sent used **19°C or 19.5°C**. Integration and test tooling now reject outgoing temperatures outside **17–24°C**.

The corrected LED test used the actual integration's `OrionMQTT` transport and `parse_status` parser against the real broker, then restored brightness 0 and idle white, both confirmed over three frames. The transport disconnected cleanly afterward.

## Scope

- All command forms listed in the Reddit comment were exercised except the two explicitly excluded commands.
- Hardware tests used one bed. Multi-device discovery and deduplication are exercised with synthetic telemetry in automated tests.
- Occupancy transitions, physical LED appearance, thermal equilibrium, full device temperature limits, cloud/app compatibility, and schedules were not validated.
- The real HA loader/config-flow/platform lifecycle is covered by a local automated Core 2026.2.3 test. Installation on the user's running HA instance and HACS download from a published GitHub repository still need to be performed after publishing.
