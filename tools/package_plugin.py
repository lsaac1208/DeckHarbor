"""Build a Decky runtime ZIP without dependencies, caches or development artifacts."""
from pathlib import Path
import hashlib
import json
import stat
import sys
import zipfile

root = Path(__file__).resolve().parents[1]
plugin_name = json.loads((root / 'plugin.json').read_text())['name']
version = json.loads((root / 'package.json').read_text())['version']
required = ('dist/index.js', 'defaults/bin/aria2c', 'defaults/bin/7zz')
missing = [name for name in required if not (root / name).is_file()]
if missing:
    raise SystemExit('Missing runtime files: ' + ', '.join(missing) + '. See docs/DEVELOPMENT.md')
output = Path(sys.argv[1]) if len(sys.argv) > 1 else root.parent / f"{plugin_name}.v.{version}.zip"
entries = [root / name for name in ('main.py', 'plugin.json', 'package.json', 'CHANGELOG-0.3.1.md', 'CHANGELOG-0.4.0.md', 'CHANGELOG-0.4.1.md', 'CHANGELOG-0.4.2.md', 'CHANGELOG-0.4.3.md', 'README.md', 'docs/catalog-sources.md')]
entries.extend(root / name for name in ('LICENSE.md', 'THIRD_PARTY_NOTICES.md', 'docs/INSTALL.md', 'docs/DEVELOPMENT.md'))
for directory in ('dist', 'defaults', 'py_modules'):
    entries.extend(p for p in (root / directory).rglob('*') if p.is_file()
                   and '__pycache__' not in p.parts and p.suffix not in ('.pyc', '.pyo', '.map'))
with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
    for p in sorted(entries):
        relative = p.relative_to(root)
        info = zipfile.ZipInfo.from_file(p, arcname=str(Path(plugin_name) / relative))
        executable = str(relative) in ('defaults/bin/aria2c', 'defaults/bin/7zz')
        info.external_attr = (stat.S_IFREG | (0o755 if executable else 0o644)) << 16
        info.compress_type = zipfile.ZIP_DEFLATED
        archive.writestr(info, p.read_bytes())
with zipfile.ZipFile(output) as archive:
    bad = archive.testzip()
    if bad:
        raise RuntimeError(f'Archive checksum failed: {bad}')
    for p in entries:
        name = str(Path(plugin_name) / p.relative_to(root))
        assert archive.read(name) == p.read_bytes(), name
checksum = hashlib.sha256(output.read_bytes()).hexdigest()
output.with_suffix(output.suffix + '.sha256').write_text(f'{checksum}  {output.name}\n')
print(f'{output}\n{len(entries)} files, {output.stat().st_size:,} bytes\nSHA256 {checksum}')
