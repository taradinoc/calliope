# Sequencer format

BGM1 revision 12 uses shared per-channel patterns in the compiler, pure Z80
driver, Python reference and both hybrid modes. Rebuild matching banks and
drivers together.

## Implemented format

The 24-byte song descriptor retains its order-table pointer. Each u24 order entry now points to a record of `voice_count + 1` u24 pointers: conductor first, then one pattern per logical voice in manifest order. Patterns are shared across orders, channels and songs when their bytes match; they align to 16 bytes and cannot cross a 32 KB ROM bank. This preserves reusable channel phrases at order boundaries. It does not introduce arbitrary phrase calls or a runtime macro interpreter.

Byte 11 of the descriptor stores 1..16 voices in bits 0..4 and PCM synchronization in bit 7. All songs now have voice metadata. Ungrouped songs receive an all-enabled layout and one default arrangement.

The conductor carries tempo, major-highlight bar boundaries, explicit markers and global YM LFO settings. Patch selection stays in each channel pattern. Synchronized external cues run on the last non-PCM channel at the same logical tick, preserving source order; unsynchronized cues remain in the conductor. Legacy conductor patch opcodes 40h..4Fh remain readable. PCM channels run before chip channels; compact patch/note commands and FM/PSG register writes share sample-frontier delivery.

Channel patterns bind their logical voice implicitly. FM music operations omit the redundant physical slot operand:

| Operation | Operands in music |
| --- | --- |
| 10h: instrument | Instrument ID, one byte |
| 11h: note / 36h: pitch update | Hardware frequency, u16 |
| 13h: note off | None |
| 37h: operator levels | Four TL bytes and sensitivity byte |
| 00h: long wait | 1..255 ticks, one byte |
| 80h..FCh: short wait | None; duration is opcode minus 7Fh (1..125 ticks) |
| FDh / FFh | Next order / song end, controlled by the conductor |

PSG/PCM operands retain their existing layouts. SFX instruction operands retain their previous layout, including physical FM slots. Empty rows no longer force waits: waits merge until the next operation or order end. Channel patterns cover the complete order duration; their FDh is a guard terminator, while the conductor loads the next order's complete set of pointers.

Runtime requests support only `CLP_IMMEDIATE`, `CLP_NEXT_BAR` and `CLP_NEXT_MARKER`. Immediate means command consumption, including while paused; held notes are not retriggered during pause. Group controls apply immediately. Bar cues use the Furnace subsong's major (second) highlight interval, counted across orders; zero disables them. That interval represents a musical bar only if the author configured it that way. Explicit markers and external cues retain their authored positions. There are no periodic row/beat cues or tick-boundary requests.

The pure Z80 code ceiling moves to 1700h; logical voice state moves to 1B00h..1BFFh. Channel cursors/waits occupy 1940h..197Fh and a PCM-first schedule occupies 1920h..192Fh. The command queue holds 32 records at 1A00h..1A7Fh; bulk delivery helpers use 1A80h..1AF1h. The shared sequence cache refills twelve bytes from the current cursor, with DMA checks between four-byte groups. PCM lookahead and both guarded stacks retain their previous sizes. See authoring.md for the full allocation.

The sequence-cache refill preserves IX across the PCM low-water refill. PCM
uses IX for its source record, while PSG/FM effects use it for their instance.
Losing that cursor can overwrite the PCM owner when independent effects start
together. `tone_group_smoke.py` now checks a simultaneous single tone, two-tone
group and looping PCM source, including their tokens and cancellation.

Stop, pause and EOF silence unowned music FM channels with TL 127 instead of
leaving a hardware release tail. Authored shadows remain intact for resume;
active effect channels retain their registers. Both pure and hybrid use this
policy. The pure volume helper also preserves its physical-shadow page test
flags while computing an operator address, so every channel uses its own
carrier mask. `volume_smoke.py` checks all five masks, unchanged envelopes,
nonaccumulating gain, immediate music silence and preservation of FM effects.
The hybrid adapter checks stop/pause isolation; its finite melody fingerprint
includes the new EOF silence writes, while the other four traces are unchanged.

