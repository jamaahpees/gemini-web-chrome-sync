# Security

Google session cookies are credentials and can grant access beyond Gemini. Use a dedicated browser profile, explicit user consent and the least network access needed.

- Never commit cookie exports, device tokens, admin keys, runtime databases, private connection files, or generated personal ZIP/CRX packages.
- Never publish a package containing `sync/default-connection.json`. The source repository intentionally omits it.
- Use private Tailscale Serve, not Funnel. Direct Tailscale HTTP requires restricted binding and ACLs.
- Each profile needs its own token and dedicated backend. Sharing a personal preconfigured package between profiles breaks that isolation.
- Extension local storage is not a secret vault. Anyone controlling the Chrome profile can access its credentials.
- The backend stores the actual cookie snapshot. Protect its volume, backups and host access. Receiver SQLite stores hashes and operational metadata.
- Cookies and tokens must not appear in logs, issues, screenshots, traces or MCP responses. Browser traces can record request bodies; disable them for real-session tests.
- Prefer normal pairing for distributing this source. One-click credentials must be provisioned privately.
- Synthetic E2E fixtures are testing-only. Never use their success response as proof of a real Google session.

Documentation screenshots are generated with synthetic metadata. Do not replace them with unredacted screenshots from a real connection; settings fields and browser traces may expose credentials.

To report a vulnerability, contact the repository owner privately. Do not open a public issue containing credentials or account data.
