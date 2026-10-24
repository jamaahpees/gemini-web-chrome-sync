# Receiver API reference

This is a private credential-ingestion API, not an inference API. Source of truth: `server/cookie_sync.py`. All examples use placeholders; never place live cookies or tokens in published examples.

## Transport, authentication and framing

Use a private Tailscale HTTPS origin such as `https://your-node.your-tailnet.ts.net:8443`. Direct HTTP may use your receiver's canonical Tailscale IPv4 in `100.64.0.0/10`, port 8787. Endpoint selection is explicit, not discovered by scanning. The extension requests permission for the configured exact host.

- `POST /v1/pair` exchanges a one-time code; all other endpoints require exactly one `Authorization: Bearer <DEVICE_TOKEN>` header.
- Target selection is bound to the token. There is no client-supplied backend URL or target field.
- Host must be loopback or match configured `allowedHosts`; a numeric port is stripped before comparison.
- POST requires `Content-Type: application/json` and a valid `Content-Length`. Maximum body: **65,536 bytes**. Duplicate/invalid lengths, `Transfer-Encoding`, and `Expect` are rejected.
- GET/DELETE accept no nonempty request body. Paths match exactly: no query-string API parameters or pagination.
- Responses are JSON with `Cache-Control: no-store` and `X-Content-Type-Options: nosniff`.
- No CORS/preflight API is provided. Use extension-worker requests, not page scripts. Unsupported HTTP methods may receive the standard handler's rejection rather than the route table's 404.
- Maximum 16 concurrent request threads; excess connections close rather than return a structured overload response. Socket timeout is 15 seconds.

## Endpoint summary

| Method | Path | Authorization | Purpose |
| --- | --- | --- | --- |
| POST | `/v1/pair` | One-time code in body | Consume pairing code |
| DELETE | `/v1/pair` | Bearer | Disable target and revoke token |
| GET | `/v1/status` | Bearer | Current caller status and sync request |
| POST | `/v1/snapshot` | Bearer | Ordered cookie snapshot or logout |
| POST | `/v1/heartbeat` | Bearer | Caller connectivity and refresh telemetry |
| GET | `/v1/profiles` | Bearer | Same-operator registered profile metadata |

## POST /v1/pair

Request must contain exactly `code`:

```json
{"code":"PAIRING_CODE_FROM_OPERATOR"}
```

Success, HTTP 200:

```json
{"deviceId":"GENERATED_DEVICE_ID","token":"GENERATED_DEVICE_TOKEN"}
```

Codes expire after 600 seconds, are single-use, and are scoped to one configured backend. Generating another code for an unbound target invalidates the earlier code. A target already bound to a device, including a revoked device, cannot be paired again. Tokens are returned in plaintext only during issuance; the store retains their SHA-256 hashes.

Pair attempts are limited to ten per source IP per ten minutes, with bounded in-memory source tracking. Behind Serve, callers may share a loopback source. Limiting is process-local, not a distributed quota.

## GET /v1/status

```sh
curl --fail-with-body 'https://your-node.your-tailnet.ts.net:8443/v1/status' \
  -H 'Authorization: Bearer DEVICE_TOKEN_PLACEHOLDER'
```

Example response:

```json
{"syncRequested":true,"status":"awaiting_sync","lastApplied":0}
```

`lastApplied` is a **sequence number**, not a timestamp. Successful calls update server-recorded `lastSeen`. Eligible backend validity checks are cached for five minutes. `good` means exactly one enabled row passed the cookie-check endpoint, not that a model request succeeded.

Statuses include `awaiting_sync`, `good`, `invalid`, `logged_out`, `backend_unavailable`, `backend_partial_failure`, and `recovery_required`. In-flight journal recovery returns `recovery_required` and requests sync. `account_changed` is a snapshot result and a worker blocking state; that rejection does not overwrite the receiver's previously stored session status.

## POST /v1/snapshot

The body must contain exactly `sequence`, `cookies`, and `logout`:

```json
{
  "sequence": 1,
  "cookies": [
    {"name":"__Secure-1PSID","value":"SYNTHETIC_BASE_SESSION","domain":".google.com","path":"/","secure":true,"httpOnly":true},
    {"name":"__Secure-1PSIDTS","value":"SYNTHETIC_SESSION_TIMESTAMP","domain":".google.com","path":"/","secure":true,"httpOnly":true}
  ],
  "logout": false
}
```

These synthetic cookies illustrate schema only and cannot authenticate a real session.

### Cookie validation

- At most 64 cookie objects; `name`, `value`, `domain`, `path`, `secure`, `httpOnly` are required. Extra cookie metadata such as `expirationDate` is ignored.
- Domain exactly one of `google.com`, `.google.com`, `gemini.google.com`, `.gemini.google.com`; path exactly `/`.
- Both flags must be JSON booleans. `__Secure-` names require `secure: true`; preserve original flags for other names.
- Values must be printable ASCII, without semicolons/control characters. Non-logout snapshots require nonempty `__Secure-1PSID` and `__Secure-1PSIDTS`.
- Duplicate name/domain/path identities are rejected. Different values for the same name across domains are rejected. Same-name/same-value across domains is accepted and flattened once.

Allowed names:

```text
SID HSID SSID APISID SAPISID SIDCC
__Secure-1PSID __Secure-3PSID __Secure-1PAPISID __Secure-3PAPISID
__Secure-1PSIDTS __Secure-3PSIDTS __Secure-1PSIDCC __Secure-3PSIDCC
__Secure-1PSIDRTS __Secure-3PSIDRTS __Secure-ENID
AEC NID SOCS COMPASS __Secure-BUCKET
```

### Ordering and results

Sequence must be an integer in `1..9223372036854775807`; JavaScript clients should stay within safe-integer bounds. Canonical content hashes sort cookies, include logout state, and ignore extra Chrome metadata.

```json
{"status":"applied","sequence":1}
```

HTTP 200 may return:

| Result | Meaning |
| --- | --- |
| `applied` | Candidate applied, or logout rows disabled |
| `unchanged` | Exact previously applied sequence/hash replay |
| `invalid` | Candidate failed cookie check; old rows retained; seen sequence consumed |
| `account_changed` | Snapshot differs from pinned base-session hash; not applied |

Do not equate HTTP 200 with accepted credentials. Lower seen sequences or same-sequence/different-content return 409. Retry exact pending content/sequence after uncertain delivery; changed content needs a higher sequence. Validation failures consume neither sequence nor hash. Backend failures can occur after remote mutations: 503 does not mean nothing changed. The initially applied `__Secure-1PSID` hash is a session pin, not a stable account identifier.

Logout example:

```json
{"sequence":2,"cookies":[],"logout":true}
```

Logout disables every target row, without deleting rows. A logout with cookies is invalid.

## POST /v1/heartbeat

Exactly the following shape is accepted:

```json
{"refresh":{"enabled":true,"minutes":30,"lastRefreshAt":1700000000,"nextRefreshAt":1700001800}}
```

`enabled` is boolean; `minutes` an integer 15–1440 even when disabled; timestamps are nonnegative integer epoch seconds. Success: `{"status":"ok"}`.

Updates caller `lastSeen` using server time and stores client-reported refresh telemetry. These values neither schedule receiver operations nor authorize remote tab control. Heartbeat failure is best effort in the worker and does not itself change sync status.

## GET /v1/profiles

```json
{
  "profiles": [
    {
      "profileId":"GENERATED_DEVICE_ID",
      "label":"Personal profile",
      "isCurrent":true,
      "online":true,
      "lastSeen":1700000000,
      "lastAppliedAt":1700000000,
      "status":"good",
      "refresh":{"enabled":true,"minutes":30,"lastRefreshAt":0,"nextRefreshAt":1700001800}
    }
  ]
}
```

Returns every non-revoked registered device in this service/config/state deployment, not arbitrary local Chrome profiles. Labels use backend `label` or backend name, truncated to 80 characters. `online` means last status/heartbeat within 180 seconds. Timestamps are epoch seconds; a never-reported refresh interval can be zero server-side. The extension sanitizer normalizes invalid/zero intervals to 30 and caps displayed profile results at 100.

No cookies, tokens, token/account hashes, backend URLs, admin credentials or API keys are returned. Metadata **is shared across authenticated profiles of the operator**; this is not mutually untrusted multi-tenancy. Listing does not freshly validate every peer or update caller `lastSeen`.

## DELETE /v1/pair

No body. Success: `{"status":"revoked"}`. All target rows are disabled and verified before token removal. Backend failure returns 503 and keeps the token valid for retry, though rows may already be partly disabled. Backend reservation remains permanent. Repeating after successful revocation returns 401; this is not an idempotent success endpoint.

## Error reference

Errors use `{"error":"ERROR_CODE"}`.

| HTTP | Representative codes |
| --- | --- |
| 400 | `invalid_host`, `invalid_framing`, `unexpected_body`, `invalid_body`, `invalid_json`, `invalid_snapshot`, `invalid_cookies`, `duplicate_cookie`, `missing_cookies`, `invalid_logout`, `invalid_heartbeat`, `invalid_pairing` |
| 401 | `unauthorized`, `invalid_pairing` |
| 404 | `not_found` |
| 408 | `request_timeout` |
| 409 | `target_already_bound`, `stale_sequence` |
| 411 | `length_required` |
| 413 | `body_too_large` |
| 415 | `json_required` |
| 429 | `rate_limited` |
| 500 | `internal_error` |
| 503 | `backend_unavailable`, `backend_partial_failure` |

The generic HTTP handler can emit `request_rejected`. No access/error request logging is enabled; do not enable credential-bearing traces to diagnose failures.

## Internal backend adapter, not browser API

All calls use the fixed loopback origin plus `/admin/api/cookies`, both `Authorization: Bearer <adminToken>` and `x-api-key: <apiKey>`, no environment proxy/redirects, a ten-second timeout and a one-MiB response cap.

| Operation | Request | Accepted response |
| --- | --- | --- |
| List | GET base path | Array, or object with `items`, `cookies`, or `data` array; rows have `id` |
| Import | POST base path, `{"cookie":"name=value; ...","status":"disabled"}` | Row with `id`, directly or under `data` |
| Check | POST `/ID/check`, `{}` | `ok: true`, `valid: true`, or `status: "ok"/"valid"` |
| Set state | PATCH `/ID`, `{"status":"disabled"}` or enabled | Follow-up GET must verify state |
| Remove | DELETE `/ID` | Follow-up GET must verify absence |

IDs are URL-escaped. Validate these shapes against the pinned patched backend before production. Cookie checks do not call Argon, 9router, or any model. Inference API behavior belongs to the external backend/router, not this receiver.
