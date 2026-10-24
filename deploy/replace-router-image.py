"""Operator helper: preserve an existing router container and replace only its image.

Run locally on the Linux Docker host after building and verifying the new image.
No environment values are printed. Never removes data volumes.
"""
import argparse
import datetime
import json
import subprocess


def docker(*args):
    return subprocess.check_output(['docker', *args], text=True).strip()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--container', default='9router')
    parser.add_argument('--image', required=True)
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    info = json.loads(docker('inspect', args.container))[0]
    docker('image', 'inspect', args.image)
    host = info['HostConfig']
    networks = list(info['NetworkSettings'].get('Networks', {}))
    if len(networks) != 1:
        raise SystemExit('Inspect multi-network setup manually; no changes applied')
    if args.dry_run:
        print(json.dumps({'container': args.container, 'network': networks[0],
                          'volumes': len(info.get('Mounts', [])), 'portGroups': len(host.get('PortBindings', {}))}))
        return
    rollback = args.container + '-before-gemini-fix-' + datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    command = ['docker', 'run', '-d', '--name', args.container, '--network', networks[0]]
    restart = host.get('RestartPolicy', {}).get('Name')
    if restart:
        command += ['--restart', restart]
    if host.get('Memory'):
        command += ['--memory', str(host['Memory'])]
    if host.get('MemorySwap') and host['MemorySwap'] > 0:
        command += ['--memory-swap', str(host['MemorySwap'])]
    for env in info['Config'].get('Env', []):
        command += ['--env', env]
    for mount in info.get('Mounts', []):
        if mount['Type'] not in ('bind', 'volume'):
            raise SystemExit('Unsupported mount; inspect manually')
        source = mount.get('Name') if mount['Type'] == 'volume' else mount['Source']
        command += ['--mount', 'type=' + mount['Type'] + ',source=' + source + ',target=' + mount['Destination'] + (',readonly' if not mount.get('RW', True) else '')]
    for container_port, bindings in host.get('PortBindings', {}).items():
        for binding in bindings or []:
            prefix = binding.get('HostIp') or ''
            if ':' in prefix:
                raise SystemExit('IPv6 binding requires manual review')
            command += ['--publish', (prefix + ':' if prefix else '') + binding['HostPort'] + ':' + container_port]
    command += [args.image] + (info['Config'].get('Cmd') or [])
    docker('stop', args.container)
    docker('rename', args.container, rollback)
    try:
        subprocess.run(command, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except subprocess.CalledProcessError:
        subprocess.run(['docker', 'rm', '-f', args.container], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        docker('rename', rollback, args.container)
        docker('start', args.container)
        raise SystemExit('Replacement failed; previous container restarted') from None
    print('Replacement started; rollback container retained:', rollback)


if __name__ == '__main__':
    main()
