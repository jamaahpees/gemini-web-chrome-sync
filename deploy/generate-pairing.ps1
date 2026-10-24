param([string]$Target = 'main-profile')
$ErrorActionPreference = 'Stop'
if ($Target -notmatch '^[a-z][a-z0-9-]{0,23}$') { throw 'Invalid target name' }
ssh -o BatchMode=yes -o ConnectTimeout=15 root@100.100.100.100 "runuser -u geminisync -- python3 /opt/gemini-cookie-sync/server/cookie_sync.py --config /etc/gemini-cookie-sync/config.json --state /var/lib/gemini-cookie-sync create-pairing $Target"
if ($LASTEXITCODE -ne 0) { throw 'Could not generate pairing code; inspect target binding on server.' }
Write-Output 'Code expires after 10 minutes. Enter it in the extension; do not paste it into chat.'
