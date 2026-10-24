# Private cookie receiver

Python 3.10+; standard library only. No installation dependencies. This directory is the server component, not a Chrome extension. Nothing here deploys or changes existing services automatically.

## Isolation requirements

**Every configured target must be a dedicated, single-browser-profile backend instance.** Rotation deletes *all* previous cookie rows on that backend. Never point this service at a shared cookie pool. Different backend names may not share a port. Do not run other cookie importers, backend auto-rotation jobs, or admin cookie edits concurrently. The existing 9router listener on localhost:20128 is not the receiver and is not a valid cookie-management target; the dedicated cookie backend is normally localhost:8083.

A target is permanently reserved by its first device, including after revocation. There is deliberately no automatic replacement/re-pair command: losing extension storage requires an operator-controlled migration to a new, empty dedicated backend and a new configured target. Do not rename an existing target or clear its SQLite binding to bypass account isolation. Both authenticated DELETE and local CLI revocation disable and verify every target backend row before removing the token. On backend outage or verification failure the token remains valid for an exact retry; some rows may already have been disabled. Revocation cannot atomically roll back remote changes. If the device is lost and the backend is unavailable, isolate the device through tailnet policy while restoring the backend, then retry local CLI revocation.

The initial successfully checked `__Secure-1PSID` hash pins the account. Any later different value is rejected as `account_changed`; this conservative rule can also reject a legitimate Google SID rotation. Handle this as an operator migration, not an automatic account replacement. There is no attempt to infer account identity from email or an LLM request.

## Local setup (Linux deployment target)

Create an unprivileged `cookie-sync` service account and install this directory read-only under `/opt/gemini-cookie-sync/server`. Copy `config.example.json` to `/etc/cookie-sync/config.json`, replace placeholders locally, and set ownership to the service account with directory mode 0700 and file mode 0600. The loader rejects group/world-accessible configuration on POSIX. Keep secrets out of shell arguments, source control, logs, issue reports, and screenshots.

`backends` maps a shell-selected name to a fixed localhost HTTP origin, `adminToken`, and `apiKey`. No HTTP request can choose a target or supply a backend URL. `allowedHosts` contains bare, lowercase tailnet hostnames or the direct Tailscale IPv4 address, without a scheme, path, or port. Loopback Host values are always accepted. Redirects and environment proxies are disabled for backend calls. Backend authentication uses `Authorization: Bearer <adminToken>` and `x-api-key: <apiKey>`; verify those headers against your backend version before deployment.

Use the same service identity and state path for all commands:

```sh
python3 cookie_sync.py --config /etc/cookie-sync/config.json --state /var/lib/cookie-sync create-pairing personal-profile
python3 cookie_sync.py --config /etc/cookie-sync/config.json --state /var/lib/cookie-sync list
python3 cookie_sync.py --config /etc/cookie-sync/config.json --state /var/lib/cookie-sync revoke DEVICE_ID
python3 cookie_sync.py --config /etc/cookie-sync/config.json --state /var/lib/cookie-sync serve
```

Only `create-pairing` prints a secret: a 192-bit random code, valid for ten minutes and one use. Transfer it privately to the intended extension. The returned device token has 256 random bits and is returned once. SQLite stores only code/token/account/snapshot hashes and metadata. Neither cookie values nor backend credentials are written to bridge storage. State directory mode is 0700 and database mode 0600 on POSIX; Linux systemd sets umask 0077. Windows can run tests but mode bits do not implement Windows ACL isolation; provision ACLs separately, and use Linux for the supplied production service.

`cookie-sync.service` is an example only. Inspect paths and ownership before installing. The example service retains the default bind of **127.0.0.1:8787**. `serve --bind` accepts only the exact loopback address `127.0.0.1` or a literal IPv4 address in `100.64.0.0/10`; wildcard, LAN, public, hostname, and IPv6 binds are rejected. On POSIX, a lifetime file lock prevents a second service process on the same state directory. A global in-process lock deliberately serializes all targets, and SQLite IMMEDIATE transactions cover backend network operations; this is stronger than per-target locking but limits throughput. Run one service/state directory per deployment. Do not run multiple bridge processes with different state directories against the same backend.

## Tailscale boundary

For direct private access at `http://100.100.100.100:8787`, add `100.100.100.100` to `allowedHosts` and explicitly start the receiver with:

```sh
python3 cookie_sync.py --config /etc/cookie-sync/config.json --state /var/lib/cookie-sync serve --bind 100.100.100.100 --port 8787
```

This address must belong to the server's Tailscale interface. Restrict tailnet ACLs/grants to the intended device/user and TCP port 8787. The bind allowlist checks address syntax/range, not interface ownership or tailnet membership; keep that shared-address range routed through Tailscale only. Direct HTTP relies on Tailscale's encrypted transport, not TLS. Pairing, bearer authentication, Host validation, and backend loopback restrictions remain unchanged. Never forward this listener from a public interface. The supplied systemd service is unchanged; changing its bind is an explicit operator action.

Alternatively, expose only this receiver using a **dedicated Tailscale Serve HTTPS listener on 8443**, private to the tailnet, e.g. after independently checking the installed CLI syntax:

```sh
tailscale serve --bg --https=8443 http://127.0.0.1:8787
tailscale serve status
```

Do not use Funnel. Do not reset or replace existing Serve routes (including existing 443 routes). Restrict tailnet ACLs/grants to the intended device/user and port 8443. Verify the actual preserved Host header matches `allowedHosts`. No forwarded identity headers are trusted; the 256-bit bearer token is still mandatory. Do not expose localhost:8083, localhost:20128, or the admin API through this listener. Do not add CORS headers: a Chrome extension should use background/service-worker fetch with the exact HTTPS host permission, not a web-page content-script fetch. OPTIONS/preflight is unsupported.

## Protocol

All POST bodies must be JSON with a single valid Content-Length, at most 65,536 bytes. Chunked transfer and Expect are rejected; sockets have a 15-second read timeout. Maximum concurrent request threads is 16; overload closes new connections. Pair attempts are limited to ten per source IP per ten minutes (behind Serve this can be a shared loopback source). There is no access/error request logging or cookie echoing.

- `POST /v1/pair` `{ "code": "..." }` → `{ "deviceId": "...", "token": "..." }`.
- `GET /v1/status`, Bearer token → `{ "syncRequested": true, "status": "awaiting_sync", "lastApplied": 0 }`. Also records `lastSeen` server-side (epoch seconds, server-trusted).
- `POST /v1/snapshot`, Bearer token → `{ "sequence": 1, "cookies": [...], "logout": false }`. Response `{ "status": "applied|unchanged|invalid|account_changed", "sequence": 1 }`. A successful `applied` result also records `lastAppliedAt`.
- `DELETE /v1/pair`, Bearer token → `{ "status": "revoked" }`.
- `GET /v1/profiles`, Bearer token → `{ "profiles": [...] }`. Returns metadata for all non-revoked devices sharing the same operator config. See **Profile visibility** below.
- `POST /v1/heartbeat`, Bearer token, `{ "refresh": { "enabled": bool, "minutes": 15..1440, "lastRefreshAt": epoch_seconds, "nextRefreshAt": epoch_seconds } }` → `{ "status": "ok" }`. Updates the caller's refresh telemetry and `lastSeen` only. The server does **not** use these values to control remote operations; they are client-reported state.

### Best-effort conversation cleanup

- `GET /v1/cleanup`, Bearer token → `{ "enabled": false, "multiTurn": false, "temporaryChat": false, "bestEffort": true }`.
- `POST /v1/cleanup`, Bearer token, exactly `{ "enabled": true }` (or `false`) → the same four-field response, read back from the backend after updating. Unknown fields, missing fields, and non-boolean values return 400 `invalid_cleanup`.

These endpoints address only the caller's permanently bound, dedicated backend. No request may select another profile, URL, admin path, or credentials. Missing, invalid, or revoked tokens return 401. No cookies, API keys, admin tokens, full configuration, or backend URLs are returned. Cleanup settings are not cached or stored in SQLite: the actual backend runtime config is the source of truth.

Adapter contract: authenticated GET `/admin/api/config` must return an object with a `config` object containing actual JSON booleans `auto_delete_conversation`, `multi_turn`, and `temporary_chat`. Missing flags or wrong types fail closed with 503 `backend_unavailable`; they are never defaulted or coerced. The adapter preserves **every** field in that runtime `config` object, including unknown/nested fields, changes only `auto_delete_conversation`, and PUTs the entire object to the same fixed endpoint. The outer `deploy`/`models` metadata is not runtime configuration and is not PUT. A fresh GET verifies the entire runtime object, not just the cleanup flag; mismatches return 503 `backend_partial_failure`. Transport/JSON errors are sanitized as `backend_unavailable` without exposing backend error text. A failed response does not prove the PUT had no effect; re-read before retrying.

Enabling returns 409 `cleanup_conflict` without a PUT if `multi_turn` or `temporary_chat` is true, even if cleanup was already enabled. Disabling remains allowed with either mode enabled. This API never turns on or changes multi-turn or temporary-chat mode, or intentionally changes any unrelated setting. Read/modify/write/re-read runs under the store lock and SQLite IMMEDIATE transaction, shared with cookie operations. External admin writers are not covered by that lock: **do not edit backend config concurrently**, or a full-config PUT could overwrite an external edit. There is no backend compare-and-swap or distributed transaction.

This toggles the backend's **best-effort deletion of newly generated conversations after responses**. It does not prove a conversation was actually removed from Google, does not guarantee deletion or privacy, and offers no mass-clear/history-deletion operation. The `bestEffort: true` marker is always present, including when disabled. Existing conversations are not cleared. Verify this config contract against the deployed backend version before use; tests exercise local synthetic HTTP backends only.

### Profile visibility

`GET /v1/profiles` returns metadata for every non-revoked device registered in the same service instance (i.e., devices sharing the same operator config and state directory). This is intentional: an operator running multiple Chrome profiles points each at a different backend, and the extension can display peer-profile status without cross-origin requests. Revoked devices are excluded entirely.

Each profile entry contains:
```json
{
  "profileId": "<device-id>",
  "label": "<backend label ≤80 chars>",
  "isCurrent": true,
  "online": false,
  "lastSeen": 1700000000,
  "lastAppliedAt": 1700001234,
  "status": "good",
  "refresh": {
    "enabled": true,
    "minutes": 30,
    "lastRefreshAt": 1700000000,
    "nextRefreshAt": 1700001800
  }
}
```

**Privacy invariants.** The response intentionally omits: bearer tokens, token hashes, account hashes, cookie identity signals, backend URLs, admin tokens, or API keys. Labels are drawn from the `label` config key (if set) or the backend name — both operator-controlled, ≤80 characters. `online` is derived solely from `lastSeen`: a device is online if its last recorded heartbeat or status call occurred within 180 seconds; `online: true` does not imply session validity. `lastSeen` is recorded server-side on each authenticated call to `/v1/status` and `/v1/heartbeat`; it is never client-supplied.

Cookie objects contain at least `name`, `value`, `domain`, `path`, `secure`, `httpOnly`. Both flags must be JSON booleans. `__Secure-` names require secure true; standard names such as SID/HSID/APISID may legitimately have secure false. Preserve the Chrome flags; transmission from the extension must still use private HTTPS. Additional Chrome metadata is ignored. Domains must be exactly `google.com`, `.google.com`, `gemini.google.com`, or `.gemini.google.com`; path `/`. Names are restricted to the explicit Google session allowlist in `cookie_sync.py`. Require nonempty `__Secure-1PSID` and `__Secure-1PSIDTS`. At most 64 cookies. Values must be printable ASCII without semicolons or control characters. Duplicate `(name, domain, path)` identities are rejected, and different values for the same name across domains are rejected rather than ambiguously flattening them. Same-name/same-value cookies across domains are allowed and flattened once. Logout requires an empty array and disables every row without deleting backend credentials.

Sequences are positive signed-64-bit integers. Preserve exact snapshot data for retries. A successful sequence+canonical hash replay is `unchanged`. A previously attempted, well-formed sequence may be retried only with identical canonical content; lower or same-sequence/different-content attempts return 409. Canonicalization sorts cookies and ignores Chrome metadata. An invalid candidate consumes the seen sequence/hash, **not** lastApplied or its hash. Corrected content must use a higher sequence. Syntax/validation rejection consumes neither. Outages permit exact retries. Persist the extension's sequence and pending snapshot before sending; never reset numbering on browser restart. HTTP 400 indicates invalid input, 401 auth/pairing failure, 409 stale ordering, 413 size limit, 429 pairing limit, and 503 unavailable/partially updated backend.

## Backend rotation and recovery

Adapter contract: GET `/admin/api/cookies` returns `{enabled: 1, items: [...]}` (also accepts a bare array or `{cookies: [...]}`/`{data: [...]}`); rows contain `id` and `status`. POST the same endpoint with `{cookie: "name=value; ...", status: "disabled"}` returns `{id: ...}` or `{data: {id: ...}}`. POST `/ID/check` returns `ok: true`, `valid: true`, or `status: "ok"/"valid"`. PATCH `/ID` accepts `{status: "disabled"/"enabled"}`; DELETE `/ID` removes a row. PATCH and DELETE effects are verified with GET. IDs are URL-escaped. **Check adapter response shapes against the real backend before enabling it.** Candidate validation is only the cookie-check endpoint; no model/Argon/9router inference is used.

The updater imports a candidate, immediately disables and verifies it before its cookie check, then disables old rows, enables the checked candidate, and deletes every old row. Before candidate activation failures trigger best-effort restoration of previously enabled rows and candidate deletion. Once cleanup starts, deleting old credentials cannot be rolled back; failed cleanup reports `backend_partial_failure` and does not advance lastApplied. Logout may also partially disable rows on an outage. Do not interpret 503 as proof that no remote changes occurred. A lost import response can leave a candidate remotely; reconciliation is necessarily best effort without a backend transaction/idempotency API. Exact retry imports a fresh candidate, checks it, and reconciles all prior rows. The browser-source backend patch honors `status: "disabled"` atomically on import. Legacy backends may ignore that field and briefly activate the candidate until the immediately following verified disable; this fallback minimizes but cannot eliminate exposure. Candidate checks always run after disabling. Validate atomic-disabled support before production when that exposure is unacceptable.

A durable in-flight journal contains only hashes/sequence metadata and is committed before network calls. Process interruption yields `recovery_required` with syncRequested true; the browser must retry the exact pending snapshot (or send a higher sequence). No cookie-bearing recovery journal is written. A backend transaction cannot be made atomic with SQLite, so exactly-once remote mutation is not claimed. Prevent concurrent external writers.

Live checks are cached for five minutes, including unavailable checks. `good` means exactly one enabled row passed a cookie check. Invalid sessions request synchronization; `backend_unavailable` is distinct from invalid credentials. `backend_partial_failure` and `recovery_required` must be reconciled rather than hidden by a later health poll. `logged_out` is not polled for session validity.

For `backend_partial_failure`: stop other writers, inspect backend row IDs/statuses locally without printing cookies, retry the exact pending snapshot, and verify only one enabled checked row remains. For a lost/revoked device, disable rows through the local backend admin channel and migrate deliberately. Back up state/config privately; metadata is sensitive even without cookies. Disable core dumps and avoid HTTP debug proxies, process-memory dumps, or swap capture if strict no-cookie persistence is required outside the application.

## Verification

```sh
cd server
python3 -m unittest -v
```

Tests use a fake loopback backend and synthetic secrets only. They cover ordering/retries, invalid candidate preservation, rotation rollback and cleanup failure, logout, account isolation, permanent target binding, pairing expiry/single use, unauthorized/revoked tokens, cached live checks, outages, crash-journal recovery, request bounds, host/framing checks, strict cookies, durable metadata, no plaintext credentials in SQLite, additive schema migration, heartbeat auth/validation/telemetry, status `lastSeen` recording, snapshot `lastApplied_at`, profile response structure and privacy, online/stale logic, label truncation, and profile HTTP auth. No live backend or deployment commands are run by the suite.
