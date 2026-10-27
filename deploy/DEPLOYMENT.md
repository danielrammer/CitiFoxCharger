# Public gateway deployment

Generic guide for CitiFoxCharger 1.10.9. Keep actual addresses, domains, usernames, gateway configuration and certificate material outside Git. Examples use `TABLET_IP`, `PROXY_PRIVATE_IP`, `charger.example.com`, `GATEWAY_USER` and `TRANSFER_DIR`; replace them locally before use. See [NETWORK.md](../NETWORK.md) for the exact permission model.

## Responsibilities

The Windows tablet owns the backend, vehicle API key, charging monitor and private HTTPS certificate. The proxy server runs Apache, public HTTPS and mandatory login. Do not run a second production charger backend on the proxy server.

## Setup order

1. Configure the domain/DNS and forward only the public HTTPS/ACME ports to the proxy. Preserve existing websites and listeners; never forward the tablet's backend ports directly.
2. Back up proxy configuration with restricted permissions. Begin with an authenticated holding page and a publicly trusted domain certificate. Check certificate renewal.
3. Configure the gateway password locally. Keep its hash and the random gateway assertion in protected proxy files, never in Git, chat, command arguments or logs.
4. Transfer only the tablet's public CA certificate. Compare its fingerprint independently and verify the upstream chain, validity and tablet IP SAN. Never copy private keys or disable TLS checks.
5. Copy `TRANSFER_DIR/public-gateway.json` into the tablet's **active data profile**, beside `network.json`. Existing installations may use the historical `Ladezentrale` folder; `--data-dir` overrides the default. The app does not read this configuration from the program folder.
6. Install the reviewed application code and use a guarded restart/update when no charging monitor blocks shutdown. Verify the profile was loaded and normal LAN access still works.
7. Configure the proxy's fixed upstream alias and public-authority header as described in [NETWORK.md](../NETWORK.md). Keep the pinned proxy identity, mandatory login and strict upstream TLS checks.
8. Replace the holding page only after those checks, validate Apache configuration, then reload gracefully. Keep public updates, administration, experimental commands and LAN-certificate downloads denied.
9. Verify missing/wrong login rejection and authenticated read access. Use simulated adapters for switching tests. Complete a separate Wi-Fi-disabled/mobile-data check; LAN/NAT loopback alone is insufficient.

## Validation and rollback

The offline Windows gateway release ran 474 tests successfully with three Linux/Apache-only skips; frontend checks are recorded in [TEST-REPORT.md](../TEST-REPORT.md). These do not prove any particular site's live Internet reachability or password configuration. Keep installation-specific status records privately.

To disable only public application access, restore the authenticated holding vhost, remove its proxy forwarding, validate Apache and reload it gracefully. Preserve the ACME challenge/renewal route and other websites. The tablet's LAN operation and charging monitor continue. Removing the private tablet gateway configuration during a later safe restart removes gateway trust as well.
