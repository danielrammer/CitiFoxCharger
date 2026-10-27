# Windows tablet operation

Current application: **1.11.0**, documentation checked **27 October 2026**.

## Installation and display

Install Python 3.11 or newer. The application uses the Python standard library; HTTPS certificate setup additionally needs Windows/.NET Framework 4.7.2 or newer. Install Git for Windows for updates. Extract into a fresh folder or clone the repository; use the supplied scripts from that program folder.

`Start.cmd` starts/reuses the background backend and opens Brave full screen at `http://localhost:8765/`. If Brave is unavailable, it uses the default browser. Press F11 to toggle full screen; an existing browser process can ignore startup flags. The app does not rotate Windows.

The target is an 800×1280 portrait viewport on a 1280×800 panel. Compact tablet layouts support display scaling and landscape. On phones, kW and battery share a row; charging animation moves below them. Language and theme are saved per browser. Target/charging state is shared by the backend.

## Command scripts

| Script | Action |
|---|---|
| `Start.cmd` | Background backend and full-screen browser |
| `Start-Background.cmd` | Background backend only |
| `Stop.cmd` | Clean backend shutdown when the charging guard permits |
| `Restart.cmd` | Guarded backend restart with the same profile |
| `Stop-Charging.cmd` | Charging stop with an explicit persistent SMARTFOX Off |
| `Update.cmd` | Guarded Git update; choose Start, Start-Background or leave stopped |
| `Setup-Network.cmd` | Configure optional HTTPS LAN access |
| `Setup-Firewall.cmd` | Administrator setup of the private-LAN firewall rule |
| `Diagnostics.cmd` | Export sanitized support data |
| `Test.cmd` | Offline Python tests using simulated devices |

Stop, Update, network/firewall setup, Diagnostics and Test windows wait for a key after completion. Start scripts do not keep a console open. Scripts use `py -3` or fall back to `python`. For a custom profile/port, use the same arguments consistently, for example `Start-Background.cmd --port 8774 --data-dir "D:\CitiFox profile"`.

## Data folder and updates

Program files and private runtime data are separate. The default is `%LOCALAPPDATA%\CitiFoxCharger`; an existing `%LOCALAPPDATA%\Ladezentrale` retains priority. `--data-dir` overrides both. The active profile contains `config.json`, `network.json` when LAN access is enabled, encrypted credentials, monitoring state and logs. Do not create a second profile to solve a missing-folder problem.

The gateway configuration belongs in that active profile beside `network.json`, **not beside Start.cmd**. The app does not search the program folder for it. The public CER certificate is a different file: network setup can export a copy into the program folder. See [NETWORK.md](NETWORK.md).

Use Settings → Application update locally or over the configured LAN listener; public-domain access cannot update. Save the API key with Windows encryption first. The guarded updater checks the expected repository, main branch, clean tracked source and fast-forward ancestry. Unrelated local reports remain; genuine incoming path collisions abort without overwriting them. No reset/stash/delete is performed. Updates restart the same profile and do not start charging. `Update.cmd` also supports ZIP installations by creating a separate adjacent Git checkout.

Updates and process-control actions remain guarded during monitoring. An update failure attempts backend recovery; `update.log` and `background.log` in the profile provide details. A page refreshed after an update gets the new frontend. Settings shows the backend version, eight-character Git hash and date.

## Screen off versus system sleep

Monitoring runs in the Python backend, independently of the browser. The app requests Windows execution/system availability during monitoring; it does not keep the display on. The screen may turn off, but system sleep, hibernation, shutdown or a flat battery can interrupt the monitor. Keep the tablet connected to power and verify its power-button action/power settings. No Windows service or automatic-login task is installed by the app.

## Optional Windows lock-screen settings

The Windows picture/clock screen that requires an upward swipe is separate from password/PIN sign-in and from CitiFoxCharger. These are manual Windows settings; the app never changes them.

First open `ms-settings:signinoptions` with Win+R. If suitable for this wall tablet, set **Require sign-in** to **Never**. This removes the request to sign in after waking; it may not remove the picture/clock screen. [Microsoft sign-in options](https://support.microsoft.com/en-us/accounts-billing/security/sign-in-options-in-windows).

On Windows 10 Pro/Enterprise/Education, `gpedit.msc` → Computer Configuration → Administrative Templates → Control Panel → Personalization → **Do not display the lock screen** → Enabled controls that screen. [Microsoft policy documentation](https://learn.microsoft.com/en-us/windows/client-management/mdm/policy-csp-admx-controlpaneldisplay#cpl_personalization_nolockscreen).

Windows 10 Home does not include that editor. A reported Home workaround is the following command in **PowerShell as administrator**:

```powershell
reg add "HKLM\SOFTWARE\Policies\Microsoft\Windows\Personalization" /v NoLockScreen /t REG_DWORD /d 1 /f
```

This is a machine-wide Windows setting. Its effect can depend on the Windows version and has not been confirmed on this tablet. Restart Windows only when no charging monitoring is active, then test screen-off/wake. To reverse it, use the same command with `/d 0`. [First-hand Home report in Microsoft Community](https://learn.microsoft.com/en-nz/answers/questions/2842948/can-you-automatically-login-after-sleep-on-windows).

## Developer checks

`Test.cmd` runs Python tests. With Node.js installed, the frontend checks are:

```powershell
node tests/test_charging_display.js
node tests/test_certificate_download.js
node tests/test_public_ui.js
```

The Python gateway tests use disposable native certificates on Windows; no OpenSSL CLI or trust-store changes are needed. Real Apache integration tests require Linux and are skipped on Windows. See [TEST-REPORT.md](TEST-REPORT.md) for actual dated results.

## Solar preparation

Sunrise/sunset are calculated locally from the optional location in the active data profile's `solar-preparation.json`. The dashboard shows the times only in Settings. This is a phase-policy preselection, not a guarantee that the idle wallbox physically switches before charging. Manual Start remains available during daytime and prepares phases on demand. The protected setup route `/api/solar/location` accepts latitude/longitude; no scheduled night-time fields or forecast API key are used. Keep the Windows date and time zone correct. Never commit the location file or a private deployment example.
