#!/usr/bin/env bash
set -euo pipefail
# Preserve existing data volume and stopped rollback container. No 9router restart.
rollback="gemini-web-before-browser-sync-$(date -u +%Y%m%dT%H%M%SZ)"
docker image inspect gemini-web-browser-source:4.23.0 >/dev/null
docker stop gemini-web >/dev/null
docker rename gemini-web "$rollback"
if ! docker run -d --name gemini-web --restart unless-stopped --network 9router-net \
  --memory 512m --env-file /root/gemini-web/secrets.env \
  -e GEMINI_BROWSER_COOKIE_SOURCE=1 -p 127.0.0.1:8083:8083 \
  -v gemini-web-data:/data gemini-web-browser-source:4.23.0 >/dev/null; then
  docker rm -f gemini-web >/dev/null 2>&1 || true
  docker rename "$rollback" gemini-web
  docker start gemini-web >/dev/null
  exit 1
fi
printf 'Rollback container retained: %s\n' "$rollback"
