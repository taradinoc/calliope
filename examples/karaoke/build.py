"""Generate the lyric table and build the standalone karaoke ROM."""
import json
from pathlib import Path
import re
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / "tools"))
from build import main as build_rom
from calliope.furnace import parse
from calliope.timing import rate5, TIMING_EFFECTS, Timing
from calliope.flow import resolve


def write_generated(output, text):
    """Keep unchanged headers intact so SGDK can reuse compiled objects."""
    output.parent.mkdir(parents=True, exist_ok=True)
    if not output.exists() or output.read_text() != text:
        output.write_text(text)


def read_lyrics(source):
    """Separate splash metadata from nonblank lyric lines."""
    metadata = {"TITLE": "", "ARTIST": ""}
    lines = []
    for raw in source.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        key, separator, value = line.partition(":")
        if separator and key in metadata:
            metadata[key] = value.strip()
        elif line:
            lines.append(line)
    for value in metadata.values():
        if len(value) > 38 or any(not 32 <= ord(c) <= 126 for c in value):
            raise ValueError("Title and artist must fit 38 columns and use the SGDK ASCII font")
    return metadata, lines


def validate_cues(lines, cues):
    """Match EEF0/EEF1 counts to the runtime space/hyphen/underscore parser."""
    counts = []
    for tick, event, _ in cues:
        if event == 0xf0:
            counts.append(1)
        elif not counts:
            raise ValueError(f"Word cue at tick {tick} precedes the first line cue")
        else:
            counts[-1] += 1
    expected = [len(re.findall(r"[^ _-]+", line)) for line in lines]
    if len(counts) != len(expected):
        raise ValueError(f"Lyric line count: {len(expected)} lines, {len(counts)} line cues")
    for number, (words, contacts) in enumerate(zip(expected, counts), 1):
        if words != contacts:
            raise ValueError(f"Lyric line {number}: {words} segments, {contacts} cues ({lines[number-1]})")


def generate_lyrics(source, output):
    metadata, lines = read_lyrics(source)
    if not lines:
        raise ValueError("Lyrics must contain at least one nonblank line")
    for line in lines:
        if len(line.replace('_', '')) > 38 or any(ord(char) < 32 or ord(char) > 126 for char in line):
            raise ValueError("Lyrics must fit 38 columns and use the SGDK ASCII font")
        if len(line) > 65535:
            raise ValueError("Lyric source offsets must fit 16 bits")
        if not re.search(r"[^ _-]+", line):
            raise ValueError("Each lyric line must contain a word")
    text = "/* Generated from the lyric text; edit the .txt, then rebuild. */\n"
    text += "#define KARAOKE_TITLE " + json.dumps(metadata["TITLE"]) + "\n"
    text += "#define KARAOKE_ARTIST " + json.dumps(metadata["ARTIST"]) + "\n"
    text += "static const char *const lyrics[] = {\n"
    text += "".join("    " + json.dumps(line) + ",\n" for line in lines)
    text += "};\n#define LYRIC_COUNT (sizeof(lyrics) / sizeof(lyrics[0]))\n"
    write_generated(output, text)


def detect_breaks(cues, lead, rows_per_bar):
    """Return (start tick, end tick, estimated bars, intro) display intervals.

    Cue tuples contain original contact ticks, event IDs, and elapsed rows in
    resolved playback order. Reserve one advance for the incoming ball flight
    and, after lyrics, one for the departing word. Require at least one more
    advance for the placeholder itself. Intro timing starts at song tick zero.
    """
    breaks = []
    previous_tick = previous_row = 0
    for tick, _, row in cues:
        intro = previous_tick == 0
        linger = 0 if intro else lead
        duration = tick - previous_tick
        if duration >= 2 * lead + linger:
            # Furnace's major row highlight defines a bar (usually 16 rows).
            # Prorate rows to exclude the held lyric. This is an estimate when
            # row speeds vary inside the gap; it includes the incoming flight.
            break_rows = (row - previous_row) * (duration - linger) / duration
            bars = max(1, round(break_rows / max(1, rows_per_bar)))
            breaks.append((previous_tick + linger, tick - lead, bars, intro))
        previous_tick, previous_row = tick, row
    return breaks


def generate_timing(source, manifest, output, lyrics_source=None):
    """Validate the playback route and emit the game's visual timing table."""
    module = parse(source)
    bank = json.loads(manifest.read_text())
    item = bank["songs"][0]
    sub = item.get("subsong", 0)
    song = module.subsongs[sub]
    channels = [voice["channel"] for voice in item["voices"]]
    lead = item.get("marker_advance_ticks", bank.get("marker_advance_ticks", 0))
    if type(lead) is not int or not 40 <= lead <= 65535:
        raise ValueError("This animation requires 40..65535 advance ticks")
    route = resolve(module, sub, channels, item.get("loop_order"))
    if route.loop is not None:
        raise ValueError("This lyric cursor requires a song without a loop")
    # The visual clock interpolates this song's constant music tick rate.
    # Row-speed and groove changes alter word spacing, not the tick rate.
    timing = Timing(module, sub)
    elapsed = 0
    previous_cue = None
    # Count rows along the resolved route, including repeated pattern visits.
    cues = []
    row_index = 0
    for bundle in route.bundles:
        for order, row in bundle:
            for channel in channels:
                for _, effect, value in module.cell(sub, order, row, channel).effects:
                    if effect in TIMING_EFFECTS - {0x09, 0x0f}:
                        raise ValueError("The karaoke visual clock requires a constant tick rate")
                    if effect == 0xee and value in (0xf0, 0xf1):
                        if elapsed < lead:
                            raise ValueError("A lyric cue would clamp to song start. Reduce the advance or move the cue")
                        if elapsed == previous_cue:
                            raise ValueError("Put each lyric cue on a separate music tick")
                        previous_cue = elapsed
                        cues.append((elapsed, value, row_index))
            ticks, _ = timing.row(order, row, channels)
            elapsed += ticks
            row_index += 1
    text = (f"#define KARAOKE_RATE5 {rate5(song.hz)}\n"
            f"#define KARAOKE_LEAD_TICKS {lead}\n")
    if lyrics_source is not None:
        validate_cues(read_lyrics(lyrics_source)[1], cues)
    breaks = detect_breaks(cues, lead, song.highlights[1])
    text += "/* Break intervals use original music ticks, before visual-clock scaling. */\n"
    text += "typedef struct { u32 start, end; u16 bars, intro; } KaraokeBreak;\n"
    text += f"#define KARAOKE_BREAK_COUNT {len(breaks)}\n"
    text += "static const KaraokeBreak karaokeBreaks[] = {\n"
    text += "".join(f"    {{{start}UL, {end}UL, {bars}, {int(intro)}}},\n" for start, end, bars, intro in breaks)
    if not breaks:
        text += "    {0, 0, 0, 0},\n"
    text += "};\n"
    write_generated(output, text)


if __name__ == "__main__":
    # The ROM bank and generated lyrics must describe the same song.
    manifest = HERE / "bank.json"
    source = HERE / json.loads(manifest.read_text())["songs"][0]["source"]
    lyrics_source = source.with_suffix(".txt")
    generate_lyrics(lyrics_source, HERE / "inc/lyrics.h")
    generate_timing(source, manifest, HERE / "inc/timing.h", lyrics_source)
    build_rom(HERE, manifest)
