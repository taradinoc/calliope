# SGDK integration

Calliope supports Windows and native Linux SGDK builds. Install Python 3.10+, SGDK and the Java
runtime required by SGDK. The default Calliope workflow does not require a JDK.
Set `GDK` to your SDK directory. No SDK files are modified.

On Linux (including WSL), install GNU Make, a native `m68k-elf-gcc` toolchain
with binutils and libgcc, SGDK's `sjasm`, and Java on `PATH`. Use SGDK libraries
built with a compatible compiler. SGDK's supplied `.exe` programs are Windows
binaries; pointing `--sgdk` at that directory does not install Linux tools.
See [SGDK's Linux setup options](https://github.com/Stephane-D/SGDK#help-and-support).
Ordinary SGDK resources may also need native `bintos`, `mac68k`, and `convsym`,
depending on the project. The direct-bank workflow uses Java JARs from SGDK's
`bin` directory without a JDK.

Once the native tools are installed, the same helper builds the karaoke example:

```sh
export GDK=/path/to/sgdk
export PATH=/path/to/m68k-toolchain/bin:/path/to/sgdk-native-tools:$PATH
python3 examples/karaoke/build.py --sgdk "$GDK"
```

All paths remain Linux paths, including `/mnt/d/sgdk` under WSL. The build uses
Linux Python, Make, GCC and sjasm. If your compiler uses another target prefix,
set `PREFIX` (for example `export PREFIX=m68k-elf-`); both configuration and
SGDK Make use it. The compiler must support SGDK's flags and libraries.
Use a checkout/SDK path without spaces, as required by SGDK's Make rules.
When switching between Windows and Linux tools in one checkout, remove the
project's generated `out` directory first so Make cannot reuse incompatible
objects or dependency files.

## New project

Start from `examples/minimal`, or create `src/main.c` and a Makefile. Keep a
Calliope checkout beside the game, or vendor it under `vendor/calliope`.
Use forward slashes in Make paths:

```make
# GDK comes from your environment or make command line.
CALLIOPE_HOME := ../Calliope
CALLIOPE_BANK := audio/manifest.json
CALLIOPE_REGION := ntsc
CALLIOPE_HYBRID := 0
include $(GDK)/makefile.gen
include $(CALLIOPE_HOME)/make/calliope.mk
```

Paths in the manifest are relative to the manifest, not the game Makefile.
Copy the manifest and original fixture assets from `examples/minimal/audio`.
Build from the game directory with `make release`, using SGDK's `bin/make.exe`
on Windows or native GNU Make on Linux.
Set `CALLIOPE_PYTHON` if `python` is not the intended interpreter. The helper
infers its library root when `CALLIOPE_HOME` is omitted.

The helper creates `audio.h`, `clp_ids.h`, bank data/assembly, a selected Z80
image, adapter/bus/DMA wrappers and configuration under `out/calliope`. Add
that directory to your ignore rules. Do not compile manual copies of those
sources. Include `calliope.h`, `audio.h` and `clp_ids.h` from your game code.

```c
if (!clp_init(clp_bank, sizeof(clp_bank))) {
    /* Show an error; do not issue playback requests after failed init. */
}
/* Use the song name generated from your manifest. Check queue acceptance. */
if (!clp_start(CLP_SONGS_MELODY)) {
    /* Report or retry the request on a later frame. */
}
```

Call `clp_update()` once per video frame before VBlank processing. It services
hybrid playback and is a no-op in pure Z80 mode. Poll `ClpEvent` batches with
`clp_poll()` and inspect `clp_status()` for faults and lost events. The minimal
example demonstrates initialization failure, request rejection, stop/replay,
music, SFX and event polling. The karaoke example demonstrates timed cues.

The helper converts directly through Python, generates IDs before compilation,
tracks converter/assets/configuration changes, links the bank uncompressed at
32768-byte alignment, then checks linked bank bytes, mode, region and ROM bounds
and finalizes the cartridge checksum. No `.res` directive is needed for this bank.
Ordinary SGDK resources and unrelated rescomp extensions continue to work.

## Existing project and migration

**The direct-bank workflow is opt-in through `CALLIOPE_BANK`.** Existing
configuration-only users and the `CALLIOPE` rescomp extension remain supported.
Public playback APIs and the BGM1 revision are unchanged by this integration
update. The helper now requires a configured `GDK`; it no longer assumes a
particular SDK installation directory.

To migrate:

1. Set `CALLIOPE_BANK` and include the helper after SGDK as shown above.
2. Remove the bank's old `CALLIOPE` or `BIN` resource directive. Keep unrelated
   resources. Remove a staged extension JAR only if nothing else needs it.
3. Remove manual adapter, hybrid bus, DMA and driver-image wrappers from the
   build. The helper supplies all four by default when a bank is configured.
4. Replace old generated resource/ID include paths with `audio.h` and
   `clp_ids.h`. Keep existing calls to the public playback API.
5. Clean once to remove dependency files referring to old generated headers.
   Subsequent mode, region and asset changes do not require a clean.

Calliope owns the sound chips and Z80. Remove competing SGDK sound-driver
initialization and playback calls. The helper compiles the configured SDK's
`dma.c` with `HALT_Z80_ON_DMA=0`; `clp_init()` registers Calliope's DMA protection
handshake. Remove another project-local DMA override to prevent duplicate
symbols. Verify this integration when updating SGDK.

## Build settings

| Setting | Default / purpose |
|---|---|
| `CALLIOPE_BANK` | Opt in with one manifest path |
| `CALLIOPE_BANK_SYMBOL` | `clp_bank`; customize the linked C symbol |
| `CALLIOPE_BUILD_DIR` | `out/calliope`; project-relative generated directory |
| `CALLIOPE_REGION` | `ntsc`, or `pal` |
| `CALLIOPE_HYBRID` | Public header default `0`; `1` enables 68000 sequencing |
| `CALLIOPE_HOST_PCM` | `0`; optional hybrid comparison path |
| `CALLIOPE_PYTHON` | `python`, or an executable path |
| `CALLIOPE_CONFIG_HEADER` | Public configuration header; optional project header |

Use `make release CALLIOPE_HYBRID=1 CALLIOPE_REGION=pal` to override defaults.
A project header must use guarded `#ifndef` defaults so command-line values
can override them. The selected mode determines PCM conversion rate and driver
image together. Pure Z80 uses timer 1016; hybrid uses timer 1020. Rebuild the
converter, banks and adapter together when updating Calliope.

Keep generated directories relative and without spaces. GNU Make/SGDK has
unquoted paths in its own recipes and include handling; paths with spaces in
Python's executable are supported, but library/SDK paths with spaces are not
a supported SGDK configuration. See maintainer validation for relocation checks.
Do not run concurrent full builds sharing one output directory.

## Existing rescomp route

Without `CALLIOPE_BANK`, the helper retains its configuration-only behavior:
compile your adapter/bus sources, arrange DMA protection, and generate IDs before
C compilation. `CALLIOPE_SOURCE_WRAPPERS=1` generates adapter/bus/DMA wrappers;
otherwise its default remains 0. `CALLIOPE_DRIVER_ASM` defaults to
`src/calliope_driver.s` in this compatibility mode.

Build `rescomp/CalliopeProcessor.java` against the configured SGDK rescomp JAR
with a JDK and stage the resulting `rescomp_ext.jar` beside the resource file.
Keep unrelated extensions; do not overwrite them. Use:

```text
CALLIOPE clp_bank "../audio/manifest.json"
```

Set `CALLIOPE_HOME`, `CALLIOPE_PYTHON`, `CALLIOPE_REGION` and
`CALLIOPE_IDS_HEADER` (the generated ID header path) for rescomp. The helper
exports the mode-derived `CALLIOPE_PCM_TIMER`.

```text
python tools/clpconvert.py ids audio/manifest.json --output generated/clp_ids.h
python tools/clpconvert.py compile audio/manifest.json --output out/bank.bin --region ntsc --mode z80
```

Direct CLI output also includes a JSON report, dependencies and IDs. Never use
an output basename that collides with the manifest or an input asset. Other
build systems can run `tools/configure.py --sgdk PATH --output-dir OUTPUT
--driver-asm OUTPUT/driver.s`, force-include `calliope_build.h`, compile the
adapter/bus/DMA sources and convert using the resolved mode. They must perform
the same placement and cartridge checks as the Make helper.

See [authoring](authoring.md), [arrangements and API](arrangements.md),
[architecture](hybrid.md) and [current limits](status.md) for playback behavior.


`u32 clp_getElapsed(void)` returns music ticks since the most recently consumed
song start. It returns zero before initialization. Pause freezes the count;
loops continue counting; stop/end retain the last position until another start.
The unsigned count wraps after 2^32 ticks. Start and stop are queued, so a query
immediately after submitting a request can still describe the previous song.
`clp_start(song)` assigns and returns a nonzero generation for each accepted
request; zero means failure. Failed requests do not advance the counter.
The counter resets on initialization and wraps from 65535 to 1. Compare the
returned value with `clp_status().generation` to identify a consumed start, or
with `completedGeneration` to recognize its completion. `clp_poll()` filters
notifications against the latest accepted start automatically.

Ticks match `ClpEvent.tick`, not video frames or milliseconds. At a constant
effective 60 Hz, divide by 60 for seconds; authored rate changes and runtime
tempo scaling require integrating the effective tempo map. Hybrid mode reads
the last consumed transport page header, rather
than the host's future preparation position. An in-progress tick/header publication
returns the last valid query result. Both modes describe driver progress; queued
PCM and chip writes can still be ahead of audible output. See
[runtime-controls.md](runtime-controls.md) for tempo semantics and proposed telemetry APIs.


`clp_setTempoScale(scale, boundary, marker)` changes the music clock relative
to authored tempo (256 = normal, 128 = half, 512 = double):

```c
clp_setTempoScale(384, CLP_NEXT_BAR, 0);                 /* 1.5x next bar */
clp_setTempoScale(CLP_TEMPO_NORMAL, CLP_NEXT_MARKER, 2); /* normal at marker 2 */
```

Tempo requests are independent of pending part/arrangement changes. The latest
tempo request wins; immediate requests apply even while paused. Stop cancels a
pending request, and each consumed song start resets the scale to normal.
SFX timing and PCM playback pitch/duration are unchanged. Effective music rate
rounds to 0.2 Hz. Hybrid changes affect future prepared audio, with its existing
lookahead delay. See [runtime controls](runtime-controls.md) for the full contract.
