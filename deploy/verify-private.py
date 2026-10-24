"""Verify the live sync listener is private without handling any credential."""
import json
import subprocess
import urllib.error
import urllib.request

config = json.loads(subprocess.check_output(['tailscale', 'serve', 'status', '--json']))
public = config.get('AllowFunnel', {})
assert not any(k.endswith(':8443') and v for k, v in public.items()), 'Sync port is exposed by Funnel'
assert any(k.endswith(':443') and v for k, v in public.items()), 'Existing public dashboard changed'
assert any(k.endswith(':8443') for k in config.get('Web', {})), 'Private sync route missing'
try:
    with urllib.request.urlopen('http://127.0.0.1:8787/v1/status', timeout=5) as response:
        raise AssertionError('Unauthenticated status must not be allowed')
except urllib.error.HTTPError as error:
    assert error.code == 401, 'Unexpected auth status: ' + str(error.code)
    data = json.load(error)
    assert set(data).issubset({'error', 'status'}), 'Unexpected unauthenticated response shape'
print(json.dumps({'privateSync8443': True, 'dashboard443Preserved': True, 'unauthenticatedStatus': 401}))
