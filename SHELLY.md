# Shelly fan and light

Version 1.13.0 adds two compact icon switches to the charging overview. Each
controls output 0 of a **Shelly 1 Mini Gen4** over its local Wi-Fi API. The
owner-authorized installation defaults are Fan at `192.168.0.100` and Light at
`192.168.0.85`. These private addresses are not credentials. Keep DHCP reservations
for them; the app rechecks the exact model/generation before every output command.
No output is switched by installation, startup, settings changes or status reads.

In **Settings → Fan / Light → When switched on**, choose independently:

- **Always on:** send explicit On without an app timer.
- **30 / 60 / 90 / 120 min:** send explicit On and the chosen automatic-Off timer
  together in a single `Switch.Set` request. Both switches default to **60 min**.

Changing this preference affects the next On operation and does not extend or
replace a running timer. Off sends explicit Off. A later deliberate Off/On starts
a new interval. Page refreshes, backend restarts and duplicate command IDs never
replay an On command or extend its timer. The app persists command intent before
writing; an ambiguous result requires checking the device before a new attempt.

The timer is executed by the **Shelly**, independently of the browser, tablet,
proxy and Internet connection. The app reads back output state and timer duration;
it does not treat an accepted command as confirmation. A missing/mismatched timed-On
confirmation requests Off as a precaution. Device time must be correct for the
app to verify/display the timer deadline. Shelly power loss/reboot and other
device schedules, physical inputs, scripts or controllers remain subject to the
device's own settings; a temporary timer is not a guarantee across a device reboot.
This integration does not rewrite those settings, initial power-on state or schedules.

Offline or stale devices show **Unknown** with a disabled overview switch. An
offline fan does not prevent operating the light. Reads use the existing tablet
worker, with ten-second caching and a sixty-second backoff after failure. The
integration adds no backend on the Linux proxy, no vehicle polling worker and no
changes to charging, energy estimation, SMARTFOX settings or charging schedules.

## Access and private state

Local tablet access can edit addresses, enable/disable devices and enter an optional
Shelly password under the advanced device-access section. Saved passwords use
per-user Windows DPAPI and are bound to the destination address. A blank password
keeps the saved password for the same address; changing addresses clears it unless
a replacement is supplied. No password is returned in API responses or diagnostics.

The direct LAN interface can change only the On behavior, without editing addresses
or credentials. Public access can operate the two switches through the existing
authenticated gateway. Public access cannot read/change Shelly setup or preferences;
the corresponding settings forms are hidden. Host, Origin, browser session, CSRF
and gateway assertion checks continue to apply to every operation.

The device transport uses the documented private-LAN HTTP API with SHA-256 Digest
authentication when configured; redirects and environment proxies are disabled.
No arbitrary URL, method, channel, duration or credential can be supplied through
the public output endpoint. It accepts only the known device identifier, a boolean
output state and a command ID; the backend applies the saved On behavior. Existing
browser-to-gateway and gateway-to-tablet HTTPS verification is unchanged.

Private files are stored under `shelly/fan/` and `shelly/light/` in the active
Windows data profile: `shelly.json`, `shelly-password.dpapi`, and
`shelly-commands.json`. They are excluded from Git. A corrupt/unreadable profile
disables its output instead of replaying a command. Disabling the integration does
not switch a relay or cancel an already running device timer.

To remove only the public route, follow [NETWORK.md](NETWORK.md#disable-only-public-access).
The tablet and its local Shelly timers continue independently.

API references: [Switch methods and timer status](https://shelly-api-docs.shelly.cloud/gen2/ComponentsAndServices/Switch/),
[authentication](https://shelly-api-docs.shelly.cloud/gen2/General/Authentication/),
[Shelly 1 Mini Gen4](https://kb.shelly.cloud/knowledge-base/shelly-1-mini-gen4).
