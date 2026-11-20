# Pure Z80 scheduling and revision 13

The resident driver accepts revision 12 and opt-in revision 13 banks. Sequencing,
FM/PSG delivery and PCM service remain on the Z80. No 68000 sequencer service is
required. Authored instruments, notes, sample data and logical musical events
are retained. This contract describes target scheduling, not recovered PCE code.

## Clock and ordered delivery

1. YM Timer B supplies the nominal 60 Hz driver clock. VBlank only polls the DAC;
   a missed video interrupt cannot lose a music tick or require a video catch-up
   burst. Timer A and the PCM conversion rate remain unchanged at timer 1016.
2. A Timer-B increment takes 16128 master clocks. NTSC alternates 55/56 increments
   with a 125/256 fractional carry; PAL alternates 54/55 with 251/256. Nominal
   rates are 59.9981 and 59.9999 Hz. Boot patches the PAL constants before READY.
   Timer register writes retain their busy/settling wait, including Nuked's
   sampled timer-load behavior.
3. Each consumed driver tick receives a fixed consumer-relative deadline, 248
   PCM samples after its clock boundary. Backlogged ticks subtract their earlier
   tick intervals; expired countdowns saturate. Refill chunk endpoints no longer
   choose the FM deadline. Changing music rates still uses fifth-Hz credit.
4. The register queue has 48 four-byte entries at 1A00..1ABF, wrapping at C0.
   An expired head stays due through ownership/PCM retries. Time eligibility is
   tested before the guards; a later head cannot inherit an expired retry.
5. A queued patch restores the saved serial ROM bank directly, with nine latch
   writes and no intervening software service. It never performs a dummy ROM
   read just to restore a bank: such a read can wait through DMA protection after
   the patch has already finished. The caller's ROM cursor is preserved.
6. PCM refills run synchronously on the main stack. FILLRUN prevents recursive
   refill calls. Saved registers and reader state allow RAM-only chip delivery
   during a refill; actual ROM reads still honor PROTECT. Busy patch/SFX contexts
   retain their delivery guards. The retired refill-stack canary remains intact.
7. Pending audio work takes priority over another control command when at least
   two ticks are pending. Command order and the 16-entry command ring are retained;
   a saturated sender receives the existing backpressure refusal instead of
   starving the audio clock. Gain writes serialize one physical voice at a time.

## Revision 13 compiler contract

Set `"z80_schedule": true` in a manifest to request revision 13 with timer 1016.
The default remains revision 12. Hybrid/timer-1020 conversion retains revision 12
and its existing byte stream, even when the manifest carries this option.

The converter resolves the complete song, including order and loop edges, before
packing channel streams. Three implicit-FM operations supplement revision 12:

| Opcode | Operand | Meaning |
| --- | --- | --- |
| 3A | patch ID | Prepare physical operator parameters during a maximum-TL gap |
| 3B | patch ID | Commit the logical patch at its original time; validate the physical ID and otherwise perform the full upload |
| 50..53 | one raw TL | Update one operator while retaining the established sensitivity mask |

Only a patch followed by full levels, pan and a note at the same clock can use
early preparation. At most one upload is placed per clock. The interval must
have maximum TL on the old output carriers, and preparation occurs after its last
TL macro update. The driver mutes all four physical operators before changing B0,
so a new algorithm cannot expose an old non-carrier. Logical patch/levels/pan and
note events remain at their original clocks. A first loop pass retains a full
fallback when no preceding tail upload has occurred.

Physical FM state uses previously unused bytes: FMLEVELS+4 holds valid/prepared
flags (20/80), and +6 holds the installed patch ID. Dirty state after stealing or
arrangement changes rejects preparation. Gain changes skip prepared voices until
commit, preserving their physical all-muted levels. They cannot interleave a
preload between a voice's flag check and its gain writes. Songs with overlapping
physical parts retain ordinary uploads; all 16 logical voices remain supported.

The queue channel byte uses bit 7 for a compact note, bit 6 for a physical
prepatch and bit 5 for a validated short patch. Other writes retain their original
physical register/value representation. Single-TL operations retain logical state
while a blocked or dirty voice waits for its existing full restoration path.

Boot code occupies 1B00..1BFF until parts_load initializes that state page.
The one-time patch-directory loader occupies 1940..197F until channels_load
initializes all 16 cursors; its trampoline retires to RET before that overwrite.
The driver remains 8192 bytes, the PCM FIFO remains 256 bytes, and SFX/command/event
capacities are unchanged. The active main-stack guard remains at 1F78..1F7F.

## Reproduction and evidence limits

Bonk2Gen's integration records commands, hashes and measurements in
`E:/Bonk2Gen/docs/genesis-audio-runtime.md`. Its retained reference-bank comparison
passes all 27 songs, 94 effects and 25 byte-identical patches. The second-area
listening test was confirmed steady with unchanged instrument attacks. Final
NTSC/PAL emulator windows have no faults, PCM underruns or changed canaries;
Nuked checks all authored operator/control values at the measured key-ons.

These results do not establish sample-identical waveforms or stock-hardware
timing. Serial chip writes still have measurable onset spread. The generic
five-fresh-instrument burst remains above its 33 ms spread gate: the retained
pre-change driver measures about 54 ms in the same Nuked fixture, versus about
49 ms in the new driver. Its instrument-register and guard checks pass. Global
strict DAC/jitter acceptance remains open; do not replace that gate with a
fault/underrun check.

The focused converter/control suite passes 95 tests. A broader 289-test run
encountered 13 existing example-bank failures at the converter's 192-byte row
limit; executing the committed pre-change compiler against that same example
bank reproduces the failure. The additional obsolete coroutine-latch assertion
was updated to inspect the direct restore latch and passes its focused check.
