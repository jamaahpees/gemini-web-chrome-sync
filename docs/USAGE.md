# Usage guide

## Prerequisites

Use a regular Chrome profile signed into Gemini, an authorized Google account, a private Tailscale connection, Python 3.10+ on the receiver host, and **one dedicated backend instance per profile**. Node/npm builds the extension. The supplied production service targets Linux.

Read [SECURITY.md](../SECURITY.md). This unofficial browser-session integration cannot bypass login/MFA, guarantee model availability, or prevent account restrictions.

## 1. Prepare the backend

Use `gemini-web2api-go` **v4.23.0**, apply `deploy/browser-source.patch`, build it, and set `GEMINI_BROWSER_COOKIE_SOURCE=1`. Review [the deployment checklist](../deploy/CHECKLIST.md) and validate the actual admin API before enabling synchronization.

Never use a shared cookie pool: rotation deletes all previous cookie rows on the target. Stop competing importers/rotation jobs. A 9router inference listener is neither the receiver nor a cookie-management target.

## 2. Configure the receiver

Copy `server/config.example.json` into a private configuration location. Generic Linux examples below assume you have created the service identity, directories and ownership. POSIX configuration permissions must be 0600.

```json
{
  "allowedHosts": ["your-node.your-tailnet.ts.net"],
  "backends": {
    "personal-profile": {
      "label": "Personal profile",
      "url": "http://127.0.0.1:8083",
      "adminToken": "REPLACE_WITH_PRIVATE_BACKEND_ADMIN_TOKEN",
      "apiKey": "REPLACE_WITH_PRIVATE_BACKEND_API_KEY"
    }
  }
}
```

Every backend needs a distinct dedicated loopback port. `allowedHosts` contains bare lowercase hostnames/IPs, not URLs or ports. Do not commit completed configuration.

```sh
python3 server/cookie_sync.py --config /etc/cookie-sync/config.json --state /var/lib/cookie-sync serve
tailscale serve --bg --https=8443 http://127.0.0.1:8787
tailscale serve status
```

Prefer private Serve HTTPS and preserve existing Serve routes. Do not use Funnel. Restrict tailnet ACLs/grants to the intended devices and port. The extension accepts `https://your-node.your-tailnet.ts.net:8443` or default-port HTTPS on the same `*.ts.net` form, with no path, query, fragment or credentials. Enter default HTTPS without an explicit `:443` suffix.

### Optional direct Tailscale IP

Use **your own receiver's Tailscale IPv4**, not a maintainer address. The extension accepts canonical addresses in `100.64.0.0/10` on port 8787 without editing source. Set that IP in `allowedHosts`, bind to it, and enter `http://YOUR_TAILSCALE_IP:8787` during pairing or in private connection JSON:

```sh
python3 server/cookie_sync.py --config /etc/cookie-sync/config.json --state /var/lib/cookie-sync serve --bind YOUR_TAILSCALE_IP --port 8787
```

Replace `YOUR_TAILSCALE_IP` with the literal assigned address. Wildcard/public/LAN binds are rejected. HTTP has no TLS layer and relies on Tailscale encrypted transport; HTTPS remains preferred. No network scanning discovers a server for you.

## 3. Build and load Chrome

Run from the repository root:

```sh
npm ci --ignore-scripts
npx grunt copy:chrome replace:chrome
```

Open `chrome://extensions`, enable **Developer mode**, select **Load unpacked**, and choose `build/chrome`. Right-click the extension toolbar icon and select **Gemini sync settings**. Use the intended regular Chrome profile; synchronization is unavailable in incognito.

## 4. Pair explicitly

On the receiver host, using the same service identity/config/state as the server:

```sh
python3 server/cookie_sync.py --config /etc/cookie-sync/config.json --state /var/lib/cookie-sync create-pairing personal-profile
```

Transfer the printed code privately. It is single-use and expires in ten minutes. In settings, enter your receiver endpoint and code, then pair and accept the requested permissions. Receiver permissions derive from your selected endpoint's exact host, alongside the required Google/Gemini permissions.

An initial upload is scheduled immediately after pairing. Keep Chrome running and Tailscale connected. Cookie changes trigger sending after debounce; alarms reconcile and retry automatically. `applied`/`unchanged` are acknowledgements, not proof that every model can respond.

![Sanitized settings demonstration](images/settings-demo.png)

### Connection JSON and private one-click packages

An operator can privately supply an already issued connection containing `endpoint`, `deviceId`, and `token`. Import it through the settings file picker, text box or clipboard controls. This is a secret-bearing file, not public configuration. Import verifies receiver status and resumes its last applied sequence.

The public repository intentionally omits `sync/default-connection.json`; a public build has no universal token and cannot offer working preconfigured one-click activation. `deploy/package-chrome.ps1` can embed a private connection for one intended profile. Review the script and its private input path before use. Never distribute that resulting package, commit its generated connection, or reuse it in another profile. Do not paste real connection JSON in issues or screenshots.

## 5. Optional tab refresh

Choose an existing Gemini tab, enable refresh, and select **15–1440 minutes** (default 30). Only the selected inactive, non-incognito Gemini tab is reloaded. Active tabs are skipped. An inactive tab may still be generating, so reload can interrupt it. Browser sleep and alarm throttling can delay execution; displayed next-refresh time is telemetry, not a guarantee. No chat prompts are generated to keep a session alive.

## 6. Read profile status

![Sanitized registered-profile demonstration](images/profiles-demo.png)

The table lists registered non-revoked devices in the same receiver deployment, not all Chrome profiles on your machine. It refreshes every 30 seconds while settings are open; use its refresh control for an immediate update.

- **Current:** the token used by this settings page.
- **Online:** a status/heartbeat reached the receiver within 180 seconds.
- **good:** the last eligible server check found one enabled valid cookie row.
- **Refresh:** reported settings and timestamps, not remote control.

Profiles share operator-controlled labels, IDs, connectivity, session status and refresh metadata. Cookies, tokens and backend credentials are not returned. Use separate deployments for users who must not share metadata.

## Disconnect and recovery

Disconnect requests server revocation before removing local connection state. It disables all rows on the dedicated backend. If the backend is unavailable, fix connectivity and retry; revocation may have partially disabled rows while retaining the token for retry.

A bound target remains reserved after revocation. Lost extension storage or `account_changed` needs an operator-controlled migration to a new empty dedicated backend/target. Do not clear SQLite or rename a bound target as a shortcut. A changed `__Secure-1PSID` can be legitimate rotation, but the guard cannot distinguish it from account replacement. See [Operations](OPERATIONS.md).

## Optional 9router integration

Register the dedicated backend as an OpenAI-compatible provider and use a distinct provider/prefix per profile. `deploy/provision.py --name personal --port 8090 --dry-run` previews its naming; actual provisioning assumes a particular existing Linux Docker/9router setup and must be reviewed first. Its configured model list is not auto-discovery or an availability guarantee. For officially supported model integrations, prefer Google's official Gemini API when the desired model is available for your account.
