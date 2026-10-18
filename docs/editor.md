# Calliope Bank Editor

The editor opens and saves version-1 JSON bank manifests. Furnace and WAV files
are read-only inputs. It uses the same converter as `clpconvert.py`; successful
parsing alone does not establish compatibility.

## Run from source

Use Python 3.10 or newer:

```text
python -m pip install -r editor/requirements.txt
python tools/calliope_editor.py examples/bank.json
```

An optional project-local dependency installation at `out/editor-deps` is also
recognized by the launcher. A standalone release includes Python, Qt, the
converter, player templates, and the native preview core; it needs no SGDK,
Furnace, emulator installation, or network connection at runtime.

Select a bank entry in the browser. Properties contains GUI forms:

- General: name, ID, source file picker, subsong selector, gain, and SFX priority.
- Channels: voice names, Furnace channel selectors, and Genesis destinations.
- Groups & arrangements: named groups/arrangements with voice membership checkboxes.
- Markers & events: numeric source positions and game event mappings.
- Playback: optional tempo/loop overrides, LFO, marker lookahead, and stealing order.
- SFX playback: loop boundaries, eligible channel checkboxes, and PSG relocation.

Controls use audio terminology rather than JSON field names. Optional overrides
can be disabled to restore source/bank defaults. Bank-level settings appear when
the bank root is selected. Unknown fields are preserved. The Bank JSON tab remains
available for advanced edits; Apply JSON commits it to the same undo history.

Select Music or Sound effects before importing a Furnace file. WAV imports are
always SFX. Import proposes mappings for review; it does not modify Furnace
channels or assign meaning to EE commands. Use the Read-only source tab to
inspect metadata and pattern cells, relink a missing file, or review newly
suggested mappings. Source channel, order, pattern, and row indices are zero based.

Save As rebases relative resource paths. Unknown JSON fields and meaningful
object order are retained, including arrangement/group order used by generated
headers. Undo/redo preserves these fields. JSON whitespace is normalized on save.

Empty optional arrangements, groups, markers, and event mappings are omitted.
Deleting the last arrangement also removes its starting-arrangement reference.
Existing banks with these empty sections are cleaned up in memory when opened;
save the bank to persist the repair. Named arrangements or groups with no members
are retained, since an intentionally silent arrangement is meaningful.

## Validate and compile

Choose region and driver mode, then press F6 to validate or F7 to export.
Long operations show a persistent progress indicator and current resource name;
Cancel stops preparation without changing the source bank. Preview/export perform
one conversion pass and cache successful banks by input content and profile.
The first conversion of a large bank can still take time; subsequent starts reuse
the cached bank until inputs, compiler implementation, or profile change.
Export writes a binary bank, JSON report, dependency list, and C IDs header.
Output paths cannot overwrite the open manifest or source resources. Each output
is replaced atomically; the four outputs are not one filesystem transaction.

Explicit validation collects independent resource failures and checks the complete bank
only when resource checks succeed. Diagnostics include source locations where
the underlying converter provides them. Unsupported used semantics remain errors.
Unsaved documents compile relative to their current base directory (the launch
directory until their first save). Source changes invalidate preview revisions.

The same services are available without Qt:

```text
python tools/clpconvert.py inspect examples/fm_ring.fur
python tools/clpconvert.py validate examples/bank.json --region pal --mode hybrid --json
python tools/clpconvert.py compile examples/bank.json --output out/editor-bank.bin --mode hybrid
```

## Audition

Select a song and Play song. Choose an SFX and Trigger, or press Space outside a
text editing control, to layer effects over music. Each trigger gets a token;
Cancel token stops a particular effect. Priority overrides are preview controls,
not manifest edits. Music can be paused/resumed or stopped independently.

Arrangement switching supports immediate, bar, or marker boundaries. The mixer exposes FM/PSG volume, music PCM priority, physical/group
mute masks, logical part selection, and tempo (50..200 percent).
Tempo applies at the selected immediate/bar/marker boundary independently of parts. These call the real public adapter API.

Channel telemetry shows real ownership and active tokens. An adapter response
means queued, not successful allocation. Allocation messages confirm observed
tokens and replacements; very short effects that finish between observations
are reported as unobserved rather than incorrectly labeled rejected. Hybrid
ownership is sampled every six player frames; its sequencer can run ahead of
audible output by the normal driver lookahead.

The preview shows live **68000 load**, averaged over six emulated frames. This
is the percentage of the PAL/NTSC frame cycle budget spent executing outside
the editor player's VBlank wait. It includes adapter commands, `clp_update`,
event polling, and telemetry. It excludes the VBlank wait and interrupts during
that wait, and is not the host computer's CPU usage. Pure Z80 mode shows only
its small 68000 adapter/player cost; hybrid mode also includes sequencing and
PCM production. Z80 load is omitted because its continuous polling would make
an execution percentage misleading. Stopping preview clears the reading;
older player/core assets without profiling support show `unavailable`.

Play or Trigger recompiles a changed bank before previewing it. When an SFX
trigger causes a rebuild, the last requested music song restarts first. Changing
region or mode closes preview. Existing playback can continue at its displayed
revision while editing or validating. Compilation failure never starts an older
bank in place of the requested revision.

The player uses the same PCM conversion as export: approximately 6.66 kHz in
NTSC pure Z80 mode and 13.32 kHz in hybrid mode, with regional PAL rates. Device
resampling affects only emulator output, never driver clocks. Output buffering
is bounded; audio-device errors appear in the status bar. Current driver jitter,
backlogs, and PCM behavior are part of the preview. Hardware validation remains
separate; preview does not simulate an arbitrary game's DMA workload.

## Build native releases

Player templates are architecture independent. Regenerate them after adapter,
driver, or mailbox changes on a development machine with SGDK and its Java runtime:

```text
python tools/build_editor_player.py --sgdk PATH
```

The existing SGDK build helper currently uses Windows executable names. This is
a release-preparation dependency, not a requirement on editor users; copy the
four generated templates and metadata into each native packaging checkout.
Banks are inserted at ROM address `0x100000`, aligned to 32 KiB, with a 4 MiB ROM
limit. Metadata pins template SHA-256, mailbox protocol 1, BGM1 revision 12,
region, PCM timer, mailbox location, and length-patch location. The player host
detects the core's work-RAM byte order from the mailbox magic and never edits
serialized emulator states.

Build the native emulator core from the pinned clean checkout:

```text
git clone https://github.com/libretro/Genesis-Plus-GX.git out/editor-core-source
git -C out/editor-core-source checkout 393f70cd64c7334564c19a424c383f53986c13f4
python tools/build_editor_core.py
```

On Windows run from a Visual Studio developer command prompt and, if needed,
pass `--generator "NMake Makefiles"`. On macOS build once with each native Python/
compiler architecture (arm64 and x86_64); on Linux build with an x86_64 compiler.
The build writes the native library, hash metadata, licenses, and complete core
source archive to `editor/assets`. Use separate checkouts/artifact directories
for different native release architectures.

Install the pinned Nuitka package required by the selected PySide release, then:

```text
python -m pip install Nuitka==4.1.1 ordered-set zstandard
python tools/package_editor.py
```

Packaging uses `pyside6-deploy` in standalone directory/application-bundle mode,
with output under `out/editor-release`. Distribute the complete directory or
`.app`, including assets and shared libraries. Windows x64 is verified. Linux and macOS packaging remain unverified. Signing and notarization need
the distributor's credentials and are not performed automatically.

The bank converter modules ship as ordinary Python source loaded by the bundled
CPython runtime. Native translation in the initial Windows release changed FM
command streams, causing silent pitched audio. Packaging now requires exact bank
byte/header parity for both regions and driver modes, plus pitched-audio playback
with PCM muted. The GUI/runtime wrapper remains compiled. To build alongside an
open existing release, pass `--name "Calliope Bank Editor 0.2"` to the packaging tool.

Run `python -m unittest discover -s tests` and
`python tools/editor_preview_smoke.py`. The preview suite starts a separate
process per region/mode to avoid unsafe emulator reinitialization. On each
native host also exercise the packaged GUI, a real output device, source edits,
repeated mode switches, missing devices, and worker termination. A build on one
OS does not establish support on the other native targets.

See `editor/THIRD_PARTY.md` and bundled component notices before redistribution.
