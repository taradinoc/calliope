# Maintainer validation

Install Python dependencies from `editor/requirements.txt` and
`requirements-dev.txt`. Set `GDK` to SGDK. Generated evidence belongs in `out/`.

```text
python -B -m unittest discover -s tests -v
python tools/adapter_test.py
python tools/adapter_test.py --hybrid
python tools/adapter_test.py --hybrid --host-pcm
python tools/build.py
python tools/green_hill_smoke.py --frames 18000 --stress --dma
python tools/music_noise_smoke.py
python tools/pcm_owner_smoke.py
python tools/editor_preview_smoke.py
```

Optional emulator tests use `--core PATH`, or a core name under
`CALLIOPE_CORES_DIR`. Fixed-core scripts also accept `--core`. Trace-based tests
use `out/trace-core/build/calliope_trace.dll`. With CMake 3.24+ and MSVC installed, build that optional core with
`python tools/build_trace_core.py --source PATH`; the tool reports unsupported
source layouts rather than modifying the input checkout. Use the Genesis Plus GX revision pinned in `editor/assets/core.json`; the
source may be the same checkout used to build the editor core. It is not needed
for ordinary game builds.

Set `CALLIOPE_FURNACE` or pass `--furnace PATH` to the optional PSG oracle.
MSVC tools discover the C++ installation through vswhere; override with
`--vcvars PATH` or `CALLIOPE_VCVARS`. Optional tests skip when dependencies are
absent; distinguish skips from verified results.

Build all NTSC/PAL and pure/hybrid combinations. Follow the integration guide
from a clean source export and test separate/vendored library paths, unchanged
rebuilds, mode/region changes without cleaning, manifest/asset edits and ordinary
SGDK resources. SDK/library paths containing spaces are subject to SGDK Make
limitations; Python executable paths containing spaces must work.

For the Windows editor, run `python tools/package_editor.py`, which performs
packaged bank-byte parity and audio regressions. Use the packaged smoke and GUI
checks outside the checkout with development environment variables removed.
Record actual results and skipped checks in the release validation report.
