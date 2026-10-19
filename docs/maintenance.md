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
python tools/order_transition_smoke.py --baseline PATH_TO_PREVIOUS_ROM
python tools/instrument_burst_smoke.py
python tools/instrument_burst_smoke.py --gain 127
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

The order-transition check measures YM key events at Green Hill's first order
boundary and saves a JSON report and WAV captures under `out/order-transition-ntsc`
(or `-pal`). Use it alongside decoder-backlog tests: a stalled logical tick does
not by itself measure an audible gap. Instrument changes must not key off active
voices several frames before their replacement notes. The instrument-burst check
changes five distinct instruments on five already-playing voices at three order
boundaries, verifies every instrument register at key-on, and bounds both silence
and onset spread. Run both checks for NTSC/PAL with matching audition ROMs.

The local SGDK XGM pipeline provides the scheduling comparison: `vgm.c` computes
per-frame YM state deltas while retaining key transitions; `xgm.c` groups port
writes; `xgc.c` prepares frame blocks and PCM lead-in. Oversized XGC frames use
continuation blocks within the same musical frame. `drv_xgm.s80` prefetches those
blocks and uses tight register loops with PCM service interleaved. Hybrid Calliope
instead moves decoding and command preparation to the 68000. These paths reduce
Z80 preparation and delivery overhead; they do not depend on previously unused FM
channels. Calliope keeps its semantic patterns and now queues compact patch/note
commands, with grouped physical instrument writes at delivery.

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
