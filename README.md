# CitiFoxCharger for Windows · 1.7.3

A local dashboard for a Škoda Citigo e iV and a SMARTFOX Pro 2 energy manager with a Charger Pro at charging point 1. The installed SMARTFOX profile is **EM3 00.01.16.08**, address **192.168.0.81**.

Documentation, program filenames, command scripts and command-window messages are English. The browser interface offers **Deutsch / English** in **Settings → Appearance → Language**. The preference is saved in each browser. A German browser initially uses German; other browsers use English.

Both light and dark appearance are available. The light palette uses darker labels and more distinct borders for better readability on the wall tablet.

## Installation and first start

1. Install Python **3.11 or newer** on the Windows tablet. The application uses the Python standard library; no additional Python packages are required. Network certificate setup also requires Windows with .NET Framework **4.7.2 or newer**.
2. Extract the complete Windows package into a new folder. For an upgrade, stop the old backend before starting the new copy. Git installations can use **Update.cmd**.
3. Run **Start.cmd**. It starts the backend in the background and opens Brave in full screen at **http://127.0.0.1:8765/**. If Brave is unavailable, the default browser opens. Press **F11** to leave full screen. An already running browser can ignore startup flags; F11 remains available.
4. Open **Settings**, enter the complete 17-character VIN and add a MyŠkoda API key. Keys are created in MyŠkoda under Profile → Smart Home; the [key-management link](https://go.skoda.eu/api-keys) opens the supported app flow. Never put the key in source code or share it in chat.
5. Choose **Save on this PC with Windows encryption** if the key must survive restarts. Windows binds the encrypted file to that user. Moving the encrypted file to another PC is not a replacement for entering the key there.

The target layout is an **800 × 1280 portrait viewport** on a 1280 × 800 tablet panel. More compact layouts support Windows scaling and landscape operation. The app does not rotate Windows itself.

## Command scripts

| Script | Purpose |
|---|---|
| **Start.cmd** | Start the background backend and open Brave in full screen. Reuse an existing instance. |
| **Start-Background.cmd** | Start or reuse the background backend without opening a browser. |
| **Stop.cmd** | Stop the backend cleanly after charging has stopped; never kill unrelated Python processes. |
| **Stop-Charging.cmd** | Request a charging stop through the running backend, with persistent Smartfox Off; works with older backends and an unreachable vehicle. |
| **Restart.cmd** | Restart the existing backend/profile and resume monitoring without sending another charging start. |
| **Update.cmd** | Fetch and fast-forward after a safe backend shutdown, then offer a startup choice. |
| **Setup-Network.cmd** | Configure optional HTTPS home network access, with or without a password. |
| **Setup-Firewall.cmd** | As administrator, allow the configured HTTPS listener on the private local subnet. |
| **Diagnostics.cmd** | Export a small support report without VIN, credentials or raw responses. |
| **Test.cmd** | Run automated tests with simulated devices. |

Update, Stop, network setup, firewall setup, Diagnostics and Test windows stay open until a key is pressed after success or failure. The Start scripts retain background operation without a permanent console window. Restart displays errors if restart cannot be confirmed.

The scripts try a working `py -3` interpreter and fall back to `python` on PATH. Direct launch examples:

```powershell
python run.py
python run.py --port 8765 --no-browser
python run.py --background --browser brave --fullscreen
python manage.py restart --no-browser
```

For a custom profile, pass `--data-dir "C:\MyDataFolder"` to the Python entry point or applicable management script. Use the same profile consistently. A per-profile lock prevents duplicate monitors even on different ports.

## Updating from GitHub

**Update.cmd** requires Git for Windows and a GitHub sign-in authorized for the private `danielrammer/CitiFoxCharger` repository. The MyŠkoda key is unrelated to GitHub authentication.

In a Git installation the updater verifies the expected origin, branch **main**, a clean worktree and fast-forward ancestry. It fetches first, then shuts down the backend cleanly and runs:

```powershell
git pull --ff-only --no-rebase origin main
```

Local edits, untracked program files, diverged history or failed fetches abort the update. There is no reset, stash, forced merge or overwrite of local edits. An active charging session blocks shutdown; stop charging in the app and wait for confirmation first. A key held only in RAM must be saved with Windows encryption before an update can close the backend.

An extracted ZIP has no Git history. On first use the updater clones into the separate neighbouring **CitiFoxCharger-Git** folder. It can reuse an existing matching checkout, but never overwrites an unrelated folder. The original ZIP folder remains. From then on use the scripts in the displayed Git folder. After a successful update, **Update.cmd** offers:

1. **Start** — start the background backend and open Brave in full screen.
2. **Start-Background** — start the background backend without opening a browser.
3. **Leave stopped** — the default when you press Enter or close the input.

Starting requires an explicit choice. It uses the updated Git folder and preserves the updater's port and data profile. Starting the backend does not request a new vehicle charge. A failed update never offers a start, and the command window still waits for a key after completion or failure. Direct `python update.py` remains non-interactive unless `--choose-start` is supplied. When upgrading from an older updater, the new menu is available the next time the updated **Update.cmd** is run.

Use `Update.cmd --port 8774` for a non-default local backend port. Both start scripts also accept custom settings, for example `Start-Background.cmd --port 8774 --data-dir "D:\CitiFox profile"`. Use the same port/profile for all startup, update and management actions.

## Existing settings and English filenames

New installations default to **%LOCALAPPDATA%\CitiFoxCharger**. If **%LOCALAPPDATA%\Ladezentrale** already exists, that historical profile remains the default so credentials, charge targets, quota pauses and the instance lock stay together. No credential or session folder is moved or deleted. `--data-dir` explicitly selects another profile. The old folder name is a compatibility detail, not a second monitor.

The Python package is now **citifoxcharger**. Script names are **Stop.cmd**, **Restart.cmd**, **Setup-Network.cmd**, **Setup-Firewall.cmd**, **Start-Background.cmd** and **Diagnostics.cmd**. The technical documents are **INTERFACES.md**, **NETWORK.md** and **TEST-REPORT.md**. Extract into a fresh folder or update through Git to avoid old program files remaining alongside the new names.

## Charging controls

The main page shows the reported battery percentage, current local charging power, charging state, an editable target slider, **Force charge** and **Stop**. A moving energy stream, smiling car, striped battery and pulsing indicator show charging only when fresh local power exceeds 0.1 kW, or fresh vehicle CHARGING is available without a local measurement. HTTP 202 alone does not animate charging. The battery percentage is never artificially increased. Reduced-motion preferences disable movement.

### Force charge

1. Select a target between **1 and 100%**, above the reported SOC.
2. The app changes Charger 1 to **M+**, verifies the returned mode and waits at least three seconds, up to fifteen seconds, for fresh readiness with an offered current of at least 6 A.
3. Only then is one vehicle start sent. Failed readiness sends no vehicle start and attempts a return to A+.
4. The backend monitors the target independently of browser timers. A valid report at or above the target, even if now old, requests a conservative stop.

You can change the slider during monitoring. Releasing it saves the new local target without another start. Lowering it to/below an already reported SOC can request a stop. Once stopping begins, target changes are locked. The application does not program the native vehicle charge limit.

### Automatic target stop and missing data

An automatic stop first requests and reads back **Off** at the wallbox, then requests the vehicle stop. Cloud failure or exhausted quota cannot skip that local attempt. Off is held until a newer, non-charging vehicle report confirms the stop. A future-dated report beyond the allowed clock tolerance cannot release the hold. Local Off retries remain independent of cloud command backoff. The UI blocks A+/M+ during this hold.

After confirmation, A+ is restored unless Off was explicitly selected. A+ allows normal surplus/scheduled operation and is not a guaranteed power interruption. A confirmed Off setting is a mode confirmation; physical power and vehicle confirmation are separate observations. The app reports unconfirmed outcomes rather than claiming success.

The target is checked against cloud vehicle reports, not a continuously measured native battery limit. Delayed reports can cause overshoot before the threshold is known. The reported 82%/86% incident on backend 1.6.0 was ended manually by the user. Version 1.6.1 fixed identified stop gaps; an exact root cause and a successful real automatic target-stop cycle on the tablet still require confirmation.

An explicitly permitted start with older SOC has a persistent **five-minute deadline** for a new report captured after the start. Without it the app requests a stop and holds Off. This can explain early stops, but is not established as the actual cause of the reported early interruptions. Later loss of data allows at most five minutes of recovery and no more than **15 minutes since the last valid report**, or a shorter configured freshness limit. It does not leave M+ active indefinitely.

### Older data and retrying a start

The **Use the last reported battery level** checkbox allows one explicit manual start only when SOC is known and no older than 24 hours, a successful API/capability read is at most five minutes old, and local readiness with no charging power is at most 30 seconds old. The displayed timestamps and stale label remain unchanged. It does not override a missing key, unsupported operation, server cooldown or active stop.

**Retry start** requires fresh local readiness, no physical charging above 0.1 kW, available start/stop access and at least 30 seconds since the previous attempt. A newer non-charging report or ten minutes of unconfirmed outcome is also required. A new deliberate click sends one new start and preserves the existing target. Duplicate request IDs do not repeat commands. Starts are never automatically replayed, including after restart.

**Stop** remains available for an active session even when the vehicle is unreachable, the key expired or the cloud command is blocked. It durably records the stop and requests verified local Smartfox **Off before any vehicle read or command**. Off is held until a newer stopped vehicle report permits the normal-mode return. A failed local request is not reported as stopped and is retried independently of cloud backoff. Unknown stop outcomes can be retried only after newer CHARGING telemetry, with bounded attempts. A newer stopped report can confirm the stop without another command.

### Recovering an older backend with a disabled Stop button

If the dashboard says the vehicle is unreachable while **Stop.cmd** says charging monitoring is active, extract the new package into a separate folder on the tablet. Do not start a second backend. Run **Stop-Charging.cmd** from that new folder; it uses the existing local backend on port 8765, requests persistent charger Off and a vehicle stop, then checks the stored stop intent and fresh local Off readback. It does not require the vehicle API key, kill a process or pretend that the vehicle confirmed the stop. For another local port use `Stop-Charging.cmd --port 8774`.

After its Off confirmation, wait at least five seconds and run **Stop.cmd**. The backend independently checks current power and status before allowing exit. You can then run **Start.cmd** from the new folder or update the existing Git installation. The recovery command leaves the charger explicitly Off until A+ is deliberately selected or a new manual session is started. If any confirmation fails, check Smartfox and leave monitoring running.

## Home energy and outputs

The compact energy panel shares the charging page. House use follows the installed vendor calculation **production + grid − battery** and includes controlled consumers. Positive grid power is import; negative is export. The moving dot covers the full line. Heater shows measured kW, percentage and temperature. Heater 2 and Hot water show relay state and assigned temperature; relay power is never invented. Missing values appear as **—**.

- **Heater / Heater 2 / Hot water:** Off, Automatic or Manual. The analog heater keeps its existing manual percentage. Relay Manual/Off cancels an existing manual timer as the vendor UI does. Temperature limits and schedules are not edited.
- **Charger:** Off, A+ or M+. Explicit Off persists. Idle M+ uses the same monitored Force charge flow. During monitoring a permitted mode change does not send another vehicle start.

Controls are limited to the installed output assignments and exact firmware. The device-side names remain unchanged for matching the verified outputs. A firmware mismatch, missing assignment or failed readback blocks control. Modbus remains read-only. See [INTERFACES.md](INTERFACES.md) for evidence and limitations.

## Freshness and estimated duration

**Last vehicle report** is the original vehicle capture time. **Last successful query** is when the API response arrived. A cloud query does not guarantee a new measurement in the car. The documented public API has no verified wake-up/force-refresh operation; community integrations use different authentication and do not prove support for this Citigo.

The refresh button respects a one-minute minimum spacing, server cooldowns and the command quota reserve. Normal polling is shared by every browser/device. Two requests are reserved for commands; remaining quota is distributed across reads. The API documentation currently describes 20 requests per hour and VIN, with response headers authoritative.

The displayed time estimate uses **32.3 kWh usable capacity**, an assumed **90% charging efficiency** and constant fresh local power. It rounds up to five minutes. Capacity source: [Škoda Citigo e iV specifications](https://www.skoda-storyboard.com/en/press-kits/skoda-citigo%E1%B5%89-iv-skoda-superb-iv-press-kit/skoda-citigo%E1%B5%89-iv-zero-emission-range-of-up-to-260-km/). Temperature, losses, schedules and reporting delay limit accuracy. Old/missing SOC or local power older than 30 seconds suppresses the numerical estimate. The estimate does not control charging or alter SOC.

## Background operation and exit

The backend keeps monitoring after the browser closes or the screen turns off. During an active session it requests Windows SystemRequired and ExecutionRequired, not DisplayRequired. It releases those requests when monitoring ends or the backend stops. Use mains power and suitable Windows sleep settings; explicit sleep, shutdown and platform power policies can still interrupt execution. No Windows service, scheduled startup or power-plan change is installed.

**Stop.cmd** refuses to abandon an active charge. After an explicit stop it can close without waiting indefinitely for cloud confirmation only when at least five seconds have passed and a fresh local read confirms the required mode, non-charging wallbox status and 0–0.1 kW. A guarded stop requires Off; an externally changed A+ is insufficient even at zero power. The session is marked explicitly closed, not falsely vehicle-confirmed. Old data, M+, errors or power flow block exit. A stored Off hold is not silently released after restart.

**Restart.cmd** requires a persisted key if a key or active session exists. It saves the session and quota state, releases worker/lock/power handles and starts the same profile. A changed instance ID confirms restart. It never repeats a vehicle start. Reload an already open browser if needed.

## Home network access and diagnostics

Run **Setup-Network.cmd** on the tablet to choose password-protected or passwordless **HTTPS** access. Leaving the password empty explicitly permits **every device on the reachable home network to operate charging and heaters**. Network access is disabled until configured. The private-IP listener, certificate trust and private-subnet firewall setup are described in [NETWORK.md](NETWORK.md). Key/device setup and backend administration remain local, including without a network password.

Run **Diagnostics.cmd before restarting** when investigating a problem. It writes **CitiFoxCharger-Diagnostics.json** beside the script, querying an existing local backend, including older versions. If unavailable it clearly labels saved data as potentially older. The report includes selected SOC/target/timestamps/quota/wallbox/monitor state, but no VIN, passwords, keys, raw responses, IP address or free-form errors. Use `Diagnostics.cmd --port 8774`, `--data-dir "C:\MyDataFolder"`, `--offline` or `--output "C:\Reports\report.json"` as needed. Normal backend monitoring remains active during the read; the script sends no start/stop request itself.

See [TEST-REPORT.md](TEST-REPORT.md) for validation and outstanding hardware checks.
