# Product requirements: Gemini Web Chrome Sync

## Purpose and audience

Provide an explicitly authorized bridge from a running Chrome profile's Gemini session to a private, dedicated Gemini Web backend. The primary user is one operator managing their own browser profiles, private receiver, and optionally 9router. This is an experimental, unofficial integration, not a Google API product or a multi-tenant credential service.

The browser remains the session source. The receiver validates ordered snapshots before replacing backend credentials. The operator must still complete Google login, MFA, account verification, and model entitlement requirements.

## Problem and intended outcomes

Manual cookie export is easy to forget and difficult to repeat safely. Independent cookie writers can overwrite one another, and a shared backend pool can accidentally route requests through the wrong browser account. This project reduces manual copying while preserving an explicit profile-to-target binding.

Success means:

- Authorized cookie changes reach the bound backend while Chrome and the private network are available.
- Duplicate delivery does not overwrite newer snapshots.
- Invalid candidates do not intentionally replace a checked working session.
- Logout and revocation disable backend rows rather than silently selecting another account.
- Users can distinguish browser connectivity, snapshot acceptance, and backend session validity.
- Other profiles' cookie values and credentials are never returned through the profile-status API.

## Personas and workflows

| Persona | Need | Workflow |
| --- | --- | --- |
| Browser user | Connect a single authorized session | Install unpacked extension, grant permissions, pair with a ten-minute code |
| Multi-profile operator | Keep accounts isolated | Allocate one dedicated backend and one scoped token per profile |
| Service operator | Recover safely from outages | Inspect metadata, restore backend connectivity, retry ordered snapshots |
| Router operator | Route an explicitly selected profile | Configure a separate OpenAI-compatible provider per dedicated backend |

See [Usage](USAGE.md), [Architecture](ARCHITECTURE.md), and [Operations](OPERATIONS.md).

## Functional requirements and acceptance criteria

“Implemented” describes the current source behavior, not a guarantee of successful operation against every future Google/backend release.

| ID | Requirement and acceptance criterion | Status |
| --- | --- | --- |
| FR-1 | Pairing consumes one valid code once, expires after 600 seconds, and returns a device-scoped bearer token | Implemented |
| FR-2 | HTTP callers cannot select arbitrary backend URLs; the server config and pairing binding choose the target | Implemented |
| FR-3 | Only allowlisted Google/Gemini session cookies in the regular cookie store are collected; incognito and partitioned cookies are excluded | Implemented in extension; receiver validates its own domain/name/flag allowlist |
| FR-4 | Cookie events use a five-second debounce with an alarm fallback; five-minute reconciliation and persisted retry metadata survive worker suspension | Implemented; browser scheduling is not a delivery SLA |
| FR-5 | Positive ordered snapshots reject stale/conflicting sequences and acknowledge exact successful replays as unchanged | Implemented |
| FR-6 | A candidate is checked while disabled before old rows are replaced | Implemented; atomic disabled import requires the supplied backend patch |
| FR-7 | Missing base session cookie produces an empty logout snapshot; receiver disables every bound backend row | Implemented; remote outages can cause partial mutation |
| FR-8 | Revocation disables and verifies backend rows before removing token authorization | Implemented; backend failures leave the token valid for retry |
| FR-9 | A different pinned `__Secure-1PSID` hash cannot silently replace the original session | Implemented; legitimate base-session rotation may also be rejected |
| FR-10 | Optional reload applies only to the selected existing inactive Gemini tab, with a 15–1440 minute interval, default 30 | Implemented; no generation-activity detection |
| FR-11 | Profile table shows shared operator metadata, current-profile marker, connectivity, session status, and refresh telemetry | Implemented; registered profiles only, not Chrome profile discovery |
| FR-12 | Public source and normal build contain no default device connection/token | Required distribution boundary; one-click packaging is private only |
| FR-13 | Optional provisioning creates an isolated backend and separate 9router provider/model entries | Implemented as an operator-specific script, not universal deployment automation |

## Security and reliability requirements

- Prefer private Tailscale Serve HTTPS. Never publish the receiver through Funnel or expose backend administration publicly.
- Keep the bearer token mandatory even behind Tailscale; enforce Host and request-framing validation.
- Store only code/token/account/snapshot hashes and metadata in receiver SQLite, not cookie values. Backend persistence is separate and contains session credentials.
- Treat extension storage, private packages, connection JSON, configuration, and backups as sensitive.
- Use one service/state directory and no competing backend cookie writers. All target operations are globally serialized.
- Do not claim atomic distributed transactions or exactly-once backend mutation. Durable journaling supports recovery, not rollback of every remote side effect.
- Metadata visibility assumes a single trusted operator. Separate deployments are needed for mutually untrusted users.

## Constraints and non-goals

The public extension does not discover arbitrary Chrome profiles or auto-provision remote services. One token belongs to one browser profile and one dedicated backend; revocation does not free that backend binding for reuse. Recovery from lost storage or a changed pinned session requires deliberate migration.

Out of scope:

- Official Google API access, billing, or entitlement management.
- Account-verification bypass, account-ban immunity, or guaranteed session longevity.
- Guaranteed availability of Argon or any other model; model labels do not prove entitlement.
- Shared cookie pools, implicit account fallback, or concurrent external cookie writers.
- Keeping sessions alive with background chat prompts.
- Synchronization while Chrome is closed/asleep or disconnected from the private network.
- Tenant-isolated profile metadata or public Internet hosting.
- General browser support guarantees merely because upstream contains multiple browser manifests.

For supported production integrations and model access, use Google's official Gemini API when the desired model is officially available to your account. This cookie bridge is not a substitute for official API authorization.

## Verification and release evidence

The current project baseline reported for this documentation is 61 JavaScript tests, 36 Python receiver tests, and 2 provisioning tests. Counts are a snapshot, not a permanent acceptance criterion; run the commands in [Contributing](../CONTRIBUTING.md) and record current results. Browser smoke and isolated transport tests add evidence but do not replace live backend contract checks. Upstream development dependencies have known audit findings; do not describe the dependency tree as vulnerability-free.

A release should demonstrate sanitized packaging, successful local suites, actual backend contract compatibility, explicit profile isolation, and a reviewed private-network deployment. Google session and model availability remain external dependencies.
