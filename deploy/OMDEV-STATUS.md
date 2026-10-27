# omdev public access status — 2026-10-26

## Completed

Apache already serves Maya on TCP 80/443. No competing proxy or production CitiFox
backend was started. DNS A resolved to 212.89.171.186; no AAAA was returned.

The user successfully ran `prepare_omdev.py` locally. CitiFox now has its own
password-protected holding vhost, after Maya's include. Public username is `dnl`.
The script verified missing/wrong login rejection and correct login acceptance.
Follow-up reads of `/`, `/app.js`, `/api/session`, `/api/status` and
`/api/diagnostics` returned 401 with the CitiFox realm. Maya retains its own realm.
No production forwarding to the tablet is enabled.

Let's Encrypt issued the public certificate, expiring 2027-01-24. HTTP-01 issuance
proves the CA reached port 80 externally; router configuration itself was not read.
The certificate-specific renewal dry run succeeded. Apache and the existing
`maya-cert-renew.timer` are enabled and active. Its existing deploy hook validates
and gracefully reloads Apache. The global ServerName warning is pre-existing.

Root-only config backup: `/var/backups/citifox-20261026-174228`.
Protected transfer configuration, outside this Git repository:
`/home/dnl/Work/CitiFoxCharger/gateway-transfer/public-gateway.json`.
Do not paste this configuration or any credentials into chat or logs.

## Required before enabling forwarding

1. Review/install the latest PR #2 code on the Windows tablet through the normal
   guarded release/update process. The updater requires main. Copy the protected
   transfer configuration into the actual active tablet data profile. Do not force
   restarts or change charging parameters, device settings, schedules or monitoring.
2. Obtain the current public `network/CitiFoxCharger-Home-Network.cer` from that
   profile. The supplied CA from 2026-10-17 failed strict verification of the live
   tablet certificate from 2026-10-19. Never bypass this failure or copy private keys.
3. Verify the new CA fingerprint independently on the tablet, and verify its live
   chain, expiry and IP SAN 192.168.0.95. The live leaf CN is CitiFoxCharger.
4. Back up /etc/hosts, check conflicts and add the fixed alias
   `192.168.0.95 citifoxcharger`. Apache uses this name for strict upstream TLS
   validation and a fixed authenticated public-authority header for the public
   Host checks. See NETWORK.md; do not enable ProxyPreserveHost for this deployment.
5. Verify gateway permissions, Host/Origin/session/CSRF rejection and unchanged
   passwordless LAN access on the tablet. Use simulated adapters for switching
   tests; production validation must be read-only.
6. Only then replace the holding page with the documented proxy include, preserving
   mandatory Basic authentication, validate Apache and reload it gracefully.
7. Test HTTPS from real mobile data with Wi-Fi disabled. Port 443 has only been
   checked from the home network; external HTTPS and live public app usability
   remain unconfirmed.

## Automated validation

Nine gateway policy/HTTPS tests and three real Apache integration tests passed,
along with the inert-DOM UI permission check. Apache tests cover missing/wrong
login, header sanitization, restricted routes, wrong CA and wrong certificate name.
They use isolated loopback listeners and inert or mocked device methods.

The earlier full Linux suite ran 467 tests with 22 skips and one existing
Windows-DPAPI-only error in
`test_restart_requires_persisted_key_and_resumes_same_target_without_start`.
Native Windows network and DPAPI coverage still needs to run on Windows.

## Disable only public access

Restore only `/etc/httpd/conf/extra/citifox-tls-closed.conf` from the checked-in
holding vhost, validate with `httpd -t` and reload `httpd.service`. Keep the HTTP
challenge vhost for renewal. This disconnects public application access while
preserving Maya and the tablet's LAN dashboard and monitoring. The current
installed configuration is already in this disconnected state.

## Follow-up — 2026-10-27

The user supplied the current public CA. Strict curl and OpenSSL verification now
succeeds against the live tablet, including its certificate chain and IP SAN
192.168.0.95. The supplied root is valid until 2028-10-19; the live server leaf is
valid until 2028-10-18. A PEM copy is staged outside Git at
`/home/dnl/Work/CitiFoxCharger/gateway-transfer/tablet-current-ca.pem`.

The live tablet reports 1.10.4, commit 5d24163b. That main commit adds certificate
downloads and battery UI improvements but does not yet contain the public gateway
implementation. PR #2 is synchronized with this main version; certificate downloads
remain available on local/LAN access and are hidden/denied on public access.
Production proxy forwarding must remain disabled until the latest gateway code and
protected gateway configuration are installed on the tablet. Installing the CA into
the system proxy configuration and activation will also require local root access.

## Windows review follow-up — 2026-10-27

PR #2 also includes the 1.10.5 charging display and is prepared as 1.10.6.
The Windows OpenSSL CLI test dependency was removed by reusing the disposable
native certificate helper; all 474 Windows offline tests completed successfully
with the three Linux/Apache-only integration tests skipped. The three Node UI
checks pass. These are simulated/loopback checks, not production switching tests.

This reviewer independently verified the tablet's HTTPS health endpoint using its
current public CA with hostname validation enabled: the running release remains
1.10.4, commit 5d24163b. No tablet update or public-server activation was performed.
The existing protected gateway transfer file still needs to be copied to the active
tablet profile; it is not included in this repository. Do not paste it into logs.
