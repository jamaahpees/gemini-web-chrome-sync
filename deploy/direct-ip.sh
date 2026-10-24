#!/usr/bin/env bash
set -euo pipefail
install -d /etc/systemd/system/gemini-cookie-sync.service.d
cat > /etc/systemd/system/gemini-cookie-sync.service.d/direct-ip.conf <<'UNIT'
[Service]
ExecStart=
ExecStart=/usr/bin/python3 /opt/gemini-cookie-sync/server/cookie_sync.py --config /etc/gemini-cookie-sync/config.json --state /var/lib/gemini-cookie-sync serve --bind 100.100.100.100 --port 8787
UNIT
systemctl daemon-reload
systemctl restart gemini-cookie-sync
