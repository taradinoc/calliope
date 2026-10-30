# Karaoke example

This example uses Calliope external-command events to control lyrics and a
bouncing ball. Start with [src/main.c](src/main.c) for the Calliope integration:
receive advance notifications, queue future segments, and apply them when due.
[src/screen.c](src/screen.c) handles SGDK graphics. [build.py](build.py) validates
the lyric assets and generates the presentation tables before building the ROM.
The event IDs, lyric parser, visual clock, and animation rules belong to this
example; Calliope supplies audio playback and timestamped notifications.

Build from the repository root:

```text
python examples/karaoke/build.py
```

Open `examples/karaoke/out/rom.bin` in a Genesis / Mega Drive emulator.
The startup menu lists Another Night and Night by Night. **Up/Down** selects a
song and **Start** plays it. **A** replays during playback. **B** stops playback
and freezes the display. After completion, **Start** returns to the song menu. Hybrid mode is the default, with about 13.32 kHz PCM.
The build also accepts `--region pal`, `--pure-z80`, and `--sgdk PATH`.
See [consumer integration](../../docs/integration.md) for the build requirements.

Each cue targets a segment: a whole word or a syllable separated by `_`.
EEF0 starts a line and its first segment. EEF1 starts each later segment.
`bank.json` sets `external_commands` to `passthrough`. Calliope then sends
EEF0 as event ID `0xf0` and EEF1 as event ID `0xf1`. These IDs have no built-in
lyric meaning. The game assigns their meaning in `receiveCue()`.

With passthrough, `event.parameter` identifies the original Furnace source
channel. This example does not need that value. `event.generation` contains
the generation returned by `clp_start()`. Each accepted replay advances the counter; `clp_poll()`
discards events from older generations. Event type `1` identifies an
external-command notification.

The Calliope calls follow this order:

1. `clp_init(clp_bank, sizeof(clp_bank))` initializes the driver with the ROM bank.
2. `generation = clp_start(song->id)` queues a new playback.
3. `clp_update()` services playback once per video frame.
4. `clp_poll()` returns notifications for the current playback.
5. `clp_status()` reports completion, driver faults, and lost events.

`pollEvents()` uses this pattern. `receiveCue()` is the game's event handler:

```c
ClpEvent events[4];
u16 count, i;

clp_update();
do {
    count = clp_poll(events, 4);
    for (i = 0; i < count; ++i) {
        if (events[i].type == 1) receiveCue(&events[i]);
    }
} while (count == 4);
```

Each call consumes at most four records. Poll full batches again to process
simultaneous notifications promptly. An empty return can also mean that the
call consumed stale records. Continue polling on the next frame.

The bank sets `marker_advance_ticks` to 95. Another Night
runs at 67.2 music ticks per second, so notifications arrive about 1.414 seconds
early. Night by Night runs at 50.4 ticks per second, so its notifications arrive
about 1.885 seconds early. Advance changes the notification time, while audio retains its original
timing.

`event.tick` is the notification's music tick. It is not the future segment's tick.
For this song, add the advance to recover the original cue time:

```c
cue->contactTime = (event->tick + KARAOKE_LEAD_TICKS) * unitsPerTick;
```

`receiveCue()` parses a future segment and adds it to a 32-entry queue. Its
`plannedLine` cursor can run ahead of `currentLine`, which describes the
currently audible lyric. EEF0 also selects the first segment on its line.
`applyDueCues()` changes the segment color only when its contact time arrives.
`updateScroll()` and `updateBall()` use the next queued cue to prepare that
contact.

The visual clock uses integer units: one tick equals `5 * videoHz` units, and
each video frame adds `5 * musicHz`. This supports fractional tick rates in
NTSC and PAL. Notifications correct the clock estimate. Contacts fall on video
frames, so this clock provides frame-level timing rather than sample-level timing.
It starts at replay so the intro meter can run before the first notification.

`screen.c` stores visible lyrics in a scrolling 64-by-64 tilemap. It reuses rows
as new lines enter the screen, so longer songs can exceed the tilemap height.
During playback, it updates segment colors and line status
through VBlank DMA. The VDP scroll register moves the lyrics without a redraw.
The next line scrolls from 15 ticks before its first cue to three ticks before
contact. The ball follows an arc between segment centers. After a long gap, the
next advance notification starts the ball's next flight.

Gaps after a lyric must last at least three times the marker advance to show a
BREAK label. The last word stays visible for one advance before the break screen.
The intro needs only twice the advance, since it has no departing word. Both use
a 32-dash meter that fills until the next flight. The intro label appears below
the title splash. Later breaks hide the ball and cover the lyrics with SGDK's
window plane. Removing the window reveals the existing highlights and scroll.

Break intervals use the resolved song timing, including row-speed and groove
changes. Bar labels are estimates: the build prorates the gap's row count to
exclude the held lyric, then rounds using the subsong's major row highlight
(normally 16 rows per bar). The bar count includes the incoming ball flight;
the placeholder ends one advance before the next contact.

The active line has white unsung segments and magenta sung segments. Previous lines
are dark magenta. Upcoming lines are gray. One blank row separates lyric lines.
The last line stays centered during the final wait.

One marker advance after the final word, the lyric screen is replaced by a
full-screen end card from `media/sound-by-calliope.bmp`. If the song ends before
that wait completes, playback completion starts the card early. One advance
after playback ends, the screen clears and `PRESS START FOR SONG MENU` appears in the
center. **Start** returns to the menu, keeping the previous selection. `res/karaoke.res` compiles the card
as an SGDK `IMAGE`. The card replaces the lyric palettes and the first user tile
block, so `screenReset()` restores both before a replay.

To add a song, add an entry with a unique ID in `bank.json` and put its lyrics
in a `.txt` file beside the `.fur` file with the same stem. Up to eight songs
fit the menu. The build generates per-song `inc/songN_lyrics.h` and
`inc/songN_timing.h` tables, plus `inc/songs.h` to bind each bank ID to its
presentation data. `inc/song.h` defines the shared runtime structure.
`TITLE:` and `ARTIST:` fields supply the opening splash. The splash replaces
lyrics until the first ball appears, and returns on replay. Blank lines and
metadata fields are omitted from the lyric table. Segments end at spaces,
hyphens, underscores, or line boundaries. Underscores stay in the source text
but occupy no display column. For example, `when_ev_er` appears as `whenever`. Its three segments require three cues, with separate
highlights and ball contacts centered on `when`, `ev`, and `er`.
Visible text must use printable ASCII and fit 40 columns (title and artist metadata must fit 38). Each timing header contains
the module's tick rate, effective advance (including a song-level override), the
expected contact count, and break intervals in original music ticks. Edit the source files instead of these generated headers.
Rebuild after changing either source asset or the manifest.

Each song must be non-looping and have one cue per segment. The build
compares every line and segment count against the cues in resolved playback order.
It reports segment mismatches by line number and rejects word cues before the
first line cue. Count validation cannot tell whether a cue matches the sung
syllable's musical timing. The build also rejects tick-rate changes,
simultaneous lyric cues, and cues that would clamp to song start. It requires
at least 40 advance ticks for the initial flight.
Row-speed and groove changes can alter segment spacing. Longer lyrics must still
use the supported line width. Runtime checks still stop playback and show an
error for lost events, queue overflow, or mismatched lyrics, because a missing
cue would shift later segment assignments.

EExx notifications differ from the manifest's `markers` array. That array
defines arrangement boundaries for `CLP_NEXT_MARKER` requests.
`marker_advance_ticks` moves EExx notifications only. It does not move those
boundaries. See [external commands](../../docs/authoring.md#external-commands)
and [arrangements](../../docs/arrangements.md) for the two mechanisms.

`inc/diagnostics.h` defines an emulator snapshot for `smoke.py`. Playback
decisions do not use that snapshot. Run `python examples/karaoke/smoke.py` to
check all contacts, early line scrolling, stable text pixels, errors, stop,
menu return,
and replay. Use `--song 2` to check Night by Night, and `--pal` after a PAL build. Use `--core PATH` to select your
Genesis Plus GX libretro DLL. Screenshots and `bounce.gif` appear in `out/smoke/`.
Run `python -m unittest discover -s tests -p test_karaoke.py` from the repository
root to check asset validation and timing rules without an emulator.
