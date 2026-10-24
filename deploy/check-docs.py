"""Check relative Markdown links and required documentation without network access."""
import pathlib
import re
import sys

root = pathlib.Path(__file__).resolve().parents[1]
required = ['README.md', 'SECURITY.md', 'CONTRIBUTING.md', 'docs/PRD.md',
            'docs/ARCHITECTURE.md', 'docs/TECH-STACK.md', 'docs/USAGE.md',
            'docs/API.md', 'docs/OPERATIONS.md', 'docs/images/settings-demo.png',
            'docs/images/profiles-demo.png']
errors = [name + ': missing' for name in required if not (root / name).is_file()]
files = [root / 'README.md', root / 'SECURITY.md', root / 'CONTRIBUTING.md'] + list((root / 'docs').glob('*.md'))
for path in files:
    if not path.exists():
        continue
    for link in re.findall(r'!?\[[^\]]*\]\(([^\s)]+)(?:\s+[^)]*)?\)', path.read_text(encoding='utf-8')):
        if '://' in link or link.startswith(('#', 'mailto:')):
            continue
        dest = link.split('#', 1)[0]
        if dest and not (path.parent / dest).exists():
            errors.append(str(path.relative_to(root)) + ': broken link ' + dest)
print('Documentation files checked:', len(files), '| Errors:', len(errors))
for error in errors:
    print(error)
sys.exit(bool(errors))
