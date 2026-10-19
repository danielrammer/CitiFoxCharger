# Communication and data flow

This describes the implementation shipped with **CitiFoxCharger 1.8.5**, checked against the source on **19 October 2026**. The addresses below are installation examples; the configured device address and active register profile are authoritative. No VIN or API key is included.

## System overview

```mermaid
flowchart TB
    Local["Tablet browser / Brave fullscreen"]
    Remote["Phone or laptop browser"]
    Backend["CitiFoxCharger Python backend on Windows tablet\nShared cache, charging monitor, target and command verification"]
    Modbus["SMARTFOX Pro 2\n192.168.0.81:502\nModbus TCP, Unit 1, function 03"]
    Web["SMARTFOX Pro 2\nhttp://192.168.0.81:80\nLocal vendor web interface"]
    Charger["SMARTFOX Charger Pro\nCharging point 1"]
    Outputs["Analogue heater 1\nRelay 1: heater 2\nRelay 2: hot water"]
    Cloud["MyŠkoda Public API\nhttps://public.api.connect.skoda-auto.cz:443"]
    Car["Škoda Citigo e iV"]
    Store["Windows user profile\nConfiguration, monitoring state, optional DPAPI-encrypted key"]

    Local <-->|"HTTP 127.0.0.1:8765\nApp JSON API and static frontend"| Backend
    Remote <-->|"HTTPS 192.168.0.95:8766\nOptional home-network listener"| Backend
    Backend -->|"Read holding registers only\nSelected profile; no Modbus writes"| Modbus
    Backend <-->|"GET /values.xml\nGET vendor CGI commands + readback"| Web
    Web <-->|"Existing SMARTFOX installation\nDevice protocol outside this app"| Charger
    Web -->|"Existing output wiring / control"| Outputs
    Charger ---|"Charging cable / charging permission and power"| Car
    Backend <-->|"HTTPS JSON; X-API-Key\nVehicle GET; charging start/stop POST"| Cloud
    Cloud <-->|"Manufacturer vehicle connection\nTelemetry can be delayed"| Car
    Backend <-->|"Local files; key never returned to browser"| Store
```

The diagram separates application connections from the existing charger/output wiring and manufacturer vehicle connection. CitiFoxCharger does **not** obtain SOC through the charging cable or SMARTFOX. It does not implement the protocol between SMARTFOX and its charger, or between the vehicle and the manufacturer's cloud.

**Port 8766 requires `https://`.** Plain `http://` on this port can result in a reset connection. Each remote client needs to trust the tablet's public network certificate. The tablet's local browser instead uses `http://127.0.0.1:8765/`. See [NETWORK.md](NETWORK.md) for certificate and firewall setup.

## Modbus reads

Source: [modbus.py](citifoxcharger/modbus.py). Target: configured SMARTFOX address, default **192.168.0.81**, TCP **502**, Unit ID **1**, function **03 / Read Holding Registers**. The app issues one read per row of the **selected** profile; it does not read both profiles together. Addresses below are the exact, zero-based **PDU addresses passed on the wire**, with no further subtraction. A word is 16 bits.

| Profile | Key | PDU start address | Words | Inclusive range | Interpretation in this app |
|---|---|---:|---:|---|---|
| `observed` (default) | `energy` | 40999 | 4 | 40999–41002 | Requested as total energy; retained as raw words/numeric candidate. Charger semantics unconfirmed. |
| `observed` | `power` | 41015 | 2 | 41015–41016 | Previously requested as charging power; **not** verified as power. Older list describes daily energy. |
| `observed` | `mode` | 42111 | 1 | 42111 | Previously requested as mode; observed 62 is **not** decoded as a mode. Absent from the older list. |
| `manufacturer_v22e` (optional) | `energy` | 41599 | 4 | 41599–41602 | Older list: Charger 1 total energy, Wh. Physical interpretation requires explicit confirmation. |
| `manufacturer_v22e` | `power` | 41605 | 2 | 41605–41606 | Older list: Charger 1 power, W. Physical interpretation requires explicit confirmation. |
| `manufacturer_v22e` | `mode` | 41607 | 1 | 41607 | Raw mode candidate only; mode codes remain unconfirmed for installed firmware. |

Word order is configurable. Only the optional manufacturer profile with `interpretation_confirmed=true` converts its energy/power candidate into a labelled physical value. **There are no Modbus write function codes or register-write calls.** The dashboard's current charging power and named charger mode come from the local web interface below, not from guessing the observed registers.

## SMARTFOX local web reads

Sources: [smartfox_web.py](citifoxcharger/smartfox_web.py), [smartfox_energy.py](citifoxcharger/smartfox_energy.py). All values are obtained in one **`GET http://192.168.0.81/values.xml`** response. XML field names are not Modbus register addresses.

| Display / check | XML fields used | Meaning / handling |
|---|---|---|
| Firmware gate | `version` | Must match `EM3 00.01.16.08`. |
| Charger mode / power | `hidCcMode1`, `cc1Power` | Named charger mode and unit-checked kW. Actual positive power is needed to show energy flow. |
| Charger connection / readiness | `hidCcSt1`, `hidCc1Info`, `hidCcConnTimeout1`, first entry of `hidCcControlCurrent` | Status, permission and offered current. Readiness is not proof of physical charging. |
| Phase switching | `hidCc1Info`, optional `hidCc1TempTime`, `hidCc1Phase` | Info 6 means switching; valid remaining seconds are shown if present. Phase indication 1/3 is not a measurement of the car's actual phase currents. |
| Existing M+ configuration | first entry of `hidCcMplusMode` | Read only; native M+ time/energy limits are not rewritten. |
| Grid / house power | `hidPower`, `hidProduction`, `batteryPower` | Grid W / 1000 = kW; positive grid = import. House kW = production + grid − battery, clamped at zero when all values are valid. |
| Analogue heater 1 | `hidAoutActivated`, `hidAoutMode`, `hidAoutStatus`, `hidAoutPercentage`, `analogOutPower`, `analogOutPercent` | Enablement, mode, status, configured manual percentage, power estimate and actual reported actuation. Manual percentage is not substituted for actual actuation. |
| Temperatures | `analogOutTemp`, `hidAoutTemperatureSensors`, `hidR1TempSensor`, `hidR2TempSensor` | Sensor assignment is retained; 0 means unassigned and 850 °C is a missing-value sentinel. |
| Relays 1 / 2 | `hidRelay1Activated`, `hidRelay2Activated`, `hidR1Mode`, `hidR2Mode`, `hidR1Status`, `hidR2Status`, `hidR1Info`, `hidR2Info` | Enablement, mode, switching state and reason. Relay electrical power is not invented. |
| Output assignment guards | `hidAoutName`, `hidR1Name`, `hidR1Type`, `hidR2Name`, `hidR2Type`; `hidR3Type` for analogue display exception | Controls require the verified installed output assignment; the vendor's type-4 exception affects the analogue percentage display. |

Heater 1 reports **Automatic · surplus** only for automatic status 0 with a positive reported actuation percentage. Status 0 at 0% shows **No surplus**. Manual, scheduled, temperature-control and other states retain their own reason.

## SMARTFOX local web commands

These are **HTTP GET requests with side effects**, matching the installed manufacturer's JavaScript. They are not a publicly specified, general-purpose SMARTFOX API. They require the existing explicit control opt-in and firmware/output checks. Every change is followed by `GET /values.xml` and a matching readback; a successful CGI response alone is insufficient. An already selected mode can return after a read without sending a redundant command.

Base URL: **`http://192.168.0.81`** (configurable private device address).

| Device / action | Method and path |
|---|---|
| Charger 1: Off | `GET /setswcc.cgi?num=0&mode=3&value=100` |
| Charger 1: A+ | `GET /setswcc.cgi?num=0&mode=2&value=100` |
| Charger 1: M+ | `GET /setswcc.cgi?num=0&mode=4&value=100` |
| Analogue heater 1: Off | `GET /setswaout.cgi?mode=0` |
| Analogue heater 1: Automatic | `GET /setswaout.cgi?mode=2` |
| Analogue heater 1: Manual | `GET /setswaout.cgi?mode=1&power={existing_manual_percent}` |
| Heater 2 / hot water: Automatic | `GET /setswrel.cgi?rel={N}&state=0` |
| Heater 2 / hot water: Manual | First `GET /setswrel.cgi?rel={N}&state=3&duration=0`, then `GET /setswrel.cgi?rel={N}&state=1` |
| Heater 2 / hot water: Off | First `GET /setswrel.cgi?rel={N}&state=3&duration=0`, then `GET /setswrel.cgi?rel={N}&state=2` |

`num=0` identifies **charging point 1**; relay `N=1` is **heater 2**, `N=2` is **hot water**. The timer-cancellation call applies only before relay Manual/Off. Analogue Manual reuses the existing valid percentage; a missing percentage blocks that command. No temperature limit, weekly schedule, native M+ timer or arbitrary relay assignment is changed.

## Škoda cloud calls

Source: [skoda.py](citifoxcharger/skoda.py). Fixed origin: **`https://public.api.connect.skoda-auto.cz`**, TCP **443**. Requests use `X-API-Key: <local secret>` and `Accept: application/json`, validated server certificates, no redirects and no environment proxy. The key remains in the backend, optionally encrypted using Windows per-user DPAPI.

| Purpose | Request | Expected response / use |
|---|---|---|
| SOC, vehicle state and permitted operations | `GET /api/v1/vehicles/{vin}?include=charging,operations` | HTTP 200; validates returned VIN. Reads `vehicle.charging.status.battery.stateOfChargeInPercent`, charging/plug state, `vehicle.charging.carCapturedTimestamp` and `vehicle.operations[].name`. |
| Start charging | `POST /api/v1/vehicles/{vin}/charging/start` | Empty body; HTTP 202 means accepted, not confirmed charging. Requires discovered `startCharging` capability and the app's start guards. |
| Stop charging | `POST /api/v1/vehicles/{vin}/charging/stop` | Empty body; HTTP 202 means accepted, not confirmed stopped. Later vehicle telemetry verifies the outcome. |

The public adapter sends **only these three request types**. It does not call a native charging-limit endpoint, wake-up endpoint, air-conditioning endpoint or duration-estimate endpoint. The slider changes a backend-monitored target, and remaining time is estimated locally. Support is discovered for the configured VIN; it is not assumed for all Citigo vehicles. See [INTERFACES.md](INTERFACES.md) for reviewed manufacturer sources and compatibility evidence.

## Browser-to-backend API

The browser never sends the Škoda API key to SMARTFOX or contacts the cloud directly. The backend serves both the local and optional HTTPS frontend from the same shared service.

| Method / path | Purpose |
|---|---|
| `GET /api/session` | Browser session / CSRF bootstrap. |
| `GET /health` | Application identity, instance ID and release metadata. |
| `GET /api/status` | Shared cached telemetry, monitoring and control availability. |
| `POST /api/refresh` | Request a permitted refresh; does not force the vehicle to measure anew. |
| `POST /api/charging/start` | Combined Smartfox M+ and guarded vehicle start, with local target monitoring. |
| `POST /api/charging/stop` | Combined local-first charging stop. |
| `POST /api/charging/target` | Change the monitored target without repeating start. |
| `POST /api/smartfox/charger-mode` | Select Off / A+ / M+. |
| `POST /api/smartfox/heater` | Select Off / Automatic / Manual for an authorized heating output. |
| `GET /api/config`, `POST /api/config` | Configuration and key-storage status view / local-only modification. The API-key value is not returned. |
| `POST /api/key`, `POST /api/key/clear` | Local-only API-key setup / removal. |
| `POST /api/skoda/command` | Local-only guarded direct vehicle command route. |
| `POST /api/smartfox/command` | Legacy local route; Modbus writes remain blocked. |
| `POST /api/app/update`, `GET /api/app/update/status` | Local-only update job and status. |
| `POST /api/app/restart`, `POST /api/app/quit` | Local-only backend process control. |

Mutations require the session/CSRF and origin checks. Remote access permits only refresh, combined charging start/stop/target and the requested SMARTFOX output controls. The page-reload button reloads the browser document; it does not invoke restart or send a charging command.

## Manual start and target stop

```mermaid
sequenceDiagram
    participant UI as Browser
    participant App as Backend monitor
    participant SF as SMARTFOX local web
    participant API as Škoda Public API
    UI->>App: POST /api/charging/start (target)
    App->>App: Validate data, capabilities, quota and session
    App->>SF: Select M+ if needed; read values.xml
    SF-->>App: Mode, readiness, current, phase-switch state
    opt Phase switching is reported
        App-->>UI: Show preparation / countdown; Stop stays available
        App->>SF: Read newer readiness after switching
        SF-->>App: Stable charging permission
    end
    App->>API: POST /charging/start (full path in table)
    API-->>App: 202 Accepted
    loop Monitoring while backend runs
        App->>SF: GET /values.xml + selected Modbus reads on schedule
        App->>API: Vehicle GET when quota / polling rules permit
        API-->>App: SOC and vehicle capture timestamp
        UI->>App: GET /api/status (cached view)
        App-->>UI: Shared status and estimate
    end
    Note over App: Target reached or user requests Stop
    App->>SF: Select Off; read back mode
    Note over App,SF: Local stop is attempted before cloud access; an unconfirmed Off is an error
    App->>API: Guarded stop processing: read / POST stop as required
    API-->>App: Accepted command / newer vehicle telemetry
    Note over App: Keep Off hold until newer vehicle telemetry confirms stopped
    App->>SF: Return to A+ after combined stop confirmation
    App-->>UI: Stop completed
```

This is the successful combined-control flow, not a promise that every operation succeeds. Start is withheld on failed readiness, authorization, phase-preparation timeout or unavailable capabilities. An uncertain start is not blindly repeated. If local Off is not confirmed, the app exposes that failure. A newer stopped vehicle report can complete the stop without another POST. An explicitly selected charger Off remains Off; A+ restoration above describes the normal combined stop. Native SMARTFOX and vehicle schedules may permit later charging in A+.

## Timing and freshness

- Browser status refresh: every **10 seconds**; this reads the backend cache and does not create one cloud request per browser.
- SMARTFOX configured polling default: **10 seconds**. Command readiness/readback can perform additional local reads.
- Škoda configured polling default: **180 seconds**, adjusted by charging state, quota and cooldown. Idle fallback is at least **600 seconds**; active fallback without usable quota information is at least **180 seconds**. With a known quota window, spare reads are budgeted while retaining two calls for charging commands.
- Manual vehicle refresh has a **60-second** minimum spacing and respects quota/cooldown. Near-target monitoring can bring forward at most two additional budgeted reads per session under stricter freshness and quota guards.
- Cloud response time and `carCapturedTimestamp` are different. A successful GET can still return old SOC. No force-refresh/wake call is implemented, and a locally monitored SOC target can be overshot because of delayed vehicle reports.
- Monitoring runs in the Python backend, not the browser. Closing the browser or switching the screen off does not itself stop monitoring; actual Windows sleep or backend termination can prevent execution.

Source map: [server.py](citifoxcharger/server.py), [service.py](citifoxcharger/service.py), [charging.py](citifoxcharger/charging.py), [config.py](citifoxcharger/config.py), [app.js](citifoxcharger/web/app.js). For authentication, rate-limit evidence and detailed failure handling, see [INTERFACES.md](INTERFACES.md).
