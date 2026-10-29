"""Run a Make recipe and publish its observed file dependencies after success.

Observation happens during real generation, never as a separate discovery run.
The depfile is also the output inventory. Make, not Python, decides freshness.
"""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import runpy
import shutil
import sys
import tempfile


def escape(path):
    return str(path).replace('\\', '/').replace('$', '$$').replace('#', '\\#').replace(' ', '\\ ')


def publish(path, data):
    """Never leave a half-written included Makefile after interruption."""
    descriptor, temporary = tempfile.mkstemp(prefix=path.name+'.', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'wb') as stream:stream.write(data)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def execute(script, arguments, stamp, depfile, roots, inputs=(), outputs=()):
    stamp, depfile = Path(stamp), Path(depfile)
    stamp.parent.mkdir(parents=True, exist_ok=True)
    stamp.unlink(missing_ok=True)
    inventory_path = Path(str(depfile) + '.json')
    previous = json.loads(inventory_path.read_text()) if inventory_path.exists() else []
    roots = [Path(p).resolve() for p in roots]
    reads, writes, directories, originals = set(), set(), set(), {}
    active = [True]

    def owned(path):
        return any(path.is_relative_to(root) for root in roots)

    def remember(path):
        if path not in originals:
            originals[path] = (path.read_bytes(), path.stat()) if path.is_file() else None
        writes.add(path)

    def audit(event, args):
        if not active[0]:
            return
        active[0] = False
        try:
            if event == 'build.input':
                reads.add(Path(args[0]).resolve())
            elif event == 'subprocess.Popen':
                executable = shutil.which(str(args[0]))
                if executable:reads.add(Path(executable).resolve())
                for argument in args[1] if isinstance(args[1], (list, tuple)) else []:
                    path = Path(str(argument))
                    if path.suffix in ('.py', '.jar') and path.is_file():reads.add(path.resolve())
            elif event == 'build.output':
                path = Path(args[0]).resolve()
                if owned(path):remember(path)
            elif event == 'open' and isinstance(args[0], (str, bytes, os.PathLike)):
                path = Path(os.fsdecode(args[0])).resolve()
                if owned(path):
                    flags = args[2]
                    if flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND):
                        remember(path)
                    else:
                        if path.suffix == '.pyc':
                            try:path = Path(importlib.util.source_from_cache(str(path)))
                            except ValueError:return
                        reads.add(path)
            elif event == 'os.scandir' and isinstance(args[0], (str, bytes, os.PathLike)):
                path = Path(os.fsdecode(args[0])).resolve()
                if owned(path):directories.add(path)
            elif event == 'os.rename':
                path = Path(args[1]).resolve()
                if owned(path):remember(path)
        finally:
            active[0] = True

    sys.addaudithook(audit)
    try:
        sys.argv = [str(script), *arguments]
        sys.path.insert(0, str(Path(script).resolve().parent))
        try:runpy.run_path(str(script), run_name='__main__')
        except SystemExit as error:
            if error.code not in (None, 0):raise
    finally:
        active[0] = False
    reads.update(Path(p).resolve() for p in inputs)
    reads.update((Path(script).resolve(), Path(__file__).resolve(), Path(sys.executable).resolve()))
    for module in list(sys.modules.values()):
        name = getattr(module, '__file__', None)
        if name:
            path = Path(name).resolve()
            third_party = any(part.lower() in ('site-packages', 'dist-packages') for part in path.parts)
            if (owned(path) or third_party) and path.suffix in ('.py', '.pyd', '.so'):reads.add(path)
    writes.update(Path(p).resolve() for p in previous if Path(p).is_file())
    writes.update(Path(p).resolve() for p in outputs)
    missing = [p for p in writes if p in {Path(v).resolve() for v in outputs} and not p.is_file()]
    if missing:raise FileNotFoundError('Recipe did not produce: ' + str(missing))
    writes = {p for p in writes if p.is_file() and '__pycache__' not in p.parts}
    for path, original in originals.items():
        if original and path.is_file() and path.read_bytes() == original[0]:
            os.utime(path, ns=(original[1].st_atime_ns, original[1].st_mtime_ns))
    # Generated directories and read-modify-write outputs are not prerequisites.
    directories = {p for p in directories if p.is_dir() and p not in roots and not any(p.is_relative_to(root/'out') for root in roots)}
    dependencies = {p for p in reads - writes if p.is_file() and '__pycache__' not in p.parts} | directories
    def relative(path):
        try:return escape(path.relative_to(Path.cwd()))
        except ValueError:return escape(path)
    target, deps = escape(stamp), ' '.join(relative(p) for p in sorted(dependencies))
    inventory = ' '.join(relative(p) for p in sorted(writes))
    text = '# Generated by make_step.py; do not edit.\n'
    text += target + ': ' + deps + '\n'
    text += 'ifneq ($(words $(wildcard ' + inventory + ')),' + str(len(writes)) + ')\n'
    text += '.PHONY: ' + target + '-missing\n' + target + ': ' + target + '-missing\nendif\n'
    for path in sorted(writes):text += relative(path) + ': | ' + target + '\n'
    # Missing old inputs must allow a catalog change to run the producer.
    for path in sorted(dependencies):text += relative(path) + ':\n'
    depfile.parent.mkdir(parents=True, exist_ok=True)
    publish(depfile, text.replace('\n', '\r\n').encode())
    publish(inventory_path, (json.dumps([str(p) for p in sorted(writes)])+'\r\n').encode())
    stamp.touch()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stamp', required=True)
    parser.add_argument('--depfile', required=True)
    parser.add_argument('--root', action='append', default=[])
    parser.add_argument('--input', action='append', default=[])
    parser.add_argument('--output', action='append', default=[])
    parser.add_argument('script')
    parser.add_argument('arguments', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    execute(args.script, args.arguments, args.stamp, args.depfile, args.root or [Path.cwd()], args.input, args.output)


if __name__ == '__main__':main()
