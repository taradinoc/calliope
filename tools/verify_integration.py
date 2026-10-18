"""Exercise a fresh external consumer, all modes, rebuilds and ordinary resources."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from toolchain import sgdk_path, sgdk_tool

ROOT = Path(__file__).resolve().parents[1]


def verify(home, sdk, destination):
    project = destination / 'game'
    shutil.copytree(home / 'examples/minimal', project,
                    ignore=shutil.ignore_patterns('out', 'boot', '__pycache__'))
    (project / 'res').mkdir(exist_ok=True)
    (project / 'res/ordinary.bin').write_bytes(b'\x01\x02')
    (project / 'res/ordinary.res').write_text('BIN ordinary "ordinary.bin" 2\n')
    source = project / 'src/main.c'
    source.write_text(source.read_text().replace('#include "audio.h"', '#include "audio.h"\n#include "ordinary.h"')
                      .replace('u16 previous = 0', 'VDP_setBackgroundColor(ordinary[0]);\n    u16 previous = 0'))
    # Resolve from the consumer, never from the caller's working directory.
    arguments = [str(sgdk_tool(sdk, 'make')), 'release', 'GDK=' + sdk.as_posix(),
                 'CALLIOPE_HOME=' + home.as_posix(), 'CALLIOPE_PYTHON=' + sys.executable]
    log = destination / 'build.log'

    def build(*settings):
        with log.open('a') as output:
            subprocess.run(arguments + list(settings), cwd=project, stdout=output,
                           stderr=subprocess.STDOUT, check=True, timeout=180)

    for hybrid in (0, 1):
        for region in ('ntsc', 'pal'):
            settings = (f'CALLIOPE_HYBRID={hybrid}', 'CALLIOPE_REGION=' + region)
            build(*settings)
            before = {p: p.stat().st_mtime_ns for p in (project / 'out').rglob('*.o')}
            rom = (project / 'out/rom.bin').read_bytes()
            build(*settings)
            assert before == {p: p.stat().st_mtime_ns for p in before}, 'Unchanged build recompiled objects'
            assert rom == (project / 'out/rom.bin').read_bytes()
            config = json.loads((project / 'out/calliope/calliope_config.json').read_text())
            assert config['region'] == region and config['hybrid'] == bool(hybrid)
    # An asset edit changes the bank; a manifest rename changes generated IDs.
    sample = project / 'audio/click.wav'
    data = bytearray(sample.read_bytes())
    data[-1] ^= 0x40
    sample.write_bytes(data)
    previous = (project / 'out/calliope/bank.bin').read_bytes()
    build('CALLIOPE_HYBRID=1', 'CALLIOPE_REGION=pal')
    assert previous != (project / 'out/calliope/bank.bin').read_bytes(), 'Asset edit was missed'
    manifest = project / 'audio/manifest.json'
    document = json.loads(manifest.read_text())
    document['songs'][0]['name'] = 'renamed'
    manifest.write_text(json.dumps(document))
    source.write_text(source.read_text().replace('CLP_SONGS_MELODY', 'CLP_SONGS_RENAMED'))
    build('CALLIOPE_HYBRID=1', 'CALLIOPE_REGION=pal')
    assert 'CLP_SONGS_RENAMED' in (project / 'out/calliope/clp_ids.h').read_text()
    print('Passed consumer mode/region matrix, incremental builds, asset/ID edits and ordinary resources:', home)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sgdk', type=Path)
    parser.add_argument('--home', type=Path, default=ROOT)
    parser.add_argument('--vendored', action='store_true')
    a = parser.parse_args()
    sdk = sgdk_path(a.sgdk)
    output = ROOT / 'out/integration-validation'
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='consumer-', dir=output) as temporary:
        destination = Path(temporary)
        home = a.home.resolve()
        if a.vendored:
            target = destination / 'vendor/Calliope'
            # Copy only inputs required for the library and minimal example.
            for directory in ('tools', 'driver', 'include', 'src', 'make', 'examples/minimal'):
                shutil.copytree(home / directory, target / directory,
                                ignore=shutil.ignore_patterns('out', '__pycache__', '*.orig', 'boot'))
            home = target
        try:
            verify(home, sdk, destination)
        except Exception:
            shutil.copyfile(destination / 'build.log', output / 'failure.log')
            raise


if __name__ == '__main__':
    main()
