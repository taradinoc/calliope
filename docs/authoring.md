# Authoring and BGM1 revision 12

This describes implemented behavior, not the entire accepted feature list. Unsupported used effects fail conversion. The strict DAC clock gate remains open.

## Additional consumer formats

Furnace 156 PATR patterns are supported alongside 232 and 248, within the
converter's bounded feature set. Non-linear SN76489 SFX expand raw divider-unit
pitch, volume, duty and explicit noise phase-reset macros at build time.
Sequences can have up to 255 entries; EA legato retains their position. These
SFX require nominal 60 Hz timing; unsupported used effects fail conversion.

Explicit loops can hold a non-linear PSG envelope without a fresh entry note
when a second traversal produces identical register state and reset events
at every tick. Unstable held loops fail. Linear loops retain the rules below.
Raw expansion is bounded to 65536 ticks. Fixed noise notes must use C, C# or D;
coupled noise requires its tone track.

Songs may explicitly override their initial rate with `tick_rate`, on the
same 0.2 Hz grid accepted by the driver. Rates are never silently rounded.
Full FM notes support Furnace's 0..179 range. Inert SM/NE sample metadata on
an FM instrument does not change its patch. Four-operator OPN instruments
retain their individual operator enable flags in music and SFX, including
fresh-note entry. Saved enable flags use the stored operator order
1,3,2,4; conversion swaps the middle bits for YM key-on order 1,2,3,4.
Disabled operators have TL 127 and are excluded from volume sensitivity,
matching Furnace `genesis.cpp` `commitState`. The pure Z80 driver stores the five physical key-on
masks at 1F0Ah..1F0Eh. Two-operator instruments and fixed-block mode remain
unsupported.

## Song LFO

Songs can set `ym_lfo` to the YM2612 register 22 value: 0 disables LFO,
8..15 enable it at the eight hardware rates. This is global chip state,
independent of channel ownership and part muting. Set it when an FM patch
uses amplitude or frequency modulation. For example, `"ym_lfo": 8` matches
the initial LFO write in Bonk's source VGM exports.

If any song in a bank specifies this setting, every song writes its own
value at entry; an omitted value then means 0. This prevents a previous
song's LFO configuration from leaking into the next. Banks without the
setting retain their existing behavior. Tracker LFO-rate changes remain
unsupported. BGM1-11 operation 39 takes one register-value byte. Rebuild
the driver and bank together. Both driver modes implement the operation.

## SFX sources and resources

Use integer PCM WAV for PCM effects. Existing gain, downmixing, regional resampling and forward-loop rules are unchanged. A Furnace effect may contain any combination of FM1..FM5 from one FM instance, or the three tone channels and noise from one PSG instance. FM6 is reserved for PCM. Mixed FM/PSG effects and effects spanning multiple chip instances are rejected. Each participating track must contain notes. The converter infers the type; no type field is needed.

FM and PSG effects share four instances. Each multi-channel effect consumes one instance and owns all participating resources until it ends. Allocation validates all victims and instance capacity before releasing any. Equal-priority effects cannot preempt. Stealing any participating channel terminates the whole effect and releases every channel it owns. Stop-by-token, stop-all and natural completion likewise release the whole set, including persistent loops.

Multi-channel FM effects keep their authored FM1..FM5 positions. Their fixed resource mask uses bits 0..4; FM1 plus FM5 is 17. FM6 is excluded.

Multi-channel PSG effects keep their authored hardware channel positions. Omit `resource_mask` or supply exactly the participating bits: tone0=32, tone1=64, tone2=128, noise=256. For example, tone0 plus tone2 uses 160; all four channels use 480. Noise and coupled effects retain fixed masks of 256 and 384. Single-tone and FM masks select eligible physical channels as before. Channel-local envelopes, pitch, releases and cuts advance independently; global E0 arpeggio speed commands resolve in source-channel order. FF on any participating track ends the whole effect after that row. A shorter stream ends the whole effect at its terminal tick.

For two or three independent tones, set `"relocatable_tones": true` to
allocate any available tone channels instead of fixing their positions.
This option rejects noise tracks and `resource_mask`. Free SFX resources
are selected in physical order, followed by strictly lower-priority owners
if necessary. The complete set must fit before any owner is changed. A
jump on tone0 can therefore continue while a two-tone jingle uses tone1
and tone2. Tone-derived noise retains its fixed tone2/noise pairing.

Relocatable groups use descriptor kind 7; byte 7 is the tone count, 2 or 3.
The converter normalizes packet latches to logical tones 0..count-1, retaining
packet widths and loop offsets. The driver remaps those latches through the
acquired mask; stop, completion and preemption release the entire group.
`tools/tone_group_smoke.py` traces every occupied-tone placement, equal-priority
rejection, three-tone preemption/completion, loops and cancellation. It accepts
`--rom` and `--driver` for a compiled consumer image, with `--consumer-title`
for Bonk's debug menu. Use `--pal` with a PAL build. Both hybrid host adapter
modes also check remapping and ownership.

`examples/multi_fm232.fur` is an original five-channel fixture, bank ID 10 (`CLP_SFX_MULTI_FM`). MODE+A plays it in the audition ROM. `python tools/multi_fm_smoke.py` checks each channel's frequencies and levels, FM6 exclusion, atomic allocation, loops and cancellation. Add `--automation` to trace five distinct patches with volume macros, fine pitch and vibrato. Use `--pal` after a PAL build and `--hybrid` after a hybrid build; hybrid arbitration and cancellation are covered by both adapter test modes. Add `--multi-fm-sfx` to the stress script to include periodic five-channel effects, alongside `--multi-psg-sfx` if desired.

`examples/multi_psg232.fur` is an original four-channel fixture, bank ID 9 (`CLP_SFX_MULTI_PSG`). MODE+DOWN plays it in the audition ROM. `python tools/multi_psg_smoke.py` checks exact chip writes, atomic rejection, preemption, token stop, loops and shared instance capacity in the pure Z80 build; use `--pal` after a PAL build. Add `--hybrid` to check the hybrid ROM's chip trace and audition shortcut. Both hybrid adapter test modes cover ownership, looping and cancellation. Add `--multi-psg-sfx` to `green_hill_smoke.py --stress --dma` to include periodic four-channel effects.

Songs may specify `"fm_steal_order": ["fm4", "fm3", "fm2", "fm1", "fm0"]` to choose which physical FM music channels are stolen first. The list must contain each of fm0..fm4 exactly once, including channels absent from the song. Omission preserves fm0 through fm4 order. The order applies to every arrangement of that song and loads on song start. SFX resource masks still restrict eligible channels, and unused eligible resources take precedence. When all four shared SFX instances are occupied, or every eligible channel is SFX-owned, the existing strictly-lower-priority/oldest-effect policy applies instead. Songs can likewise specify `"psg_steal_order": ["tone2", "tone1", "tone0"]`. This list must contain each of tone0..tone2 exactly once; omission preserves ascending physical order. The same unused-resource and SFX-priority rules apply. Fixed-resource PSG effects do not use these orders. Music continues sequencing while stolen and restores on its next fresh note after release.

Multi-channel effects add descriptor kind 6 within BGM1-11. Descriptor byte 7 holds a compact PSG mask (bits 0..3 for tone0, tone1, tone2, noise); the manifest uses physical resource bits 5..8. Stream packets contain physical PSG latches. The 16-byte descriptor layout and existing effect kinds are unchanged. Rebuild the driver and adapter together to play kind 6. Z80 instance byte 14 holds the acquired PSG mask for kinds 4..7.

Multi-channel FM uses descriptor kind 8 in BGM1-12, preserving kind 7 for relocatable PSG groups. Patch, note, pitch, level, panning and off operands select physical FM slots. Rebuild driver, adapter and banks together.

Revision 8 extends song descriptors from 16 to 24 bytes. Bytes 16..20 store steal ranks for physical fm0..fm4; zero is first to steal. Bytes 21..23 store the corresponding ranks for tone0..tone2. Effect descriptors remain 16 bytes. Rebuild banks with the revision-8 converter and adapter. The active song's eight ranks occupy 1F6Bh..1F72h. Rank loading uses the remaining space in the PSG helper region; assembly checks code and state boundaries. The core ends at 15FFh, PSG helpers at 17FCh, and control helpers at 18D0h. `python tools/fm_sfx_smoke.py` checks both custom orders; use `--pal` after a PAL build.

An effect uses its initial speed sequence on a nominal 60 Hz clock. Initial grooves may have up to sixteen entries, each 1..255 ticks. Music tempo changes do not change SFX duration. Tempo effects, external cues and backward jumps inside SFX remain forbidden. FF or the end of the order list ends a one-shot effect.

## Explicit Furnace SFX loops

Declare a loop in the SFX bank entry:

```json
{
  "id": 8,
  "name": "coupled_loop",
  "source": "coupled232.fur",
  "priority": 2,
  "loop": {"start": [0, 0], "end": [0, 5]}
}
```

Positions are zero-based [order,row] pairs. End is exclusive. [orderCount,0] denotes the end of the song. The start row must contain a fresh note and explicit instrument on each participating channel; noise also needs an explicit 20xx mode. This makes loop entry restore patch, envelope and noise-mode state. The converter splits waits at both boundaries and omits material after end. Initial groove durations are included when locating boundaries. Stop-by-token, stop-all and preemption terminate a persistent effect immediately at the sequencer level. A queued mixed PCM tail is a separate behavior.

## PSG noise

Furnace 2000 selects fixed periodic noise; 2001 selects fixed white noise. Fixed rates use C, C# and D (period divisors corresponding to hardware rates 2, 1 and 0). Other fixed-noise notes are rejected rather than silently remapped.

2010 and 2011 select tone-derived periodic and white noise. SFX using them require both tone-3 and noise tracks. The noise track supplies the derived period using Furnace's noise octave/divider convention. At the same tick, tone-track writes precede noise-track writes, matching the source-channel order. An explicit mode update mutes tone 3 as Furnace does. The control track must contain notes to establish participation.

A mode change currently needs a note on the same row. An explicit 20xx writes the noise control even if unchanged; otherwise unchanged control writes are coalesced to avoid resetting the LFSR. Plain fixed-noise music can use the noise slot and participate in music muting and next-note restoration. Tone-derived noise music uses the noise slot alone. Its period comes from the noise notes; the converter reserves tone2 (physical tone 3) for the whole song and rejects an independent tone2 mapping, including an alternate. Furnace's default noise mode is 2011. Either resource being muted or stolen silences this music pair. Fixed-noise music supports volume sequences. PSG SFX retain their restricted volume-sequence envelope and release semantics.

## Music timing

Music rates range from 25 to 120 Hz in 0.2 Hz steps. The Z80 accrues fractional credit on its regional nominal 60 Hz clock; PAL adds the existing sixth tick every five frames. This is video-relative timing, not a claim of sample-exact wall-clock synchronization.

The converter accepts an initial 1..16-entry speed sequence, 09 groove/first-speed selection, 0F speed selection, C0..C3 direct Hz, and F0 BPM-derived Hz (value times 2/5). It reads GROV blocks from both supported formats. Zero speed values leave speed unchanged. Virtual tempo must be 1:1. Legacy speed selection with a nonuniform sequence is rejected, including sequences introduced by effects.

A looping song must re-enter with the same speed sequence, phase and rate, or explicitly restore them at the loop entry. The converter rejects a mismatched loop. Modern 0B/0D control flow is compiled as described below.

## Experimental binary additions

All multi-byte values are little-endian; offsets are bank-relative 24-bit values. Header byte 4 is 8. Old banks must be rebuilt.

| Structure | Field |
| --- | --- |
| Song descriptor, bytes 14..15 | Initial tick rate times five, u16 |
| SFX descriptor, byte 2 | 1 PCM, 2 FM, 3 tone, 4 noise, 5 coupled |
| FM/PSG SFX descriptor, bytes 8..10 | Loop entry offset; zero means one-shot |
| FM/PSG SFX descriptor, bytes 11..15 | Reserved zero |
| Opcode 33 | Noise control byte, volume latch byte |
| Opcode 34 | New music rate times five, u16 |
| Opcode 35 | PSG automation: byte count, then latch/data bytes; cannot acquire a waiting music voice |
| FM patch byte 31 | Operator mask for additive runtime music attenuation |

Opcode 33 uses bit 3 of its control operand as a force-reset flag; the driver clears that bit before writing hardware. The driver compares the physical noise control shadow and restores it after stealing when necessary. PCM descriptor bytes 7..15 retain their prior length and loop-bound layout. No runtime decompressor was added.

## PSG music macros and fine pitch

PSG tone music accepts bounded normal volume, relative arpeggio and pitch sequences (macro codes 0, 1 and 4). Each sequence has 1..64 steps and its own loop, release, delay and nonzero speed. Signed byte, 16-bit and 32-bit storage are decoded; supported values are volume 0..15, arpeggio -96..96 semitones and pitch -32768..32767. Fixed-note arpeggios, procedural ADSR/LFO macros and other macro codes fail conversion.

Pitch mode zero replaces the fine offset; mode one accumulates it with signed 16-bit saturation. Pitch values use 1/128 semitone and require full linear pitch. Format 232 partial linearity (value 1) is rejected for these operations; format 248 reads the linearPitch compatibility setting. PSG tone effect E5 sets the persistent fine offset (value minus 128); it also requires linear pitch. A fresh note restarts instrument sequences, while note release advances their release portions. Instant release is supported. Delay applies after the first sequence value, as in Furnace; speed controls subsequent steps.

The converter expands these sequences to sparse period/volume updates within shared channel patterns. This spends ROM to avoid a runtime macro interpreter and additional per-voice macro RAM. Equal values produce no operation. The 192-byte per-tick bound still applies. A loop that carries active macro state must have a fresh note or a note-off at its entry; fine pitch changed within a loop must also reset at entry; phase-dependent unrolling across the loop is rejected. FM pitch macros are described below; the broader tracker effect subset remains unfinished.

Opcode 35 updates logical period shadows but cannot clear the physical next-note restoration flag. Opcode 30 retains fresh-note acquisition behavior. Thus silent alternatives keep advancing, and macro pitch changes do not revive a voice after an SFX.

## External commands

A song may set `"external_commands": "passthrough"`. An unmapped EExx then emits event ID xx and parameter equal to the original zero-based Furnace source-channel index. Explicit entries in the events dictionary override passthrough. Repeated commands are preserved. A/B/CA/CB/CC annotations have no special interpretation; application code may use them to call `clp_setPartGroups` or perform any other game action. Muting never suppresses these cues.

Set `"marker_advance_ticks": 30` at the bank root to deliver EExx notifications
30 music ticks before their source rows. A song can override this value;
`"marker_advance_ticks": 0` disables advance for that song. The default is 0.
Values must be integers from 0 to 65535. At a constant 60 Hz tick rate, 30 ticks
is half a second; at 50 Hz it is 0.6 seconds. Tick-rate changes affect elapsed
lead time, while groove and row-speed changes are included in the compiled
timeline. This setting does not apply to SFX.

The converter moves only EExx events, including mapped and passthrough events,
across rows and order boundaries on the resolved playback route. Notes, tempo
commands, bar boundaries, and explicit `markers` used for arrangements retain
their original timing. Events retain their IDs, parameters, duplicates, and
source order when they land on the same tick. Events that would precede song
start clamp to its first tick. Events in a loop clamp to loop entry so they
still fire on the first pass and every repeat; cues close to loop entry can
therefore have less than the requested lead time. Advancing events can bring
more events together, so the existing runtime event-queue capacity still applies.

## Current RAM allocation

The main code has a 5888-byte ceiling at 1700h. Sixteen logical voices use 1B00h..1BFFh, sixteen bytes each. Offsets 0..3 retain slot/group-mask/enabled metadata, offset 4 the FM patch, offsets 5..6 the current FM frequency/block and offset 7 its held gate (zero means off). PSG voices instead use offsets 6..8 for latch/data/volume. Tone-derived noise additionally stores its tone latch/data at offsets 9..10. FM TL shadows occupy offsets 9..13.

A single 32-byte FM patch cache occupies 1700h..171Fh. Banks support up to 255 deduplicated patches (IDs 0..254); patch acquisition copies one bounded 32-byte block with DAC checks and DMA guards; a protected copy yields and restarts after remapping. This replaces the 512-byte resident patch array to make room for code, at the cost of extra cartridge reads when patches change. It does not establish worst-case scheduling feasibility.

PCM descriptor scratch uses 1720h..172Fh, the refill guard/stack 1738h..177Fh, and four SFX states 1800h..183Fh. The sequence cache reserves 1900h..191Fh and refills twelve bytes from the current cursor. A PCM-first channel schedule uses 1920h..192Fh, the synchronization flag uses 193Eh, and sixteen four-byte channel cursor/wait records occupy 1940h..197Fh. Group/LFO helpers occupy 1980h..19FFh. Song patterns align to sixteen bytes and remain inside one 32 KB ROM bank. Context changes invalidate the shared cache. The synchronized command queue occupies 1A00h..1A7Fh (32 four-byte records); patch/note delivery helpers occupy 1A80h..1AF1h. Logical voices use 1B00h..1BFFh, the PCM FIFO 1C00h..1CFFh, and shared ABI 1D00h..1E7Fh. The main stack guard remains 1F78h..1F7Fh. The image remains 8192 bytes. Assembly rejects overlap; measured stack guards supplement, but do not prove, all possible stack bounds.

Tempo scaling uses command 13: boundary flags, scale in the argument low word,
and marker in the high word. Pure-driver state occupies 1D22h..1D2Ah: active
scale:u16, pending boundary:u8, pending scale:u16, marker:u16, effective
fifth-Hz rate:u16. The elapsed-publication sequence remains at 1D21h.

## Authored order flow

Music accepts modern 0B order jumps and 0D pattern breaks. Commands are evaluated in source channel/effect-column order. The last 0B selects the destination order; 0D selects its row, irrespective of the relative placement of the two commands. FF ends playback after its row. A break past the final order ends playback; natural order exhaustion does not imply a loop. Out-of-range explicit jump targets are errors.

The converter follows the reachable source rows, splits bundles at discontinuities and loop entry, and represents the first repeated source coordinate as one terminal loop. This supports an intro plus a loop into the middle of a pattern without adding a runtime branch interpreter. Source order/row coordinates remain in diagnostics and marker lookup. There may be at most 256 compiled bundles, with loop index at most 254 (255 is the no-loop sentinel).

A manifest loop_order remains a source order index, not a compiled bundle index. It must be reachable and agree with an authored loop. Legacy jumpTreatment 1 keeps the first jump/break; treatment 2 allows a later break to replace a jump. Both match the pinned format-232 Furnace source. The ignoreJumpAtEnd flag is honored in both supported file versions. Inherited instrument, volume and noise-mode changes must be reset before the first dependent loop note; timing and active PSG macro loop restrictions still apply. These checks prevent a single compiled loop from silently freezing first-pass state.

## PSG tone SFX sequences

Single-tone SFX now use the same bounded volume/arpeggio/pitch sequence interpreter as music, including delay, speed, looping, release and instant release. Tone E5 and 04 vibrato support full-linear and format-232 partial-linear modes. Tempo changes and external events remain forbidden. A persistent SFX loop that uses E5 or 04 must explicitly reset that effect at the declared loop start; vibrato loops also require per-note phase reset. Sequences are expanded at build time to sparse PSG commands; no runtime macro state was added.

Fixed-noise and combined tone/noise effects retain their noise pitch constraints. Their volume sequences and slides share the tone backend semantics described below.

## FM frequency automation (BGM1-5)

Opcode 11 now contains a slot byte followed by a little-endian 16-bit YM f-number/block. It installs a fresh note and starts its envelope. Opcode 36 has the same operands but updates frequency without key-on, patch installation or acquisition of a music voice waiting for its next fresh note. Opcode 13 still ends a note. Rebuild all banks with the revision-5 driver and adapter.

The converter computes region-specific frequencies with Furnace's absolute-frequency rounding followed by block shifts. This corrects occasional one-unit differences from the former per-semitone rounded tables. It removes the runtime octave calculation and note tables. Logical FM voices retain current frequency and a separate gate, for current pitch and release tracking. Music pitch keeps advancing while its physical voice is stolen; SFX completion still waits for a fresh note.

FM instruments accept bounded relative arpeggio and pitch sequences (codes 1 and 4), using the shared delay/speed/loop/release interpreter. Pitch accumulation saturates to Furnace's signed -1048576..1048575 range; individual sequence values retain the format's bounded range. Full-linear E5 sets the persistent fine offset. Pitch macros require full linearity. Format-232 partial-linear E5, slides and vibrato use the original quantized pitch arithmetic. Loop entry must reset changed fine pitch and restart active macro sequences. These operations work in music and FM SFX.

Remaining note timing effects are not enabled by this revision. Source diagnostics continue to reject them. FM frequency scratch is at 1F60h..1F61h. Current code and memory bounds are in the revision-7 section below.

FM 04xy sine vibrato is supported for full-linear pitch. The high nibble is the phase increment on the 64-step sine table; the low nibble is depth. Its integer table/depth arithmetic follows Furnace playback.cpp, and the effect persists across rows until changed or stopped by 0400. A fresh note resets phase unless continuousVibrato is enabled. Vibrato runs before macro frequency emission, without extra Z80 state. Music loops must preserve the vibrato settings and phase or reset them explicitly; persistent SFX loops require per-note phase reset when vibrato is used. Format-232 partial-linear vibrato is also supported; non-linear mode remains rejected.

FM 01/02 slides, 03 portamento and EA legato are supported under full-linear pitch. Slides advance in 1/128-semitone units, using the module's pitchSlideSpeed multiplier, and persist until cancelled or reaching their target. noSlidesOnFirstTick, limitSlides, targetResetsSlides and note-off slide reset flags are honored. Legacy buggyPortaAfterSlide and brokenPortaLegato modes are rejected when used. Portamento on a held voice emits frequency updates without key-on; reaching its target clears it when targetResetsSlides is enabled. EA retains the current macro/envelope state. Patch changes during portamento/legato remain rejected. Macro-release (REL / note 182) releases macros without key-off; note-off and envelope-release still key off.

Expanded slide loops require a fresh note with an explicit slide restart/cancel at entry. Loops that use EA require EA00 at entry. This restriction prevents replaying first-pass frequency data with a different persistent pitch state. The register-level check `python tools/fm_pitch_smoke.py` (or `--pal` after a PAL build) checks the complete frequency trajectory and counts key-ons independently of the runtime decoder.

## FM panning (BGM1-12 extension)

Furnace `80xx` works in FM music and FM SFX. `8000` selects the left speaker,
`80FF` selects the right speaker, and `8001`..`80FE` select both speakers.
The YM2612 has binary stereo enables, so intermediate values do not change
the balance. An empty value acts as `8000`.
Furnace `08xy` uses the high nibble for the left speaker and the low nibble
for the right speaker. A nonzero nibble enables that speaker: `08F0` is left,
`080F` is right, and `08FF` is both. `0800` (including an empty value) selects
both speakers, matching Furnace's Genesis backend. Nibble magnitudes do not
change volume. The last `08xx` or `80xx` column wins when they share a row.
This follows Furnace's [Genesis panning implementation](https://github.com/tildearrow/furnace/blob/master/src/engine/platform/genesis.cpp).
Panning persists across notes and instrument changes and preserves the current
instrument's AMS/FMS settings. Held-note changes do not restart the envelope.
PSG tracks accept the effect without a stereo change. PCM tracks reject it
because the shared mono mixer cannot pan individual tracks.

Opcode 16 carries slot:u8 and the full YM B4 register value:u8. Music channel
streams omit the slot. Logical FM voices retain it at offset 14, including
while disabled or stolen. The operation never acquires a stolen voice; a fresh
note restores the retained value after patch installation.
A changed pan at a music loop boundary requires an explicit `08xx`/`80xx` or an
instrument at loop entry. Instrument entries emit the first-pass pan so music
and explicit SFX loops repeat consistently. Rebuild the driver when compiling
banks that use this extension. Existing banks keep their original commands.

The pure Z80 driver uses checked helper regions at 1840h..185Fh,
190Ch..191Fh, 1930h..193Dh, 1EB0h..1EBEh, 1EC3h..1EDAh and
1EF7h..1F04h. SFX allocator scratch moves to 1EBFh. The sequence cache still
uses twelve bytes; audio buffers, queue capacity and stack bounds stay the
same. `python tools/fm_pan_smoke.py` checks chip writes, envelope continuity,
SFX pan, stolen shadows and next-note restoration; use `--pal` after a PAL build.

## FM volume automation (BGM1-6)

Furnace FM SFX accept `fm_attenuation` in the bank manifest (integer 0..127, default 0). Each step adds 0.75 dB of attenuation to the patch's volume-sensitive operators; 8 gives the same 6 dB reduction as music volume 119. Conversion applies it once to initial patches and every authored level update, clamps at TL 127, and preserves other operators. It works for single-channel and multi-channel FM effects in both playback backends, without runtime work. Music-volume controls remain independent.

Volume-column changes on held FM notes and bounded volume macros (code 0, values 0..127) are supported in music and SFX. Arpeggio and pitch macros can run alongside them. The converter follows newVolumeScaling and ceilVolumeScaling, combines the authored volume with the macro, and applies the result only to volume-sensitive operators. Four TL values plus a sensitivity mask form each sparse update. Patch identity is independent of volume, so an envelope with many levels does not consume many patches.

Furnace effects `12xx` through `15xx` set operator 1 through 4 total levels (0..127) in FM music and SFX. Lower values are louder. Levels persist across rows and notes, including repeated use of the same instrument; selecting a different instrument restores its levels. Channel volume, volume macros, and SFX attenuation apply to the changed levels through the existing volume-sensitivity rules. Disabled operators stay silent. The converter maps musical operator order to YM register order (1,3,2,4) and emits sparse level updates without creating new patches. Loops must preserve these levels or explicitly reset changed operators at entry.

Opcode 37 operands are slot:u8, tl[4]:u8, sensitivity:u8. It updates logical levels while a part is silent and physical music shadows while a voice is stolen. It writes hardware only for a current audible owner; it never clears the next-note restoration requirement. Fresh restoration installs current levels. Runtime FM gain adds attenuation to these unattenuated shadows, without accumulation. FM SFX ignore music gain.

The five logical bytes occupy offsets 9..13. Five physical shadows occupy 1860h..18FFh with a 32-byte stride; each stores TLs at offsets 0..3 and sensitivity at 31. Both converter and adapter now require revision 6. Tests: `python tools/fm_level_smoke.py` and `--pal` after a PAL build.

## Volume slides

0Axy is supported for FM and PSG music/SFX, including fixed noise and coupled tone/noise SFX. A nonzero low nibble slides down; otherwise the high nibble slides up. Each unit is one quarter of a volume step per tick. Fractional volume and speed persist across rows and new notes; an explicit volume resets the fraction, and 0A00 cancels the speed. noSlidesOnFirstTick suppresses the first slide step of each row. Slides clamp at the channel range and stop, except when the modern noVolSlideReset flag requests persistent speed at the bound. The legacyVolumeSlides underflow mode is rejected when a slide is used.

Slides combine with volume macros before sparse register updates are emitted. They do not add Z80 interpreter state or key-on operations. A compiled loop using slides requires explicit volume and 0A restart/cancel at its entry. Noise SFX use the shared bounded volume-sequence interpreter, including delay, speed, loop and release; pitch/arpeggio macros on noise remain unsupported. `tools/volume_slide_smoke.py` traces tone-SFX slides; `--music` tests music, and `--pal` selects PAL after a regional build.

Noise volume regression: add `--noise` to `tools/volume_slide_smoke.py`, with optional `--music` and `--pal`. The trace checks the exact attenuation sequence and no tone-period writes. Host tests also verify that slides do not repeat the noise-control write, preserving the LFSR phase. Existing paired-resource tests cover atomic acquisition, equal-priority rejection, preemption and pair release.

## Legacy DAC sample selection

Format-232 YM2612 channel 6 can use 17xx to enable legacy sample selection (nonzero) or disable it (zero), and EBxx to select a bank of twelve samples. A note selects bank * 12 + pitch_class; its octave does not transpose playback. The source rate is SMP2's legacy-rate field. An explicit sample instrument switches back to normal instrument selection, using its center rate and pitched note. Missing samples and legacy effects in format 248 are rejected. The runtime still reserves FM6 for DAC, so FM notes while sample mode is disabled are unsupported.

Mode and bank persist across rows. Loop validation requires changed state to be reset before a dependent note, allowing empty rows before the first sample trigger. Note-off/envelope-release stop PCM; macro-release alone has no PCM action because PCM macros remain unsupported. Sample variants deduplicate by source, sample index, playback rate, gain and region.

Song manifests accept `pcm_gain` (default 1.0), a finite nonnegative multiplier combined with authored sample volume before quantization. The converter preserves full signed 8-bit sample amplitude, clamps source gain and resampling overshoot to -128..127, and reports the clipped sample count. The converter adds the DAC bias once when packing the bank. The driver copies these unsigned bytes directly; it does not mix or normalize samples. Accepted PCM SFX replace music PCM and suppress music sample requests until the last queued SFX byte has played. The interrupted music sample is discarded; only a fresh music sample request can play afterward. Equal/lower-priority SFX requests cannot replace an active SFX. Stop/pause/mute of music leave PCM SFX running. Source changes take effect at the generation frontier; already queued audio retains the FIFO latency. The isolated Green Hill Zone DAC fixture uses explicit gain 0.9 and ordinary external-command passthrough; the user asset remains unchanged. `python tools/legacy_pcm_smoke.py` tests its two drum samples with rapid concurrent Chu SFX; use `--pal` after a PAL build.

## Tracker arpeggio

00xy cycles base note, base+x and base+y without restarting the envelope. It is supported on FM and PSG tones in music and SFX, separately from instrument arpeggio macros. E0xx sets the global number of ticks per step; E000 leaves it unchanged. The converter reads each subsong's initial arpeggio speed in formats 232 and 248. Simultaneous E0 commands resolve in source-channel/effect-column order before voice ticks, including the noise track of a coupled SFX.

Phase and countdown persist across rows. compatibleArpeggio, arp0Reset, rowResetsArpPos and resetArpPhaseOnNewNote follow Furnace playback semantics. FM slides/portamento suspend tracker-arpeggio stepping; instrument macros continue as supported by the full-linear profile. PSG fresh notes after key-off clear the old tracker arpeggio before processing the new row's effects. Noise/PCM tracker arpeggios remain unsupported.

Loops must preserve arpeggio countdown/phase or use applicable authored reset behavior; changed global speed must reset at loop entry. A loop that changes compatibleArpeggio's first-note yield is rejected. Persistent SFX loops combining tracker arpeggio with slides/legato are currently rejected rather than approximated. Normal one-shot SFX support the same effects as their music voice type.

`python tools/arpeggio_smoke.py` checks FM music; add `--psg`, `--sfx` and/or `--pal` to cover the other paths. It traces exact frequency/period sequences and checks that FM key-on occurs only for the authored note.

## Delayed cut and release

ECxx cuts a note at tick xx within its row; EC00 has no effect. FCxx releases the note/envelope at tick xx, including FC00 at tick zero. Later valid cut/release columns override earlier ones. Strict policies ignore values at or beyond the row duration. Lax cross-row cuts/releases are rejected with a source diagnostic; row-bounded values work under all recognized policies.

FM cuts clear the logical held gate and key off; release additionally advances released macros. PSG cuts mute immediately at the scheduled tick; release continues the instrument's release sequence. PCM cuts/releases stop the music sample at the generation frontier, retaining the documented mixed-FIFO latency. Cuts neither allocate a stolen voice nor suppress conductor events. The operations are expanded offline and do not add Z80 counters.

EDxx delays FM channel row processing by xx ticks in music and SFX. ED00 processes the row immediately. The previous voice and its pitch/volume automation continue until the delayed row applies its instrument, note, volume, panning, operator levels, and other supported channel effects. Cuts/releases on the delayed row count from the delayed start and must remain within that row. Global timing, flow, and stop effects cannot share a delayed FM row. Strict policy ignores delays at or beyond the row duration; strict-old ignores values beyond it. Lax delays beyond the row are rejected. Delayed cuts/releases exactly at the next row boundary are supported, including instrument and volume columns; other boundary-delayed rows remain rejected. PCM retains its row-bounded ED support; PSG ED and 0C retrigger remain unsupported.

`python tools/note_timing_smoke.py` checks authored cut/release timestamps from chip writes. Add `--delay` for FM ED timing, or `--psg`, `--sfx` and `--pal` for other paths. The SFX fixtures run a silent reference song because the public music tick remains frozen without music.

## Revision 7: Green Hill Zone and resource limits

PCM resampling and the driver share timer-A value 1016: 1152 YM clocks per output sample. The build rejects a mismatch. Rates are 6658.38 Hz NTSC and 6597.65 Hz PAL. The 192-sample output target represents about 29 ms; its 255-sample maximum represents about 39 ms. PCM changes affect the generation frontier. Measured DAC rate/jitter and the unimplemented original-plan features are recorded in status.md.

The main code ends at 159Eh (5534 bytes, ceiling 1600h). PSG ownership helpers occupy 1780h..17FCh; clock helpers start at 1860h. Assembly enforces their region ends. Physical FM level shadows remain compact eight-byte records at 18D8h..18FFh. PCM source state occupies 1EA0h..1EAFh; SFX ownership and end-frontier bookkeeping use 1EC0h..1EC2h. Sync queue head/tail are 16-bit addresses at 1EE8h..1EEBh, followed by count/capture/head-distance bytes at 1EECh..1EEEh. Silence TL constants use 1EF0h..1EF3h; adjacent-write ownership checks use 1EF5h..1EF6h. Instrument installs write the complete patch. The main stack guard is back at 1F78h..1F7Fh, leaving a 128-byte stack. The image remains 8192 bytes.

Revision 11 retains the revision-10 alignment operation 38: mode 0 prepares a tick and marks its PCM generation frontier, mode 1 captures subsequent music YM/PSG writes with that frontier, and mode 2 restores direct writes. PCM start/stop operands are prepared before capture. The DAC consumer releases queued writes when their sample position is reached. Conductor events retain their logical music tick; physical writes take serial CPU time, so this does not claim simultaneous hardware writes.

Opcode 33 accepts tone-derived control values E3/E7. The following PSG operation sets the reserved tone-3 period and mutes its audible tone output. Logical noise state retains the period separately from control/volume. SFX can use either resource, but music cannot resume until the pair is free and a fresh note arrives. Music mode state is at 1F67h; patch refills restart from the cached patch ID.

Legacy PSG base periods round before multiplication by Furnace's quantized reverse-pitch table. Partial-mode instrument pitch macros remain unsupported. PSG vibrato and detune are expanded at build time; loop boundaries must preserve or reset their state. A note-off at a loop entry is a valid macro reset. Cancelled FM slides at both boundaries need no redundant restart command. Active slides retain the conservative loop checks.

Relocatable tone groups and song LFO helpers use 1980h..19FFh. The current revision-12 pure core ends at 16FAh (5882 bytes). PCM lookahead, FIFO and guarded stacks retain their previous sizes. The new channel cursors require relocating logical state and using 32 compact command records. See the current RAM allocation above.

Synchronized instrument changes enqueue a patch ID instead of expanding thirty
YM writes during pattern decoding. Fresh FM notes likewise enqueue their complete
frequency as one command. At the PCM frontier, delivery loads the patch, keys off
the active voice, writes operator groups and applies the new note. Raw operator
groups poll the DAC between four writes; TL writes retain music gain. A voice's
previous instrument remains active until delivery, including when every channel
was already playing. The queue applies backpressure when full. Opcode 40h..4Fh
conductor patch commands remain readable for existing revision-12 banks, but the
compiler keeps instruments in their channel patterns. Synchronized EE events run
on the last non-PCM channel at the same logical tick and retain source order.

The live distance to a newly marked frontier uses a saturating alternate-A
counter. A decoder that reaches an empty queue late therefore cannot mistake a
wrapped FIFO address for a future deadline. Patch-cache access blocks nested
command delivery. Refill waits may service PCM from main, but cannot resume an
already-running producer stack recursively.
