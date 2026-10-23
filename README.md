# CitiFoxCharger for Windows · 2.0.0 development

<img src="citifoxcharger/web/brand/logo.png" alt="CitiFoxCharger logo: smooth electric city car, red fox tail and charging plug" width="620">

**Development branch:** native-target state-machine refactor; real Citigo limit readback is not yet confirmed. See [TEST-REPORT.md](TEST-REPORT.md) before installing.

A local dashboard for a Škoda Citigo e iV and a SMARTFOX Pro 2 energy manager with a Charger Pro at charging point 1. The installed SMARTFOX profile is **EM3 00.01.16.08**, address **192.168.0.81**.

Documentation, program filenames, command scripts and command-window messages are English. The browser interface offers **Deutsch / English** in **Settings → Appearance → Language**. The preference is saved in each browser. A German browser initially uses German; other browsers use English.

Both light and dark appearance are available. The light palette uses darker labels and more distinct borders for better readability on the wall tablet.

The header displays the current date and time from the browser device, independently of vehicle telemetry. It refreshes on returning to the page and uses the selected interface language. The clock fits inside the existing header height.

The Settings page identifies the running backend as **v2.0.0 | first eight Git characters | commit date**. Git installations read the exact program repository at startup; release ZIPs include the same commit/date in `citifoxcharger/build-info.json`. If metadata are unavailable, the hash is shown as a dash rather than invented. The **Reload page** button (⟳) in the header reloads the current browser page without restarting the backend or sending a charging command. It works during charging and is temporarily disabled while a command or app update is in progress.

## Architecture and communication

See [COMMUNICATION.md](COMMUNICATION.md) for the system and start/stop diagrams, exact Modbus addresses and word counts, XML data fields, SMARTFOX CGI commands, Škoda API calls and browser-to-backend routes. Mermaid diagrams render directly on GitHub. Protocol evidence and compatibility limits are in [INTERFACES.md](INTERFACES.md).

The proposed charging-controller simplification is documented in [CHARGING-REFACTOR.md](CHARGING-REFACTOR.md): explicit states, transition rules, module responsibilities and regression scenarios. This is a design document; it does not change the running controller.

## Vehicle-controlled manual charging

New manual sessions use the explicit state machine in [native_charging/machine.py](citifoxcharger/native_charging/machine.py). [CHARGING-REFACTOR.md](CHARGING-REFACTOR.md) explains the states and migration boundary. The existing controller remains only for sessions created by older versions.

1. Validate access, known SOC, readable vehicle limit, local telemetry and request budget **before changing devices**. Older SOC is accepted automatically within the existing access bounds; its original age stays visible.
2. Hold SMARTFOX Off and wait up to 30 seconds for zero charging power, then set the exact requested vehicle limit and wait for a newer vehicle capture that reads it back. HTTP 202 means accepted, not confirmed. Unsupported values are never rounded automatically.
3. Select M+, wait for stable readiness (including phase switching), then send one vehicle Start.
4. Confirm charging from two distinct fresh SMARTFOX measurements of at least 0.5 kW, at least three seconds apart. An accepted Start with 0 kW remains **Starting**.
5. Let the vehicle enforce its confirmed limit. Old SOC or exhausted read quota alone no longer stops a native-protected charge. A newer report at or above the target initiates cleanup; an unexpected power interruption is shown separately from target completion.
6. Stop locally first, send vehicle Stop, then require stopped vehicle telemetry and local Off/zero power. Restore the previous vehicle limit and confirm its exact readback before releasing normal A+. An explicitly selected Off remains Off.

**Citigo compatibility is still unconfirmed:** the supplied vehicle responses advertise `setChargingLimit` but omit the current limit. These responses cannot pass the native preflight. The app explains this **without changing SMARTFOX or the vehicle**. It does not silently revert to app SOC control. The implementation cannot promise vehicle-controlled target accuracy until the real car passes write/readback and physical charging trials.

The official contract accepts integer requests but notes that vehicles commonly support only 50–100% in ten-point steps. Selecting 87% requests exactly 87%; a rejection requires choosing another target explicitly. Charging profiles, timers, battery-care mode and charge mode are not rewritten. An active battery-care cap below the requested target blocks preparation.

Preparation/restore verification lasts at most 180 seconds with at most three spaced probes per operation. Readiness/phase switching has a fixed 360-second deadline; Start has 180 seconds to produce charging power. A known request window must have at least ten requests available before preparation or active retargeting. These are named workflow limits, not a charging-duration estimate. Unknown write outcomes are observed rather than replayed.

### Existing sessions and recovery

An existing version-1 session must finish Stop/restoration before a new native session can start. Its established recovery behavior is retained; this includes the old protective SOC/quota stops. This avoids treating an unresolved command as a new idle session.

The legacy **Release A+ normal mode** recovery remains available only for an aborted older preparation that never sent vehicle Start. It explicitly releases the unconfirmed-limit hold without claiming that 80% was restored. A+ can allow vehicle/SMARTFOX schedules to charge again.

New version-2 sessions persist one state plus operation/evidence data. Restart enters **Recovering**, never replays Start and reconciles local power. Unfinished preparation is cancelled and cleaned up. An unconfirmed Stop or restoration needs explicit retry in Settings; it does not generate an endless automatic Off/PUT loop. Local Off failure still permits a best-effort vehicle Stop. An unresolved setting restoration blocks normal exit/update; restart remains available with a Windows-encrypted key.

Diagnostics include the state, step, reason and a bounded transition log. They remain available through authenticated local or home-network access and exclude VIN, credentials and raw vehicle responses. Optional charging-profile information is read with the existing GET and reduced to anonymous capability/percentage/timer-count fields; profiles are never written.

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

Open **Settings → Application update → Update & restart**, locally or from another device on the configured HTTPS home-network connection, then confirm the update. It performs the same protected fast-forward Git update, restarts the backend with the same profile/port and reloads the current browser page. It does not start charging or open another browser window. Install 1.8.11 once from the tablet to enable remote updates and diagnostics. Remote updates are explicitly permitted in both password-protected and passwordless network modes. Existing charging/shutdown guards and the Windows-encrypted key requirement remain in force.

A detached helper survives the backend shutdown. Progress appears in the page; reconnecting refreshes the local browser session protection before polling the job. Duplicate requests reuse a running job. Each Git operation has a two-minute timeout. Changes to tracked source, incoming file collisions, unexpected remotes or divergent history remain blocked. If an update fails after shutdown, the helper attempts to restart the program without starting a charge and keeps the result marked failed. Details are written to **update.log** in the data profile; **Update.cmd** and **Start.cmd** remain recovery options. Git for Windows and network access to the repository must already be available. Reading the public repository does not require a GitHub sign-in.

**Update.cmd** requires Git for Windows and access to the public `danielrammer/CitiFoxCharger` repository. The MyŠkoda key is unrelated to GitHub authentication.

In a Git installation the updater verifies the expected origin, branch **main**, no tracked program changes and fast-forward ancestry. It fetches first, checks for local filename conflicts and then shuts down the backend cleanly. It applies exactly the checked commit using:

```powershell
git fetch origin main
git merge --ff-only --no-autostash --no-overwrite-ignore FETCH_HEAD
```

Local reports and other untracked files do not block updates and remain untouched. Common diagnosis filenames, including **Diagnosis.json**, **CitiFoxCharger-Diagnose.json** and **CitiFoxCharger-Diagnostics.json**, are ignored by Git. Tracked edits (including staged new files), real file/directory collisions, diverged history or failed fetches still abort the update. Collision checks include ignored files and run before backend shutdown and again before applying the update; Git also refuses to overwrite ignored files created after those checks. There is no reset, stash, forced merge or deletion of local files. An active charging session blocks shutdown; stop charging in the app and wait for confirmation first. A key held only in RAM must be saved with Windows encryption before an update can close the backend.

If an older **Update.cmd** refuses an extra diagnosis file before it can fetch this correction, run `git pull --ff-only` once in the Git program folder. Future **Update.cmd** runs use the corrected checks; there is no need to delete diagnosis files.

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

**Force charge** starts the vehicle-controlled sequence above. **Stop** remains available if the cloud becomes unreachable: local Off is attempted first. Target changes during confirmed charging repeat exact-limit verification under Off while retaining the original restore value. A lower target already reached according to the report requests Stop immediately. No automatic rounding or energy-estimate stop is used.

A phase-switch countdown stays within the existing status area. The backend owns all state and commands; multiple browser clients share the same session and quota. Settings → Vehicle charging limit offers explicit cleanup retry. Manual restoration confirmation is offered only when restoration itself is unconfirmed, after checking the actual setting directly in MyŠkoda or the car.

## Home energy and outputs

The analogue heater uses **actuation percentage** as its main reading in manual/forced modes, matching the installed Smartfox display. A 100% output can coexist with an unavailable/zero Smartfox power estimate; it is not shown as zero consumption or an inactive output. The percentage is the actual reported output, not the configured manual setpoint, and it does not prove the heater's electrical consumption (a thermostat can interrupt it). The green indicator follows reported actuation when present. Relay mode values **0 and 1 both mean Automatic**; `m` is Manual and `x` is Off. Other unknown values remain unknown. The station alone uses Off/A+/M+; heating outputs use Off/Automatic/Manual.

The compact energy panel shares the charging page. House use follows the installed vendor calculation **production + grid − battery** and includes controlled consumers. Positive grid power is import; negative is export. The moving dot covers the full line. Heater shows measured kW, percentage and temperature. Heater 2 and Hot water show relay state and assigned temperature; relay power is never invented. Missing values appear as **—**.

- **Heater / Heater 2 / Hot water:** Off, Automatic or Manual. The analog heater keeps its existing manual percentage. Relay Manual/Off cancels an existing manual timer as the vendor UI does. Temperature limits and schedules are not edited.
- **Charger:** Off, A+ or M+. Explicit Off persists. Idle M+ uses the same monitored Force charge flow. During monitoring a permitted mode change does not send another vehicle start.

Controls are limited to the installed output assignments and exact firmware. The device-side names remain unchanged for matching the verified outputs. A firmware mismatch, missing assignment or failed readback blocks control. Modbus remains read-only. See [INTERFACES.md](INTERFACES.md) for evidence and limitations.

## Freshness and estimated duration

For migrated legacy sessions only, bounded early SOC/freshness reads and protective data/quota stops retain their earlier behavior. New native sessions do not use those SOC-age stop rules or extra near-target probes: the confirmed vehicle limit is authoritative.

**Last vehicle report** is the original vehicle capture time. **Last successful query** is when the API response arrived. A cloud query does not guarantee a new measurement in the car. The documented public API has no verified wake-up/force-refresh operation; community integrations use different authentication and do not prove support for this Citigo.

The refresh button respects a one-minute minimum spacing, server cooldowns and the command quota reserve. Normal polling is shared by every browser/device. Native-protected sessions reserve three requests for Stop/restoration; migrated legacy sessions reserve two. Remaining quota is distributed across reads. The API documentation currently describes 20 requests per hour and VIN, with response headers authoritative.

The displayed time estimate uses **32.3 kWh usable capacity**, an assumed **90% charging efficiency** and constant fresh local power. It rounds up to five minutes. Capacity source: [Škoda Citigo e iV specifications](https://www.skoda-storyboard.com/en/press-kits/skoda-citigo%E1%B5%89-iv-skoda-superb-iv-press-kit/skoda-citigo%E1%B5%89-iv-zero-emission-range-of-up-to-260-km/). Temperature, losses, schedules and reporting delay limit accuracy. Old/missing SOC or local power older than 30 seconds suppresses the numerical estimate. The estimate does not control charging or alter SOC.

## Background operation and exit

The backend keeps monitoring after the browser closes or the screen turns off. During an active session it requests Windows SystemRequired and ExecutionRequired, not DisplayRequired. It releases those requests when monitoring ends or the backend stops. Use mains power and suitable Windows sleep settings; explicit sleep, shutdown and platform power policies can still interrupt execution. No Windows service, scheduled startup or power-plan change is installed.

**Stop.cmd** refuses to abandon an active charge. For new native sessions, Stop and setting restoration must finish before exit. The following local-only closure exception applies to migrated legacy sessions: After an explicit stop it can close without waiting indefinitely for cloud confirmation only when at least five seconds have passed and a fresh local read confirms the required mode, non-charging wallbox status and 0–0.1 kW. A guarded stop requires Off; an externally changed A+ is insufficient even at zero power. The session is marked explicitly closed, not falsely vehicle-confirmed. Old data, M+, errors or power flow block exit. A stored Off hold is not silently released after restart.

**Restart.cmd** requires a persisted key if a key or active session exists. It saves the session and quota state, releases worker/lock/power handles and starts the same profile. A changed instance ID confirms restart. It never repeats a vehicle start. Reload an already open browser if needed.

## Home network access and diagnostics

Run **Setup-Network.cmd** on the tablet to choose password-protected or passwordless **HTTPS** access. Leaving the password empty explicitly permits **every device on the reachable home network to operate charging and heaters**. Network access is disabled until configured. The private-IP listener, certificate trust and private-subnet firewall setup are described in [NETWORK.md](NETWORK.md). Key/device setup and backend administration remain local, including without a network password.

Run **Diagnostics.cmd before restarting** when investigating a problem. It writes **CitiFoxCharger-Diagnostics.json** beside the script, querying an existing local backend, including older versions. If unavailable it clearly labels saved data as potentially older. The report includes selected SOC/target/timestamps/quota/wallbox/monitor state, but no VIN, passwords, keys, raw responses, IP address or free-form errors. Use `Diagnostics.cmd --port 8774`, `--data-dir "C:\MyDataFolder"`, `--offline` or `--output "C:\Reports\report.json"` as needed. Normal backend monitoring remains active during the read; the script sends no start/stop request itself.

See [TEST-REPORT.md](TEST-REPORT.md) for validation and outstanding hardware checks.

Heater 1 shows **Automatic · surplus** in its existing status area when Smartfox reports automatic operation (status 0) and positive actual output. Zero output remains **No surplus**; manual and schedule states retain their own labels.

The selected smooth CitiFoxCharger logo combines a green city car, red fox tail and charging plug. Its symbol appears in the header, browser favicon and touch icon. Transparent PNG assets and usage notes are in [BRANDING.md](BRANDING.md).

The repository was reviewed before public publication, including reachable Git history and excluded runtime files. See [PUBLICATION-REVIEW.md](PUBLICATION-REVIEW.md) for scope, findings and limitations. API keys and personal configuration belong only in the local Windows profile.


### Diagnostics in Settings

Open **Settings → Diagnostics and connection details → Show diagnostics**, or **Download JSON**, locally or over the configured HTTPS home-network listener. `GET /api/diagnostics` uses the current authenticated session and returns a shared allowlisted report: versions, cached SOC and vehicle timestamps, charger status, charging target, stop cause and trigger timestamps, freshness-probe count and monitoring state. It excludes VIN, API keys, passwords, paths, arbitrary upstream text and raw device responses. Opening or downloading diagnostics does not contact devices or send commands. `Diagnostics.cmd` remains available on the tablet.

### Missing vehicle data during charging

The overview shows the age badge and vehicle/report timestamps without an extra older-data consent panel. The target tablet layout stays compact.

Within the last five minutes before the SOC age limit, the scheduler can advance up to three cloud reads per unchanged vehicle report, at least 90 seconds apart, when quota and server cooldown permit. The two-command reserve is retained. Regular earlier reads are reused; restarting does not reset the probe bound. This reads the cloud cache and cannot force the car to measure again.

If the cloud still supplies no current SOC, the existing maximum 15-minute monitoring age limit remains. A confirmed safety stop is shown as **target not confirmed reached**, with the cause retained across restarts, rather than successful completion. After vehicle stop confirmation the existing A+ restoration remains; schedules can charge later. No automatic start retry is introduced.


Version 1.8.12 adds a read-only `vehicle_charging` section to diagnostics from the existing charging/operations GET: reported vehicle target, preferred/available modes, battery-care target/mode, current limit and whether the response advertises mode/limit operations. Cable plug/lock states are included when known. Missing fields remain unknown. No mode, limit or profile writes are implemented; support advertised by a vehicle is evidence for diagnosis, not permission or proof of a successful command. The API may accept a start while later vehicle telemetry still reports `CHARGING_INTERRUPTED`; physical charging is verified separately through wallbox power.


Version 1.8.13 gives **Download JSON** a dedicated authenticated `/api/diagnostics/download` attachment route with a fixed filename. The GUI fetches the sanitized report in the current session and downloads a UTF-8 JSON Blob, avoiding dependence on a navigation link's `download` attribute. Without JavaScript, the link still points to the attachment route. Device polling and charging control are unchanged.
