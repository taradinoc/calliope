"""Export reviewed working files tracked by Mercurial and package the Windows editor."""
import argparse
import hashlib
import importlib.metadata
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
EXCLUDED = {'.hg', '.git', '.vscode', '.agents', '.codex', '__pycache__', 'out'}
TEXT = {'.md', '.txt', '.py', '.c', '.h', '.s', '.i80', '.s80', '.mk', '.json', '.bat', '.res', '.yml', '.yaml'}


def eligible(name):
    path = PurePosixPath(name.replace('\\', '/'))
    return (not set(path.parts).intersection(EXCLUDED)
            and path.suffix not in ('.orig', '.pyc', '.pdb', '.obj', '.o', '.log')
            and not path.name.startswith('.env'))


def scan(name, data):
    """Reject developer paths and obvious secrets in distributed text files."""
    # Also scan native binaries for the actual checkout root (including wide
    # strings). Generated Python bytecode is excluded from archives entirely.
    root = str(ROOT)
    for value in (root, root.replace('\\', '/')):
        if value.encode().lower() in data.lower() or value.encode('utf-16le').lower() in data.lower():
            raise ValueError('Build checkout path embedded in ' + name)
    path = PurePosixPath(name)
    if path.suffix.lower() not in TEXT and path.name not in ('Makefile', 'LICENSE', 'VERSION', '.hgignore'):
        return
    text = data.decode('utf-8', errors='replace')
    if re.search(r'\b[A-Za-z]:[/\\](?:Calliope|sgdk|BonkGen|Emu|Furnace|Users)[/\\\s"\x27.]?', text, re.I):
        raise ValueError('Developer-specific path in ' + name)
    if re.search(r'-{5}BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-{5}', text):
        raise ValueError('Private key in ' + name)


def archive(destination, prefix, entries):
    inventory = []
    with zipfile.ZipFile(destination, 'w', zipfile.ZIP_DEFLATED) as output:
        for name, source in sorted(entries):
            data = source.read_bytes()
            scan(name, data)
            output.writestr(prefix + '/' + name, data)
            inventory.append({'file': name, 'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()})
    return inventory


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-only', action='store_true')
    parser.add_argument('--editor-dir', type=Path, default=ROOT/'out/editor-release/Calliope Bank Editor.dist')
    a = parser.parse_args()
    version = (ROOT/'VERSION').read_text().strip()
    if not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+(?:-[A-Za-z0-9.]+)?', version):
        raise ValueError('Invalid release version')
    destination = ROOT/'out/releases'/version
    destination.mkdir(parents=True, exist_ok=True)
    tracked = subprocess.check_output(['hg', 'files', '-0'], cwd=ROOT).decode().split('\0')
    entries = [(name.replace('\\', '/'), ROOT/name) for name in tracked
               if name and eligible(name) and (ROOT/name).is_file()]
    required = {'LICENSE', 'THIRD_PARTY.md', 'VERSION', 'tools/toolchain.py', 'tools/consumer.py',
                'docs/integration.md', 'examples/ASSETS.md', 'examples/minimal/Makefile'}
    missing = required - {name for name, _ in entries}
    if missing:
        raise ValueError('Add release inputs to Mercurial first: ' + ', '.join(sorted(missing)))
    files = []
    source = destination/f'calliope-{version}-source.zip'
    inventory = {'source': archive(source, f'calliope-{version}', entries)}
    files.append(source)
    if not a.source_only:
        for name in ('calliope_editor.exe', 'LICENSE', 'THIRD_PARTY.md', 'README.md',
                     'assets/genesis-plus-gx-source.zip', 'assets/GENESIS-PLUS-GX-LICENSE.txt',
                     'assets/SGDK-LICENSE.txt', 'assets/qt-licenses'):
            if not (a.editor_dir/name).exists():
                raise ValueError('Incomplete editor package: ' + name)
        editor = destination/f'calliope-{version}-windows-editor.zip'
        editor_entries = [(p.relative_to(a.editor_dir).as_posix(), p)
                          for p in a.editor_dir.rglob('*') if p.is_file() and eligible(p.relative_to(a.editor_dir).as_posix())]
        inventory['editor'] = archive(editor, 'Calliope Bank Editor', editor_entries)
        files.append(editor)
    dependencies = {'python': sys.version.split()[0]}
    local = ROOT/'out/editor-deps'
    distributions = importlib.metadata.distributions(path=[str(local)]) if local.exists() else importlib.metadata.distributions()
    for item in distributions:
        name = item.metadata.get('Name', '')
        if name.lower().startswith(('pyside6', 'shiboken6', 'nuitka')):
            dependencies[name] = item.version
    dependencies['Genesis Plus GX'] = json.loads((ROOT/'editor/assets/core.json').read_text())['revision']
    inventory['dependencies'] = dependencies
    inventory['publication_blockers'] = [
        'Complete supplied asset permissions and credits in examples/ASSETS.md.',
        'Confirm the originating BonkGen harness license/permission.',
        'Complete exact Qt/third-party corresponding-source and redistribution review before publishing the editor.',
    ]
    report = destination/'inventory.json'
    report.write_text(json.dumps(inventory, indent=2)+'\n')
    files.append(report)
    (destination/'SHA256SUMS.txt').write_text(''.join(hashlib.sha256(p.read_bytes()).hexdigest()+'  '+p.name+'\n' for p in files))
    print('Prepared unpublished candidates:', destination)


if __name__ == '__main__':
    main()
