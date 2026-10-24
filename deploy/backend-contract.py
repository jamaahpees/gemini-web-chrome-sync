"""Verify deployed adapter semantics using synthetic credentials in an isolated backend."""
import json
import os
from pathlib import Path
import secrets
import subprocess
import tempfile
import time
import urllib.error
import urllib.request

name = 'gemini-sync-contract-test'
image = os.environ.get('GEMINI_TEST_IMAGE', 'gemini-web-browser-source:4.23.0')
with tempfile.TemporaryDirectory(prefix='sync-contract-') as temporary:
    token = secrets.token_urlsafe(32)
    api = secrets.token_urlsafe(32)
    env = Path(temporary) / 'env'
    env.write_text('ADMIN_TOKEN=' + token + '\nAPI_KEY=' + api + '\nGEMINI_BROWSER_COOKIE_SOURCE=1\n')
    env.chmod(0o600)
    subprocess.run(['docker', 'run', '-d', '--name', name, '--network', 'none', '--env-file', str(env),
                    image, '--db', '/tmp/contract.db', '--port', '8083'], check=True, stdout=subprocess.DEVNULL)
    # distroless has no HTTP client. Enter its network namespace with host nsenter/curl.
    try:
        pid = subprocess.check_output(['docker', 'inspect', name, '--format', '{{.State.Pid}}'], text=True).strip()
        def call(method, path='', data=None):
            args = ['nsenter', '-t', pid, '-n', 'curl', '-sS', '--max-time', '10', '-X', method,
                    '-H', 'Authorization: Bearer ' + token, '-H', 'Content-Type: application/json',
                    'http://127.0.0.1:8083/admin/api/cookies' + path]
            if data is not None:
                args.extend(['--data-binary', '@-'])
            raw = subprocess.check_output(args, input=None if data is None else json.dumps(data).encode(), stderr=subprocess.DEVNULL)
            return json.loads(raw)
        for attempt in range(20):
            try:
                call('GET')
                break
            except (subprocess.CalledProcessError, ValueError):
                if attempt == 19:
                    raise
                time.sleep(.25)
        ident = call('POST', data={'cookie': '__Secure-1PSID=synthetic; __Secure-1PSIDTS=synthetic; SAPISID=synthetic', 'status': 'disabled'})['id']
        rows = call('GET')['items']
        assert len(rows) == 1 and rows[0]['status'] == 'disabled', 'Atomic disabled import unsupported'
        result = call('POST', '/' + str(ident) + '/check', {})
        assert result.get('ok') is False, 'Synthetic offline credential should fail check'
        call('PATCH', '/' + str(ident), {'status': 'enabled'})
        assert call('GET')['items'][0]['status'] == 'enabled'
        call('PATCH', '/' + str(ident), {'status': 'disabled'})
        assert call('GET')['items'][0]['status'] == 'disabled'
        call('DELETE', '/' + str(ident))
        assert call('GET')['items'] == []
        print(json.dumps({'atomicDisabledImport': True, 'failedCookieCheck': True, 'enableDisableDelete': True, 'isolatedNoNetwork': True}))
    finally:
        subprocess.run(['docker', 'rm', '-f', name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
