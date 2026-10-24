# Operations and troubleshooting

## Deployment invariants

1. One dedicated, empty-at-provisioning backend per Chrome profile. Different ports must not alias the same backend database.
2. One receiver service/state directory and no competing backend cookie writers.
3. Pin upstream backend v4.23.0, apply the browser-source patch and set `GEMINI_BROWSER_COOKIE_SOURCE=1`.
4. Private Tailscale access only; prefer Serve HTTPS. Never Funnel, wildcard binds or public backend administration.
5. Keep config/state/private packages protected; never publish cookie-bearing diagnostics.

See [Usage](USAGE.md) for setup and [API](API.md) for protocol limits. Supplied shell/PowerShell deployment scripts are operator examples, not an unconditional installer. Review paths, ports, containers, ownership, existing Serve routes and rollback steps first.

## Configuration reference

| Field | Requirement |
| --- | --- |
| `allowedHosts` | List of bare lowercase private hostnames/IPs, no scheme/path/port; loopback Host values always accepted |
| `backends` | Nonempty mapping from operator-selected target names to backend configurations |
| `backends.<name>.url` | Fixed HTTP loopback origin using `127.0.0.1`, `localhost`, or `::1`, explicit unique port, no credentials/query/fragment or non-root path |
| `backends.<name>.adminToken` | Nonempty backend admin bearer token |
| `backends.<name>.apiKey` | Nonempty backend API key, sent as `x-api-key` |
| `backends.<name>.label` | Optional string shown in shared metadata, truncated to 80 characters; use a non-sensitive label |

Credentials are local config, never supplied by the extension. Config is read when a CLI/service process starts; restart the service after reviewed changes. Do not rename a bound target or clear SQLite bindings to switch accounts.

On POSIX the config loader rejects group/world permissions. State directory and SQLite modes are enforced as 0700/0600. Use the same unprivileged service identity for commands. Windows mode bits are not ACL isolation; provision Windows ACLs separately. The supplied production service is Linux-oriented.

The database creates additive telemetry columns when older state is opened: `last_seen`, `last_applied_at`, `refresh_enabled`, `refresh_minutes`, `last_refresh_at`, `next_refresh_at`. Back up state before upgrades. Never modify journal/binding fields by hand as a routine repair.

## Actual CLI

Global options precede the subcommand:

```sh
python3 server/cookie_sync.py --config /etc/cookie-sync/config.json --state /var/lib/cookie-sync serve
python3 server/cookie_sync.py --config /etc/cookie-sync/config.json --state /var/lib/cookie-sync create-pairing personal-profile
python3 server/cookie_sync.py --config /etc/cookie-sync/config.json --state /var/lib/cookie-sync list
python3 server/cookie_sync.py --config /etc/cookie-sync/config.json --state /var/lib/cookie-sync revoke DEVICE_ID
```

`serve` defaults to `127.0.0.1:8787`, accepts `--port` and `--bind`. Binding accepts only exact `127.0.0.1` or canonical Tailscale-range IPv4. POSIX serving holds `service.lock` for its lifetime; do not start a second service on the same state.

`create-pairing` prints a secret valid ten minutes and one use. `list` prints only `id`, `backend`, `revoked`, `applied`, `status`; it is not a cookie export. `revoke` disables/verifies backend rows before removing authorization. CLI errors intentionally use a generic failure message; inspect local configuration and ownership without dumping credentials.

### Network exposure

Preferred:

```sh
tailscale serve --bg --https=8443 http://127.0.0.1:8787
tailscale serve status
```

Keep existing routes intact and verify the preserved Host matches `allowedHosts`. Tailnet policy must restrict port 8443. Bearer auth is mandatory; forwarded identity headers are not trusted.

For direct mode, use your own assigned Tailscale IPv4 in `allowedHosts` and `serve --bind YOUR_TAILSCALE_IP --port 8787`. Configure that same origin manually or through private connection JSON. The extension accepts canonical `100.64.0.0/10` addresses on 8787 without source edits and requests exact-host permission dynamically. It does not scan the network. HTTP relies on Tailscale encryption, not TLS. The range check does not prove interface ownership or tailnet membership.

## Routine health checks

- Confirm Chrome is running, awake, paired and connected to the tailnet.
- Review the settings profile table: recent `lastSeen` establishes connectivity only; `good` establishes cached cookie-check validity only.
- Check receiver process status and private Serve routing without enabling HTTP payload logging.
- Inspect backend row IDs/statuses locally, not cookie values. Expect exactly one enabled checked row after successful synchronization, or disabled rows after logout/revoke.
- Test intended model responses separately. HTTP 200 can contain an upstream error message. Provider model labels and `/models` listings do not prove availability.

The status alarm runs every minute, reconciliation every five minutes, retries back off 1/2/4/5 minutes, and live backend checks are cached for five minutes. Sleep, worker suspension and network downtime can delay them. Heartbeat refresh timestamps are client reports; they do not schedule server work.

## Troubleshooting matrix

| Symptom | Likely boundary | Safe action |
| --- | --- | --- |
| Pairing rejected | Expired/used code, target already bound, permissions, private routing | Check endpoint and permissions; generate a new code only for an unbound target |
| `unauthorized` | Invalid/revoked token | Stop retries; check local device listing and plan deliberate migration if needed |
| `retrying` | Permission, transport, response or server failure | Check Tailscale, exact host permission, Host allowlist and service/backend health |
| `invalid` | Candidate cookie check failed | Open Gemini in the intended profile and complete login/MFA; allow changed cookies to trigger a new snapshot |
| `account_changed` | Pinned base-session value differs | Treat as an isolation stop; legitimate rotation is possible; migrate deliberately rather than disabling the guard |
| `stale_sequence` | Reset local numbering or a second writer | Stop duplicate clients; preserve pending metadata; do not reset numbering/database |
| `backend_unavailable` | Backend down, credentials/contract mismatch or timeout | Restore loopback backend and verify admin contract without printing secrets |
| `backend_partial_failure` | Remote mutation partly completed | Follow reconciliation runbook below; do not assume old state survived |
| `recovery_required` | Process stopped during durable in-flight operation | Resume the browser's exact pending snapshot or a higher sequence and verify backend state |
| Profile offline but status good | No contact within 180 seconds | Check Chrome/network; cached good is not current browser connectivity |
| Online but invalid | Heartbeat works, cookie check fails | Repair the Google session, not the private network |
| Refresh enabled but no reload | Active/closed/mismatched tab, blocked status, sleep | Select an existing inactive Gemini tab; do not expect a reload during active use |
| Private one-click unavailable | No embedded private connection | Normal for public builds; use manual pairing or import your own secret connection |
| Models fail despite good session | Model entitlement/upstream request behavior | Inspect actual content safely; do not keep replacing cookies or claim ban immunity |

## Reconcile a partial failure

1. Stop all external cookie writers and verify only one receiver manages the target.
2. Restore backend reachability and verify admin response shapes using synthetic tests where possible.
3. Inspect row IDs and enabled/disabled statuses locally without printing cookies.
4. Let the browser retry its durable pending snapshot. Exact content retains its sequence; changed content must advance it.
5. Verify one checked enabled row remains and the receiver acknowledges application. Logout/revocation instead require disabled rows.
6. If verification still fails, isolate the profile/backend from routing and investigate before re-enabling it.

Candidate cleanup after activation is not fully reversible. A lost import response can leave a remote candidate. No distributed transaction exists between SQLite and the backend, and 503 never guarantees zero side effects. Do not conceal `backend_partial_failure` with an unrelated health result.

## Lost device, changed session, or account migration

Revocation retains a permanent backend reservation. Lost extension storage, a revoked token, or a changed pinned session cannot be repaired by simply generating another code for the old target.

1. If the device is lost, restrict its tailnet access immediately.
2. Restore backend access and revoke locally; if disable/verification fails, the token remains valid for retry. Some rows may already be disabled.
3. Disable/remove the old target from inference routing through the local admin channel as appropriate.
4. Provision a new empty dedicated backend/database on a distinct port and add a new target configuration.
5. Restart the receiver with the reviewed config, generate a new target-specific pairing code and pair the intended Chrome profile.
6. Check browser identity manually, application acknowledgement, backend row state and intended inference route.

Do not reuse another profile's private connection, delete SQLite, rename an existing target or clear its account hash to bypass isolation.

## Backups, upgrades and rollback

Back up config, receiver state and backend data privately under restricted access. Metadata is sensitive even without cookies. Stop the service for a consistent SQLite file copy or use SQLite's supported backup mechanism. A restored old database can rewind sequence/revocation knowledge; do not replay it against a live changed backend without reconciliation.

Before upgrading, record versions/configuration, run local tests, validate the patched backend contract, and stage a synthetic isolated target. Preserve existing Serve routes and backend volumes. Roll back binaries/configuration only with a deliberate plan for already-applied remote cookie changes; restoring code alone does not undo remote mutations.

Keep credentials out of command arguments, logs, screenshots and support reports. Disable core dumps and avoid debug proxies or memory/swap captures when strict no-cookie persistence is required. Receiver storage omits cookie values, but backend databases, browser storage, memory and private packages still contain sensitive session material.

## Optional provisioning and QA

```sh
python3 deploy/provision.py --name personal --port 8090 --dry-run
```

Actual provisioning requires root on Linux, the expected Docker image/network and existing local 9router CLI authentication layout. It creates credentials, container/storage and provider/model entries; it is not a universal installer or automatic model discovery. Review failures for partial resources before retrying. Do not publish generated target config.

Run the tests in [Contributing](../CONTRIBUTING.md). Optional `deploy/browser-smoke.py`, `deploy/browser-transport-test.py`, `deploy/backend-contract.py`, and `deploy/remote-e2e.py` have environment-specific assumptions: inspect their source/arguments before running. Isolated fixtures must never receive real accounts. Transport harnesses that pregrant permissions do not test interactive permission prompts.
