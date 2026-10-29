"""Real Make 3.81 scheduling tests for observed dependencies and output recovery."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
MAKE = Path(os.environ.get('GDK', 'D:/sgdk'))/'bin/make.exe'


@unittest.skipUnless(MAKE.exists(), 'SGDK Make required')
class MakeStep(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='make step ')
        self.root = Path(self.temporary.name)
        (self.root/'source').mkdir()
        (self.root/'source/a.txt').write_text('hello')
        (self.root/'produce.py').write_text('''from pathlib import Path
import sys
values = [p.read_text() for p in sorted(Path('source').glob('*.txt'))]
if 'FAIL' in values:raise ValueError('controlled failure')
for name in ('one.txt', 'two.txt'):
    path = Path(name)
    sys.audit('build.output', str(path))
    data = ''.join(values).encode()
    if not path.exists() or path.read_bytes() != data:path.write_bytes(data)
count = Path('count.txt')
count.write_text(str(int(count.read_text())+1 if count.exists() else 1))
''')
        runner = (ROOT/'tools/make_step.py').as_posix()
        make = '''.DEFAULT_GOAL := all
.PHONY: all
all: one.txt two.txt
step.stamp: produce.py
\t"PYTHON" "RUNNER" --root . --stamp step.stamp --depfile step.d produce.py
step.d: step.stamp ;
-include step.d
one.txt two.txt: | step.stamp
'''.replace('PYTHON', sys.executable).replace('RUNNER', runner)
        (self.root/'Makefile').write_text(make)

    def tearDown(self):self.temporary.cleanup()

    def make(self, success=True):
        result = subprocess.run([str(MAKE), '-j4'], cwd=self.root, capture_output=True, text=True, timeout=30)
        if success:self.assertEqual(result.returncode, 0, result.stdout+result.stderr)
        else:self.assertNotEqual(result.returncode, 0)
        time.sleep(1.1)  # GNU Make 3.81 compares whole-second timestamps.
        return result

    def count(self):return int((self.root/'count.txt').read_text())

    def test_first_idle_missing_and_parallel(self):
        self.make();self.assertEqual(self.count(), 1)
        before = (self.root/'one.txt').stat().st_mtime_ns
        idle = self.make();self.assertEqual(self.count(), 1)
        self.assertNotIn('make_step.py', idle.stdout)
        (self.root/'two.txt').unlink()
        self.make();self.assertEqual(self.count(), 2)
        self.assertEqual((self.root/'one.txt').stat().st_mtime_ns, before)
        self.make();self.assertEqual(self.count(), 2)

    def test_changed_and_identical_inputs_and_membership(self):
        self.make()
        source = self.root/'source/a.txt'
        source.write_text('changed')
        self.make();self.assertEqual((self.root/'one.txt').read_text(), 'changed')
        before = (self.root/'one.txt').stat().st_mtime_ns
        source.touch()
        self.make();self.assertEqual(self.count(), 3)
        self.assertEqual((self.root/'one.txt').stat().st_mtime_ns, before)
        self.make();self.assertEqual(self.count(), 3)
        (self.root/'source/b.txt').write_text('new')
        self.make();self.assertEqual((self.root/'one.txt').read_text(), 'changednew')
        source.unlink()
        self.make();self.assertEqual((self.root/'one.txt').read_text(), 'new')

    def test_failure_invalidates_success(self):
        self.make()
        (self.root/'source/a.txt').write_text('FAIL')
        self.make(False)
        self.assertFalse((self.root/'step.stamp').exists())
        (self.root/'source/a.txt').write_text('recovered')
        self.make();self.assertEqual((self.root/'one.txt').read_text(), 'recovered')


if __name__ == '__main__':unittest.main()
