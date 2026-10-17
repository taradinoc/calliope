# Minimal SGDK example

Set `GDK` to your SGDK installation. From this directory run SGDK's
`bin/make.exe release` (or use that make on PATH). Open `out/rom.bin` in a
Genesis emulator. A replays, B stops and C plays a PCM click.

Run with `CALLIOPE_HYBRID=1` or `CALLIOPE_REGION=pal` to change modes/region.
Only original synthetic fixtures are used. Read `src/main.c` and the
[consumer guide](../../docs/integration.md) before integrating into a game.

The screen displays `clp_getElapsed()` in music ticks. Pause freezes the count,
loops keep counting, and replay resets it when the queued start is consumed.
Tick duration follows the authored tempo; this is not a millisecond clock.
