# Verified interfaces

Evidence reviewed on **16–22 October 2026**; application version **1.9.0**. Model descriptions and the earlier conversation are not treated as proof of a working endpoint.

For an implementation-level overview with diagrams and request/register tables, see [COMMUNICATION.md](COMMUNICATION.md).

## SMARTFOX Pro 2 and Charger Pro 1

The user confirmed Pro 2, Charger Pro at point 1 and firmware **EM3 00.01.16.08**. The firmware was also read from the local device display at **192.168.0.81**.

Primary sources:

- [SMARTFOX manuals/downloads](https://smartfox.at/downloads/anleitungen/)
- [Register list v22e / 00.01.03.10](https://smartfox.at/wp-content/uploads/2022/12/Modbus-Register-SMARTFOX-Pro-SMARTFOX-Pro-2-v22e-00.01.03.10.xlsx)
- [Charger manual](https://smartfox.at/wp-content/uploads/2022/12/Anleitung-Installations-Bedienungsanleitung-SMARTFOX-Pro-Charger.pdf), printed pages 55 and 60
- [EM2/EM3 manufacturer information](https://smartfox.de/neues-software-update/)

Reviewed register-list SHA-256: `ADCE7FC601E46D72E372FC1DE8E8126780328A787B27A125368663908BBB0058`.

### Modbus remains read-only

Use TCP **502**, Unit ID **1**, function **03**. The manufacturer list uses an offset of −1. Direct requested PDU addresses remain raw observations:

| PDU address | Requested label | Evidence limitation |
|---:|---|---|
| 40999 | Total energy | Older list describes grid energy; current charger semantics unconfirmed. |
| 41015 | Charging power | Older list describes daily energy; not verified power for this firmware. |
| 42111 | Charge mode | Absent from the older list; an observed value of 62 is not a verified mode code. |

The older optional profile uses **41599** (four words, energy), **41605** (two words, power) and **41607** (one word, mode) for Charger 1. A value of 2 was observed at the latter on newer firmware, while the old document describes 0/1. Physical interpretation requires explicit firmware/word-order confirmation. There is no Modbus write implementation or raw-PDU write route.

### Local vendor web control

The installed vendor UI reads `/values.xml`. Verified fields include `version`, `hidCcMode1`, `cc1Power` with an explicit kW unit, `hidCcSt1`, `hidCc1Info`, `hidCcConnTimeout1` and `hidCcControlCurrent`.

| Vendor charger mode | Code |
|---|---:|
| A | 0 |
| M | 1 |
| A+ | 2 |
| OFF | 3 |
| M+ | 4 |

For Charger 1 the vendor UI uses `/setswcc.cgi?num=0&mode=<code>&value=100`. This was checked in the installed vendor HTML/JavaScript; no public manufacturer CGI API contract was found. It does not authorize Modbus writes.

The user explicitly authorized local web control on 16 October 2026. The adapter is limited to the specific private address/profile, Charger 1, Off/A+/M+ and the requested heating outputs. Without opt-in the class blocks writes. It uses no redirects/proxy and validates firmware/XML size/schema. Every mode change requires a matching readback. A real A+ → M+ → A+ cycle was read back successfully; vehicle start/stop and new heating/Off operations used in automated tests are simulated unless separately reported by the user.

Status **2** is ready, **3** is charging. Info **2/8/10** represents active charging permission; **14** requests authorization. Connection counters ≤3 are treated as disconnected. After M+ the adapter requires three consecutive seconds of observed readiness with at least 6 A offered current, within fifteen seconds total. A not-ready sample resets this settling window; a brief ready sample at the deadline cannot trigger start. Readiness is permission, not proof of vehicle power draw, and is not an interpretation of register 42111.

The installed vendor JavaScript defines `CC_SWITCH = 6` and maps `hidCc1Info` to that phase-switch state. Its display uses the optional `hidCc1TempTime` as remaining seconds. CitiFoxCharger shows it only with a fresh connection and info 6; absent or invalid seconds produce a waiting message without an invented countdown. `hidCc1Phase` accepts only the vendor's 1/3-phase indication. It is not a measurement of how many phases the Citigo actually draws. `hidCc1WaitTime` is not used as a phase timer.

Verified phase switching returns promptly to the backend worker so the UI can show progress and accept Stop. The app sends one deferred vehicle start only after the phase flag clears and a newer local report confirms three seconds of readiness. The app imposes a fixed six-minute preparation limit and secures Off on timeout, lost mode/authorization or vehicle access. Stop and restart cancel deferred starts. Phase preparation does not bypass the shared vehicle polling and quota controls. Live 0 kW takes precedence over a raw wallbox “Charging” label; no charging animation or duration estimate is shown during verified phase switching. The [manufacturer integration guide](https://www.smartfox.at/wp-content/uploads/2022/12/Anleitung-Einbindung-Ansteuerung-SMARTFOX-Car-Charger-KEBA-ABB-BMW-EATON-Ladestationen.pdf) describes safety checks that can make phase switching take several minutes; the installed firmware signal is the basis for the app state.

A+ combines surplus operation with the SMARTFOX weekly schedule. Vehicle schedules are a separate layer. Existing M+ energy/time limits and fallback remain active and can end M+ before the percentage target. The app does not rewrite these settings or vehicle profiles.

### Energy and heating outputs

House power is `hidProduction` (kW) + `hidPower` (W / 1000) − `batteryPower` (kW). Positive grid power is import. `analogOutPower`, `analogOutPercent`, `analogOutTemp`, sensor assignments and relay status/info codes come from the same XML read. Sensor 0 is unassigned; 850 °C is a missing-value sentinel. Only finite, correctly unit-labelled, bounded values are accepted. Relay electrical power is not estimated.

Analog heater mode `hidAoutMode`: 0=Off, 1=Manual, 2=Automatic. Commands are `/setswaout.cgi?mode=0`, `?mode=2` and `?mode=1&power=<existing percentage>`. Missing manual percentage blocks Manual.

Relay mode x=Off, m=Manual, 0/1=Automatic. Automatic uses `/setswrel.cgi?rel=N&state=0`. Manual/Off first cancels a timer with `?rel=N&state=3&duration=0`, then uses state 1/2. N is fixed to relay 1 for Heater 2 and relay 2 for Hot water. Assignment checks retain the installed vendor names and types: `Heizstab` on the analog output, `Heizstab 2` type 0 on relay 1 and `Warmwasser` type 2 on relay 2. These identifiers are protocol matching data; the dashboard labels can be English or German. Temperature limits and schedules are not edited.

The installed vendor JavaScript maps relay modes `m` and `x` explicitly and treats automatic switching values `0`/`1` as Automatic; the parser accepts both without treating arbitrary future values as valid. In `checkAoutState`, manual mode and forced/status cases prioritize the percentage and hide the unreliable power estimate (with the vendor's relay-type-4 exception). CitiFoxCharger follows this display rule and shows a positive reported percentage when a zero estimate would imply inactivity. It does not substitute the manual setpoint for actual actuation. [SMARTFOX's explanation of missing manual boiler curves](https://smartfox.atlassian.net/wiki/spaces/SMR/pages/228851771/Boilerkurve%2Bfehlt%2Bim%2Bmanuellen%2BModus%2Bin%2Bder%2BSchaltzeit) confirms that 100% actuation is not direct consumption measurement. No heater command was sent during the 19 October inspection; current idle XML and cached vendor code were read only.

## MyŠkoda Public API and Citigo compatibility

Primary sources:

- [Official documentation](https://public.api.connect.skoda-auto.cz/docs)
- [OpenAPI 1.1.0](https://public.api.connect.skoda-auto.cz/docs/myskoda-public-api.yaml)
- [Changelog](https://public.api.connect.skoda-auto.cz/docs/changelog)
- [API key management](https://go.skoda.eu/api-keys)
- [Citigo remote charging description](https://www.skoda-auto.com/models/layers/layers/citigoe-iv-connectivity)
- [Manufacturer mobile-network shutdown information](https://www.skoda-auto.com/_doc/1b32a97a-fce0-4d19-a832-0c362b5f7c78)

Reviewed contract SHA-256: `FB0514BF3E289E4BFB634F13DD5B7DFD24EB06CBAB829CCF6A6FBB831ECF4CFC`.

| Purpose | Verified contract |
|---|---|
| Authentication | `X-API-Key`, entered locally by the user |
| Vehicle data/capabilities | `GET /api/v1/vehicles/{vin}?include=charging,operations` |
| SOC | `vehicle.charging.status.battery.stateOfChargeInPercent` |
| Vehicle capture time | `vehicle.charging.carCapturedTimestamp` |
| Commands | `vehicle.operations[].name`, including `startCharging` / `stopCharging` |
| Start | `POST /api/v1/vehicles/{vin}/charging/start`, empty body, expected 202 |
| Stop | `POST /api/v1/vehicles/{vin}/charging/stop`, empty body, expected 202 |

The adapter uses the fixed official HTTPS origin with certificate validation, no redirects and no environment proxy. There is no embedded account password or S-PIN. Keys stay in RAM or optional Windows per-user DPAPI and are never returned to the frontend or logged.

Capabilities are discovered per VIN. The specific Citigo returned SOC and start/stop operations, and the user confirmed real charging after a start. This is evidence for that vehicle/account, not general compatibility for every Citigo or every remote operation. Equipment and mobile connectivity can affect availability. HTTP 202 confirms acceptance, not physical completion. Only newer matching vehicle telemetry confirms the outcome. Missing or unknown derived states are not interpreted as disconnection.

### Rate limits and freshness

The reviewed documentation describes **20 requests per hour and VIN**; response `RateLimit-*` and `Retry-After` headers are authoritative. Reads and commands share quota. Two calls are reserved for commands; spare reads are spaced according to the remaining window. Manual refresh has at least 60 seconds spacing and respects server cooldown and reserve. Reading the cloud does not force a new in-car measurement.

No verified public wake-up or force-refresh endpoint was found. The [community MyŠkoda client](https://github.com/skodaconnect/myskoda/blob/main/myskoda/rest_api.py) has a wake-up route with different authentication and daily limits. The [Home Assistant integration](https://github.com/skodaconnect/homeassistant-myskoda/blob/main/docs/entities.md#wake-up-car) also warns about vehicle-dependent limits. Neither proves support for this Citigo or availability using the public API key; no community wake-up was attempted.

The public contract contains `setChargingLimit`, but support and permitted steps for this Citigo are unconfirmed. Typical documented steps of ten percent do not prove that a native 82% limit can be programmed. Version 1.9.0 sends an exact requested value but withholds Start until a matching newer vehicle readback; actual Citigo support remains unconfirmed.

### Legacy local target monitoring (sessions without a verified vehicle limit)

`POST /api/charging/target` changes only the local target, never repeats a start and does not alter the native vehicle limit. A valid old SOC already at/above target authorizes a conservative stop, not a start. Both manual and automatic stopping request verified local OFF before any vehicle read or cloud request, even when cloud access is blocked. The temporary Off hold survives restart and is released only after newer stopped vehicle telemetry. Future reports beyond the allowed five-second release tolerance cannot release it. A+ is not treated as a power-off guarantee. `charging.can_stop` exposes local stop availability independently of cloud-only `controls.stop`.

The explicit older-SOC start exception requires a known capture time ≤24 hours old, a successful API read ≤five minutes old and wallbox readiness/no power ≤30 seconds old. It does not falsify freshness. A persistent five-minute post-start deadline requires a new SOC; later loss of data also has bounded recovery and a maximum 15-minute report age. Uncertain starts are never replayed. Stops require newer charging telemetry for bounded retries; a newer stopped report can complete without another command.

Estimated minutes are `ceil((32.3 * (target - SOC) / 100 / (wallbox_kW * 0.9) * 60) / 5) * 5`. The [32.3 kWh usable capacity](https://www.skoda-storyboard.com/en/press-kits/skoda-citigo%E1%B5%89-iv-skoda-superb-iv-press-kit/skoda-citigo%E1%B5%89-iv-zero-emission-range-of-up-to-260-km/) is a manufacturer figure; 90% efficiency is an assumption. Fresh SOC and power ≤30 seconds old are required. Estimates do not control charging or modify displayed SOC.

## Process control and Windows background operation

Stop/Restart use loopback product checks, session and CSRF, with no proxy/redirects. They do not kill arbitrary Python processes. Restart requires saved credentials, persists session/quota state and releases the instance lock/worker/power handles before restarting the same profile. A new instance ID confirms restart. The old default profile remains in place for existing installations.

The [Brave Windows flag documentation](https://support.brave.app/hc/en-us/articles/360044860011-How-Do-I-Use-Command-Line-Flags-in-Brave) and [Chromium source](https://chromium.googlesource.com/chromium/src.git/+/2eee6cb90276be4e16fe7f2cf898ec558863a2d1/content/public/common/content_switches.cc) support full-screen startup. Existing browser processes may ignore flags; F11 is available.

Background launch uses a detached Windows process with CREATE_NO_WINDOW, redirected streams and a fixed working folder. No service or startup task is installed. [PowerCreateRequest](https://learn.microsoft.com/en-us/windows/win32/api/winbase/nf-winbase-powercreaterequest) and [PowerSetRequest](https://learn.microsoft.com/en-us/windows/win32/api/winbase/nf-winbase-powersetrequest) request SystemRequired and ExecutionRequired, not DisplayRequired or AwayModeRequired. Handles are released after monitoring and at shutdown. Modern Standby/battery policy and explicit sleep can end execution; the tablet must be checked on mains power.

## Optional HTTPS network listener

The optional listener shares the same service and monitor. It binds a concrete RFC1918 IPv4, normally port 8766. The access policy object exists in both password and explicitly passwordless mode, so neither grants local administrator privileges. Existing configurations default to password authentication. Passwordless mode is selected only by an explicit `authentication: "none"` configuration; malformed authentication never silently disables protection.

Password mode uses HTTP Basic only inside TLS, PBKDF2-HMAC-SHA256 with 600000 iterations and a 128-bit salt, per-client throttling and a bounded cache containing only hashed authentication headers. Both modes enforce Host, HTTPS Origin, same-site fetch restrictions, HttpOnly/SameSiteStrict/Secure session cookies and CSRF. Allowed network mutations are combined charging, target, refresh and the requested outputs. Key/device configuration, generic vehicle commands and backend administration remain local-only.

Certificates use Windows/.NET [CertificateRequest](https://learn.microsoft.com/en-us/dotnet/api/system.security.cryptography.x509certificates.certificaterequest?view=netframework-4.8.1): SHA256/RSA2048, IP SAN, server EKU, key usage, basic constraints and subject/authority identifiers. The leaf private key is DPAPI encrypted; a short-lived ACL-protected file is removed after TLS loading. The CA signing key is not saved. No trust store is modified automatically. The separate administrator-run firewall step allows only the Python path, tablet IP, HTTPS port, LocalSubnet and Private profile. See [NETWORK.md](NETWORK.md).


### Read-only native charging settings (1.8.12)

The [official OpenAPI schema](https://public.api.connect.skoda-auto.cz/docs/myskoda-public-api.yaml), inspected on 22 October 2026, documents `charging.settings.targetStateOfChargeInPercent`, `preferredChargeMode`, `availableChargeModes`, battery-care fields and AC current limit. The existing GET now retains only validated percentages/current values, known mode enums and presence flags for `setChargeMode`/`setChargingLimit` in `vehicle.operations`. No additional request is needed. The schema documents `PUT /api/v1/vehicles/{vin}/charging/mode`, but this application does not call it; per-vehicle support, actual behavior and restoration requirements must be established first. Existing vehicle plans and limits remain unchanged. Diagnostics do not include charging-profile contents, names, locations, VIN or arbitrary upstream fields.

### 1.8.14 quota stop

The backend requests local Off as soon as a known quota/cooldown prevents further vehicle reads during monitoring, even if the cached SOC is still within the freshness threshold. Stops retain `request_quota` in sanitized diagnostics and remain unsuccessful after confirmation/restart. A known budget of three or fewer remaining requests prevents a new start until reset. No native charging-mode, limit or schedule write is introduced. Delayed cloud reports still prevent a guarantee of an exact percentage target.

## Delayed stop confirmation (1.8.15)

SOC freshness is not itself a stop-confirmation requirement. A non-charging vehicle capture must follow the latest stop intent, accepted stop and session start, must match the configured VIN, be available and not lie in the future. When that report is aged, recovery additionally requires the existing settled local stop proof: fresh available wallbox telemetry, Off, a non-charging status and finite power at or below 0.1 kW. Only then is the guard released and the normal mode restored. Explicit Off stays Off. The session retains its unsuccessful safety-stop cause; no new Start or native-limit write is sent.

## Manual older-SOC permission (1.8.16)

The browser sends the existing Boolean `allow_stale: true` on each manually initiated combined start, including a new session selected through M+. This permission stays scoped to the monitored manual session and does not change the raw vehicle-command route. Age, key/capability, successful-read, local readiness, quota and stop checks remain server-side. Automatic stops persist their pre-command remaining quota, cooldown seconds and quota-reset seconds in the allowlisted diagnostic report, so later counter changes cannot obscure the trigger.
### Native limit writes (1.9.0)

The official `setChargingLimit` PUT is implemented with a JSON `targetStateOfChargeInPercent` body and per-VIN advertised-operation gating. New GUI/API starts require an exact selected integer target, including a requested 87% test and a newer post-write vehicle capture reading back that setting. No charge-profile or mode write is performed. The previous valid target is restored after stopped confirmation; when absent, the user's explicit fallback is 80%. Restoration also requires readback before A+ release. New native-protected sessions do not stop merely because of stale SOC or an exhausted read quota. A timeout, rejection or missing field is explicitly unconfirmed and never enables Start. The current Citigo diagnostics advertise the operation but omit its target; an actual successful native-limit/start/stop/restoration cycle remains to be validated on the tablet. Existing sections describing absence of native writes refer to the preceding 1.8.x implementation.

### Explicit manual restoration and power proof

`POST /api/charging/confirm-restored-limit` requires a valid session, same-origin/CSRF protections, exact pending restoration percentage, unique request ID and `confirmed_at_vehicle: true`. It is available only after automatic restoration reached the attention state, for the same VIN, with a fresh local Off/non-charging/zero-power readback. It records `restoration_confirmation: user`, whereas automatic readback records `api`. It is an explicit user statement after checking the actual vehicle setting, not an inferred result or a vehicle Start. The route is available to already-authorized home-network control sessions.

Start power proof uses only the charger-specific local power field: two finite fresh readings >=0.5 kW spanning at least three seconds after the current start, on M+ with live connection and no phase switch. Re-reading a cached sample cannot confirm it. This records a start time/source without changing SOC or confirming completion.

### Preparation versus actual Start (1.9.1)

The internal `prepare_limit` access check skips only the age of the last successful API read, solely for limit-only preparation. Known bounded SOC, valid key/configuration, cached advertised operations, quota and server cooldown checks still apply; older-data preparation also requires fresh local readiness. The real Start path never sets this flag. It must pass the ordinary read-age check after the new post-write target confirmation. No HTTP request can turn the raw Start route into this exception.

### Missing native readback and recovery (1.9.2)

Before a new automatic target preparation, a current integer vehicle limit must be present in the cached API settings. An omitted limit now blocks without SMARTFOX or vehicle writes, instead of entering an unverifiable Off preparation. This gate does not establish that all integer targets are supported. Existing saved attempts can still restore the explicitly chosen 80% fallback.

The explicit release route documented in [COMMUNICATION.md](COMMUNICATION.md) is restricted to cancelled preparations with no recorded vehicle Start/started-at, no retarget, the same VIN and fresh local Off/non-charging power <=0.1 kW. It readback-verifies A+, atomically closes control with phase `released`, stops further limit writes and preserves the unconfirmed-limit warning. It does not unlock an active/uncertain vehicle start or mark API restoration successful. No remote access guard is weakened.

The [official error documentation](https://public.api.connect.skoda-auto.cz/docs) checked on 23 October distinguishes HTTP 429 `rate-limit-exceeded` from `vehicle-not-accepting-requests`. Both obey the cooldown, but only allowlisted published problem names are retained, with HTTP status and separate target/restoration outcome and budget snapshots. Raw upstream problem bodies, arbitrary text and URLs are never persisted or exported. A later successful GET or replenished quota cannot prove an earlier limit PUT succeeded.
