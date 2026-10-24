"""Batch provision and pair multiple profiles (e.g. 1 to 10 or custom names)."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--count', type=int, default=5, help='Number of profiles to provision')
    parser.add_argument('--start', type=int, default=3, help='Starting profile index')
    parser.add_argument('--ip', default='100.112.195.84', help='Tailscale receiver IP')
    args = parser.parse_args()

    for idx in range(args.start, args.start + args.count):
        name = f'profile-{idx}'
        port = 8090 + idx  # 8093, 8094, etc.
        print(f"=== Provisioning {name} on port {port} ===")
        cmd_prov = f"python3 /opt/gemini-cookie-sync/deploy/provision.py --name {name} --port {port}"
        res = subprocess.run(['ssh', '-o', 'BatchMode=yes', 'root@100.112.195.84', cmd_prov], capture_output=True, text=True)
        if res.returncode != 0:
            print(f"Provision failed or already exists: {res.stderr}")
        
        # Register in config & pair
        remote_script = f"""
import json, pathlib, sys
p = pathlib.Path('/etc/gemini-cookie-sync/config.json')
d = json.loads(p.read_text())
target_file = pathlib.Path('/root/gemini-cookie-sync/targets/{name}/target.json')
if target_file.exists():
    t = json.loads(target_file.read_text())
    t['label'] = 'Profile {idx} (Gemini Web)'
    d['backends']['{name}'] = t
    p.write_text(json.dumps(d))
    p.chmod(0o600)

sys.path.insert(0, '/opt/gemini-cookie-sync/server')
from cookie_sync import Store, load_config
store = Store('/var/lib/gemini-cookie-sync', d)
code = store.create_code('{name}')
device = store.pair(code)
conn = {{'endpoint': 'http://{args.ip}:8787', **device}}
out = pathlib.Path('/var/lib/gemini-cookie-sync/{name}.connection.json')
out.write_text(json.dumps(conn))
out.chmod(0o600)
print('Paired: {name}')
"""
        res_pair = subprocess.run(['ssh', '-o', 'BatchMode=yes', 'root@100.112.195.84', f"python3 -c \"{remote_script}\""], capture_output=True, text=True)
        print(res_pair.stdout)

        # Download connection json
        local_dest = Path.home() / '.gemini-cookie-sync' / f'{name}.connection.json'
        subprocess.run(['scp', f'root@100.112.195.84:/var/lib/gemini-cookie-sync/{name}.connection.json', str(local_dest)], check=False)
        print(f"Saved local: {local_dest}")

    # Restart receiver service
    subprocess.run(['ssh', '-o', 'BatchMode=yes', 'root@100.112.195.84', 'systemctl restart gemini-cookie-sync'], check=True)
    print("Done provisioning batch!")

if __name__ == '__main__':
    main()
