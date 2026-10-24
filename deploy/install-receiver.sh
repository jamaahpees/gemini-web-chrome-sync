#!/usr/bin/env bash
set -euo pipefail
# Run on the server after copying server/ into /opt/gemini-cookie-sync/server.
if [[ $(id -u) != 0 ]]; then
  echo 'Run as root.' >&2
  exit 1
fi
if ! id geminisync >/dev/null 2>&1; then
  useradd --system --home-dir /var/lib/gemini-cookie-sync --shell /usr/sbin/nologin geminisync
fi
install -d -m 0700 -o geminisync -g geminisync /var/lib/gemini-cookie-sync
install -d -m 0750 -o root -g geminisync /etc/gemini-cookie-sync
if [[ ! -f /etc/gemini-cookie-sync/config.json ]]; then
  echo 'Create mode0600 geminisync:geminisync /etc/gemini-cookie-sync/config.json before enabling service.' >&2
  exit 1
fi
chmod 0600 /etc/gemini-cookie-sync/config.json
chown geminisync:geminisync /etc/gemini-cookie-sync/config.json
cat > /etc/systemd/system/gemini-cookie-sync.service <<'UNIT'
[Unit]
Description=Private Gemini browser cookie sync receiver
After=network-online.target docker.service tailscaled.service
Wants=network-online.target

[Service]
Type=simple
User=geminisync
Group=geminisync
WorkingDirectory=/opt/gemini-cookie-sync/server
ExecStart=/usr/bin/python3 /opt/gemini-cookie-sync/server/cookie_sync.py --config /etc/gemini-cookie-sync/config.json --state /var/lib/gemini-cookie-sync serve
Restart=on-failure
RestartSec=5
UMask=0077
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/var/lib/gemini-cookie-sync
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectControlGroups=true
RestrictSUIDSGID=true
RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX

[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable --now gemini-cookie-sync.service
# A dedicated Serve port preserves the existing Funnel dashboard on 443.
tailscale serve --bg --https=8443 --yes http://127.0.0.1:8787
python3 - <<'PY'
import json, subprocess
config = json.loads(subprocess.check_output(['tailscale', 'serve', 'status', '--json']))
assert not any(k.endswith(':8443') and v for k, v in config.get('AllowFunnel', {}).items()), 'Unsafe Funnel8443 configuration'
print('Private Serve8443 configured; existing Serve/Funnel settings retained.')
PY
