# 13.32 kHz hybrid prototype

The default hybrid build sequences on the 68000 and fetches PCM on the Z80. See [current limits](status.md) for revision-12 measurements. It remains experimental: strict DAC jitter, fresh-patch onset alignment and physical hardware acceptance are outstanding. The ordinary build still uses the Z80 sequencer at timer 1016.

## Build and use

```text
build.bat --hybrid
python tools/build.py --hybrid --region pal
```

The audition ROM is `audition/out/rom.bin`. Complete expensive initialization before `clp_init`, then call `clp_update` once per video frame on the main thread. Do not call it from interrupts or while holding the Z80 bus. `clp_update` is a no-op in the ordinary build.

Consumer builds can set `CALLIOPE_HYBRID=1` through `make/calliope.mk` or a project configuration header. The shared helper selects `CALLIOPE_HOST_PCM=0`, `driver/hybrid_rom.s80`, the adapter/bus assembly configuration and timer-1020 conversion together. `CALLIOPE_HYBRID=0` selects the pure Z80 mode. See [integration.md](integration.md) for header/Makefile examples and build dependencies.

The previous host PCM preparer remains available with `--hybrid --host-pcm`. That comparison uses `CALLIOPE_HOST_PCM=1` and `driver/hybrid.s80`; its current stress cost is 25.05%. `--hybrid --z80-pcm` is an explicit alias for the new default. These PCM selection switches require `--hybrid` and are mutually exclusive.

The build sets `CALLIOPE_PCM_TIMER=1020` before conversion and rescomp. Direct conversion uses the same environment variable; the default remains 1016. The cache key includes the timer. BGM1-12 stores the timer in little-endian header bytes 19-20 and region in byte 21 (0 NTSC, 1 PAL). The hybrid adapter rejects mismatches. The ordinary adapter requires revision 12 and a matching timer-1016 bank (zero is also accepted in the timer field). This optimization changes the private RAM transport, not the musical bank layout.

Both hybrid modes now decode the same revision-12 conductor/channel pattern layout as the pure Z80 driver. Channel cursors and waits reside on the 68000. Immediate requests apply at control consumption; only major-highlight bar boundaries and explicit markers remain available. The measurements below predate this format change unless identified as revision 12.

## Division of work

The 68000 decodes musical streams, advances sixteen logical voices and four FM/PSG effects, handles arrangements/groups/mutes, resolves ownership, applies music attenuation and schedules cues. Inactive alternatives advance. Restoration uses current logical state and defaults to a fresh note. Volume/physical mutes persist across song starts; stale generation controls are discarded.

The Z80 writes both chips, installs resident FM patches, fetches PCM from cartridge ROM, consumes the local FIFO, maintains the sample clock and publishes status. Music/SFX source changes arrive as descriptors at generation deadlines. PCM remains one exclusive source: accepted SFX replace music, suppressed drum triggers are discarded, and interrupted drums do not resume. The original two-source mixer is not implemented.

Moving sample bytes back to ROM removes the main-thread PCM staging/copy/upload path. Sequencing remains on the 68000, avoiding the older Z80 sequencer's deadline workload. The formerly failing ROM-fetch comparison used a small FIFO and expanded FM patches. The revised fetcher uses the passing prototype's 1024-byte FIFO and resident patch loop. This is a measured workable split, not proof of the theoretical minimum offload.

Genesis Z80 access cannot read 68000 work RAM. Chip packets still use short BUSREQ transfers into local Z80 RAM; sample fetching uses the banked cartridge window. One fetcher owns the serial nine-bit bank register, caches its value, splits transfers at 32 KB boundaries and polls DAC between bank-bit pairs. Assets must remain in linear ROM below 4 MB.

## Decoder and packet scheduling

The musical decoder uses a local cursor and checks fixed-width operands once per opcode. Variable PSG tails are separately bounded before state changes. Waits, validated jumps and failure exits commit the cursor explicitly. The 1024-opcode budget and existing fault behavior remain. Golden traces cover all five songs for 6000 clocks each; timing, output records and stream positions match the earlier implementation.

A private sixteen-command ring keeps the public API high-level. `clp_update` processes controls at the next unprepared nominal 60 Hz clock. Music uses compiled fractional tick credit; SFX keep the nominal 60 Hz clock. A division-free rational accumulator advances regional sample deadlines using master clock / 241920 samples per clock.

The host prepares approximately 1024 samples ahead, about 77 ms NTSC. Requests affect that frontier. Control/SFX latency remains about 77-94 ms plus requested boundaries. Shortening buffering needs separate validation; this optimization does not solve that action-game limitation. Cue handlers cannot change a boundary already prepared.

There are eight 256-byte transport pages. ROM-fetch pages use this header:

| Offset | Field |
|---|---|
| 0 | Record count |
| 1 | PCM action: 0 unchanged, 1 start, 2 stop |
| 2 | Playback/generation deadline u16 |
| 4 | Generation u16 |
| 6 | Song tick u32 |
| 10 | Playing u8 |
| 11 | Completed u8 |
| 12 | Absolute source pointer u24 |
| 15 | First segment length u24 |
| 18 | Absolute loop pointer u24 |
| 21 | Loop length u24 |
| 24 | Next generation deadline u16 |
| 26 | Next deadline known u8 |
| 27 | Reserved |

Up to 76 three-byte records follow: YM port-0, YM port-1, PSG byte or resident-patch installation. All multibyte fields are little-endian. Pages for one clock share its deadline. The host emits directly into seven staging pages, retaining the 512-record-per-clock limit without a second record-to-packet copy.

The producer counter is published last. The Z80 has independent generation and playback consumers; the host reuses pages only after both have consumed them. At most eight pages are outstanding; byte counters wrap. Generation applies source descriptors at the FIFO frontier and stops refilling when the next source-changing timeline is unknown. Playback applies chip records when the sample clock reaches their deadline. A page becomes reusable only after its last chip operation.

Each chip transfer holds the bus for eight payload bytes. Indexing, header construction, capacity checks and pointer arithmetic happen before BUSREQ. The assembly helpers preserve SR and prior bus ownership. Service is bounded to 48 fragments / 384 payload bytes per update; a dense clock continues next update. Cached page cursors avoid repeating preparation for every fragment. The lookahead limit is fixed per call rather than chasing elapsed time.

Static patches are resident in a 63-entry, 32-byte table at 0800h..0FDfh, below the guard at 0FF8h. Banks with more than 63 patches are rejected during compilation and initialization. A dedicated Z80 install operation replaces expanded register records in both hybrid modes. Current TL overrides follow installation. Frequency high/low pairs and key events remain explicit. PSG uses latch bytes and avoids redundant noise-mode writes that restart the LFSR.

## PCM FIFO and DMA

At initialization the Z80 primes 896 silence bytes before starting the timers. Runtime refills begin below a low-water target of 896 queued samples and copy at most 128 bytes, giving a maximum of 1023 queued bytes with one empty ring entry. Copies stop at source/loop ends, next generation deadlines, cartridge boundaries and FIFO-page boundaries. Bulk ROM work copies eight bytes between DAC polls; tails use bounded individual copies. Exact lengths and forward loops are preserved.

DMA protection blocks new cartridge reads and PSG writes. An in-progress refill checks protection between bursts and waits locally with DAC service while protection is active. Resident DAC/FM work can continue. SGDK DMA protection is required; `HALT_Z80_ON_DMA` is unsuitable. Long DMA or main-thread/BUSREQ stalls remain finite-buffer hazards. Underrun outputs silence and sets sticky diagnostics; it does not change the rate or invoke hidden 68000 streaming.

The host-PCM comparison uses a 2048-byte staging ring and an upper queue target of 896. Upload planning clips bounded spans to both ring ends and caches source/destination/publication metadata, refreshing the clock only when another block needs it. Each 32-byte upload is four eight-byte holds with release gaps; the last hold publishes the pointer and sequence. The Z80 checks that sequence around pointer loads and refreshes its cached producer before reporting empty. The recommended ROM-fetch mode does not upload PCM bytes.

## Clock and cues

Timer A=1020 gives 13316.7597 Hz NTSC and 13195.2937 Hz PAL. Timer B=255 spans four Timer-A periods. Bounded recovery bursts replace samples lost to a coalesced A flag and preserve average pitch. They do not provide uniform DAC spacing. Losing entire Timer-B intervals is outside the supported workload. Busy polling does not call DAC while waiting for YM BUSY to clear, because each DAC write would restart that busy interval.

EE commands remain ordinary cues. The host schedules them at audio deadlines and delivers them after the Z80 clock passes. IDs, parameters, original source-channel passthrough and generation filtering remain. A private 256-cue scheduling journal feeds a sixteen-event delivery queue. Overflow preserves queued events, drops new events and saturates the loss counter. Polling consumes at most four delivered records; completion/fault status is separate. Clock/status snapshots reject torn publication.

## Resources and limits

Current workload measurements and unpassed timing gates are summarized in
[status.md](status.md). The ROM-fetch path removes host PCM staging but still
uses packet pages, cue buffers, chip shadows and voice/effect state on the
68000. Budget stack and game work separately. All bank data must remain in
linear cartridge ROM below 4 MB.

## Reproduce validation

```text
python tools/adapter_test.py --hybrid
python tools/adapter_test.py --hybrid --host-pcm
python tools/adapter_test.py
python -m unittest discover -s tests -q
python tools/green_hill_smoke.py --hybrid --frames 18000 --stress --dma
python tools/green_hill_smoke.py --hybrid --frames 3000 --stress --dma --multi-psg-sfx --multi-fm-sfx
python tools/multi_fm_smoke.py --hybrid
python tools/multi_fm_smoke.py --hybrid --automation
python tools/green_hill_smoke.py --hybrid --core blastem --frames 6500 --stress --dma
python tools/hybrid_profile.py --frames 3000 --stress --dma
python tools/hybrid_profile.py --frames 3000
```

Set `CALLIOPE_PCM_TIMER=1020` before `pcm_preemption_smoke.py --hybrid`, `pcm_rom_smoke.py --dma`, or `pcm_alignment_smoke.py --hybrid --dma`. Build PAL and add `--pal` to the Green Hill and ROM PCM tests for regional functional checks. `pcm_rom_smoke.py` requires the ROM-fetch hybrid image.

`trace_dac.py --timer 1020 --rom audition/out/rom.bin --strict` and the alignment test are expected to reject the documented limits and retain diagnostic reports. Add `--no-dma` for the quiet DAC test. Do not relax those gates to accept this optimization.

`tools/build_trace_core.py` builds project-local instrumentation without changing the reference emulator source. Host tests cover decoder truncation/budgets, counter and multi-page wrapping, record overflow, current-state SFX restoration, next-note entry, attenuation, coupled resources, PCM source headers/loops/priority/token stops, stale generations and event overflow. The host-PCM tests also cover upload planning at both ring wraps. The ordinary build retains its own tests.

Multi-channel FM effects share five physical SFX shadows, adding 70 bytes of 68000 state. Their descriptor kind 8 uses physical slot operands, and one shared effect context owns the full authored mask. Independent patches, levels and gates use the existing renderer; allocation and release are atomic across the mask. MODE+A plays the five-channel fixture. See [authoring.md](authoring.md) for source and mask rules.
