"""Fail before publication if tracked source contains known credential formats or private files."""
import pathlib
import re
import subprocess
import sys

files = subprocess.check_output(['git', 'ls-files'], text=True).splitlines()
patterns = [r'g\.a000[A-Za-z0-9_-]{20}', r'sidts-Cj[A-Za-z0-9_-]{15}',
            r'gh[pousr]_[A-Za-z0-9]{25,}', r'-----BEGIN .*PRIVATE KEY']
findings = []
for name in files:
    path = pathlib.Path(name)
    if path.suffix.lower() in ('.png', '.jpg', '.webp', '.ico'):
        continue
    if path.resolve() == pathlib.Path(__file__).resolve():
        continue
    text = path.read_text(errors='ignore')
    if any(re.search(pattern, text) for pattern in patterns):
        findings.append((name, 'credential-format'))
    if name.endswith('.connection.json') or name.endswith('default-connection.json'):
        findings.append((name, 'private-config'))
print('Tracked files scanned:', len(files), '| Findings:', len(findings))
for name, kind in findings:
    print(name, kind)
sys.exit(bool(findings))
