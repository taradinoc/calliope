# Logical parts and arrangements

This is the implemented BGM1-5 subset. The exact DAC timing acceptance gate remains open. The broad effect subset and worst-case sixteen-voice scheduling are not accepted yet.

## Authoring

Each entry in `voices` maps one monophonic Furnace source channel to a physical slot. With groups, arrangements or shared slots, every entry needs a unique nonempty `name`. Channel selectors may use a unique Furnace channel name or an explicit channel index. Extra chip instances provide source channels; they do not imply extra runtime chips.

```json
{
  "voices": [
    {"name":"happy", "channel":0, "slot":"fm0"},
    {"name":"sad", "channel":1, "slot":"fm0"},
    {"name":"bass", "channel":2, "slot":"fm1"}
  ],
  "groups": {"melody":["happy","sad"], "bass":["bass"]},
  "arrangements": {"happy":["happy","bass"], "sad":["sad","bass"]},
  "initial_arrangement":"happy",
  "markers":[{"order":0,"row":10,"id":2}]
}
```

This fragment belongs inside a song descriptor alongside its ID, name and source. The supplied bank contains the complete example. Up to sixteen voices, sixteen overlapping groups and sixteen arrangements are supported. An arrangement cannot enable two voices targeting one physical slot. Empty arrangements can select silence. An empty arrangement table is invalid. Group and arrangement indices follow manifest insertion order; voice mask bits follow the original voice-list order.

Bar boundaries use the selected Furnace subsong's major (second) row highlight interval. This is an author-defined interval, so it represents a musical bar only when the song is configured that way. A zero interval disables automatic bar boundaries. They count rows from the start of the order list; an order boundary does not reset this count. Markers identify explicit order/row positions and repeat when playback revisits that position. There is at most one marker per row. Music uses a fractional 25..120 Hz clock; row durations follow compiled speed/groove operations. SFX retain their independent nominal 60 Hz clock.

## Runtime control

`clp_setParts(mask, boundary, marker)` validates the requested mask and rejects physical-slot conflicts before publishing a command. `clp_selectArrangement(index, boundary, marker)` selects a compiled mask. Boundaries are CLP_IMMEDIATE, CLP_NEXT_BAR or CLP_NEXT_MARKER. Tick, row and beat requests are removed. Immediate requests apply when the playback consumer reads the command, including while paused; they do not wait for a music tick. The marker argument is used only for CLP_NEXT_MARKER. A successful return means queued; it is not an application acknowledgement.

The playback consumer holds one pending part request. The latest consumed request replaces the previous one. A marker request waits until that marker is reached; it has no timeout. Pause freezes musical boundary progress. Starting another song clears the pending request and loads its initial arrangement.

`clp_muteGroups(mask)` combines the selected part mask with the union of the muted groups, then submits that result for immediate application. It therefore supersedes any outstanding arrangement request, including one waiting for a marker. Immediate application while paused does not retrigger a held note. Unmuting keeps the selected arrangement. Physical music muting remains a separate filter. All these controls leave SFX eligible and preserve existing SFX owners.

Disabled FM/PSG streams continue consuming notes, patches and waits. Enabling a part keys off a previous music owner and waits for a fresh note. When SFX releases a slot, the currently selected part installs its current patch at its next note. Unchanged parts are not keyed off by an arrangement change. Disabled PCM parts skip triggers and do not simulate hidden sample phase. Cancellation acts at the mixer generation frontier; queued DAC output remains audible.

`clp_setPartGroups(partIndex, groups)` replaces the membership mask of one logical part in the adapter, then reapplies current group muting immediately. The index is zero-based, not a part bitmask. It supersedes a pending boundary request in the same way as group muting. If the command queue is full, membership and the existing desired mask are preserved. Source annotations never call this API automatically.

EE cues are global operations and survive part/group/physical muting. Simultaneous authored cues retain source-channel order. This does not make their delivery sample-exact.

## Format and RAM revision

BGM1-12 song descriptors remain 24 bytes. Bytes 16..23 hold FM/PSG steal ranks. Bytes 8..10 hold the logical metadata pointer; byte 11 holds the voice count in bits 0..4 and the PCM synchronization flag in bit 7. All music has 1..16 logical voices. Bytes 12..13 hold arrangement count/initial index, and bytes 14..15 the initial music rate times five.

Metadata contains four bytes per voice: physical slot, little-endian group mask and initial enabled byte. Arrangement masks follow. Each order pointer addresses a record containing a conductor pointer followed by one pointer per voice. Patterns share storage across orders and voices. Channel patterns bind their voice implicitly and contain no selection opcode. Conductor operation 0x15 carries flags 8 (bar), 16 (marker), or 24 (both), then a little-endian marker ID. Row/beat cues and scheduler tick boundaries are removed. Explicit markers retain their authored row position.

Logical states now occupy 1B00h..1BFFh; channel cursors/waits occupy 1940h..197Fh. The main code ceiling is 1700h and the synchronized write queue holds 64 records at 1A00h..1AFFh. See authoring.md for the complete RAM map and sequencer-study.md for instruction operands.

Command 11 uses flags for the boundary, the argument low word for the effective voice mask and the high word for the marker ID. The public adapter validates conflicts; the compact Z80 consumer trusts converter-generated metadata and adapter commands. Writing raw mailbox commands bypasses that validation and is not a supported public API.

## Validation

`python tools/parts_smoke.py` exercises the assembled Z80 and 68000 API in an instrumented Genesis Plus GX core. Build that local core with `python tools/build_trace_core.py`. The test verifies six-button controls, uninterrupted bass, advancement of the silent melody, next-note restoration after SFX, group muting and bar/marker application at ticks 97/61. Repeat after a PAL build with `--pal`.

The host tests cover conflicting masks, overlapping groups, the sixteenth mask bit, generated IDs, latest-request replacement, muted cues and boundary timing. The synthetic arrangements232.fur loads through the installed Furnace text exporter. These tests establish functional behavior for the fixtures, not maximum-load or hardware timing acceptance.

The additional volume_smoke.py, psg_controls_smoke.py and macro_smoke.py tests trace live gain, FM/PSG next-note entry, current macro state and next-note restoration after stealing. Use --pal on the PSG and macro checks after a PAL build.
