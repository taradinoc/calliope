# Current capabilities and limitations

Calliope supports BGM1 revision 12 banks with matching converter, adapter and
Z80 image. Shared channel patterns, sixteen logical voices, arrangement/group
controls, FM/PSG effects, PCM preemption and external-command events are
implemented. See authoring and API documentation for supported effects and limits.

## Playback acceptance

- PCM is one exclusive source, not a mixer. Accepted SFX replace music PCM;
  suppressed drum triggers and interrupted samples do not resume.
- Strict DAC drift/jitter acceptance remains open. Zero underruns alone does
  not establish sample-accurate scheduling or hitch-free playback.
- Pure Z80 PAL full-song DMA/SFX stress previously reached 21325 ticks against
  a 21420 minimum over 18000 video frames. The gate remains an acceptance failure.
- The compact patch/note delivery change passes the pure Z80 NTSC 6000-frame
  hitch workload: 129 stalled frames (previous image: 175), 48 stalls with work
  pending (previous: 97), maximum pending/stall run 3, and no faults or underruns.
  Green Hill's first lead-note interval measures 112 ms versus 143 ms before;
  changing voices' patch key-off gaps fall from 53–74 ms to about 3 ms.
- Five already-playing voices changing to five distinct instruments pass the
  burst checks in NTSC/PAL, including all instrument registers and runtime gain.
  Writes remain serial: measured entrance spread reaches about 23 ms. The
  6000-frame DMA/SFX stress and bundled pure-player controls pass in both regions.
  These checks do not close strict jitter or full-song PAL throughput acceptance.
- The October 18 hybrid optimization measured 12.07% / 11.94% of a 68000 frame
  for normal NTSC/PAL audio service, including per-frame status polling. The
  3000-frame DMA/SFX stress profiles measured 12.90% / 12.63%, with zero faults
  or underruns. The fresh NTSC baseline was 15.27%; the 9% target remains unmet.
  Control latency, strict DAC jitter and fresh-patch onset alignment remain open.
- Physical hardware acceptance is outstanding. Linux/macOS source builds and
  standalone editor packaging have not been natively verified.

These are workload-specific historical measurements, not guarantees for a game.
Run the [validation commands](maintenance.md) against the integrated consumer.
The release-preparation changes do not claim to close runtime timing gates.

## Compatibility

Banks, converter and driver must be updated together. The current boundary API
uses immediate, next-bar and explicit-marker requests. Older bank revisions and
stale effect-index data are rejected. Runtime macros and arbitrary phrase calls
are not implemented; supported modulation is compiled into timed updates.

The Make integration now offers direct Python bank preparation, enabled with
`CALLIOPE_BANK`; the configuration-only/rescomp route remains available. See
[integration and migration](integration.md) before changing an existing project.

## SFX token completion query

`clp_sfxActive(token)` checks sequencer and PCM ownership without stopping the
sound. Pending commands conservatively return busy, so a queued start cannot
appear finished. It returns false before initialization. The Z80 adapter reads
the owner contexts under a bounded bus lock. Hybrid reports its preparation
frontier; a subsequent music start is prepared after the final SFX page and
plays behind that page. No driver or bank ABI changes are required.

The adapter host tests cover token matching, queued commands, PCM ownership,
and bus ownership. `tests/adapter/sfx_status_test.c` is a focused hybrid target
using the same fixtures. Run
`python tools/adapter_test.py --hybrid --sfx-status --z80-pcm` and repeat
with `--host-pcm`. The existing full
hybrid authored-bank golden trace assertion also fails on the pristine current
Mercurial baseline; the token-query change does not update those golden hashes.
