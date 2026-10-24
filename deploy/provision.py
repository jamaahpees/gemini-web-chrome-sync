"""Provision one isolated Gemini backend and 9router provider (run on server)."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import socket
import subprocess
import urllib.request


def router_call(path, method='GET', data=None):
    root = Path('/var/lib/docker/volumes/9router-data/_data')
    token = hashlib.sha256((root.joinpath('machine-id').read_text().strip() +
                            '9r-cli-auth' + root.joinpath('auth/cli-secret').read_text().strip()).encode()).hexdigest()[:16]
    req = urllib.request.Request('http://127.0.0.1:20128' + path, method=method,
                                 data=None if data is None else json.dumps(data).encode(),
                                 headers={'x-9r-cli-token': token, 'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=30) as response:
        return json.load(response)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--name', required=True)
    parser.add_argument('--port', type=int, default=8090)
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    if not re.fullmatch(r'[a-z][a-z0-9-]{0,23}', args.name) or not 8090 <= args.port <= 8190:
        parser.error('Use a lowercase target name and port 8090..8190')
    container = 'gemini-sync-' + args.name
    prefix = 'gweb-' + args.name
    if args.dry_run:
        print(json.dumps({'container': container, 'prefix': prefix, 'isolated': True, 'port': args.port}))
        return
    if os.name != 'posix' or os.geteuid() != 0:
        parser.error('Run as root on the Linux server')
    os.umask(0o077)
    directory = Path('/root/gemini-cookie-sync/targets') / args.name
    if directory.exists():
        parser.error('Target already exists; refusing to overwrite credentials')
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', args.port))
    image = subprocess.check_output(['docker', 'image', 'inspect', 'gemini-web-browser-source:4.23.0', '--format', '{{.Id}}'], text=True).strip()
    directory.mkdir(parents=True, mode=0o700)
    admin = secrets.token_urlsafe(32)
    api_key = 'sk-gemini-' + secrets.token_hex(24)
    env = directory / 'secrets.env'
    env.write_text('ADMIN_TOKEN=' + admin + '\nAPI_KEY=' + api_key + '\nGEMINI_BROWSER_COOKIE_SOURCE=1\n')
    env.chmod(0o600)
    subprocess.run(['docker', 'run', '-d', '--name', container, '--restart', 'unless-stopped',
                    '--network', '9router-net', '--memory', '384m', '--env-file', str(env),
                    '-p', '127.0.0.1:%d:8083' % args.port,
                    '-v', container + '-data:/data', image, '--db', '/data/gemini.db', '--port', '8083'],
                   check=True, stdout=subprocess.DEVNULL)
    # The bridge target is operator-controlled; never accept this URL from the extension.
    target = {'url': 'http://127.0.0.1:%d' % args.port, 'adminToken': admin, 'apiKey': api_key}
    config = directory / 'target.json'
    config.write_text(json.dumps(target))
    config.chmod(0o600)
    node = router_call('/api/provider-nodes', 'POST', {
        'name': 'gemini web ' + args.name, 'prefix': prefix,
        'type': 'openai-compatible', 'apiType': 'chat', 'baseUrl': 'http://' + container + ':8083/v1'})['node']
    connection = router_call('/api/providers', 'POST', {
        'provider': node['id'], 'name': 'gemini web ' + args.name, 'apiKey': api_key,
        'defaultModel': 'gemini-4-argon-thinking-medium'})['connection']
    models = ['gemini-3.1-pro', 'gemini-3.1-pro-thinking', 'gemini-3.5-flash-lite',
              'gemini-3.5-flash-lite-thinking', 'gemini-3.6-flash', 'gemini-3.6-flash-thinking',
              'gemini-3.7-flash', 'gemini-3.7-flash-thinking', 'gemini-3.8-flash',
              'gemini-3.8-flash-thinking', 'gemini-4-argon', 'gemini-4-argon-thinking',
              'gemini-4-argon-thinking-medium']
    for model in models:
        router_call('/api/models/custom', 'POST', {'providerAlias': node['id'], 'id': model, 'type': 'llm'})
    print(json.dumps({'target': args.name, 'container': container, 'prefix': prefix,
                      'nodeId': node['id'], 'connectionId': connection['id'], 'targetConfig': str(config)}))


if __name__ == '__main__':
    main()
