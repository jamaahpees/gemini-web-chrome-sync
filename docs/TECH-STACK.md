# Technology stack

## Runtime and build inventory

| Layer | Technology | Why it is present / constraints |
| --- | --- | --- |
| Browser extension | JavaScript ES modules, Chrome extension APIs, HTML/CSS | Service-worker sync, cookies, alarms, local storage, optional host permissions, tabs and context menus |
| Base extension | Cookie-Editor fork | Existing cookie-management interface; upstream licensing and notices remain applicable |
| Build tooling | Node.js/npm, Grunt | Copies and rewrites browser-specific extension packages into `build/`; use a current Node version supporting the built-in test runner |
| JavaScript tests | `node:test`, Sinon | Helper/worker/UI regression coverage; provisioning tests use a separate `.cjs` test command |
| Code checks | ESLint 8 configuration, Google style, Prettier integration | Match repository conventions rather than introducing a second formatting stack |
| Receiver | Python 3.10+ standard library | `http.server`, `urllib`, `sqlite3`, hashing, randomness and locking; no third-party receiver runtime dependencies |
| Receiver tests | Python `unittest` | Synthetic requests and fake backend behavior, without live credentials |
| Receiver persistence | SQLite | Durable bindings, hashes, sequences, in-flight recovery marker and telemetry |
| Private network | Tailscale; preferably Serve HTTPS | Tailnet access plus application bearer authentication; not a public endpoint |
| Gemini backend | External `gemini-web2api-go` v4.23.0, Go, supplied patch | Cookie admin contract and optional OpenAI-compatible inference; patch must match the upstream version |
| Deployment | Linux, optional systemd and Docker | Supplied service/container scripts are operator examples requiring review |
| Optional router | External 9router | One OpenAI-compatible provider per dedicated backend |
| Optional browser QA | Python Playwright and Chromium | Unpacked extension smoke tests, transport checks and sanitized screenshots |
| Packaging | PowerShell helpers, Grunt | Public unpacked builds or private credential-bearing connection packages |

## Installation boundaries

The Python receiver needs no pip dependencies. Playwright is optional test tooling, not a production receiver dependency. Build tools are npm **development dependencies**; they are not bundled as a Node server runtime.

```sh
npm ci --ignore-scripts
npx grunt copy:chrome replace:chrome
python3 server/cookie_sync.py --help
```

The generic upstream package name/version and repository metadata still describe Cookie-Editor; do not interpret them as an independently versioned backend compatibility declaration. Other browser manifests exist upstream, but this project's documented sync workflow targets Chrome.

## Backend integration contract

`GEMINI_BROWSER_COOKIE_SOURCE=1` activates the patched backend behavior that disables competing background rotation/Set-Cookie merging and supports disabled candidate imports. Without validated disabled import support, the receiver's immediate disable fallback cannot eliminate transient candidate activation.

The receiver uses only the backend cookie administration API, authenticated with both an admin bearer token and `x-api-key`. It does not run an LLM prompt to identify accounts or test model availability. See [API](API.md) for the exact adapter contract.

The optional provisioning script expects an existing Linux Docker/9router arrangement, image tag, network, local CLI authentication files and API shapes. It has fixed environment assumptions and is not a portable installer. Its hard-coded model list is a configuration convenience, not model discovery or an entitlement check. Argon naming in provider configuration does not prove official API availability or successful inference. Prefer Google's official API for models officially supported for your account.

## Dependency and license considerations

- Cookie-Editor and this fork carry **GPL-3.0-only** licensing; preserve notices and satisfy distribution obligations. See [LICENSE](../LICENSE) and [UPSTREAM-README](../UPSTREAM-README.md).
- `gemini-web2api-go` is MIT licensed. This repository distributes a patch, not upstream binaries; preserve upstream notices when distributing a patched build.
- Known audit findings exist in inherited development dependencies. Run `npm audit` to inspect the current lockfile; do not claim a clean audit or blindly run `npm audit fix --force`. Major dependency updates can break Grunt or upstream packaging and need separate verification.
- Pin backend revisions and inspect build scripts before building downloaded source. Keep production credentials out of build logs, issue reports and source archives.

## Verification matrix

The documented baseline is 61 JavaScript tests, 36 Python tests and 2 provisioning tests; rerun rather than treating these counts as permanent. Commands and contribution expectations are in [CONTRIBUTING.md](../CONTRIBUTING.md). Optional browser tests require explicit setup and do not automatically certify a live Google session or every model.
