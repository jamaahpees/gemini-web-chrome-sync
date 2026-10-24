# Contributing

## Scope and orientation

This fork adds private Gemini-session synchronization to Cookie-Editor. Read [PRD](docs/PRD.md), [Architecture](docs/ARCHITECTURE.md), [Technology stack](docs/TECH-STACK.md), [Usage](docs/USAGE.md), [API](docs/API.md), and [Operations](docs/OPERATIONS.md) before changing behavior. Follow [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) and report sensitive issues through [SECURITY.md](SECURITY.md), not public credential dumps.

Important source boundaries:

- `sync/worker.js`: persisted state, serialized commands, alarms and uploads.
- `sync/helpers.js`: endpoint/cookie validation and profile sanitization.
- `sync/settings.js`, `sync/settings.html`: permissions and settings UI.
- `server/cookie_sync.py`: receiver, SQLite store, CLI and backend adapter.
- `deploy/browser-source.patch`: pinned upstream backend behavior.
- `test/`, `server/test*.py`: synthetic regression coverage.
- `deploy/`: operator-reviewed deployment and verification helpers.

## Local setup

Use Node.js with built-in test-runner support, npm, Python 3.10+, and Chrome for manual extension testing. The receiver has no third-party runtime dependencies.

```sh
npm ci --ignore-scripts
npx grunt copy:chrome replace:chrome
```

Load `build/chrome` as an unpacked extension through `chrome://extensions`. Rebuild and reload the extension after source changes; editing generated build files is not a source fix. Use synthetic cookies and isolated fake backends wherever possible. Do not point development tests at production cookie pools.

## Verification commands

Run from the repository root:

```sh
npm test
npm run lint
python -m unittest discover -s server -p 'test*.py'
node --test test/provision-sync.test.cjs
```

The baseline reported for the documentation task is **61 JavaScript tests, 36 Python tests and 2 provisioning tests**. Counts may change as coverage grows; include actual command output/results in your change report. This document does not certify a fresh run. Existing upstream DevTools tests can emit diagnostic warnings while passing; inspect exit status and assertions rather than hiding warnings.

Optional browser tests require a separate setup:

```sh
python -m pip install playwright
python -m playwright install chromium
python deploy/browser-smoke.py
```

Build the unpacked extension first and inspect harness assumptions. Remote/transport/backend-contract helpers may require isolated network fixtures; review source and CLI arguments before use. Never give a fixture real Google cookies or production credentials. A test manifest that pregrants permissions does not verify permission-dialog UX. Live backend compatibility and Google model access need separate operator checks.

## Required behavior to preserve

- Explicit consent and endpoint selection; no network scanning or universal Chrome profile discovery.
- HTTPS `*.ts.net` support and canonical direct Tailscale IPv4 validation; exact-host permissions derived from user configuration.
- Regular store only; no incognito or partitioned-cookie sync.
- One dedicated backend per profile; no cookie pool, implicit fallback or concurrent external cookie writer.
- Durable ordered snapshots, identical retries, invalid-candidate preservation and visible partial failures.
- Session-value pinning is conservative and may reject legitimate rotation; never present it as stable identity detection.
- Ten-minute single-use codes, scoped bearer auth, Host/framing bounds and no cookie echo.
- Same-operator shared metadata remains explicit; no accidental cookie/token/backend-secret exposure in profile responses.
- Optional refresh only for selected inactive Gemini tabs, without background prompts or availability guarantees.

Add regression coverage for changes to these invariants. Describe implemented guarantees separately from external backend/Google assumptions. Do not claim account-ban immunity, guaranteed Argon availability, or official API authorization through this bridge.

## Secrets and public packaging

Never commit receiver configs, state databases, real connection JSON, tokens, cookies, backend environment files or private archives. Public builds intentionally lack `sync/default-connection.json`. A private package embeds credentials for one profile and must not be uploaded as a release artifact.

Use placeholder endpoints/credentials and synthetic account material in tests, screenshots and examples. Review generated files and staged diffs before publication. Do not include private machine paths, device identifiers, tailnet names, session values or secrets in issues. Keep documentation screenshots under `docs/images/` sanitized.

## Style, dependencies and review

Match nearby module style and existing lint/format configuration. Prefer small, scoped changes with regression tests and accurate documentation. Do not combine inherited UI rewrites or mass dependency upgrades with a protocol/security fix.

Known audit findings exist in upstream development dependencies. Run `npm audit` for current evidence and explain scope; do not call the tree vulnerability-free or apply `npm audit fix --force` without reviewing compatibility and testing packaging.

A useful change description includes the problem, changed behavior, isolation/security implications, executed verification, remaining limitations and any migration/rollback requirement. Preserve Cookie-Editor's GPL-3.0-only notices and the external backend's MIT notices when distributing patched builds. This project is unofficial and is not affiliated with Google or upstream maintainers.
