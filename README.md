# CitiFoxCharger for Windows · 1.10.5

<img src="citifoxcharger/web/brand/logo.png" alt="CitiFoxCharger logo: smooth electric city car, red fox tail and charging plug" width="620">

A local dashboard for a Škoda Citigo e iV and a SMARTFOX Pro 2 energy manager with a Charger Pro at charging point 1. The installed SMARTFOX profile is **EM3 00.01.16.08**, address **192.168.0.81**.

Documentation, program filenames, command scripts and command-window messages are English. The browser interface offers **Deutsch / English** in **Settings → Appearance → Language**. The preference is saved in each browser. A German browser initially uses German; other browsers use English.

Both light and dark appearance are available. The light palette uses darker labels and more distinct borders for better readability on the wall tablet.

The header displays the current date and time from the browser device, independently of vehicle telemetry. It refreshes on returning to the page and uses the selected interface language. The clock fits inside the existing header height.

The Settings page identifies the running backend as **v1.10.5 | first eight Git characters | commit date**. Git installations read the exact program repository at startup; release ZIPs include the same commit/date in `citifoxcharger/build-info.json`. If metadata are unavailable, the hash is shown as a dash rather than invented. The **Reload page** button (⟳) in the header reloads the current browser page without restarting the backend or sending a charging command. It works during charging and is temporarily disabled while a command or app update is in progress.

## Architecture and communication

See [COMMUNICATION.md](COMMUNICATION.md) for the system and start/stop diagrams, exact Modbus addresses and word counts, XML data fields, SMARTFOX CGI commands, Škoda API calls and browser-to-backend routes. Mermaid diagrams render directly on GitHub. Protocol evidence and compatibility limits are in [INTERFACES.md](INTERFACES.md).

## Supervised vehicle-stop experiment (introduced in 1.9.5; updated in 1.9.7)

**1.9.7 uses a maximum of 60 minutes from the one Start POST for new trials.** It does not stop at the selected SOC. The +2 SOC and device-error fallbacks, phase waiting bounds and manual Stop remain. Any app/user fallback is explicitly `inconclusive_app_stop`; a prior power drop remains `external_power_drop_observed`, not proof of a native target. Historical reports keep their original runtime limit; updates never extend an active experiment. New Start still requires a limit attempt at most ten minutes old, which is a separate freshness gate.

The overview links directly to trial preparation. **Charger Off for test** is available in the test panel; it sends no limit or Start. After the explicit limit request arms isolation, the overview Start button becomes **Start vehicle-stop test · 60 min**, with the same explicit confirmation as Settings. It cannot send a normal app-monitored Start while isolation is armed. The normal button identifies **app stops at target**, and the active trial header says **no app stop at target**. Diagnostics retain the completed trial even after an explicit normal 80% request.

**1.9.6 introduced handling for phase switching reported after Start.** A known phase-switching pause no longer counts as an external target stop. The backend keeps M+, waits at most six minutes for switching and then at most three minutes for fresh physical charging power. It sends no additional Start. If power does not resume, the distinct `phase_resume_failed` app stop explains that limitation; it is not successful vehicle target completion. The persisted trial deadline and target +2 fallback remain in force and are never extended. A vehicle that needs another authorization after switching may still require a separate user attempt; automatic Start replay is not introduced.

This optional experiment permits **one explicit Start after an accepted or uncertain limit write**, without readback. An upstream HTTP 500 stays unknown; a definite rejection blocks the trial. This is a supervised compatibility test, **not reliable target charging**. The normal manual charging strategy is unchanged.

For a reported SOC of **35%** and an exact test target of **37%**:

1. End regular charging. Select **Charger → Off in CitiFoxCharger** and wait for Off and 0 kW.
2. In **Settings → Vehicle charging limit → Isolated vehicle-limit test**, enter **37** and click **Send test limit · no charging Start** once. The API schema permits integer limits; the vehicle may reject 37%. There is no rounding.
3. Click **Supervised Start with unconfirmed limit**, read and confirm. It uses the exact last limit attempt, at most ten minutes old and 1–5 points above reported SOC. It does **not** resend the limit. The backend selects M+, waits up to six minutes for phase switching/readiness and three seconds of settled readiness, then sends one Start. No automatic Start retry.
4. Observe power and the car's SOC. The app **does not stop at 37%** during this trial. Charging proof requires two local ≥0.5 kW samples at least three seconds apart. A subsequent ≤0.1 kW period lasting 30 seconds is recorded **before** securing Off. That shows power dropped without an app stop; it does **not** prove the cause or successful application of the target. Check actual vehicle SOC separately.
5. **Stop trial · charger Off**, the main Stop button and the Off dropdown all cancel the trial. Fallbacks also stop locally after **60 minutes from Start**, a **new SOC report at target +2 points**, no confirmed power within three minutes, lost local telemetry, failed preparation or backend restart. Each has a distinct stop cause and is explicitly marked **inconclusive** rather than successful vehicle target completion. These fallbacks require a running backend and reachable SMARTFOX.
6. The trial leaves SMARTFOX **Off**, with isolation active. Request the normal **80%** explicitly, inspect the result, then end isolation. Restoration remains unconfirmed without readback or direct vehicle verification. Choose A+ separately. No profiles or timers are edited.

The backend monitors with the browser closed. Updates, restart and exit are blocked while a trial runs. Unexpected restart cancels pending Start and secures Off; it never resumes the trial or replays Start/limit. Diagnostics include responses, power/phase timestamps and stop cause without credentials or VIN. The small state machine is in [`limit_trial.py`](citifoxcharger/limit_trial.py): `waiting_ready → observing ↔ waiting_resume → stopping → finished`, persisted in private `vehicle-limit-test.json`.

## Isolated vehicle-limit test (introduced in 1.9.4)

This release adds a capability test in **Settings → Vehicle charging limit → Isolated vehicle-limit test**. It does not deploy the separate 2.0 state-machine branch and does not fix or change the normal SOC-controlled charging strategy below.

1. End the current charging session first.
2. On the CitiFoxCharger overview, choose **Charger → Off** and wait for Off and 0 kW. Selecting Off in CitiFox also preserves the explicit Off choice against normal A+ normalization.
3. Open the test in Settings, explicitly enter an integer from **1 to 100%** and click **Send test limit · no charging Start** once. The vehicle may reject particular values; no rounding is performed. Do **not** click Force charge for this test.
4. Read the immediate result. **API accepted / HTTP 202 is not vehicle confirmation.** If the API omits the limit, the result explicitly says unconfirmed. The test performs no extra GET loop, automatic retry, charging Start/Stop, SMARTFOX mode write, profile write or timer edit. Check the actual setting in MyŠkoda or the vehicle if available. A missing field is unknown, not proof that setting failed.
5. Use **Send normal 80% limit** to request restoration explicitly. It is still unconfirmed unless a newer capture reads back exactly 80% or you check it directly. Then choose **End test · keep Off** and acknowledge the explanation. Ending isolation sends no vehicle or SMARTFOX command; explicit Off remains selected. Select A+ separately only when ready to permit normal schedules again.

Normal charging starts, A+/M+ changes, VIN edits and key removal are blocked while test isolation is active; the separate supervised trial above is an explicit exception. Local Off/Stop and key replacement remain available. Test state, uncertain outcomes and request IDs survive restart; no write is replayed. A 30-second spacing prevents repeat test commands (the first explicit 80% restoration can immediately follow another target). The test reserves requests for restoration and respects upstream cooldowns. Existing scheduled vehicle reads may confirm an exact newer readback; there is no automatic wait for a field the car does not return.

The same protected endpoints and sanitized diagnostics work over the configured HTTPS home network, including passwordless mode. Diagnostics include targets, accepted/rejected/unknown outcomes, HTTP status, published problem identifiers and readback state, but no VIN, key, identifiers or raw API body. Private test state belongs in the local profile, not Git.

## Manual charging strategy

**Since 1.10.0, new manual sessions without a readable native vehicle limit use measured SMARTFOX charging energy plus vehicle SOC reports.** Manual Start works while A+ surplus charging is already running. It selects M+, adopts existing energy flow, and sends a vehicle Start only if settled local readiness has no power. Phase switching is a pause, not a completed charge. Stale cloud SOC or exhausted read quota alone no longer stop these sessions. The app stops locally first when either its energy-based SOC estimate or a vehicle SOC report reaches the target. No vehicle-limit PUT or charging-profile change is made by this route.

Older SOC up to 24 hours is used automatically as the initial estimate, with its original age visible. **This is not an exact battery limit:** the starting integer SOC may be old, usable capacity changes, and 90% charging efficiency is an assumption. The estimate is marked `*`. A newer vehicle capture corrects the anchor at its capture time, adding only measured energy since that time. Measurement gaps increase uncertainty and do not immediately stop charging: the requested tolerance is up to roughly five extra points, with a separate modelled `target +5` upper-bound fallback. Storage failure still secures Off. API cooldowns queue a start with a waiting indication. At most three vehicle Start attempts are allowed, with settled zero-power readiness and spacing of at least three minutes; no attempt is sent during phase switching or measured charging. See [MANUAL-CHARGING.md](MANUAL-CHARGING.md) for states, stop causes and calibration limits.

When a native limit **is** readable and its operation is advertised, Start retains the native exact-target preflight below. Existing active native sessions keep their verification/restoration logic; an aborted pending native attempt must be explicitly released before switching strategy. There is no silent fallback after an uncertain native write. The normal route uses the common server-side strategy dispatcher, including over the home network.

## Vehicle-enforced charging target

**Native compatibility gate introduced in 1.9.2:** a valid current vehicle limit must already be readable before a native target attempt can change SMARTFOX or the vehicle. Advertised `setChargingLimit` support alone is insufficient. The supplied Citigo responses omit this field, so native target charging remains unconfirmed; 1.9.3 uses the app-monitored path above instead of blocking all manual Start.

If a 1.9.0/1.9.1 preparation was cancelled before any vehicle Start and is stuck restoring the limit, **Release A+ normal mode** replaces the disabled Start button and is also available in Settings. Read and confirm the warning: A+ returns control to vehicle/SMARTFOX rules and schedules and may allow charging; the vehicle limit, including the expected 80%, remains unconfirmed. The action checks fresh local Off/non-charging power at or below 0.1 kW, sends only a readback-verified local A+, closes target control and stops automatic limit-restoration attempts. It never sends vehicle Start or another limit PUT, and never labels the limit restored. It is not available after a vehicle Start, during retargeting, for another VIN or while restarting/updating. Failed local A+ readback retains the guard. A restart preserves the unconfirmed-limit warning.

To upgrade a backend stuck in this older preparation: press **Stop** once, wait at least five seconds for settled local Off/zero power, then use **Update.cmd**. The old backend's existing local-stop check permits a clean update without claiming cloud stop confirmation. After version 1.9.2 starts, use **Release A+ normal mode** and confirm its warning. If local stop cannot be confirmed, check the charger rather than forcing process termination.

Diagnostics preserve target and restoration PUT outcomes separately: HTTP status, an allowlisted published problem identifier, error category, remaining budget and cooldown/reset snapshots. Initial verification evidence is not overwritten by restoration. `vehicle-not-accepting-requests` is distinguished from `rate-limit-exceeded`; both can use HTTP 429. No raw problem details, arbitrary URLs, VIN or key are exported. Reports generated by old versions cannot recover these missing historical answers.

Version 1.9.1 fixes a disabled Start button when the vehicle report is aged and the last successful API read is more than five minutes old. Limit-only preparation may use the bounded known SOC without first requiring a newer API read. The actual vehicle Start still requires the new matching post-write limit capture and the ordinary access/readiness checks. This distinction cannot be used through the raw Start command route. Closed old monitoring is labelled as closed rather than as an active Off hold.

After passing that compatibility gate, a new manual start writes the exact selected target using the official `PUT /api/v1/vehicles/{vin}/charging/limit`. An accepted request is not enough: the same target must be read back from `charging.settings.targetStateOfChargeInPercent` in a newer vehicle capture after the write. Until then, the wallbox stays Off and no vehicle Start is sent. Some vehicles accept only 50–100% in steps of ten; the app permits an exact integer such as **87%** for testing, but never rounds or retries another target automatically. A rejected or unconfirmed value is an error, not claimed compatibility.

Once confirmed, M+ and the existing phase-readiness gate precede one vehicle Start. The verified vehicle target remains in force if SOC polling becomes stale or quota runs out, avoiding the old cloud-only freshness stops. Physical accuracy and native-limit support on this Citigo still require a real write/readback/charge/restore test. Existing SMARTFOX M+ time/energy settings can independently stop a charge and are not changed.

The previous readable vehicle limit is restored after the stop; when missing, the user-selected normal value is **80%**. Charging schedules and charge mode are not written. Restoration also requires readback before returning to A+. Settings exposes a bounded retry if restoration is unconfirmed. If the API permanently omits the field, an explicit alternative lets the user confirm the actual restored limit directly in MyŠkoda/the vehicle after local Off and zero-power verification. A timer profile alone is not proof. This manual confirmation is recorded separately from API proof and never sends Start.

## Separate proof of charging power

The app confirms a start from two fresh SMARTFOX charge-point power reports of at least **0.5 kW**, at least three seconds apart. House consumption, a raw wallbox status label and an accepted API command do not establish this proof. The diagnostic records `start_confirmed_at` and `start_confirmation_source: smartfox_power`. This proves energy flow at that time; it neither updates SOC nor proves target completion.

## Older SOC and existing sessions

Manual Start automatically allows the last reported SOC within the existing age/access bounds; there is no checkbox. The original capture timestamp and age badge remain visible. Requests still respect Škoda cooldowns and the shared per-VIN quota, reserving calls for stop and restoration. Diagnostic stop snapshots preserve remaining quota, cooldown and reset seconds at the trigger, even if the quota later replenishes.

Sessions saved before native-limit control retain the earlier cloud-only five-minute/15-minute age and request-quota stop rules. They are not silently treated as vehicle-protected. A delayed post-stop vehicle capture can release an old stop guard when the existing fresh local Off/zero-power checks also pass. Explicitly selected Off and deliberately closed sessions remain respected.

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

The wider battery graphic shows **Current** and **Target** side by side. It uses the same reported/estimated SOC as the main percentage (including `*` for an estimate), and follows slider edits immediately. Its height is unchanged on the tablet; narrow phones place battery/power on a separate row.

The main page shows vehicle SOC or the explicitly marked energy estimate (`*`), current local charging power, charging state, an editable target slider, **Manual charge** and **Stop**. A moving energy stream, smiling car, striped battery and pulsing indicator show charging only when fresh local power exceeds 0.1 kW, or fresh vehicle CHARGING is available without a local measurement. HTTP 202 alone does not animate charging. Reduced-motion preferences disable movement.

### Manual charge with a readable native vehicle limit

1. Choose **50, 60, 70, 80, 90 or 100%**, above the reported SOC.
2. The wallbox is held Off while the vehicle limit is set and verified. The interface shows this preparation; Stop and explicit wallbox Off cancel a pending start.
3. Once the vehicle limit is confirmed, select M+ and require three consecutive seconds of fresh readiness/current permission. An explicitly reported phase switch continues in the cancellable backend preparation state, with its existing six-minute deadline. One vehicle Start follows stable readiness. Restart never replays a pending start.
4. Fresh positive local power confirms that charging actually began. An accepted Start alone is not charging confirmation. The vehicle enforces its verified target without relying on live cloud SOC; the backend still requests its local-first stop when a target report arrives.

The slider remains editable during monitoring. Changing a native target pauses the wallbox, writes and verifies the new limit, and resumes only if charging was physically confirmed before that change. An explicitly Off or unconfirmed session is not started by changing its target. Lowering the target to an already reported SOC requests a local-first stop immediately. If a lower limit cannot be changed because of exhausted quota, the app stops locally as a precaution; it does not keep using a higher old limit while displaying a lower new one.

### Stop and restoration

Stop first requests and reads back **Off** at the wallbox, then requests vehicle Stop. Cloud failure cannot bypass the local Off attempt. The previous/normal vehicle limit is restored only after stop confirmation, then verified before normal A+ is released. Missing restoration confirmation remains visible and holds Off. Stop never claims the vehicle reached the target merely because power is zero.

### Older data and retrying a start

Clicking **Force charge** or selecting M+ for a new manual session automatically permits the last reported battery level; there is no separate checkbox. For limit-only preparation with an aged report, SOC must be known and no older than 24 hours, the cached access/operation checks must pass, and local readiness with no charging power must be at most 30 seconds old. The actual Start still requires a recent successful API read and the new matching limit confirmation. The displayed timestamps and stale label remain unchanged. It does not override a missing key, unsupported operation, server cooldown or active stop.

**Retry start** requires fresh local readiness, no physical charging above 0.1 kW, available start/stop access and at least 30 seconds since the previous attempt. A newer non-charging report or ten minutes of unconfirmed outcome is also required. A new deliberate click sends one new start and preserves the existing target. Duplicate request IDs do not repeat commands. Starts are never automatically replayed, including after restart.

**Stop** remains available for an active session even when the vehicle is unreachable, the key expired or the cloud command is blocked. It durably records the stop and requests verified local Smartfox **Off before any vehicle read or command**. Off is held until a newer stopped vehicle report permits the normal-mode return. A failed local request is not reported as stopped and is retried independently of cloud backoff. Unknown stop outcomes can be retried only after newer CHARGING telemetry, with bounded attempts. A newer stopped report can confirm the stop without another command.

### Recovering an older backend with a disabled Stop button

If the dashboard says the vehicle is unreachable while **Stop.cmd** says charging monitoring is active, extract the new package into a separate folder on the tablet. Do not start a second backend. Run **Stop-Charging.cmd** from that new folder; it uses the existing local backend on port 8765, requests persistent charger Off and a vehicle stop, then checks the stored stop intent and fresh local Off readback. It does not require the vehicle API key, kill a process or pretend that the vehicle confirmed the stop. For another local port use `Stop-Charging.cmd --port 8774`.

After its Off confirmation, wait at least five seconds and run **Stop.cmd**. The backend independently checks current power and status before allowing exit. You can then run **Start.cmd** from the new folder or update the existing Git installation. The recovery command leaves the charger explicitly Off until A+ is deliberately selected or a new manual session is started. If any confirmation fails, check Smartfox and leave monitoring running.

## Home energy and outputs

The analogue heater uses **actuation percentage** as its main reading in manual/forced modes, matching the installed Smartfox display. A 100% output can coexist with an unavailable/zero Smartfox power estimate; it is not shown as zero consumption or an inactive output. The percentage is the actual reported output, not the configured manual setpoint, and it does not prove the heater's electrical consumption (a thermostat can interrupt it). The green indicator follows reported actuation when present. Relay mode values **0 and 1 both mean Automatic**; `m` is Manual and `x` is Off. Other unknown values remain unknown. The station alone uses Off/A+/M+; heating outputs use Off/Automatic/Manual.

The compact energy panel shares the charging page. House use follows the installed vendor calculation **production + grid − battery** and includes controlled consumers. Positive grid power is import; negative is export. The moving dot covers the full line. Heater shows measured kW, percentage and temperature. Heater 2 and Hot water show relay state and assigned temperature; relay power is never invented. Missing values appear as **—**.

- **Heater / Heater 2 / Hot water:** Off, Automatic or Manual. The analog heater keeps its existing manual percentage. Relay Manual/Off cancels an existing manual timer as the vendor UI does. Temperature limits and schedules are not edited.
- **Charger:** Off, A+ or M+. Explicit Off persists. Idle M+ uses the same monitored Force charge flow. During monitoring a permitted mode change does not send another vehicle start.

Controls are limited to the installed output assignments and exact firmware. The device-side names remain unchanged for matching the verified outputs. A firmware mismatch, missing assignment or failed readback blocks control. Modbus remains read-only. See [INTERFACES.md](INTERFACES.md) for evidence and limitations.

## Freshness and estimated duration

Near an active manual target (one or two percentage points remaining), the backend can advance at most **two cloud reads per session** to **60 seconds after the previous attempt**. This requires actual fresh charging power, a vehicle report no older than five minutes and at least six requests remaining in a known quota window. Each distinct vehicle capture can schedule only one earlier read. Missing data, phase preparation and server cooldowns do not trigger faster reads. The allowance survives a backend restart. Other polling keeps the existing quota budget and command reserve. An earlier cloud read does not wake the vehicle or guarantee a newer capture.

A reported charge level at or above the target bypasses the ten-second housekeeping interval. An already known target report secures local Off before starting another routine cloud query. The app still stops on reported SOC, not on a predicted charge level; it does not intentionally stop one percentage point early. Diagnostics record the triggering SOC, original capture time, retrieval time and decision time to separate data delay from stop latency.

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
