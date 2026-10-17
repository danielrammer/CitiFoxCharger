# Validation report · 1.7.4

Date: **17 October 2026**. Environment: Windows, Python 3.14. Device and vehicle requests in automated tests are simulated. Tests do not send real charging or heating commands, open a real firewall rule or alter the Windows certificate trust store.

## Existing baseline and new work

The 1.6.2 update/window checkpoint passed **202 tests** and was pushed to the private repository before the English migration. Version 1.7.0 renames the Python package and command/document files, translates CLI/backend/documentation to English, retains an optional German/English browser view and adds explicit passwordless HTTPS setup. That release was pushed before the separate 1.7.1 light-theme contrast adjustment.

The complete 1.7.3 offline suite passed **230 tests**; the prior 1.7.2 suite passed 218 tests. The prior 1.7.1 suite passed 208 tests. Version 1.7.2 adds the post-update startup choice and distinguishes browser startup from backend-only startup. Test coverage includes the following behavior; hardware limitations are separate.

For 1.7.4, **28 targeted Git-update, startup-choice and Windows command-window tests passed**. This release changes update/file handling and ignore rules; charging and network logic are unchanged from the validated 1.7.3 baseline.

Version 1.7.3 fixes a reported deadlock after the tablet slept on a low battery: the vehicle showed unreachable/unknown command outcome, the UI disabled Stop using cloud permissions, and backend shutdown required a stop intent. Local stop availability is now separate from cloud capability. Every stop secures Off before cloud access. Tests cover offline stop, failed local Off, mode changes, shutdown intent and no fabricated vehicle confirmation. The recovery command was also exercised against the actual 1.6.2 backend code with simulated devices: it secured Off, recorded the stop, passed the legacy shutdown guard and did not replay a start. The reported tablet itself remains unmodified until the user installs the package.

### Charging, profiles and lifecycle

- Strict Modbus reads, schema/range/unit checks and no write route.
- Per-VIN SOC and capabilities, original capture times, key expiry, partial/error responses, request quota and cooldown.
- M+ and physical readiness before one vehicle start; failure/unknown outcomes and bounded retries without automatic starts.
- Editable target with no new start, fresh/old above-target stops, explicit Off persistence and no reopening of a stopping session.
- Regression for 82% target with an old 86% report, including blocked/offline cloud access. Local Off is attempted before cloud stop and retried independently of cloud backoff.
- Initial five-minute new-data deadline, bounded later data loss, maximum monitored report age, recovery and restart without extending deadlines.
- Off hold until a newer non-future stopped report, resistance to external mode changes and preservation across explicit close/restart.
- Exit guard requiring explicit stop, a settling period, fresh local readback in the required mode (Off for a guarded stop), non-charging status and at most 0.1 kW. No fabricated cloud confirmation.
- Persisted key requirement, cache/quota/session recovery, instance locking, same-profile restart and no device actions after shutdown intent.
- Windows power requests with no display requirement and proper cleanup. Background startup and Brave/default-browser selection.
- New profiles use the English folder; existing historical profiles retain their credentials and lock without movement or duplication.

### Heating and energy

Tests cover all three output modes, fixed vendor name/type assignments, existing analog manual percentage, relay timer cancellation, readback failures and idempotency. Energy tests cover units, import/export sign, house consumption formula, temperature slot assignment and missing 850 °C values. Relay power is never invented.

### HTTPS access and diagnostics

Tests generate native Windows certificates and use real loopback TLS with full chain/IP verification. Private keys remain DPAPI encrypted and temporary loading files are removed. Host/Origin/session/CSRF checks, local-only administration, shared monitor lifecycle, bad configuration and authenticated resource coverage are exercised.

The explicitly passwordless mode is tested separately. Missing credentials are accepted only after explicit valid configuration; existing configurations remain protected. HTTPS, remote permission boundaries and session/CSRF still apply. Empty setup password writes no hash or plaintext password. A missing session in this mode does not create an unwanted Basic Authentication prompt.

Diagnostics tests check old-backend compatibility, GET-only collection, clearly marked offline fallback and the exclusion of VIN, credentials, session tokens, raw responses and arbitrary text.

### Git updater and command windows

Real temporary local Git repositories test fast-forward, tracked edits, allowed untracked reports, diverged history, missing remote, unexpected origin, ZIP bootstrap/reuse and refusal to overwrite an unrelated directory. Version 1.7.4 adds regression checks for diagnosis aliases created before new ignore rules arrive, arbitrary local filenames with spaces/Unicode, staged new program files, ignored/untracked incoming collisions, file/directory conflicts and an ignored file created after preflight. Local data must survive every case. Updates apply the exact fetched commit without auto-stash or overwriting ignored files. Fake backend control tests protect active monitoring and RAM-only credentials. Actual Windows batch processes verify that Update, Stop, Setup-Network and Setup-Firewall wait for a key after both success and failure and preserve return codes. Update.cmd remains safe when its own file changes during the Git update. No production backend is stopped for these tests.

Startup-choice tests cover explicit browser/backend-only selection, empty input/cancellation/EOF with no start, invalid choices, startup failures, and no menu after a failed update. Launch uses a fresh Python process from the returned Git folder, retaining a custom port and profile with spaces. Real Windows start scripts run harmless argument-recording stubs; backend-only startup is verified to suppress browser opening for both new and reused instances. The updater menu flag is checked in actual batch execution. No real browser, vehicle or energy manager is started by these tests.

## Hardware evidence and limits

The installed Pro 2 / Charger 1 / EM3 00.01.16.08 firmware and local display fields were read. A+ → M+ → A+ was read back successfully after user authorization. SOC and start/stop capabilities were received from the actual Citigo, and the user confirmed physical charging after a start.

The user reported early interruptions and a failure to stop automatically at 82%, continuing to 86% until manually stopped. The supplied report came from backend **1.6.0**. It records the manual stop request at **08:14:44**, an 86% vehicle capture at **08:14:56**, and completion at **08:15:04**, Vienna time on 17 October. At report generation, local power was 0 kW and mode A+. The saved report does not contain earlier SOC history or prove that 86% was known before the manual stop. Quota reservation observed after the session is not by itself proof of the earlier cause.

Version 1.6.1 corrected identified stop gaps, but the exact tablet incident cause and a successful complete automatic percentage-stop cycle on the real car still require confirmation. Delayed telemetry can overshoot a local target. The app does not configure a verified native 82% vehicle limit or implement an unverified wake-up operation.

The tablet network listener had not been set up, as confirmed by the user. A physical second-device HTTPS/certificate-import test remains outstanding. No real passwordless listener is enabled as part of development. Explicit sleep, shutdown, mobile connectivity loss and long-term tablet power behavior are not covered by a continuous hardware trial. The app cannot monitor while Windows is actually asleep or powered off.

## Browser validation

Preview uses fake hardware and an explicit TEST label. The new light theme was checked at 800 × 1280, 640 × 1024, 534 × 854 and 1280 × 800 without page overflow. English/German switching, translated backend status and target-save messages, number/date formatting, language persistence after reload and a target change during simulated charging were verified. Dark mode was checked separately at 640 × 1024. The browser reported no JavaScript errors. These checks must not be confused with a real charging session.

Light mode now uses darker secondary text, stronger control borders, clearer status colours and readable disabled controls. Computed palette contrast ratios are 6.29:1 for secondary text on white, 5.80:1 on device tiles, 5.39:1 on the page background and 7.26:1 for the primary button text. These are checks of the listed colour pairs, not a complete accessibility certification. No charging, heating or polling behavior changed in the theme adjustment.

The simulated 800 × 1280 browser check verified an unreachable vehicle with an enabled Stop button. Clicking Stop produced verified local Off at 0 kW, kept cloud confirmation pending, blocked A+/M+ during the hold and enabled backend exit after the existing settling/readback checks. The displayed normal-mode note identifies the Off hold. This is simulated UI evidence, not a change to the physical tablet.
