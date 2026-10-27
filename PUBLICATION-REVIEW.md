# Public repository review

Review date: **19 October 2026**. Application baseline: **1.8.7**, commit `dc74a8d`. This report records a publication review, not a guarantee that future changes cannot introduce private data.

## Scope and result

Before changing repository visibility, the review covered all **25 reachable commits**, **315 distinct historical file blobs** (309 text, six PNG versions), every historical tracked path, branch/tag inventory, commit metadata and current tracked files. The two binary paths contain only generated logo/icon artwork; no live dashboard screenshots or diagnostics were tracked. PNG chunks and provenance metadata were additionally checked for local user paths, the supplied vehicle prefix, private-key headers and LAN addresses.

**No live credentials, personal vehicle VIN, private key material, persisted authentication state or private diagnostic exports were found in that scope.**

Checks performed:

- **Gitleaks 8.30.1**, downloaded from its official release and verified against the release checksum, scanned the entire reachable Git history with `--log-opts=--all` and redacted output.
- The scanner reported **one candidate** in the first commit's former `SCHNITTSTELLEN.md`: the explicitly labelled SHA-256 checksum of the reviewed public OpenAPI contract. Manual inspection confirmed that it is a public document checksum, not an API key. No real secret was found in that candidate.
- A separate pass inspected every reachable blob for provider token formats, private-key headers, credential assignments, VIN-like strings, email addresses, private IPv4 addresses, local user paths and runtime/diagnostic filenames.
- The two VIN-like strings belong exclusively to synthetic vehicle fixtures and mismatch tests. The hard-coded network password belongs exclusively to fake-network tests. Another assignment candidate was Python syntax, not a credential value.
- No tracked current or historical `.env`, saved device configuration, network configuration, vehicle cache, charging session, diagnostic JSON, DPAPI file, certificate, private key, log or ZIP archive was found.
- GitHub had one branch, no tags, no pull requests/issues, no releases or release attachments, no Actions runs/artifacts, no wiki and no discussions for this repository. GitHub secret-scanning results were unavailable while it was private; that absence was not treated as a clean scan.

## Documentation privacy update — 27 October 2026

Current Markdown documentation uses generic host/address/account/path placeholders and a reserved example domain. Installation-specific IPs, domain names, proxy usernames/paths and deployment status records have been removed from the current documentation. The README is a short overview with diagrams; detailed setup remains in linked guides.

This is a current-documentation cleanup, not a new full source/history secret audit. Application defaults, deployment helpers/templates and fixtures are outside this documentation-only change. Older Git commits still retain earlier text; ordinary file edits do not purge history. Keep real deployment settings in private runtime configuration and private operational notes.

## Intentionally visible information

- The original review baseline included private-LAN example/default addresses. Current documentation now uses placeholders; this does not remove earlier Git history. Ports and the supported firmware identifier remain technical compatibility information.
- Existing commits contain the author's name and email address, also present in the author's already-public portfolio repository. History was not rewritten to hide ordinary public authorship metadata.
- Tests contain clearly synthetic vehicle identifiers, dummy keys and a fake-network password. These must never be reused as real credentials.
- The selected artwork uses the application's original car/fox/charging design in manufacturer-inspired colors. It does not contain official manufacturer emblems or imply manufacturer sponsorship.

## Files excluded from publication

The repository's `.gitignore` excludes configuration, local vehicle/session state, DPAPI secrets, network certificates/keys, diagnostic exports, logs, archives and browser profiles. Local ignored runtime folders are not copied to the public repository or portfolio. Ignoring a file does not remove it from Git history, which is why historical blobs were checked separately.

The portfolio uses only purpose-made artwork and explicitly labelled **simulated dashboard data**, never the user's live setup, VIN, API key, private certificate or uploaded diagnostic exports.

## Rechecking

Run a history scan before future publication-sensitive changes:

```text
gitleaks git . --log-opts=--all --redact=100
```

Review candidates rather than automatically allowing whole commits or disabling generic-key detection. The public OpenAPI checksum candidate described above can recur in a historical scan.

Never publish real credentials in an issue, screenshot, sample configuration or support attachment. If a real secret ever enters Git, revoke/rotate it first and review historical exposure; deleting only the latest file is insufficient.
