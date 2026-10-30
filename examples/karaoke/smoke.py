"""Check advance scheduling, contacts, hardware scroll and stable lyric pixels."""

import argparse
import importlib.util
import json
from pathlib import Path
import re
import sys

from PIL import Image

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / "tools"))
from libretro_host import Host

# Import the local asset parser explicitly: tools/build.py has the same name.
spec = importlib.util.spec_from_file_location("karaoke_build", HERE / "build.py")
karaoke_build = importlib.util.module_from_spec(spec)
spec.loader.exec_module(karaoke_build)
bank_songs = json.loads((HERE / "bank.json").read_text())["songs"]
source = HERE / bank_songs[0]["source"]
parser = argparse.ArgumentParser()
parser.add_argument("--pal", action="store_true")
parser.add_argument("--song", type=int, choices=range(1, len(bank_songs) + 1), default=1)
parser.add_argument(
    "--lyrics",
    type=Path,
    default=source.with_suffix(".txt"),
    help="lyric source matching the built ROM, including test fixtures",
)
parser.add_argument("--core", type=Path, default=Path("genesis_plus_gx"))
parser.add_argument(
    "--end-only",
    action="store_true",
    help="fast-forward and check blank rows below the final lyric and replay",
)
args = parser.parse_args()
if args.lyrics == source.with_suffix(".txt"):
    args.lyrics = (HERE / bank_songs[args.song - 1]["source"]).with_suffix(".txt")

timing_header = (HERE / f"inc/song{args.song - 1}_timing.h").read_text()
music_rate5 = int(re.search(r"KARAOKE_RATE5 (\d+)", timing_header)[1])
lead_ticks = int(re.search(r"KARAOKE_LEAD_TICKS (\d+)", timing_header)[1])
tick_scale = 250 if args.pal else 300
advance_units = lead_ticks * tick_scale
host = Host(
    args.core,
    HERE / "out/rom.bin",
    HERE / "out/smoke",
    {b"genesis_plus_gx_region_detect": b"pal" if args.pal else b"ntsc"},
)
symbols = (HERE / "out/symbol.txt").read_text()

# Match KaraokeDiagnostics in inc/diagnostics.h (68000 big-endian fields).
debug_address = (
    int(re.search(r"^([0-9a-f]+) \w karaokeDebug$", symbols, re.M)[1], 16) & 65535
)
field_names = (
    "line",
    "wordEnd",
    "started",
    "lineCues",
    "wordCues",
    "fault",
    "eventsLost",
    "completed",
    "scroll",
    "ballX",
    "ballY",
    "contacts",
    "queued",
    "textWrites",
    "clock",
    "lastCueTime",
    "stage",
)
field_offsets = {name: index * 2 for index, name in enumerate(field_names[:14])}
field_offsets.update(clock=28, lastCueTime=32, stage=36, selectedSong=38)


def value(name, length=2):
    address = debug_address + field_offsets[name]
    ram = host.memory()
    return int.from_bytes(bytes(ram[(address + i) ^ 1] for i in range(length)), "big")


word = value
lines = karaoke_build.read_lyrics(args.lyrics)[1]
gaps = [
    tuple(map(int, match))
    for match in re.findall(r"\{(\d+)UL, (\d+)UL, (\d+), (\d+)\}", timing_header)
]


def in_interlude():
    clock = value("clock", 4)
    return any(
        start * tick_scale <= clock < end * tick_scale
        for start, end, bars, intro in gaps
    )


targets = []
visible_spans = []
for line_number, line in enumerate(lines):
    x = (40 - len(line.replace("_", ""))) // 2 * 8
    for match in re.finditer(r"[^ _-]+", line):
        start = len(line[: match.start()].replace("_", ""))
        end = len(line[: match.end()].replace("_", ""))
        targets.append((line_number, match.end(), x + (start + end) * 4))
        visible_spans.append((x + start * 8, x + end * 8))


def await_stage(target, limit=600):
    """Run frames until the presentation reaches the requested end stage."""
    frames = 0
    while word("stage") != target:
        host.run(1)
        frames += 1
        assert word("fault") == word("eventsLost") == 0, (target, frames)
        assert frames < limit, (target, word("stage"))
    return frames


def check_end_image(screen):
    """The end card must match the source BMP's black and lit pixels."""
    bmp = Image.open(HERE.parents[1] / "media/sound-by-calliope.bmp")
    source = bmp.load()
    target = screen.load()
    different = 0
    # Each 16-color group starts with black, so only the phase inside the
    # group matters. The image is drawn unscrolled at the top left.
    for y in range(bmp.height):
        for x in range(bmp.width):
            if ((source[x, y] % 16) == 0) != (target[x, y] == (0, 0, 0)):
                different += 1
    assert different == 0, f"{different} end-card pixels differ from the BMP"


def check_restart_prompt(screen, center_y):
    """The end sequence clears both planes before drawing one centered row."""
    assert set(screen.crop((0, 0, 320, center_y)).get_flattened_data()) == {
        (0, 0, 0)
    }, "End card was not cleared"
    row = set(screen.crop((0, center_y, 320, center_y + 8)).get_flattened_data())
    assert (238, 238, 238) in row, "Restart prompt missing"
    assert set(
        screen.crop((0, center_y + 8, 320, screen.height)).get_flattened_data()
    ) == {(0, 0, 0)}, "Restart prompt is not centered"


def enter_song():
    host.run(4, (3,))  # Start
    host.run(3)
    assert word("stage") == 0
    assert word("contacts") == word("scroll") == word("queued") == 0


def return_to_menu():
    host.run(4, (3,))
    host.run(3)
    assert word("stage") == 3
    frozen = value("clock", 4)
    host.run(60)
    assert value("clock", 4) == frozen
    host.screenshot().save(HERE / "out/smoke/menu-return.png")
    # Start the other song after the end card has replaced palettes and tiles.
    checkpoint = host.save()
    host.run(4, (5,))
    host.run(4)
    other = args.song % len(bank_songs)
    assert word("selectedSong") == other
    enter_song()
    first_line = karaoke_build.read_lyrics(
        (HERE / bank_songs[other]["source"]).with_suffix(".txt"))[1][0]
    first_word = re.search(r"[^ _-]+", first_line)
    for _ in range(5000):
        host.run(1)
        assert word("fault") == word("eventsLost") == 0
        if word("contacts"):
            break
    assert word("contacts") == 1
    assert word("line") == 0 and word("wordEnd") == first_word.end()
    host.load(checkpoint)
    host.run(4)


host.run(60)
assert word("stage") == 3, "Startup song menu missing"
assert word("contacts") == value("clock", 4) == 0
host.screenshot().save(HERE / "out/smoke/menu.png")
# Up wraps from the first song to the last; Down wraps back.
host.run(4, (4,))
host.run(4)
assert word("selectedSong") == len(bank_songs) - 1
host.run(4, (5,))
host.run(4)
assert word("selectedSong") == 0
for _ in range(args.song - 1):
    host.run(4, (5,))
    host.run(4)
assert word("selectedSong") == args.song - 1
enter_song()


if args.end_only:
    try:
        # Fast-forward to the last words. The final wait is shorter than one
        # advance, so stop at the final contact before the end card appears.
        while word("contacts") < len(targets) - 2:
            host.run(60)
            assert word("fault") == word("eventsLost") == 0
        for frame in range(600):
            host.run(1)
            assert word("fault") == word("eventsLost") == 0, frame
            if word("contacts") == len(targets):
                break
        assert word("contacts") == len(targets), "Song did not reach the final word"
        host.run(2)  # deliver the queued highlight
        assert word("line") == len(lines) - 1
        assert word("scroll") == (len(lines) - 1) * 16
        screen = host.screenshot()
        screen.save(HERE / "out/smoke/end-lyric.png")
        center_y = (screen.height // 16) * 8
        assert any(
            pixel != (0, 0, 0)
            for pixel in screen.crop(
                (0, center_y, 320, center_y + 8)
            ).get_flattened_data()
        ), "Final lyric missing"
        assert set(
            screen.crop((0, center_y + 8, 320, screen.height)).get_flattened_data()
        ) == {(0, 0, 0)}, "Old lyrics repeat below the final line"
        await_stage(1)
        host.run(1)
        image = host.screenshot()
        image.save(HERE / "out/smoke/end-image.png")
        check_end_image(image)
        frames = 1 + await_stage(2, limit=300)
        # The image upload can delay one main-loop iteration by a video frame.
        assert abs(frames - -(-advance_units // music_rate5)) <= 2, (
            frames,
            advance_units,
        )
        host.run(1)
        end = host.screenshot()
        end.save(HERE / "out/smoke/end.png")
        check_restart_prompt(end, center_y)
        return_to_menu()
        enter_song()
        assert word("contacts") == word("scroll") == word("queued") == 0
        assert word("stage") == 0
        replay = host.screenshot()
        replay.save(HERE / "out/smoke/replay.png")
        assert (238, 238, 238) in set(
            replay.get_flattened_data()
        ), "Title splash missing on replay"
        print(
            f"Final lyric {len(lines)-1}: trailing rows blank; end card and "
            f"{frames}-frame prompt; replay passed."
        )
    finally:
        host.close()
    sys.exit(0)

try:
    host.run(60)
    assert word("started") == 0
    host.screenshot().save(HERE / "out/smoke/initial.png")
    initial = host.screenshot()
    center_y = (initial.height // 16) * 8
    assert (238, 238, 238) in set(initial.get_flattened_data()), "Title splash missing"
    seen = set()
    contacts = 0
    previous_scroll = 0
    previous_writes = word("textWrites")
    previous_screen = initial
    idle_frames = scroll_frames = prescrolled_lines = 0
    stable_last = False
    capture_active = False
    bounce_frames = []
    pending_highlight = None
    for frame in range(60000):
        host.run(1)
        assert word("fault") == 0, frame
        assert word("eventsLost") == 0, frame
        stage = word("stage")
        scroll = word("scroll")
        assert previous_scroll <= scroll <= (len(lines) - 1) * 16
        interlude = in_interlude()
        if (
            pending_highlight is not None
            and not interlude
            and not stage
            and scroll == word("line") * 16
        ):
            left, right = visible_spans[pending_highlight]
            colors = set(
                host.screenshot()
                .crop((left, center_y, right, center_y + 8))
                .get_flattened_data()
            )
            assert (238, 0, 238) in colors and colors <= {
                (0, 0, 0),
                (238, 0, 238),
            }, pending_highlight
            pending_highlight = None
        # A window overlay or early line scroll moves the expected pixels.
        if interlude or stage or scroll != word("line") * 16:
            pending_highlight = None
        new_contacts = word("contacts")
        if new_contacts != contacts:
            assert new_contacts == contacts + 1
            line, end, x = targets[contacts]
            actual = (word("line"), word("wordEnd"), word("ballX"))
            assert actual == (line, end, x), (contacts, actual, (line, end, x))
            assert word("ballY") == center_y - 8
            clock, due = value("clock", 4), value("lastCueTime", 4)
            assert 0 <= clock - due <= tick_scale + music_rate5, (frame, clock, due)
            assert scroll == line * 16
            pending_highlight = contacts
            contacts = new_contacts
        if scroll != previous_scroll:
            scroll_frames += 1
            # A frame may queue new lyric rows and change the window labels.
            assert word("textWrites") - previous_writes < 600
        if scroll > word("line") * 16 and scroll % 16 == 0:
            prescrolled_lines |= 1 << (scroll // 16)
        assert word("ballY") <= center_y - 8
        screen = host.screenshot()
        # Sprite stays above this area. With no text changes or scroll in
        # either frame, every lyric pixel must remain identical.
        writes = word("textWrites")
        if (
            not interlude
            and not stage
            and scroll == previous_scroll
            and writes == previous_writes
            and stable_last
        ):
            assert (
                screen.crop((0, center_y, 320, initial.height)).tobytes()
                == previous_screen.crop((0, center_y, 320, initial.height)).tobytes()
            ), frame
            idle_frames += 1
        stable_last = (
            not interlude
            and not stage
            and scroll == previous_scroll
            and writes == previous_writes
        )
        previous_scroll, previous_writes, previous_screen = scroll, writes, screen
        if capture_active:
            screen.save(HERE / "out/smoke/active.png")
            assert (98, 0, 98) in set(
                screen.crop((0, center_y - 16, 320, center_y - 8)).get_flattened_data()
            )
            colors = set(
                screen.crop((0, center_y, 320, center_y + 8)).get_flattened_data()
            )
            assert (238, 238, 238) in colors and (238, 0, 238) in colors
            assert (139, 137, 139) in set(
                screen.crop((0, center_y + 16, 320, center_y + 24)).get_flattened_data()
            )
            capture_active = False
        if word("started"):
            line = word("line")
            if line not in seen:
                seen.add(line)
                if line == 1:
                    capture_active = True
        if contacts in (4, 5) and len(bounce_frames) < 70:
            bounce_frames.append(screen.copy())
        if word("completed"):
            break
    assert word("completed"), "Song did not complete"
    assert seen == set(range(len(lines))), seen
    assert word("lineCues") == len(lines)
    assert word("wordCues") == len(targets) - len(lines)
    assert word("wordEnd") == targets[-1][1]
    assert contacts == len(targets) and word("queued") == 0
    assert prescrolled_lines == sum(1 << line for line in range(1, len(lines)))
    assert scroll_frames > 150 and idle_frames > 500
    assert word("textWrites") < 30000, word("textWrites")
    # Playback ends with the end card. It lasts one advance, then the prompt
    # replaces it; a short tail cuts that first wait to the song end.
    assert word("stage") == 1, word("stage")
    host.run(1)
    image = host.screenshot()
    image.save(HERE / "out/smoke/end-image.png")
    check_end_image(image)
    frames = 1 + await_stage(2, limit=300)
    # The image upload can delay one main-loop iteration by a video frame.
    assert abs(frames - -(-advance_units // music_rate5)) <= 2, (
        frames,
        advance_units,
    )
    host.run(1)
    end = host.screenshot()
    end.save(HERE / "out/smoke/end.png")
    check_restart_prompt(end, center_y)
    bounce_frames[0].save(
        HERE / "out/smoke/bounce.gif",
        save_all=True,
        append_images=bounce_frames[1:],
        duration=17,
        loop=0,
    )
    return_to_menu()
    enter_song()
    assert word("started") == 0 and word("line") == 0
    assert word("wordEnd") == 0
    assert word("stage") == 0
    host.run(900)
    host.run(1, (0,))  # Genesis B
    stopped = (word("contacts"), word("scroll"), value("clock", 4))
    host.run(90)
    assert stopped == (word("contacts"), word("scroll"), value("clock", 4))
    host.run(1, (1,))
    host.run(3)
    assert word("contacts") == word("scroll") == word("queued") == 0
    print(
        f"{len(targets)} centered contacts; all {len(lines)-1} later lines scroll before their cue; {idle_frames} stable frames, {scroll_frames} scroll frames; replay passed."
    )
finally:
    host.close()
