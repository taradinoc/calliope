"""Build a real generic SGDK consumer, including same-build resource headers."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import unittest
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/"tools"))
from sgdk_make import adapt

ROOT = Path(__file__).resolve().parents[1]
SDK = Path(os.environ.get('GDK', 'D:/sgdk'))


@unittest.skipUnless((SDK/'bin/make.exe').exists(), 'Windows SGDK required')
class SgdkIncremental(unittest.TestCase):
    def test_resources_configuration_and_idle_cartridge(self):
        self.exercise('OUT')

    def test_out_dir_layout(self):
        self.exercise('OUT_DIR')

    def exercise(self, output_variable):
        with tempfile.TemporaryDirectory(prefix='sgdk consumer ') as directory:
            root = Path(directory)
            shutil.copytree(ROOT/'examples/minimal/audio', root/'audio')
            (root/'src').mkdir();(root/'res').mkdir()
            source = (ROOT/'examples/minimal/src/main.c').read_text()
            (root/'src/main.c').write_text('#include "res/data.h"\n#include "expected.h"\n_Static_assert(sizeof(fixture_data)==EXPECTED_BYTES,"stale resource header");\n'+source)
            (root/'expected.h').write_text('#define EXPECTED_BYTES 32\n')
            (root/'res/data.res').write_text('BIN fixture_data "data.bin" 2\n')
            (root/'res/data.bin').write_bytes(bytes(range(32)))
            integration = '$(CALLIOPE_HOME)/make/sgdk.mk'
            if output_variable == 'OUT_DIR':
                sdk = (SDK/'makefile.gen').read_text().replace('OUT := out', 'OUT_DIR := out/release').replace('$(OUT)', '$(OUT_DIR)')
                (root/'sdk.mk').write_text(adapt(sdk))
                # Model the matching boot include used by an OUT_DIR SDK.
                (root/'src/boot').mkdir()
                boot=(SDK/'src/boot/sega.s').read_text().replace('out/rom_head.bin','out/release/rom_head.bin')
                (root/'src/boot/sega.s').write_text(boot)
                integration = 'sdk.mk'
            (root/'Makefile').write_text('GDK := '+SDK.as_posix()+'\nCALLIOPE_HOME := '+ROOT.as_posix()+'\nCALLIOPE_BANK := audio/manifest.json\ninclude '+integration+'\ninclude $(CALLIOPE_HOME)/make/calliope.mk\n')
            def build():
                result = subprocess.run([str(SDK/'bin/make.exe'), 'release', '-j4'], cwd=root, capture_output=True, text=True, timeout=120)
                self.assertEqual(result.returncode, 0, result.stdout+result.stderr)
                return result.stdout+result.stderr
            build()
            self.assertTrue((root/'out/rom.bin').exists())
            self.assertTrue((root/'out/symbol.txt').exists())
            before = {p: p.stat().st_mtime_ns for p in root.rglob('*') if p.is_file()}
            idle = build()
            self.assertNotIn('python', idle.lower())
            self.assertEqual(before, {p: p.stat().st_mtime_ns for p in before})
            time.sleep(1.1)
            (root/'res/data.bin').write_bytes(bytes(range(64)))
            (root/'expected.h').write_text('#define EXPECTED_BYTES 64\n')
            build()  # Fails at compile time if rescomp's old header was cached.
            resource_dep = 'out/release/res/data.d' if output_variable == 'OUT_DIR' else 'out/res/data.d'
            for name in ('res/data.h', resource_dep):
                (root/name).unlink();build()
                self.assertTrue((root/name).exists())
            time.sleep(1.1);(root/'src/main.c').touch()
            build()  # Resource assembly/depfiles must survive unrelated C builds.
            self.assertNotIn('python', build().lower())
            # Removing a source must remove its object from the retained response.
            extra = root/'src/extra.c';extra.write_text('int unused_probe(void) { return 7; }\n')
            build()
            response = root/('out/release/cmd_' if output_variable == 'OUT_DIR' else 'out/cmd_')
            self.assertIn('extra.o', response.read_text())
            extra.unlink();build()
            self.assertNotIn('extra.o', response.read_text())
            self.assertNotIn('python', build().lower())


if __name__ == '__main__':unittest.main()
