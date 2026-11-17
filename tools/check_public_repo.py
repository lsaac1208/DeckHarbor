"""Check tracked files for common credential/state leaks without printing values."""
from pathlib import Path
import ast
import re
import subprocess
import sys

root = Path(__file__).resolve().parents[1]
paths = subprocess.check_output(['git', 'ls-files', '-z'], cwd=root).decode().split('\0')
errors = []
patterns = [rb'gh[pousr]_[A-Za-z0-9]{30,}', rb'github_pat_[A-Za-z0-9_]{30,}', rb'-----BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY-----']
for name in filter(None, paths):
    path = root / name
    if not path.is_file():
        continue
    data = path.read_bytes()
    if any(re.search(pattern, data) for pattern in patterns):
        errors.append(name + ': possible credential')
    if path.suffix in ('.log', '.so', '.pyc') or '/node_modules/' in '/'+name or '/__pycache__/' in '/'+name:
        errors.append(name + ': runtime/build file')
    if path.name in ('credentials.json', 'settings.json', 'catalog_cache.json', 'github-auth.json'):
        errors.append(name + ': private state filename')
    if path.suffix == '.py':
        tree = ast.parse(data.decode('utf-8-sig'))
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                names = [t.id for t in node.targets if isinstance(t, ast.Name)]
                if any(n in ('SGDB_API_KEY', 'FREEDECK_BAIDU_APP_KEY', 'FREEDECK_BAIDU_SECRET_KEY') for n in names) and node.value.value:
                    errors.append(name + ': bundled service credential')
if errors:
    print('\n'.join(errors))
    sys.exit(1)
print('Tracked-file credential and runtime-state checks passed')
